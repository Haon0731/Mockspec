"""Auditable staged scenario inference research controller for M1 variants B/C.

This module consumes a supplied ``UnifiedTask`` payload.  It is deliberately
outside the production package: synthetic replay is research evidence and does
not approve an input for production use.
"""

from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass
from decimal import Decimal
import hashlib
import json
import math
from pathlib import Path
import time
from typing import Annotated, Any, Callable, Literal, TypeVar
from uuid import uuid4

from agents import Agent, ModelBehaviorError, ModelRetrySettings, ModelSettings, RunConfig, Runner
from agents.models.interface import Model
from agents.models.openai_responses import OpenAIResponsesModel
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError

from mock_spec.budget import BudgetHooks, InferenceBudget, UsageLedger
from mock_spec.execution_metadata import collect_execution_metadata
from mock_spec.inference import InferenceError
from mock_spec.scenario import ScenarioContract, validate_contract
from mock_spec.schemas import UnifiedTask
from mock_spec.validation import SourceStore

from query import QueryExecutor


Text = Annotated[str, StringConstraints(min_length=1, pattern=r"\S")]
RECORD_VERSION = "scenario-inference-record/v0.1"
MANIFEST_VERSION = "scenario-inference-manifest/v0.1"
POLICY_VERSION = "staged-scenario-inference/v0.1"
PROMPT_VERSION = "staged-scenario-inference-prompts/v0.1"


class StageModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


StageResult = TypeVar("StageResult", bound=StageModel)


class SourceReference(StageModel):
    source_id: Text
    start_line: Annotated[int, Field(ge=1)]
    end_line: Annotated[int, Field(ge=1)]
    quote: Text


class CandidateDraft(StageModel):
    scenario_id: Text
    description: Text
    evidence: list[SourceReference]


class ExtractResult(StageModel):
    schema_version: Literal["staged-extract/v0.1"]
    result_status: Literal["CONTINUE", "INCONCLUSIVE"] = "CONTINUE"
    original_goals: list[SourceReference]
    candidates: Annotated[list[CandidateDraft], Field(max_length=2)]
    unresolved: list[Text]


class QueryAssessment(StageModel):
    attempt_sequence: Annotated[int, Field(ge=1)]
    impact: Literal["supports", "weakens", "no_new_information"]
    rationale: Text


class QueryResult(StageModel):
    schema_version: Literal["staged-query/v0.1"]
    decision: Literal["request_query", "finish_queries"]
    query_attempt: Any | None
    previous_receipt_assessment: Any | None = None
    impact: Text


class CriticalBehavior(StageModel):
    behavior: Text
    observation: Text
    evidence_ids: Annotated[list[Text], Field(min_length=1)]


class ConstructResult(StageModel):
    schema_version: Literal["staged-construct/v0.1"]
    result_status: Literal["SELECTED", "AMBIGUOUS", "INCONCLUSIVE"]
    selected_scenario_id: Text | None
    selection_rationale: Text
    claim_basis: list["ClaimBasis"]
    critical_behaviors: Annotated[list[CriticalBehavior], Field(max_length=3)]
    allowed_variations: list[Text]
    unresolved: list[Text]
    nonblocking_unknowns: list[Text] = Field(default_factory=list)
    contract: ScenarioContract | None


class ClaimBasis(StageModel):
    claim: Text
    basis: Literal["task_requirement", "implementation_fact", "scenario_inference"]


class ContrastResult(StageModel):
    schema_version: Literal["staged-contrast/v0.1"]
    status: Literal["proposed", "none_identified"]
    execution_status: Literal["not_executed"]
    alternative: Text | None
    bypass_behavior: Text | None
    evidence_ids: list[Text]
    distinguishing_observation: Text | None
    revision_requested: bool
    revision_reason: Text | None


class RevisionResult(StageModel):
    schema_version: Literal["staged-revise/v0.1"]
    revised_construct: ConstructResult
    modified_objects: Annotated[list[Text], Field(min_length=1)]
    evidence_reason: Text
    evidence_ids: Annotated[list[Text], Field(min_length=1)]
    request_another_revision: bool = False


class DeliverResult(StageModel):
    schema_version: Literal["staged-deliver/v0.1"]
    result_status: Literal["SELECTED", "AMBIGUOUS", "INCONCLUSIVE"]
    selected_scenario_id: Text | None
    contract: ScenarioContract | None


@dataclass(frozen=True)
class StagedRunConfig:
    variant: Literal["B", "C"] = "B"
    run_mode: Literal["synthetic_replay", "live_inference"] = "synthetic_replay"
    model_name: str = "scripted-offline"
    max_model_calls: int = 10
    max_query_attempts: int = 6
    max_output_tokens: int = 4_096
    max_total_tokens: int = 60_000
    max_tool_output_chars: int = 16_000
    timeout_seconds: float = 120
    parsing_timeout_seconds: float = 15
    max_cost_usd: Decimal | None = None
    input_cost_per_million: Decimal | None = None
    output_cost_per_million: Decimal | None = None

    def __post_init__(self) -> None:
        if self.variant not in ("B", "C"):
            raise ValueError("variant must be B or C")
        if not isinstance(self.model_name, str) or not self.model_name.strip():
            raise ValueError("model_name is required")
        if self.run_mode not in ("synthetic_replay", "live_inference"):
            raise ValueError("run_mode must be synthetic_replay or live_inference")
        if self.run_mode == "synthetic_replay" and self.model_name != "scripted-offline":
            raise ValueError("synthetic replay requires the scripted-offline model profile")
        if self.run_mode == "live_inference" and self.model_name == "scripted-offline":
            raise ValueError("live inference requires an explicit provider model")
        for name in ("max_model_calls", "max_query_attempts"):
            value = getattr(self, name)
            if type(value) is not int or value < 1:
                raise ValueError(f"{name} must be a positive integer")
        if self.max_query_attempts > 6:
            raise ValueError("max_query_attempts cannot exceed six")
        for name in ("max_output_tokens", "max_total_tokens"):
            value = getattr(self, name)
            if type(value) is not int or value < 1:
                raise ValueError(f"{name} must be a positive integer")
        if type(self.max_tool_output_chars) is not int or self.max_tool_output_chars < 512:
            raise ValueError("max_tool_output_chars must be an integer >= 512")
        if self.max_output_tokens > self.max_total_tokens:
            raise ValueError("max_output_tokens cannot exceed max_total_tokens")
        for name in ("timeout_seconds", "parsing_timeout_seconds"):
            value = getattr(self, name)
            if (not isinstance(value, (int, float)) or isinstance(value, bool)
                    or not math.isfinite(value) or value <= 0):
                raise ValueError(f"{name} must be positive")
        prices = (self.input_cost_per_million, self.output_cost_per_million)
        if (prices[0] is None) != (prices[1] is None):
            raise ValueError("input and output prices must be configured together")
        if self.run_mode == "live_inference" and (
            self.max_cost_usd is None or None in prices
        ):
            raise ValueError("live inference requires cost limit and input/output prices")


class StagedInferenceError(RuntimeError):
    def __init__(self, message: str, *, stage: str, stop_reason: str):
        super().__init__(message)
        self.stage = stage
        self.stop_reason = stop_reason


class _StagedRunComplete(Exception):
    """Internal control flow for a valid terminal result before DELIVER."""

    def __init__(self, result_status: str, stop_reason: str):
        self.result_status = result_status
        self.stop_reason = stop_reason


def canonical_json(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _write_json_exclusive(path: Path, value: object, *, canonical: bool = False) -> None:
    data = canonical_json(value) if canonical else (
        json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    ).encode("utf-8")
    with path.open("xb") as stream:
        stream.write(data)


def _hash_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def _stage_agent(stage: str, output_type: type[StageResult], model: Model,
                 config: StagedRunConfig) -> Agent[None]:
    stage_protocol = ""
    if stage == "QUERY":
        stage_protocol = (
            " You may finish with zero queries when the visible task already resolves the candidates."
            " Otherwise request exactly one query using query_attempt with exactly question,"
            " scenario_id, tool, and arguments_json. Available tools are: inspect_source"
            " {source_id:string,start_line:integer,end_line:integer};"
            " inspect_program_structure {source_id:string}; inspect_dependency_usage"
            " {source_id:string}. arguments_json must encode the named argument object."
            " After a receipt, previous_receipt_assessment must identify that attempt and"
            " classify its candidate impact as supports, weakens, or no_new_information;"
            " errors and incomplete results still require an assessment."
        )
    elif stage == "CONSTRUCT":
        stage_protocol = (
            " Preserve test_objectives as only the original visible goals; place newly"
            " inferred checks in verification_candidates. Every dependency constraint must"
            " state its condition, target call, parameter relationship, allowed return or"
            " exception, and relevant state effect. Require exact values, counts, or order"
            " only when cited evidence supports them. Describe supported same-scenario"
            " variations, or explicitly say none were identified without claiming uniqueness."
            " Keep expected_interactions assertion_policy as not_implied. A test name or an"
            " implementation branch alone does not make an interaction required."
        )
    elif stage == "CONTRAST":
        stage_protocol = (
            " Perform one static, not-executed degeneracy attempt. For proposed, identify an"
            " alternative Mock plan, the supported behavior it bypasses, evidence IDs, and the"
            " smallest distinguishing observation. Use none_identified only after completing"
            " the attempt without an evidence-backed alternative. An AMBIGUOUS result may be"
            " contrasted without selecting either candidate. Request semantic revision only"
            " when the contrast establishes that a legal same-scenario alternative should"
            " replace or change the formal candidates."
        )
    elif stage == "REVISE":
        stage_protocol = (
            " Make at most this one semantic revision and do not request another. Preserve the"
            " original test_objectives. Record every changed field using its exact dotted path"
            " in modified_objects and cite the evidence reason. Put new checks only in"
            " verification_candidates, keep expected_interactions assertion_policy not_implied,"
            " and retain at most two formal candidates. Do not reopen queries."
        )
    instructions = (
        f"{PROMPT_VERSION}\n"
        f"Stage: {stage}. Return only the requested strict structure. "
        "Treat all host identity, usage, and hashes as host-owned facts. "
        "Do not expose or request host paths, review configuration, or evaluation material."
        f"{stage_protocol}"
    )
    return Agent(
        name=f"Staged Scenario Inference {stage}",
        instructions=instructions,
        model=model,
        model_settings=ModelSettings(
            store=False,
            max_tokens=config.max_output_tokens,
            retry=ModelRetrySettings(max_retries=0),
        ),
        tools=[],
        handoffs=[],
        output_type=output_type,
    )


async def _call_stage(
    stage: str,
    output_type: type[StageResult],
    payload: object,
    *,
    model: Model,
    config: StagedRunConfig,
    ledger: UsageLedger,
    responses: list[dict],
    deadline: float,
) -> StageResult:
    if ledger.requests_started >= config.max_model_calls:
        raise StagedInferenceError(
            "model-call limit exhausted", stage=stage, stop_reason="budget_exhausted"
        )
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise StagedInferenceError(
            "run deadline exhausted", stage=stage, stop_reason="budget_exhausted"
        )
    agent = _stage_agent(stage, output_type, model, config)
    try:
        result = await asyncio.wait_for(
            Runner.run(
                agent,
                input=canonical_json(payload).decode("utf-8"),
                max_turns=1,
                hooks=BudgetHooks(ledger, responses),
                run_config=RunConfig(
                    tracing_disabled=True,
                    trace_include_sensitive_data=False,
                    workflow_name=POLICY_VERSION,
                ),
            ),
            timeout=min(remaining, config.parsing_timeout_seconds),
        )
    except asyncio.TimeoutError:
        raise StagedInferenceError(
            "stage timed out", stage=stage, stop_reason="budget_exhausted"
        ) from None
    except InferenceError as exc:
        reason = "budget_exhausted" if exc.category == "budget_exhausted" else "model_error"
        raise StagedInferenceError("stage model call failed", stage=stage, stop_reason=reason) from None
    except (ModelBehaviorError, ValidationError):
        raise StagedInferenceError(
            "stage model output is invalid", stage=stage, stop_reason="output_invalid"
        ) from None
    except Exception:
        raise StagedInferenceError(
            "stage model call failed", stage=stage, stop_reason="model_error"
        ) from None
    if not isinstance(result.final_output, output_type):
        raise StagedInferenceError(
            "stage model output is invalid", stage=stage, stop_reason="output_invalid"
        )
    return result.final_output


def _check_source_reference(reference: SourceReference, task: UnifiedTask) -> None:
    source = SourceStore(task).get(reference.source_id)
    if not source.start_line <= reference.start_line <= reference.end_line <= source.end_line:
        raise ValueError("source reference outside visible input")
    lines = source.text.splitlines()
    quote = "\n".join(lines[
        reference.start_line - source.start_line:
        reference.end_line - source.start_line + 1
    ])
    if quote != reference.quote:
        raise ValueError("source reference quote mismatch")


def _delivery_snapshot(raw: object) -> dict:
    """Accept only unambiguous envelope/version and decimal-line formatting repairs.

    All semantic fields remain mandatory. A malformed JSON document, unknown
    field, missing claim, or changed source/quote cannot establish a snapshot.
    """
    from copy import deepcopy

    if not isinstance(raw, dict) or set(raw) - {
        "schema_version", "result_status", "selected_scenario_id", "contract"
    }:
        raise ValueError("cannot establish delivery semantic snapshot")
    normalized = deepcopy(raw)
    normalized["schema_version"] = "staged-deliver/v0.1"
    contract = normalized.get("contract")
    if isinstance(contract, dict):
        contract["schema_version"] = "mock-spec.scenario/v0.1"
        for reference in contract.get("evidence", []):
            if not isinstance(reference, dict):
                raise ValueError("cannot establish evidence snapshot")
            for key in ("start_line", "end_line"):
                value = reference.get(key)
                if isinstance(value, str) and value.isascii() and value.isdecimal():
                    reference[key] = int(value)
    return DeliverResult.model_validate(normalized).model_dump(mode="json")


async def _deliver_with_repair(
    payload: dict, *, constructed: ConstructResult, task: UnifiedTask,
    model: Model, config: StagedRunConfig, ledger: UsageLedger,
    responses: list[dict], stages: list[dict], repair_log: list[dict], deadline: float,
) -> DeliverResult:
    expected = DeliverResult(
        schema_version="staged-deliver/v0.1", result_status=constructed.result_status,
        selected_scenario_id=constructed.selected_scenario_id, contract=constructed.contract,
    ).model_dump(mode="json")
    response_start = len(responses)
    try:
        delivered = await _call_stage(
            "DELIVER", DeliverResult, payload, model=model, config=config,
            ledger=ledger, responses=responses, deadline=deadline,
        )
    except StagedInferenceError as exc:
        if exc.stop_reason != "output_invalid" and not (
            exc.stop_reason == "budget_exhausted" and len(responses) > response_start
        ):
            raise
        from mock_spec.validation import decode_json

        # Hooks preserve raw responses even when SDK structured parsing fails.
        if len(responses) != response_start + 1:
            raise ValueError("cannot establish delivery snapshot without one response") from None
        output = responses[-1]["output"]
        if len(output) != 1 or output[0].get("type") != "message":
            raise ValueError("cannot establish delivery snapshot from non-message output") from None
        content = output[0].get("content", [])
        if len(content) != 1 or content[0].get("type") != "output_text":
            raise ValueError("cannot establish delivery snapshot from mixed content") from None
        raw = decode_json(content[0]["text"])
        if exc.stop_reason == "budget_exhausted":
            # Actual usage can exhaust the budget before SDK output validation.
            # Never let that ordering publish a known-invalid final response.
            stages.append({"stage": "DELIVER", "unvalidated_result": raw})
            exhausted_delivery = DeliverResult.model_validate(raw)
            if exhausted_delivery.model_dump(mode="json") != expected:
                raise ValueError("budget-exhausted DELIVER changed semantics or evidence")
            raise exc
        stages.append({"stage": "DELIVER", "invalid_result": raw})
        snapshot = _delivery_snapshot(raw)
        if snapshot != expected:
            raise ValueError("invalid DELIVER changed semantics or evidence") from None
        # Exact equality includes source ID, normalized range, and original quote;
        # the already validated construct supplies the source identity proof.
        entry = {"before": raw, "semantic_snapshot": snapshot, "after": None,
                 "status": "requested"}
        repair_log.append(entry)
        try:
            delivered = await _call_stage(
                "FORMAT_REPAIR", DeliverResult,
                {**payload, "stage": "FORMAT_REPAIR", "invalid_delivery": raw,
                 "semantic_snapshot": snapshot,
                 "repair_rule": "Repair envelope/version or decimal line formatting only; "
                                "preserve every semantic field and evidence source/range/quote."},
                model=model, config=config, ledger=ledger, responses=responses, deadline=deadline,
            )
        except StagedInferenceError:
            entry["status"] = "failed"
            raise StagedInferenceError(
                "final format repair failed or its shared budget was exhausted",
                stage="FORMAT_REPAIR", stop_reason="output_invalid",
            ) from None
        entry["after"] = delivered.model_dump(mode="json")
        stages.append({"stage": "FORMAT_REPAIR", "result": entry["after"]})
        if delivered.model_dump(mode="json") != expected:
            entry["status"] = "rejected_semantic_change"
            raise StagedInferenceError(
                "FORMAT_REPAIR changed semantics or evidence",
                stage="FORMAT_REPAIR", stop_reason="output_invalid",
            )
        entry["status"] = "completed"
    else:
        stages.append({"stage": "DELIVER", "result": delivered.model_dump(mode="json")})
    if delivered.model_dump(mode="json") != expected:
        raise ValueError("DELIVER changed the constructed semantics")
    if delivered.contract is not None:
        validate_contract(delivered.contract, task)
    return delivered


def _validate_constructed(
    constructed: ConstructResult, task: UnifiedTask
) -> ScenarioContract | None:
    """Validate state-specific delivery rules and return a revalidated contract."""
    contract = (
        None if constructed.contract is None
        else validate_contract(constructed.contract, task)
    )
    hypotheses = {} if contract is None else {
        item.id: item for item in contract.scenario_hypotheses
    }
    if len(hypotheses) > 2:
        raise ValueError("a staged result may contain at most two candidates")

    if constructed.result_status == "SELECTED":
        if contract is None:
            raise ValueError("SELECTED requires a complete contract")
        selected = constructed.selected_scenario_id
        if selected not in hypotheses:
            raise ValueError("selected scenario is not a current candidate")
        if hypotheses[selected].status != "candidate":
            raise ValueError("selected scenario must have candidate status")
        if constructed.unresolved:
            raise ValueError("SELECTED cannot have blocking unresolved items")
        if not 1 <= len(constructed.critical_behaviors) <= 3:
            raise ValueError("SELECTED requires one to three critical behaviors")
        if not constructed.claim_basis or not constructed.allowed_variations:
            raise ValueError("SELECTED requires claim basis and allowed variations")
    elif constructed.result_status == "AMBIGUOUS":
        if contract is None:
            raise ValueError("AMBIGUOUS requires a complete contract")
        if constructed.selected_scenario_id is not None:
            raise ValueError("AMBIGUOUS cannot select a scenario")
        if len(hypotheses) != 2 or any(
            item.status != "candidate" for item in hypotheses.values()
        ):
            raise ValueError("AMBIGUOUS requires exactly two candidate scenarios")
        if not constructed.unresolved:
            raise ValueError("AMBIGUOUS must preserve the missing selection basis")
    else:
        if constructed.selected_scenario_id is not None:
            raise ValueError("INCONCLUSIVE cannot select a scenario")
        if not constructed.unresolved:
            raise ValueError("INCONCLUSIVE must identify missing material and its impact")

    if contract is not None:
        evidence_ids = {item.id for item in contract.evidence}
        if any(
            not set(item.evidence_ids) <= evidence_ids
            for item in constructed.critical_behaviors
        ):
            raise ValueError("critical behavior references unknown evidence")
    elif constructed.critical_behaviors:
        raise ValueError("critical behaviors require a complete contract")
    return contract


def _validate_contrast(
    contrast: ContrastResult, contract: ScenarioContract
) -> None:
    evidence_ids = {item.id for item in contract.evidence}
    if not set(contrast.evidence_ids) <= evidence_ids:
        raise ValueError("degeneracy contrast references unknown evidence")
    if contrast.status == "proposed":
        if not all((
            contrast.alternative,
            contrast.bypass_behavior,
            contrast.evidence_ids,
            contrast.distinguishing_observation,
        )):
            raise ValueError("proposed contrast requires an alternative, bypass, evidence, and observation")
    elif any((
        contrast.alternative,
        contrast.bypass_behavior,
        contrast.distinguishing_observation,
        contrast.revision_requested,
        contrast.revision_reason,
    )):
        raise ValueError("none_identified contrast cannot propose or revise a candidate")
    if contrast.revision_requested:
        if contrast.status != "proposed" or contrast.revision_reason is None:
            raise ValueError("a revision request requires a proposed contrast and reason")
    elif contrast.revision_reason is not None:
        raise ValueError("revision reason requires a revision request")


def _changed_paths(before: object, after: object, prefix: str = "") -> set[str]:
    if isinstance(before, dict) and isinstance(after, dict) and set(before) == set(after):
        changed: set[str] = set()
        for key in before:
            path = f"{prefix}.{key}" if prefix else key
            changed.update(_changed_paths(before[key], after[key], path))
        return changed
    return set() if before == after else {prefix}


async def execute_staged_run(
    task_payload: object | None = None,
    *,
    output_dir: Path,
    model: Model,
    config: StagedRunConfig | None,
    requested_case_id: str | None = None,
    task_loader: Callable[[], object] | None = None,
    reviewed_snapshot: Path | None = None,
) -> dict:
    """Execute B/C and persist snapshot, record, optional contract, and manifest.

    The output directory must not exist. Once created, every handled attempt gets
    a record and manifest when storage remains available. A valid contract is
    published for SELECTED and AMBIGUOUS, and may be retained for INCONCLUSIVE.
    """
    if output_dir.exists():
        raise FileExistsError(f"output directory already exists: {output_dir}")
    output_dir.mkdir(parents=True, exist_ok=False)
    run_id = str(uuid4())
    started = time.monotonic()
    started_wall = time.time()
    deadline = started + (
        config.timeout_seconds if isinstance(config, StagedRunConfig) else 120
    )
    stages: list[dict] = []
    responses: list[dict] = []
    format_repair_log: list[dict] = []
    contract: ScenarioContract | None = None
    selected_scenario_id: str | None = None
    result_status = "ERROR"
    stop_reason = "input_invalid"
    error_stage: str | None = "INPUT"
    error: dict[str, str] | None = None
    task: UnifiedTask | None = None
    input_hash: str | None = None
    case_id: str | None = None
    language: str | None = None
    constructed: ConstructResult | None = None
    constructed_contract: ScenarioContract | None = None
    contrast: ContrastResult | None = None
    revision_log: list[dict[str, object]] = []
    query_executor: QueryExecutor | None = None
    early_unresolved: list[str] = []
    current_stage = "INPUT"
    adapter_reviewed = False

    budget: InferenceBudget | None = None
    ledger: UsageLedger | None = None

    try:
        current_stage = "CONFIG"
        if not isinstance(config, StagedRunConfig):
            raise StagedInferenceError(
                "staged run configuration is required",
                stage="CONFIG",
                stop_reason="input_invalid",
            )
        if not isinstance(model, Model):
            raise StagedInferenceError(
                "synthetic replay requires an injected SDK Model",
                stage="CONFIG",
                stop_reason="input_invalid",
            )
        synthetic_identity = getattr(model, "synthetic_replay_identity", None)
        if (
            config.run_mode == "synthetic_replay"
            and synthetic_identity != "mockspec-fixed-script/v0.1"
        ):
            raise StagedInferenceError(
                "synthetic replay requires the fixed script model",
                stage="CONFIG",
                stop_reason="input_invalid",
            )
        if config.run_mode == "live_inference" and synthetic_identity is not None:
            raise StagedInferenceError(
                "live inference cannot use a scripted model",
                stage="CONFIG",
                stop_reason="input_invalid",
            )
        if config.run_mode == "live_inference":
            if not isinstance(model, OpenAIResponsesModel):
                raise StagedInferenceError(
                    "live inference requires the OpenAI Responses provider model",
                    stage="CONFIG",
                    stop_reason="input_invalid",
                )
            if str(model.model) != config.model_name:
                raise StagedInferenceError(
                    "live inference model identity differs from configuration",
                    stage="CONFIG",
                    stop_reason="input_invalid",
                )
        if config.run_mode == "live_inference" and reviewed_snapshot is None:
            raise StagedInferenceError(
                "live inference requires a reviewed snapshot",
                stage="CONFIG",
                stop_reason="input_invalid",
            )
        if config.run_mode == "live_inference" and (
            task_payload is not None or task_loader is not None
        ):
            raise StagedInferenceError(
                "live inference cannot accept an injected task payload or loader",
                stage="CONFIG",
                stop_reason="input_invalid",
            )
        if config.run_mode == "synthetic_replay" and reviewed_snapshot is not None:
            raise StagedInferenceError(
                "synthetic replay cannot use a reviewed snapshot",
                stage="CONFIG",
                stop_reason="input_invalid",
            )
        budget = InferenceBudget(
            max_turns=config.max_model_calls,
            max_tool_calls=config.max_query_attempts,
            max_output_tokens=config.max_output_tokens,
            max_total_tokens=config.max_total_tokens,
            max_tool_output_chars=config.max_tool_output_chars,
            analysis_timeout_seconds=config.parsing_timeout_seconds,
            max_cost_usd=config.max_cost_usd,
            input_cost_per_million=config.input_cost_per_million,
            output_cost_per_million=config.output_cost_per_million,
        )
        ledger = UsageLedger(budget)
        current_stage = "INPUT"
        if config.run_mode == "live_inference":
            from mock_spec.adapters.python import load_task as load_reviewed_python_task

            try:
                assert reviewed_snapshot is not None
                task_payload = load_reviewed_python_task(
                    reviewed_snapshot, case_id=requested_case_id or ""
                ).to_dict()
                adapter_reviewed = True
            except Exception as exc:
                raise StagedInferenceError(
                    f"input review failed: {exc}",
                    stage="INPUT",
                    stop_reason="input_invalid",
                ) from None
        elif task_loader is not None:
            if task_payload is not None:
                raise ValueError("provide task_payload or task_loader, not both")
            try:
                task_payload = task_loader()
            except Exception as exc:
                raise StagedInferenceError(
                    f"input review failed: {exc}",
                    stage="INPUT",
                    stop_reason="input_invalid",
                ) from None
        if not isinstance(task_payload, dict):
            raise ValueError("task payload must be an object")
        task = UnifiedTask.from_dict(task_payload)
        canonical_task = task.to_dict()
        input_bytes = canonical_json(canonical_task)
        input_hash = sha256_bytes(input_bytes)
        case_id, language = task.case_id, task.language
        _write_json_exclusive(output_dir / "input_snapshot.json", canonical_task, canonical=True)

        current_stage = "EXTRACT"
        extract = await _call_stage(
            "EXTRACT", ExtractResult,
            {"stage": "EXTRACT", "visible_task": canonical_task}, model=model,
            config=config, ledger=ledger, responses=responses, deadline=deadline,
        )
        stages.append({"stage": "EXTRACT", "result": extract.model_dump(mode="json")})
        for reference in extract.original_goals:
            _check_source_reference(reference, task)
        for candidate in extract.candidates:
            for reference in candidate.evidence:
                _check_source_reference(reference, task)
        if extract.result_status == "INCONCLUSIVE":
            if not extract.unresolved:
                raise ValueError(
                    "early INCONCLUSIVE must identify missing material and its impact"
                )
            early_unresolved = list(extract.unresolved)
            raise _StagedRunComplete("INCONCLUSIVE", "evidence_exhausted")
        if not extract.original_goals or not extract.candidates:
            raise ValueError("continuing EXTRACT requires goals and candidate drafts")
        draft_ids = [item.scenario_id for item in extract.candidates]
        if len(set(draft_ids)) != len(draft_ids):
            raise ValueError("EXTRACT candidate IDs must be unique")

        current_stage = "QUERY"
        query_executor = QueryExecutor(
            task,
            max_attempts=config.max_query_attempts,
            max_output_chars=config.max_tool_output_chars,
            analysis_timeout_seconds=config.parsing_timeout_seconds,
            deadline=deadline,
        )
        candidate_ids = {item.scenario_id for item in extract.candidates}
        pending_receipt: dict[str, object] | None = None
        while True:
            query = await _call_stage(
                "QUERY", QueryResult,
                {
                    "stage": "QUERY",
                    "visible_task": canonical_task,
                    "extract": extract.model_dump(mode="json"),
                    "query_receipts": query_executor.attempts,
                },
                model=model, config=config, ledger=ledger,
                responses=responses, deadline=deadline,
            )
            stages.append({"stage": "QUERY", "result": query.model_dump(mode="json")})
            observed_attempt = None
            if query.decision == "request_query" or query.query_attempt is not None:
                observed_attempt = query_executor.register(query.query_attempt)
            if not ledger.usage_complete:
                if observed_attempt is not None:
                    query_executor.reject(
                        observed_attempt,
                        status="budget_exhausted",
                        code="prior_usage_unknown",
                        diagnostic="Model usage is unknown, so the query cannot be dispatched.",
                    )
                raise StagedInferenceError(
                    "query response usage is unavailable",
                    stage="QUERY",
                    stop_reason="budget_exhausted",
                )
            raw_assessment = query.previous_receipt_assessment
            assessment: QueryAssessment | None = None
            if raw_assessment is not None:
                try:
                    assessment = QueryAssessment.model_validate(raw_assessment)
                except ValidationError:
                    if observed_attempt is not None:
                        query_executor.reject(
                            observed_attempt,
                            status="protocol_error",
                            code="invalid_previous_receipt_assessment",
                            diagnostic="The previous receipt assessment is malformed.",
                        )
                    raise ValueError("QUERY previous receipt assessment is malformed") from None
            if pending_receipt is None:
                if assessment is not None:
                    if observed_attempt is not None:
                        query_executor.reject(
                            observed_attempt,
                            status="protocol_error",
                            code="unexpected_previous_receipt_assessment",
                            diagnostic="No prior receipt exists to assess.",
                        )
                    raise ValueError("QUERY assessed a receipt that does not exist")
            else:
                if assessment is None:
                    if observed_attempt is not None:
                        query_executor.reject(
                            observed_attempt,
                            status="protocol_error",
                            code="missing_previous_receipt_assessment",
                            diagnostic="The prior receipt must be assessed before another query.",
                        )
                    raise ValueError("QUERY must assess the previous receipt")
                if assessment.attempt_sequence != pending_receipt["sequence"]:
                    if observed_attempt is not None:
                        query_executor.reject(
                            observed_attempt,
                            status="protocol_error",
                            code="misassociated_previous_receipt_assessment",
                            diagnostic="The assessment names a different prior receipt.",
                        )
                    raise ValueError("QUERY assessment references the wrong receipt")
                pending_receipt["assessment"] = assessment.model_dump(mode="json")
                pending_receipt["assessment_status"] = "complete"

            if (
                observed_attempt is not None
                and ledger.total_tokens >= config.max_total_tokens
            ):
                query_executor.reject(
                    observed_attempt,
                    status="budget_exhausted",
                    code="total_token_limit",
                    diagnostic="The shared total-token budget is exhausted.",
                )
                raise StagedInferenceError(
                    "total-token limit exhausted before query dispatch",
                    stage="QUERY",
                    stop_reason="budget_exhausted",
                )

            if (
                observed_attempt is not None
                and not observed_attempt["within_budget"]
            ):
                raise StagedInferenceError(
                    "query-call limit exhausted",
                    stage="QUERY",
                    stop_reason="budget_exhausted",
                )

            if query.decision == "finish_queries":
                if observed_attempt is not None:
                    query_executor.reject(
                        observed_attempt,
                        status="protocol_error",
                        code="attempt_after_finish",
                        diagnostic="finish_queries cannot include a query attempt.",
                    )
                    raise ValueError("finish_queries cannot include a query attempt")
                break
            assert observed_attempt is not None
            pending_receipt = await query_executor.execute_registered(
                observed_attempt, candidate_ids=candidate_ids
            )
            receipt_result = pending_receipt["result"]
            if (
                isinstance(receipt_result, dict)
                and receipt_result.get("error_code") == "run_deadline_exhausted"
            ):
                raise StagedInferenceError(
                    "run deadline exhausted",
                    stage="QUERY",
                    stop_reason="budget_exhausted",
                )

        current_stage = "CONSTRUCT"
        constructed = await _call_stage(
            "CONSTRUCT", ConstructResult,
            {"stage": "CONSTRUCT", "visible_task": canonical_task,
             "extract": extract.model_dump(mode="json"),
             "query": query.model_dump(mode="json"),
             "query_receipts": query_executor.attempts}, model=model,
            config=config, ledger=ledger, responses=responses, deadline=deadline,
        )
        stages.append({"stage": "CONSTRUCT", "result": constructed.model_dump(mode="json")})
        constructed_contract = _validate_constructed(constructed, task)
        if constructed_contract is not None:
            contract_ids = {
                item.id for item in constructed_contract.scenario_hypotheses
            }
            if not contract_ids <= set(draft_ids):
                raise ValueError("constructed contract contains an unknown candidate ID")

        if config.variant == "C" and constructed.result_status in ("SELECTED", "AMBIGUOUS"):
            assert constructed_contract is not None
            current_stage = "CONTRAST"
            contrast = await _call_stage(
                "CONTRAST", ContrastResult,
                {"stage": "CONTRAST", "visible_task": canonical_task,
                 "extract": extract.model_dump(mode="json"),
                 "query_receipts": query_executor.attempts,
                 "construct": constructed.model_dump(mode="json")}, model=model,
                config=config, ledger=ledger, responses=responses, deadline=deadline,
            )
            stages.append({
                "stage": "CONTRAST", "result": contrast.model_dump(mode="json")
            })
            _validate_contrast(contrast, constructed_contract)
            if contrast.revision_requested:
                current_stage = "REVISE"
                before_revision = constructed
                revision = await _call_stage(
                    "REVISE", RevisionResult,
                    {"stage": "REVISE", "visible_task": canonical_task,
                     "extract": extract.model_dump(mode="json"),
                     "query_receipts": query_executor.attempts,
                     "construct": before_revision.model_dump(mode="json"),
                     "contrast": contrast.model_dump(mode="json")}, model=model,
                    config=config, ledger=ledger, responses=responses, deadline=deadline,
                )
                stages.append({
                    "stage": "REVISE", "result": revision.model_dump(mode="json")
                })
                before_dump = before_revision.model_dump(mode="json")
                after_dump = revision.revised_construct.model_dump(mode="json")
                changed_objects = _changed_paths(before_dump, after_dump)
                if not changed_objects:
                    raise ValueError("semantic revision must modify the constructed result")
                revised_contract = _validate_constructed(revision.revised_construct, task)
                if revised_contract is None:
                    raise ValueError("semantic revision must preserve a complete contract")
                # A legal replacement can use a new candidate ID. Rebinding an
                # unchanged original goal is allowed; its content and evidence are not.
                original_goals = [
                    item.model_dump(exclude={"scenario_ids"})
                    for item in constructed_contract.test_objectives
                ]
                revised_goals = [
                    item.model_dump(exclude={"scenario_ids"})
                    for item in revised_contract.test_objectives
                ]
                if revised_goals != original_goals:
                    raise ValueError("semantic revision cannot change original test objectives")
                old_ids = {item.id for item in constructed_contract.scenario_hypotheses}
                new_ids = {item.id for item in revised_contract.scenario_hypotheses}
                for old_goal, new_goal in zip(
                    constructed_contract.test_objectives, revised_contract.test_objectives
                ):
                    old_bindings, new_bindings = set(old_goal.scenario_ids), set(new_goal.scenario_ids)
                    if (
                        len(old_goal.scenario_ids) != len(new_goal.scenario_ids)
                        or old_bindings & new_ids != new_bindings & old_ids
                        or not new_bindings - old_bindings <= new_ids - old_ids
                    ):
                        raise ValueError("original goal bindings may only track candidate replacement")
                declared_objects = set(revision.modified_objects)
                if (
                    any(
                        not any(
                            changed == declared or changed.startswith(f"{declared}.")
                            for declared in declared_objects
                        )
                        for changed in changed_objects
                    )
                    or any(
                        not any(
                            changed == declared or changed.startswith(f"{declared}.")
                            for changed in changed_objects
                        )
                        for declared in declared_objects
                    )
                ):
                    raise ValueError("revision modified_objects does not match the semantic diff")
                known_revision_evidence = {
                    item.id for item in (
                        *constructed_contract.evidence, *revised_contract.evidence
                    )
                }
                if not set(revision.evidence_ids) <= known_revision_evidence:
                    raise ValueError("revision references unknown evidence")
                revision_log.append({
                    "modified_objects": revision.modified_objects,
                    "evidence_reason": revision.evidence_reason,
                    "evidence_ids": revision.evidence_ids,
                    "before": before_dump,
                    "after": after_dump,
                })
                constructed = revision.revised_construct
                constructed_contract = revised_contract
                if revision.request_another_revision:
                    raise ValueError("a second semantic revision request is not allowed")

        current_stage = "DELIVER"
        delivered = await _deliver_with_repair(
            {"stage": "DELIVER", "visible_task": canonical_task,
             "extract": extract.model_dump(mode="json"),
             "query": query.model_dump(mode="json"),
             "query_receipts": query_executor.attempts,
             "construct": constructed.model_dump(mode="json"),
             "host": {"run_id": run_id, "case_id": case_id, "language": language,
                      "input_hash": input_hash, "variant": config.variant}}, model=model,
            constructed=constructed, task=task, stages=stages, repair_log=format_repair_log,
            config=config, ledger=ledger, responses=responses, deadline=deadline,
        )
        contract = delivered.contract
        selected_scenario_id = delivered.selected_scenario_id
        result_status, stop_reason, error_stage = (
            delivered.result_status,
            (
                "evidence_exhausted"
                if delivered.result_status == "INCONCLUSIVE"
                else "completed"
            ),
            None,
        )
    except _StagedRunComplete as exc:
        result_status, stop_reason, error_stage = (
            exc.result_status, exc.stop_reason, None
        )
    except (ValueError, TypeError, KeyError) as exc:
        contract = None
        selected_scenario_id = None
        result_status = "ERROR"
        error_stage = "INPUT" if task is None else current_stage
        stop_reason = "input_invalid" if task is None else "output_invalid"
        error = {"type": type(exc).__name__, "message": str(exc)}
    except StagedInferenceError as exc:
        error_stage, stop_reason = exc.stage, exc.stop_reason
        error = {"type": type(exc).__name__, "message": str(exc)}
        if exc.stop_reason == "budget_exhausted" and exc.stage == "REVISE":
            contract = None
            selected_scenario_id = None
            result_status = "INCONCLUSIVE"
            early_unresolved = [
                "Contrast requested a semantic revision but the shared budget ended; "
                "the unrevised draft is diagnostic only and downstream generation is blocked."
            ]
        elif exc.stop_reason == "budget_exhausted" and constructed is not None:
            contract = constructed_contract
            selected_scenario_id = constructed.selected_scenario_id
            result_status = constructed.result_status
        elif exc.stop_reason == "budget_exhausted":
            contract = None
            selected_scenario_id = None
            result_status = "INCONCLUSIVE"
            early_unresolved = [
                "The shared budget ended before a complete contract could be "
                "constructed; downstream generation is blocked."
            ]
        else:
            contract = None
            selected_scenario_id = None
            result_status = "ERROR"
    except asyncio.CancelledError:
        contract = None
        selected_scenario_id = None
        result_status = "INCONCLUSIVE"
        error_stage, stop_reason = current_stage, "budget_exhausted"
        error = {
            "type": "CancelledError",
            "message": "The staged run was cancelled and active analysis was reclaimed.",
        }
        early_unresolved = [
            "The run was cancelled before a complete contract could be constructed."
        ]
    except Exception as exc:
        contract = None
        selected_scenario_id = None
        result_status = "ERROR"
        error_stage, stop_reason = "ARTIFACT", "artifact_error"
        error = {"type": type(exc).__name__, "message": "unexpected staged run failure"}

    if isinstance(config, StagedRunConfig) and config.variant == "C":
        degeneracy_contrast = (
            {
                "status": "unavailable",
                "execution_status": "not_executed",
                "alternative": None,
                "bypass_behavior": None,
                "evidence_ids": [],
                "distinguishing_observation": None,
                "revision_requested": False,
                "revision_reason": None,
            }
            if contrast is None
            else contrast.model_dump(mode="json", exclude={"schema_version"})
        )
    else:
        degeneracy_contrast = {
            "status": "disabled_by_variant",
            "execution_status": "not_executed",
            "alternative": None,
            "bypass_behavior": None,
            "evidence_ids": [],
            "distinguishing_observation": None,
            "revision_requested": False,
            "revision_reason": None,
        }

    record: dict[str, object] = {
        "schema_version": RECORD_VERSION,
        "run_id": run_id,
        "requested_case_id": requested_case_id,
        "case_id": case_id,
        "language": language,
        "input_hash": input_hash,
        "result_status": result_status,
        "stop_reason": stop_reason,
        "error_stage": error_stage,
        "selected_scenario_id": selected_scenario_id,
        "selection_rationale": None if constructed is None else constructed.selection_rationale,
        "claim_basis": [] if constructed is None else [
            item.model_dump(mode="json") for item in constructed.claim_basis
        ],
        "query_log": [] if query_executor is None else query_executor.attempts,
        "query_accounting": {
            "observed_attempts": 0 if query_executor is None else query_executor.observed_attempts,
            "budgeted_attempts": 0 if query_executor is None else query_executor.budgeted_attempts,
            "limit": (
                config.max_query_attempts
                if isinstance(config, StagedRunConfig) else None
            ),
        },
        "critical_behaviors": [] if constructed is None else [
            item.model_dump(mode="json") for item in constructed.critical_behaviors
        ],
        "allowed_variations": [] if constructed is None else constructed.allowed_variations,
        "nonblocking_unknowns": (
            [] if constructed is None else constructed.nonblocking_unknowns
        ),
        "degeneracy_contrast": degeneracy_contrast,
        "revision_log": revision_log,
        "format_repair_log": format_repair_log,
        "unresolved": (
            early_unresolved or ([] if constructed is None else constructed.unresolved)
        ),
        "stages": stages,
        "model_responses": responses,
        "error": error,
    }
    record_path = output_dir / "inference_record.json"
    _write_json_exclusive(record_path, record)
    contract_path = output_dir / "scenario_contract.json"
    if contract is not None:
        _write_json_exclusive(contract_path, contract.model_dump(mode="json"))

    metadata = collect_execution_metadata()
    research_root = Path(__file__).parent
    research_hashes = {
        path.name: sha256_bytes(path.read_bytes())
        for path in (
            research_root / "controller.py",
            research_root / "query.py",
            research_root / "scripted.py",
            research_root / "run.py",
        )
    }
    artifact_hashes = {
        "input_snapshot.json": (
            _hash_file(output_dir / "input_snapshot.json")
            if (output_dir / "input_snapshot.json").is_file() else None
        ),
        "inference_record.json": _hash_file(record_path),
        "scenario_contract.json": _hash_file(contract_path) if contract_path.is_file() else None,
    }
    valid_config = config if isinstance(config, StagedRunConfig) else None
    manifest = {
        "schema_version": MANIFEST_VERSION,
        "run_mode": (
            "synthetic_replay" if valid_config is None else valid_config.run_mode
        ),
        "input_origin": (
            "synthetic_fixture"
            if valid_config is None or valid_config.run_mode == "synthetic_replay"
            else (
                "reviewed_python_adapter" if adapter_reviewed
                else "unvalidated_review_request"
            )
        ),
        "adapter_reviewed": adapter_reviewed,
        "variant": None if valid_config is None else valid_config.variant,
        "run_id": run_id,
        "requested_case_id": requested_case_id,
        "case_id": case_id,
        "language": language,
        "input_hash": input_hash,
        "versions": {
            "policy": POLICY_VERSION,
            "prompts": PROMPT_VERSION,
            "record": RECORD_VERSION,
            "contract": "mock-spec.scenario/v0.1",
        },
        "configuration": None if valid_config is None else {
            key: str(value) if isinstance(value, Decimal) else value
            for key, value in asdict(valid_config).items()
        },
        "model_identity": {
            "configured_name": (
                None if valid_config is None else valid_config.model_name
            ),
            "script": getattr(model, "synthetic_replay_identity", None),
        },
        "code": {
            "commit": metadata.code_commit,
            "dirty": metadata.code_dirty,
            "files_sha256": metadata.code_files_sha256,
            "research_files_sha256": research_hashes,
        },
        "result_status": result_status,
        "stop_reason": stop_reason,
        "error_stage": error_stage,
        "budget": None if budget is None else {
            key: str(value) if isinstance(value, Decimal) else value
            for key, value in asdict(budget).items()
        },
        "usage": None if ledger is None else {
            **ledger.to_dict(),
            "usage_source": (
                "scripted_fixture"
                if valid_config is None or valid_config.run_mode == "synthetic_replay"
                else "provider_response"
            ),
            "remote_cost_applicability": (
                "not_applicable"
                if valid_config is None or valid_config.run_mode == "synthetic_replay"
                else (
                    "host_priced_estimate"
                    if ledger is not None and ledger.prices_configured
                    else "price_unavailable"
                )
            ),
        },
        "query_accounting": {
            "observed_attempts": 0 if query_executor is None else query_executor.observed_attempts,
            "budgeted_attempts": 0 if query_executor is None else query_executor.budgeted_attempts,
            "limit": None if valid_config is None else valid_config.max_query_attempts,
        },
        "deadline": {
            "clock": "time.monotonic",
            "started": started,
            "absolute": deadline,
            "wall_clock_started_unix": started_wall,
            "wall_clock_deadline_unix": started_wall + (deadline - started),
        },
        "elapsed_seconds": time.monotonic() - started,
        "artifact_sha256": artifact_hashes,
    }
    _write_json_exclusive(output_dir / "run_manifest.json", manifest)
    return {"record": record, "contract": contract, "manifest": manifest}


__all__ = [
    "ConstructResult", "ContrastResult", "DeliverResult", "ExtractResult", "QueryResult",
    "RevisionResult",
    "StagedInferenceError", "StagedRunConfig", "canonical_json", "execute_staged_run",
]
