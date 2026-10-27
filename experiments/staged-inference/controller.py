"""Auditable staged scenario inference research controller for M1 variants B/C.

This module consumes a supplied ``UnifiedTask`` payload.  It is deliberately
outside the production package: synthetic replay is research evidence and does
not approve an input for production use.
"""

from __future__ import annotations

import asyncio
from copy import deepcopy
from dataclasses import asdict, dataclass
from decimal import Decimal
import hashlib
import json
import math
import os
from pathlib import Path
import re
import time
from typing import Annotated, Any, Callable, Literal, TypeVar
from uuid import uuid4

from agents import Agent, ModelBehaviorError, ModelRetrySettings, ModelSettings, RunConfig, Runner
from agents.agent_output import AgentOutputSchema
from agents.models.interface import Model
from agents.models.openai_responses import OpenAIResponsesModel
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError

from mock_spec.budget import BudgetHooks, InferenceBudget, UsageLedger
from mock_spec.execution_metadata import collect_execution_metadata
from mock_spec.inference import InferenceError
from mock_spec.scenario import ScenarioContract, validate_contract
from mock_spec.schemas import UnifiedTask
from mock_spec.source_repository import ReviewedRepository
from mock_spec.validation import SourceStore

from query import QueryExecutor, validate_query_request


Text = Annotated[str, StringConstraints(min_length=1, pattern=r"\S")]
RECORD_VERSION = "scenario-inference-record/v0.3"
MANIFEST_VERSION = "scenario-inference-manifest/v0.1"
POLICY_VERSION = "staged-scenario-inference/v0.3"
PROMPT_VERSION = "staged-scenario-inference-prompts/v0.3"
CITATION_MODE = "source_locator_host_materialized_quote/v1"
FLASK_ABORT_CASE_ID = "flask_308"
CLOUDFOUNDRY_APPLICATION_URI_CASE_ID = (
    "de.codecentric.boot.admin.client.registration."
    "CloudFoundryApplicationFactoryTest#should_use_application_uri@d0085edf"
)
REVIEWED_LIVE_CASES: dict[str, Literal["python", "java"]] = {
    FLASK_ABORT_CASE_ID: "python",
    CLOUDFOUNDRY_APPLICATION_URI_CASE_ID: "java",
}


def reviewed_live_case_language(case_id: str | None) -> Literal["python", "java"]:
    """Resolve one explicitly reviewed live case without opening generic input paths."""
    try:
        return REVIEWED_LIVE_CASES[case_id or ""]
    except KeyError:
        raise ValueError("live inference requires an explicitly reviewed case") from None


class StageModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


StageResult = TypeVar("StageResult", bound=StageModel)


class SourceReference(StageModel):
    source_id: Text
    start_line: Annotated[int, Field(ge=1)]
    end_line: Annotated[int, Field(ge=1)]
    quote: Text


class SourceLocator(StageModel):
    source_id: Text
    start_line: Annotated[int, Field(ge=1)]
    end_line: Annotated[int, Field(ge=1)]


class EvidenceSelection(SourceLocator):
    id: Text
    rationale: Text


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


class CandidateSelection(StageModel):
    scenario_id: Text
    description: Text
    evidence: list[SourceLocator]


class ExtractSelection(StageModel):
    schema_version: Literal["staged-extract/v0.2"]
    result_status: Literal["CONTINUE", "INCONCLUSIVE"] = "CONTINUE"
    original_goals: list[SourceLocator]
    candidates: Annotated[list[CandidateSelection], Field(max_length=2)]
    unresolved: list[Text]


class QueryAssessment(StageModel):
    attempt_sequence: Annotated[int, Field(ge=1)]
    impact: Literal["supports", "weakens", "no_new_information"]
    rationale: Text


class QueryAssessmentSelection(StageModel):
    """Model-facing assessment; the host owns receipt association."""

    impact: Literal["supports", "weakens", "no_new_information"]
    rationale: Text


class QueryAttempt(StageModel):
    """Provider-facing shape; host validation still follows attempt registration."""

    question: Text
    scenario_id: Text
    tool: Text
    arguments_json: Text


class RemainingUnknown(StageModel):
    question: Text
    impact: Text
    blocking: bool


class ExplorationStop(StageModel):
    reason: Literal["evidence_sufficient", "unknowns_nonblocking", "no_further_evidence"]
    rationale: Text
    evidence: list[SourceReference]
    remaining_unknowns: list[RemainingUnknown]


class ExplorationStopSelection(StageModel):
    reason: Literal["evidence_sufficient", "unknowns_nonblocking", "no_further_evidence"]
    rationale: Text
    evidence: list[SourceLocator]
    remaining_unknowns: list[RemainingUnknown]


class QueryResult(StageModel):
    schema_version: Literal["staged-query/v0.1"]
    decision: Literal["request_query", "finish_queries"]
    query_attempt: QueryAttempt | None
    previous_receipt_assessment: QueryAssessment | None = None
    impact: Text
    stop_explanation: ExplorationStop | None = None


class QuerySelection(StageModel):
    schema_version: Literal["staged-query/v0.3"]
    decision: Literal["request_query", "finish_queries"]
    query_attempt: QueryAttempt | None
    previous_receipt_assessment: QueryAssessmentSelection | None
    impact: Text
    stop_explanation: ExplorationStopSelection | None


class CriticalBehavior(StageModel):
    behavior: Text
    observation: Text
    evidence_ids: list[Text]
    semantic_role: Literal[
        "dependency_contract", "mut_path_condition", "test_objective"
    ] | None = None
    formal_claim_index: Annotated[int, Field(ge=0)] | None = None
    support: Literal["evidence", "assumption"] | None = None
    assumption_reason: Text | None = None


class ConstructContent(StageModel):
    result_status: Literal["SELECTED", "AMBIGUOUS", "INCONCLUSIVE"]
    selected_scenario_id: Text | None
    selection_rationale: Text
    claim_basis: list["ClaimBasis"]
    critical_behaviors: Annotated[list[CriticalBehavior], Field(max_length=3)]
    allowed_variations: list[Text]
    unresolved: list[Text]
    nonblocking_unknowns: list[Text] = Field(default_factory=list)


class ConstructResult(ConstructContent):
    schema_version: Literal["staged-construct/v0.1"]
    contract: ScenarioContract | None


class ScenarioContractSelection(ScenarioContract):
    """Formal contract wire DTO that changes only evidence representation."""

    # Pydantic intentionally specializes the inherited field while retaining
    # every formal field definition and ScenarioContract reference validator.
    evidence: list[EvidenceSelection]  # type: ignore[assignment]


class ConstructSelection(ConstructContent):
    schema_version: Literal["staged-construct/v0.2"]
    contract: ScenarioContractSelection | None


class ClaimBasis(StageModel):
    claim: Text
    basis: Literal["task_requirement", "implementation_fact", "scenario_inference"]
    target: Literal[
        "selection_rationale", "allowed_variation", "unresolved",
        "nonblocking_unknown", "formal_claim"
    ] | None = None
    target_index: Annotated[int, Field(ge=0)] | None = None
    semantic_role: Literal[
        "dependency_contract", "mut_path_condition", "test_objective"
    ] | None = None
    evidence_ids: list[Text] = Field(default_factory=list)
    support: Literal["evidence", "assumption"] | None = None
    assumption_reason: Text | None = None


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
    support: Literal["evidence", "assumption"] | None = None
    assumption_reason: Text | None = None
    semantic_role: Literal[
        "dependency_contract", "mut_path_condition", "test_objective"
    ] | None = None


class RevisionResult(StageModel):
    schema_version: Literal["staged-revise/v0.1"]
    revised_construct: ConstructResult
    modified_objects: Annotated[list[Text], Field(min_length=1)]
    evidence_reason: Text
    evidence_ids: Annotated[list[Text], Field(min_length=1)]
    request_another_revision: bool = False


class RevisionSelection(StageModel):
    schema_version: Literal["staged-revise/v0.2"]
    revised_construct: ConstructSelection
    modified_objects: Annotated[list[Text], Field(min_length=1)]
    evidence_reason: Text
    evidence_ids: Annotated[list[Text], Field(min_length=1)]
    request_another_revision: bool = False


class DeliverResult(StageModel):
    schema_version: Literal["staged-deliver/v0.1"]
    result_status: Literal["SELECTED", "AMBIGUOUS", "INCONCLUSIVE"]
    selected_scenario_id: Text | None
    contract: ScenarioContract | None


class DeliverSelection(StageModel):
    schema_version: Literal["staged-deliver/v0.2"]
    result_status: Literal["SELECTED", "AMBIGUOUS", "INCONCLUSIVE"]
    selected_scenario_id: Text | None
    contract: ScenarioContractSelection | None


@dataclass(frozen=True)
class StagedRunConfig:
    variant: Literal["B", "C"] = "B"
    run_mode: Literal["synthetic_replay", "live_inference"] = "synthetic_replay"
    model_name: str = "scripted-offline"
    repository_read: bool = False
    max_model_calls: int = 10
    max_query_attempts: int = 6
    repetition_threshold: int = 4
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
        if type(self.repository_read) is not bool:
            raise ValueError("repository_read must be a boolean")
        if self.repository_read and self.run_mode != "live_inference":
            raise ValueError("repository_read requires live_inference")
        if self.run_mode == "synthetic_replay" and self.model_name != "scripted-offline":
            raise ValueError("synthetic replay requires the scripted-offline model profile")
        if self.run_mode == "live_inference" and self.model_name == "scripted-offline":
            raise ValueError("live inference requires an explicit provider model")
        for name in ("max_model_calls", "max_query_attempts", "repetition_threshold"):
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


@dataclass
class FormatRepairAllowance:
    """One irreversible reservation shared by QUERY and DELIVER repair."""

    state: Literal["available", "reserved"] = "available"
    category: str | None = None

    def reserve(self, category: str) -> bool:
        if self.state != "available":
            return False
        self.state = "reserved"
        self.category = category
        return True


class StagedInferenceError(RuntimeError):
    def __init__(self, message: str, *, stage: str, stop_reason: str):
        super().__init__(message)
        self.stage = stage
        self.stop_reason = stop_reason


class CitationMaterializationError(ValueError):
    def __init__(
        self, message: str, *, field_path: str, locator: object = None,
    ) -> None:
        super().__init__(message)
        raw = locator.model_dump() if isinstance(locator, SourceLocator) else locator
        raw = raw if isinstance(raw, dict) else {}
        self.audit = {
            "field_path": field_path,
            "source_id": raw.get("source_id") if isinstance(raw.get("source_id"), str) else None,
            "start_line": raw.get("start_line") if type(raw.get("start_line")) is int else None,
            "end_line": raw.get("end_line") if type(raw.get("end_line")) is int else None,
            "error": _safe_external_text(message, limit=200),
        }


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


_SECRET_PATTERNS = (
    re.compile(r"(?i)\bBearer\s+[^\s,;]+"),
    re.compile(r"\bsk-[A-Za-z0-9_-]+"),
    re.compile(
        r"(?i)\b(api[_-]?key|authorization|access[_-]?token|secret|password)\b"
        r"[\"']?\s*[:=]\s*[\"']?[^\s,;\"'}]+"
    ),
)


def _safe_external_text(value: object, *, limit: int = 500) -> str:
    """Return a bounded diagnostic without credentials or authorization values."""
    text = str(value or "")
    api_key = os.environ.get("OPENAI_API_KEY", "")
    if api_key:
        text = text.replace(api_key, "<REDACTED>")
    text = _SECRET_PATTERNS[0].sub("Bearer <REDACTED>", text)
    text = _SECRET_PATTERNS[1].sub("<REDACTED>", text)
    text = _SECRET_PATTERNS[2].sub(
        lambda match: f"{match.group(1)}=<REDACTED>", text
    )
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > limit:
        text = text[: limit - 1].rstrip() + "…"
    return text


def _exception_diagnostic(exc: BaseException, *, classification: str) -> dict[str, object]:
    """Extract stable provider diagnostics without persisting bodies or headers."""
    body = getattr(exc, "body", None)
    provider_error = body.get("error", body) if isinstance(body, dict) else None
    error_code = (
        provider_error.get("code")
        if isinstance(provider_error, dict) else None
    )
    if error_code is None:
        error_code = getattr(exc, "code", None)
    cause_types: list[str] = []
    seen = {id(exc)}
    current = exc.__cause__ or exc.__context__
    while current is not None and id(current) not in seen and len(cause_types) < 6:
        seen.add(id(current))
        cause_types.append(type(current).__name__)
        current = current.__cause__ or current.__context__
    return {
        "classification": classification,
        "type": type(exc).__name__,
        "message": _safe_external_text(exc),
        "status_code": getattr(exc, "status_code", None),
        "request_id": (
            None
            if getattr(exc, "request_id", None) is None
            else _safe_external_text(getattr(exc, "request_id"), limit=200)
        ),
        "error_code": (
            None if error_code is None else _safe_external_text(error_code, limit=200)
        ),
        "cause_types": cause_types,
    }


def _finish_model_call(
    event: dict[str, object], *, started: float, ledger: UsageLedger,
    responses: list[dict], response_start: int,
) -> None:
    requests_before = event.pop("ledger_requests_started_before")
    if type(requests_before) is not int:
        raise TypeError("model-call trace contains an invalid ledger baseline")
    event["model_request_started"] = ledger.requests_started > requests_before
    event["elapsed_seconds"] = time.monotonic() - started
    event["response_refs"] = [
        {
            "response_index": index,
            "response_id": response.get("response_id"),
            "request_id": response.get("request_id"),
            "usage": response.get("usage"),
        }
        for index, response in enumerate(responses[response_start:], start=response_start)
    ]


class _StageOutputSchema(AgentOutputSchema):
    """Inline union references for the provider; retain SDK/Pydantic validation."""

    def json_schema(self) -> dict[str, Any]:
        schema = deepcopy(super().json_schema())

        def expand(
            value: Any, active: frozenset[str] = frozenset(),
            definitions: dict[str, Any] | None = None,
        ) -> None:
            if isinstance(value, list):
                for item in value:
                    expand(item, active, definitions)
            elif isinstance(value, dict):
                definitions = value.get("$defs", definitions)
                for branch in value.get("anyOf", []):
                    reference = branch.get("$ref")
                    if reference is not None:
                        name = reference.removeprefix("#/$defs/")
                        if (
                            reference in active
                            or not reference.startswith("#/$defs/")
                            or definitions is None
                            or name not in definitions
                        ):
                            raise ValueError("unsupported stage union reference")
                        definition = deepcopy(definitions[name])
                        expand(definition, active | {reference}, definitions)
                        branch.pop("$ref")
                        branch.update(definition)
                for child in value.values():
                    expand(child, active, definitions)

        expand(schema)
        return schema


def _stage_agent(stage: str, output_type: type[StageResult], model: Model,
                 config: StagedRunConfig) -> Agent[None]:
    stage_protocol = ""
    if stage == "EXTRACT":
        stage_protocol = (
            " Infer conditional dependency constraints, not hidden Mock setup or hidden"
            " verification text. State unconfirmed behavior as an if-then hypothesis and"
            " preserve its missing basis in unresolved. Imports, test names, and final"
            " assertions alone do not establish a hidden substitute, stub value, or call count."
        )
    elif stage == "QUERY":
        repository_protocol = (
            " inspect_source supports one bounded operation per call: search with"
            " {operation:'search',path_pattern?:string,text?:string,cursor?:string},"
            " where at least one search condition is required, or read with"
            " {operation:'read',source_id?:string,path?:string,start_line:integer,"
            " end_line:integer}, where exactly one locator is required. Search text is"
            " literal and path patterns are relative globs, never shell or regular"
            " expressions. Follow next_cursor with the identical query when incomplete."
            " Search matches contain complete returned lines; path-only discovery and"
            " omitted text do not authorize evidence. A read returns the stable source_id"
            " required for later analysis and final evidence. Host paths are forbidden."
            " Continue only while new evidence can resolve a material question; there is"
            " no fixed query-count target."
            if config.repository_read else
            " inspect_source is limited to the supplied source IDs and visible ranges."
        )
        stage_protocol = (
            " You may finish with zero queries when the visible task already resolves the candidates."
            " Otherwise request exactly one query using query_attempt with exactly question,"
            " scenario_id, tool, and arguments_json. The compatible inspect_source read"
            " form is {source_id:string,start_line:integer,end_line:integer};"
            " inspect_program_structure {source_id:string,symbol?:string}; inspect_dependency_usage"
            " {source_id:string,symbol?:string}. Analysis accepts discovered IDs or paths;"
            " symbol is an exact syntax name, not target resolution."
            " Read source lines before citing them."
            " arguments_json must encode the named argument object."
            " After a receipt, previous_receipt_assessment classifies its candidate impact"
            " as supports, weakens, or no_new_information; the host binds it to the one"
            " pending receipt, so never return a receipt or attempt sequence field."
            " errors and incomplete results still require an assessment."
            " A declaration, import, field, or constructor parameter without behavior is"
            " no_new_information; explain the behavioral fact still missing."
            " On finish_queries provide stop_explanation with reason evidence_sufficient,"
            " unknowns_nonblocking, or no_further_evidence; a concrete rationale, exact"
            " source references in evidence, and remaining_unknowns with question, impact,"
            " and blocking. Preserve unknowns rather than treating failed exploration as proof."
            f"{repository_protocol}"
        )
    elif stage == "CONSTRUCT":
        stage_protocol = (
            " Infer conditional dependency constraints, not hidden Mock setup or hidden"
            " verification text. Imports, test names, and final assertions alone do not"
            " prove hidden behavior or counts; preserve unsupported details as unresolved."
            " Preserve test_objectives as only the original visible goals; place newly"
            " inferred checks in verification_candidates. Every dependency constraint must"
            " state its condition, target call, parameter relationship, allowed return or"
            " exception, and relevant state effect. Require exact values, counts, or order"
            " only when cited evidence supports them. Describe supported same-scenario"
            " variations, or explicitly say none were identified without claiming uniqueness."
            " Keep expected_interactions assertion_policy as not_implied. A test name or an"
            " implementation branch alone does not make an interaction required."
            " For every claim_basis and critical_behavior, classify semantic_role as"
            " dependency_contract, mut_path_condition, or test_objective, and state whether"
            " support is evidence or assumption. Evidence support requires formal evidence"
            " IDs; assumption support requires an explicit reason and no evidence IDs. Each"
            " critical behavior must point by zero-based formal_claim_index to the matching"
            " dependency constraint, scenario hypothesis, or test objective for its role."
            " Map every remaining blocking and nonblocking unknown through claim_basis target"
            " unresolved or nonblocking_unknown and its zero-based index."
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
            " Mark the contrast as evidence-supported with evidence_ids, or as an explicit"
            " assumption with an assumption_reason and no evidence IDs. It remains a static"
            " prediction with execution_status not_executed."
            " Classify its semantic_role using the same three-way distinction."
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
        "Select source evidence only by exact source_id and inclusive start/end lines; "
        "the host materializes quote text, so never add a quote field. "
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
        output_type=_StageOutputSchema(output_type),
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
    model_calls: list[dict],
    deadline: float,
    model_call_limit: int | None = None,
) -> StageResult:
    if isinstance(payload, dict) and "visible_task" in payload:
        task = payload["visible_task"]
        # Coordinate metadata for the already-visible text, not new evidence or
        # repaired output. Preserve whitespace exactly as SourceStore.quote does.
        payload = {**payload, "source_line_index": {
            source["source_id"]: [
                {"line": number, "text": text}
                for number, text in enumerate(
                    source["text"].splitlines(), start=source["start_line"]
                )
            ]
            for source in (
                task["masked_test"], task["mut_source"], *task["supporting_sources"]
            )
        }}
    payload_bytes = canonical_json(payload)
    agent = _stage_agent(stage, output_type, model, config)
    assert isinstance(agent.output_type, AgentOutputSchema)
    started = time.monotonic()
    response_start = len(responses)
    event: dict[str, object] = {
        "sequence": len(model_calls) + 1,
        "stage": stage,
        "status": "started",
        "model_request_started": False,
        "request_start_basis": "usage_ledger_before_model_call_hook",
        "model_name": config.model_name,
        "output_type": output_type.__name__,
        "prompt_version": PROMPT_VERSION,
        "instructions": agent.instructions,
        "requested_output_schema": agent.output_type.json_schema(),
        "payload": json.loads(payload_bytes),
        "payload_sha256": sha256_bytes(payload_bytes),
        "payload_bytes": len(payload_bytes),
        "max_output_tokens": config.max_output_tokens,
        "timeout_seconds": None,
        "elapsed_seconds": None,
        "response_refs": [],
        "exception": None,
        "ledger_requests_started_before": ledger.requests_started,
    }
    model_calls.append(event)
    effective_call_limit = (
        config.max_model_calls if model_call_limit is None else model_call_limit
    )
    if effective_call_limit >= 0 and ledger.requests_started >= effective_call_limit:
        event["status"] = "blocked"
        event["exception"] = {
            "classification": "host_budget",
            "type": "StagedInferenceError",
            "message": "model-call limit exhausted",
            "status_code": None,
            "request_id": None,
            "error_code": "model_call_limit",
            "cause_types": [],
        }
        _finish_model_call(
            event, started=started, ledger=ledger,
            responses=responses, response_start=response_start,
        )
        raise StagedInferenceError(
            "model-call limit exhausted", stage=stage, stop_reason="budget_exhausted"
        )
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        event["status"] = "blocked"
        event["exception"] = {
            "classification": "host_deadline",
            "type": "StagedInferenceError",
            "message": "run deadline exhausted",
            "status_code": None,
            "request_id": None,
            "error_code": "run_deadline",
            "cause_types": [],
        }
        _finish_model_call(
            event, started=started, ledger=ledger,
            responses=responses, response_start=response_start,
        )
        raise StagedInferenceError(
            "run deadline exhausted", stage=stage, stop_reason="run_timeout"
        )
    timeout_seconds = min(remaining, config.parsing_timeout_seconds)
    event["timeout_seconds"] = timeout_seconds
    try:
        result = await asyncio.wait_for(
            Runner.run(
                agent,
                input=payload_bytes.decode("utf-8"),
                max_turns=1,
                hooks=BudgetHooks(ledger, responses),
                run_config=RunConfig(
                    tracing_disabled=True,
                    trace_include_sensitive_data=False,
                    workflow_name=POLICY_VERSION,
                ),
            ),
            timeout=timeout_seconds,
        )
    except asyncio.CancelledError as exc:
        event["status"] = "cancelled"
        event["exception"] = _exception_diagnostic(exc, classification="cancelled")
        _finish_model_call(
            event, started=started, ledger=ledger,
            responses=responses, response_start=response_start,
        )
        raise
    except asyncio.TimeoutError as exc:
        event["status"] = "failed"
        event["exception"] = _exception_diagnostic(exc, classification="host_timeout")
        _finish_model_call(
            event, started=started, ledger=ledger,
            responses=responses, response_start=response_start,
        )
        raise StagedInferenceError(
            "stage timed out", stage=stage,
            stop_reason="run_timeout" if remaining <= config.parsing_timeout_seconds else "timeout"
        ) from None
    except InferenceError as exc:
        event["status"] = "failed"
        event["exception"] = _exception_diagnostic(
            exc,
            classification=(
                "host_budget" if exc.category == "budget_exhausted" else "inference_error"
            ),
        )
        _finish_model_call(
            event, started=started, ledger=ledger,
            responses=responses, response_start=response_start,
        )
        reason = "budget_exhausted" if exc.category == "budget_exhausted" else "model_error"
        raise StagedInferenceError("stage model call failed", stage=stage, stop_reason=reason) from None
    except (ModelBehaviorError, ValidationError) as exc:
        event["status"] = "failed"
        event["exception"] = _exception_diagnostic(exc, classification="output_invalid")
        _finish_model_call(
            event, started=started, ledger=ledger,
            responses=responses, response_start=response_start,
        )
        raise StagedInferenceError(
            "stage model output is invalid", stage=stage, stop_reason="output_invalid"
        ) from None
    except Exception as exc:
        classification = "api_status" if getattr(exc, "status_code", None) is not None else (
            "provider_transport"
            if type(exc).__name__ in {"APIConnectionError", "APITimeoutError"}
            else "model_error"
        )
        event["status"] = "failed"
        event["exception"] = _exception_diagnostic(exc, classification=classification)
        _finish_model_call(
            event, started=started, ledger=ledger,
            responses=responses, response_start=response_start,
        )
        raise StagedInferenceError(
            "stage model call failed", stage=stage, stop_reason="model_error"
        ) from None
    if not isinstance(result.final_output, output_type):
        output_error = TypeError("stage final output has an unexpected type")
        event["status"] = "failed"
        event["exception"] = _exception_diagnostic(
            output_error, classification="output_invalid"
        )
        _finish_model_call(
            event, started=started, ledger=ledger,
            responses=responses, response_start=response_start,
        )
        raise StagedInferenceError(
            "stage model output is invalid", stage=stage, stop_reason="output_invalid"
        )
    event["status"] = "completed"
    _finish_model_call(
        event, started=started, ledger=ledger,
        responses=responses, response_start=response_start,
    )
    return result.final_output


def _check_source_reference(
    reference: SourceReference, source_store: SourceStore
) -> None:
    if reference.end_line < reference.start_line:
        raise ValueError("source reference has a reversed range")
    quote = source_store.quote(reference.source_id, reference.start_line, reference.end_line)
    if quote != reference.quote:
        raise ValueError(
            f"source reference quote mismatch: {reference.source_id}:"
            f"{reference.start_line}-{reference.end_line}"
        )


def _materialize_reference(
    locator: SourceLocator, store: SourceStore, *, field_path: str,
) -> SourceReference:
    try:
        quote = store.quote(locator.source_id, locator.start_line, locator.end_line)
    except ValueError as exc:
        raise CitationMaterializationError(
            str(exc), field_path=field_path, locator=locator,
        ) from None
    return SourceReference(**locator.model_dump(), quote=quote)


def _materialize_contract(
    raw: object, store: SourceStore, *, field_path: str = "contract",
) -> ScenarioContract | None:
    if raw is None:
        return None
    if isinstance(raw, ScenarioContractSelection):
        raw = raw.model_dump(mode="json")
    if not isinstance(raw, dict):
        raise ValueError("contract selection must be an object or null")
    selected = deepcopy(raw)
    evidence = selected.get("evidence")
    if not isinstance(evidence, list):
        raise ValueError("contract selection evidence must be a list")
    materialized = []
    for index, item in enumerate(evidence):
        item_path = f"{field_path}.evidence.{index}"
        try:
            locator = EvidenceSelection.model_validate(item)
        except ValidationError as exc:
            raise CitationMaterializationError(
                "invalid evidence locator", field_path=item_path, locator=item,
            ) from exc
        try:
            quote = store.quote(locator.source_id, locator.start_line, locator.end_line)
        except ValueError as exc:
            raise CitationMaterializationError(
                str(exc), field_path=item_path, locator=locator,
            ) from None
        materialized.append({
            **locator.model_dump(),
            "quote": quote,
        })
    selected["evidence"] = materialized
    return ScenarioContract.model_validate(selected)


def _contract_selection(contract: ScenarioContract | None) -> dict | None:
    if contract is None:
        return None
    selected = contract.model_dump(mode="json")
    for evidence in selected["evidence"]:
        evidence.pop("quote")
    return selected


def _materialize_extract(wire: ExtractSelection, store: SourceStore) -> ExtractResult:
    return ExtractResult(
        schema_version="staged-extract/v0.1",
        result_status=wire.result_status,
        original_goals=[
            _materialize_reference(item, store, field_path=f"original_goals.{index}")
            for index, item in enumerate(wire.original_goals)
        ],
        candidates=[CandidateDraft(
            scenario_id=item.scenario_id,
            description=item.description,
            evidence=[
                _materialize_reference(
                    ref, store, field_path=f"candidates.{candidate_index}.evidence.{index}"
                )
                for index, ref in enumerate(item.evidence)
            ],
        ) for candidate_index, item in enumerate(wire.candidates)],
        unresolved=wire.unresolved,
    )


def _materialize_query(
    wire: QuerySelection, store: SourceStore, *, assessment_sequence: int | None = None,
) -> QueryResult:
    stop = None
    if wire.stop_explanation is not None:
        stop = ExplorationStop(
            reason=wire.stop_explanation.reason,
            rationale=wire.stop_explanation.rationale,
            evidence=[
                _materialize_reference(
                    item, store, field_path=f"stop_explanation.evidence.{index}"
                )
                for index, item in enumerate(wire.stop_explanation.evidence)
            ],
            remaining_unknowns=wire.stop_explanation.remaining_unknowns,
        )
    assessment = None
    if wire.previous_receipt_assessment is not None:
        if assessment_sequence is None:
            raise ValueError("QUERY assessment has no host-bound receipt")
        assessment = QueryAssessment(
            attempt_sequence=assessment_sequence,
            impact=wire.previous_receipt_assessment.impact,
            rationale=wire.previous_receipt_assessment.rationale,
        )
    return QueryResult(
        schema_version="staged-query/v0.1",
        decision=wire.decision,
        query_attempt=wire.query_attempt,
        previous_receipt_assessment=assessment,
        impact=wire.impact,
        stop_explanation=stop,
    )


def _materialize_construct(
    wire: ConstructSelection, store: SourceStore, *, field_path: str = "",
) -> ConstructResult:
    return ConstructResult(
        schema_version="staged-construct/v0.1",
        result_status=wire.result_status,
        selected_scenario_id=wire.selected_scenario_id,
        selection_rationale=wire.selection_rationale,
        claim_basis=wire.claim_basis,
        critical_behaviors=wire.critical_behaviors,
        allowed_variations=wire.allowed_variations,
        unresolved=wire.unresolved,
        nonblocking_unknowns=wire.nonblocking_unknowns,
        contract=_materialize_contract(
            wire.contract, store,
            field_path=f"{field_path}.contract".lstrip("."),
        ),
    )


def _materialize_revision(wire: RevisionSelection, store: SourceStore) -> RevisionResult:
    return RevisionResult(
        schema_version="staged-revise/v0.1",
        revised_construct=_materialize_construct(
            wire.revised_construct, store, field_path="revised_construct"
        ),
        modified_objects=wire.modified_objects,
        evidence_reason=wire.evidence_reason,
        evidence_ids=wire.evidence_ids,
        request_another_revision=wire.request_another_revision,
    )


def _materialize_delivery(wire: DeliverSelection, store: SourceStore) -> DeliverResult:
    return DeliverResult(
        schema_version="staged-deliver/v0.1",
        result_status=wire.result_status,
        selected_scenario_id=wire.selected_scenario_id,
        contract=_materialize_contract(wire.contract, store, field_path="contract"),
    )


def _wire_stage_entry(
    stage: str, wire: StageModel, *, model_call_sequence: int,
) -> dict[str, object]:
    return {
        "stage": stage,
        "model_call_sequence": model_call_sequence,
        "citation_mode": CITATION_MODE,
        "wire_result": wire.model_dump(mode="json"),
        "result": None,
    }


def _complete_stage_entry(entry: dict[str, object], result: StageModel) -> None:
    entry["result"] = result.model_dump(mode="json")


def _record_citation_error(
    entry: dict[str, object], error: CitationMaterializationError,
) -> None:
    entry["citation_error"] = error.audit


def _validate_exploration_stop(stop: ExplorationStop, store: SourceStore) -> None:
    for reference in stop.evidence:
        _check_source_reference(reference, store)
    if stop.reason in {"evidence_sufficient", "unknowns_nonblocking"}:
        if not stop.evidence:
            raise ValueError("supported exploration stop requires evidence")
        if any(item.blocking for item in stop.remaining_unknowns):
            raise ValueError("evidence-sufficient stop cannot hide blocking unknowns")
    if stop.reason == "unknowns_nonblocking" and not stop.remaining_unknowns:
        raise ValueError("nonblocking stop must explain the remaining unknowns")
    if stop.reason == "no_further_evidence" and not stop.remaining_unknowns:
        raise ValueError("no-further-evidence stop must preserve unknowns and their impact")


def _completed_response_json(response: object) -> object:
    """Decode one complete final text while rejecting ambiguous provider output."""
    from mock_spec.validation import decode_json

    if not isinstance(response, dict):
        raise ValueError("response trace is not an object")
    output = response.get("output")
    if not isinstance(output, list):
        raise ValueError("response trace has no output list")
    messages = [item for item in output if isinstance(item, dict) and item.get("type") == "message"]
    if len(messages) != 1:
        raise ValueError("response must contain exactly one final message")
    if any(not isinstance(item, dict) or item.get("type") not in {"message", "reasoning"}
           for item in output):
        raise ValueError("response contains a non-reasoning side output")
    message = messages[0]
    if message.get("status") != "completed":
        raise ValueError("response message is not completed")
    content = message.get("content")
    if (not isinstance(content, list) or len(content) != 1
            or not isinstance(content[0], dict)
            or content[0].get("type") != "output_text"
            or not isinstance(content[0].get("text"), str)):
        raise ValueError("response must contain exactly one output_text")
    return decode_json(content[0]["text"])


def _query_repair_snapshot(raw: object) -> tuple[QuerySelection, dict, list[list[object]]]:
    """Remove only eligible null extras and strictly validate the comparison projection."""
    if not isinstance(raw, dict):
        raise ValueError("QUERY output is not a JSON object")
    if set(raw) != set(QuerySelection.model_fields):
        raise ValueError("QUERY must include every known top-level field")
    try:
        QuerySelection.model_validate(raw)
    except ValidationError as exc:
        errors = exc.errors(include_url=False)
    else:
        raise ValueError("QUERY output is already valid")
    assessment = raw.get("previous_receipt_assessment")
    if not isinstance(assessment, dict):
        raise ValueError("QUERY assessment is not an object")
    known = set(QueryAssessmentSelection.model_fields)
    if not known <= set(assessment):
        raise ValueError("QUERY assessment must include every known field")
    unknown = set(assessment) - known
    if not unknown or "attempt_sequence" in unknown:
        raise ValueError("QUERY has no eligible assessment-only null extras")
    eligible_locations = {
        ("previous_receipt_assessment", field) for field in unknown
    }
    if any(
        error.get("type") != "extra_forbidden"
        or tuple(error.get("loc", ())) not in eligible_locations
        for error in errors
    ):
        raise ValueError("QUERY has validation errors outside eligible null extras")
    if any(assessment[field] is not None for field in unknown):
        raise ValueError("QUERY assessment extras must be JSON null")
    projected = deepcopy(raw)
    for field in unknown:
        del projected["previous_receipt_assessment"][field]
    wire = QuerySelection.model_validate(projected)
    return wire, wire.model_dump(mode="json"), [
        ["previous_receipt_assessment", field] for field in sorted(unknown)
    ]


def _pending_receipt_identity(
    pending: dict[str, object] | None, *, run_id: str, case_id: str,
) -> dict[str, object] | None:
    if pending is None:
        return None
    return {
        "run_id": run_id,
        "case_id": case_id,
        "sequence": pending.get("sequence"),
        "response_id": pending.get("response_id"),
        "content_sha256": sha256_bytes(canonical_json(pending)),
    }


def _validate_repairable_query(
    wire: QuerySelection, *, pending_receipt: dict[str, object] | None,
    candidate_ids: set[str], source_store: SourceStore, task: UnifiedTask,
    require_explicit_stop: bool = False,
) -> QueryResult:
    if pending_receipt is None and wire.previous_receipt_assessment is not None:
        raise ValueError("QUERY assessment has no pending receipt")
    if pending_receipt is not None and wire.previous_receipt_assessment is None:
        raise ValueError("QUERY omitted the pending receipt assessment")
    if wire.decision == "request_query":
        if wire.query_attempt is None or wire.stop_explanation is not None:
            raise ValueError("request_query has an inconsistent envelope")
        validation = validate_query_request(
            wire.query_attempt.model_dump(mode="json"), candidate_ids=candidate_ids
        )
        if not validation.valid:
            raise ValueError(validation.error_code or "invalid query request")
        assert validation.tool is not None and validation.arguments is not None
        if validation.tool == "inspect_source":
            if validation.arguments.get("operation") == "read":
                source_id = validation.arguments.get("source_id")
                if not isinstance(source_id, str):
                    raise ValueError("repair cannot prove a path-only read identity")
                initial_sources = {
                    source.source_id: source
                    for source in (
                        task.masked_test, task.mut_source, *task.supporting_sources
                    )
                }
                initial_source = initial_sources.get(source_id)
                if source_store.repository is not None:
                    # A repository source may be known from a prior bounded search while
                    # the requested next range is intentionally not observed yet. Repository
                    # reads may also expand an initially supplied locator beyond its fragment.
                    if source_id not in source_store.ids():
                        raise ValueError("read source identity is not visible in this run")
                elif initial_source is not None:
                    start_line = validation.arguments.get("start_line")
                    end_line = validation.arguments.get("end_line")
                    if (
                        type(start_line) is not int
                        or type(end_line) is not int
                        or not initial_source.start_line
                        <= start_line <= end_line <= initial_source.end_line
                    ):
                        raise ValueError("read range is outside the supplied source")
                else:
                    raise ValueError("read source identity is not visible in this run")
        else:
            source_id = validation.arguments.get("source_id")
            if not isinstance(source_id, str):
                raise ValueError("analysis query has no stable source identity")
            if source_store.repository is not None:
                if source_id not in source_store.ids():
                    raise ValueError("analysis source identity is not visible in this run")
            else:
                source_store.get(source_id)
    else:
        if wire.query_attempt is not None:
            raise ValueError("finish_queries cannot contain a query attempt")
        if require_explicit_stop and wire.stop_explanation is None:
            raise ValueError("repository exploration requires an explicit stop explanation")
    sequence = None if pending_receipt is None else pending_receipt.get("sequence")
    if sequence is not None and type(sequence) is not int:
        raise ValueError("pending receipt has an invalid sequence")
    source_store.validate_task(task)
    query = _materialize_query(wire, source_store, assessment_sequence=sequence)
    if query.decision == "finish_queries" and query.stop_explanation is not None:
        _validate_exploration_stop(query.stop_explanation, source_store)
    return query


async def _repair_query_output(
    raw: object, *, original_call_sequence: int,
    original_attempt_sequence: int | None, pending_receipt: dict[str, object] | None,
    run_id: str, case_id: str, candidate_ids: set[str], task: UnifiedTask,
    source_store: SourceStore, model: Model, config: StagedRunConfig,
    ledger: UsageLedger, responses: list[dict], model_calls: list[dict],
    stages: list[dict], repair_log: list[dict], allowance: FormatRepairAllowance,
    deadline: float, model_call_limit: int,
) -> QuerySelection:
    entry: dict[str, object] = {
        "stage": "QUERY_FORMAT_REPAIR",
        "category": "assessment_unknown_null_fields",
        "original_call_sequence": original_call_sequence,
        "original_attempt_sequence": original_attempt_sequence,
        "repaired_attempt_sequence": None,
        "repair_call_sequence": None,
        "error_positions": [],
        "eligible": False,
        "raw_before": raw,
        "comparison_snapshot": None,
        "comparison_snapshot_sha256": None,
        "pending_receipt_identity": None,
        "raw_after": None,
        "outcome": "ineligible",
        "allowance_use": allowance.state,
        "budget_reason": None,
    }
    repair_log.append(entry)
    try:
        projected_wire, snapshot, locations = _query_repair_snapshot(raw)
        _validate_repairable_query(
            projected_wire, pending_receipt=pending_receipt,
            candidate_ids=candidate_ids, source_store=source_store, task=task,
            require_explicit_stop=config.repository_read,
        )
    except (ValueError, TypeError, CitationMaterializationError) as exc:
        entry["budget_reason"] = _safe_external_text(exc, limit=200)
        raise StagedInferenceError(
            "QUERY output is outside the bounded repair policy",
            stage="QUERY", stop_reason="output_invalid",
        ) from None
    identity = _pending_receipt_identity(pending_receipt, run_id=run_id, case_id=case_id)
    entry.update({
        "eligible": True,
        "error_positions": locations,
        "comparison_snapshot": snapshot,
        "comparison_snapshot_sha256": sha256_bytes(canonical_json(snapshot)),
        "pending_receipt_identity": identity,
    })
    known_budget_failure = None
    if not ledger.usage_complete:
        known_budget_failure = "prior_usage_unknown"
    elif time.monotonic() >= deadline:
        known_budget_failure = "run_deadline"
    elif ledger.total_tokens >= config.max_total_tokens:
        known_budget_failure = "total_token_limit"
    elif (
        config.max_cost_usd is not None
        and ledger.rate_estimated_cost_usd is not None
        and ledger.rate_estimated_cost_usd >= config.max_cost_usd
    ):
        known_budget_failure = "cost_limit"
    elif model_call_limit >= 0 and ledger.requests_started >= model_call_limit:
        known_budget_failure = "model_call_limit"
    if known_budget_failure is not None:
        entry["outcome"] = "blocked_budget"
        entry["budget_reason"] = known_budget_failure
        raise StagedInferenceError(
            "QUERY repair budget is unavailable", stage="QUERY",
            stop_reason="budget_exhausted" if known_budget_failure != "run_deadline" else "run_timeout",
        )
    if not allowance.reserve("QUERY_FORMAT_REPAIR"):
        entry["outcome"] = "allowance_exhausted"
        entry["allowance_use"] = allowance.state
        raise StagedInferenceError(
            "format repair allowance is exhausted", stage="QUERY", stop_reason="output_invalid"
        )
    entry["allowance_use"] = allowance.state
    repair_response_start = len(responses)
    try:
        repaired = await _call_stage(
            "QUERY_FORMAT_REPAIR", QuerySelection,
            {
                "stage": "QUERY_FORMAT_REPAIR",
                "invalid_query": raw,
                "eligible_errors": [
                    {"location": location, "type": "extra_forbidden", "value": None}
                    for location in locations
                ],
                "comparison_snapshot": snapshot,
                "repair_rule": (
                    "Return the complete staged-query/v0.3 object. Remove only the listed"
                    " unknown null fields and preserve every listed value exactly; do not"
                    " add evidence, source text, tools, or new reasoning."
                ),
            },
            model=model, config=config, ledger=ledger, responses=responses,
            model_calls=model_calls, deadline=deadline, model_call_limit=model_call_limit,
        )
    except asyncio.CancelledError:
        if model_calls and model_calls[-1].get("stage") == "QUERY_FORMAT_REPAIR":
            entry["repair_call_sequence"] = int(model_calls[-1]["sequence"])
            entry["outcome"] = "cancelled"
        if len(responses) == repair_response_start + 1:
            try:
                entry["raw_after"] = _completed_response_json(responses[-1])
            except (ValueError, TypeError) as raw_exc:
                entry["raw_after_error"] = _safe_external_text(raw_exc, limit=200)
        raise
    except StagedInferenceError as repair_exc:
        if model_calls and model_calls[-1].get("stage") == "QUERY_FORMAT_REPAIR":
            entry["repair_call_sequence"] = int(model_calls[-1]["sequence"])
            entry["outcome"] = (
                "blocked_budget"
                if repair_exc.stop_reason == "budget_exhausted"
                else (
                    "rejected_invalid_repair"
                    if repair_exc.stop_reason == "output_invalid"
                    else "failed"
                )
            )
        if len(responses) == repair_response_start + 1:
            try:
                entry["raw_after"] = _completed_response_json(responses[-1])
            except (ValueError, TypeError) as raw_exc:
                entry["raw_after_error"] = _safe_external_text(raw_exc, limit=200)
        raise
    entry["repair_call_sequence"] = int(model_calls[-1]["sequence"])
    if len(responses) != repair_response_start + 1:
        entry["outcome"] = "rejected_missing_raw_response"
        raise StagedInferenceError(
            "QUERY repair did not preserve one raw response",
            stage="QUERY_FORMAT_REPAIR", stop_reason="output_invalid",
        )
    try:
        after = _completed_response_json(responses[-1])
    except (ValueError, TypeError) as raw_exc:
        entry["outcome"] = "rejected_incomplete_raw_response"
        entry["raw_after_error"] = _safe_external_text(raw_exc, limit=200)
        raise StagedInferenceError(
            "QUERY repair response is not uniquely complete",
            stage="QUERY_FORMAT_REPAIR", stop_reason="output_invalid",
        ) from None
    entry["raw_after"] = after
    if (
        not isinstance(after, dict)
        or set(after) != set(QuerySelection.model_fields)
        or canonical_json(after) != canonical_json(snapshot)
        or repaired.model_dump(mode="json") != snapshot
    ):
        entry["outcome"] = "rejected_semantic_change"
        raise StagedInferenceError(
            "QUERY format repair changed protocol semantics",
            stage="QUERY_FORMAT_REPAIR", stop_reason="output_invalid",
        )
    try:
        if _pending_receipt_identity(
            pending_receipt, run_id=run_id, case_id=case_id
        ) != identity:
            raise ValueError("pending receipt identity changed")
        _validate_repairable_query(
            repaired, pending_receipt=pending_receipt, candidate_ids=candidate_ids,
            source_store=source_store, task=task,
            require_explicit_stop=config.repository_read,
        )
    except (ValueError, TypeError, CitationMaterializationError) as final_exc:
        entry["outcome"] = "rejected_post_validation"
        entry["budget_reason"] = _safe_external_text(final_exc, limit=200)
        raise StagedInferenceError(
            "QUERY repair failed pending identity or source validation",
            stage="QUERY_FORMAT_REPAIR", stop_reason="output_invalid",
        ) from None
    entry["outcome"] = "completed"
    stages.append(_wire_stage_entry(
        "QUERY_FORMAT_REPAIR", repaired,
        model_call_sequence=int(model_calls[-1]["sequence"]),
    ))
    return repaired


def _delivery_snapshot(raw: object) -> dict:
    """Accept only unambiguous envelope/version and decimal-line formatting repairs.

    All semantic fields remain mandatory. A malformed JSON document, unknown
    field, missing claim, or changed source/range cannot establish a snapshot.
    """
    from copy import deepcopy

    if not isinstance(raw, dict) or set(raw) - {
        "schema_version", "result_status", "selected_scenario_id", "contract"
    }:
        raise ValueError("cannot establish delivery selection snapshot")
    normalized = deepcopy(raw)
    normalized["schema_version"] = "staged-deliver/v0.2"
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
    return DeliverSelection.model_validate(normalized).model_dump(mode="json")


async def _deliver_with_repair(
    payload: dict, *, constructed: ConstructResult, task: UnifiedTask,
    source_store: SourceStore,
    model: Model, config: StagedRunConfig, ledger: UsageLedger,
    responses: list[dict], model_calls: list[dict], stages: list[dict],
    repair_log: list[dict], allowance: FormatRepairAllowance,
    deadline: float, model_call_limit: int,
) -> DeliverResult:
    expected = DeliverResult(
        schema_version="staged-deliver/v0.1", result_status=constructed.result_status,
        selected_scenario_id=constructed.selected_scenario_id, contract=constructed.contract,
    ).model_dump(mode="json")
    expected_wire = {
        "schema_version": "staged-deliver/v0.2",
        "result_status": constructed.result_status,
        "selected_scenario_id": constructed.selected_scenario_id,
        "contract": _contract_selection(constructed.contract),
    }
    response_start = len(responses)
    raw: object
    try:
        wire = await _call_stage(
            "DELIVER", DeliverSelection, payload, model=model, config=config,
            ledger=ledger, responses=responses, model_calls=model_calls, deadline=deadline,
            model_call_limit=model_call_limit,
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
            exhausted_delivery = DeliverSelection.model_validate(raw)
            if exhausted_delivery.model_dump(mode="json") != expected_wire:
                raise ValueError("budget-exhausted DELIVER changed semantics or evidence")
            raise exc
        stages.append({"stage": "DELIVER", "invalid_result": raw})
    else:
        stage_entry = _wire_stage_entry(
            "DELIVER", wire, model_call_sequence=int(model_calls[-1]["sequence"])
        )
        stages.append(stage_entry)
        source_store.validate_task(task)
        try:
            delivered = _materialize_delivery(wire, source_store)
        except CitationMaterializationError as exc:
            _record_citation_error(stage_entry, exc)
            raise
        _complete_stage_entry(stage_entry, delivered)
        raw = None
    if raw is not None:
        entry = {
            "stage": "FORMAT_REPAIR", "category": "delivery_format",
            "original_call_sequence": int(model_calls[-1]["sequence"]),
            "original_attempt_sequence": None, "repaired_attempt_sequence": None,
            "repair_call_sequence": None, "before": raw,
            "semantic_snapshot": None, "after": None, "status": "requested",
            "allowance_use": allowance.state, "budget_reason": None,
        }
        repair_log.append(entry)
        try:
            snapshot = _delivery_snapshot(raw)
            if snapshot != expected_wire:
                raise ValueError("invalid DELIVER changed semantics or evidence")
        except (ValueError, TypeError) as snapshot_exc:
            entry["status"] = "ineligible"
            entry["budget_reason"] = _safe_external_text(snapshot_exc, limit=200)
            raise ValueError("invalid DELIVER changed semantics or evidence") from None
        entry["semantic_snapshot"] = snapshot
        # Exact equality includes source ID and normalized range; the already
        # validated construct supplies the source identity proof.
        known_budget_failure = None
        if not ledger.usage_complete:
            known_budget_failure = "prior_usage_unknown"
        elif time.monotonic() >= deadline:
            known_budget_failure = "run_deadline"
        elif ledger.total_tokens >= config.max_total_tokens:
            known_budget_failure = "total_token_limit"
        elif (
            config.max_cost_usd is not None
            and ledger.rate_estimated_cost_usd is not None
            and ledger.rate_estimated_cost_usd >= config.max_cost_usd
        ):
            known_budget_failure = "cost_limit"
        elif model_call_limit >= 0 and ledger.requests_started >= model_call_limit:
            known_budget_failure = "model_call_limit"
        if known_budget_failure is not None:
            entry["status"] = "blocked_budget"
            entry["budget_reason"] = known_budget_failure
            raise StagedInferenceError(
                "final format repair budget is unavailable", stage="FORMAT_REPAIR",
                stop_reason=(
                    "run_timeout" if known_budget_failure == "run_deadline"
                    else "budget_exhausted"
                ),
            )
        if not allowance.reserve("FORMAT_REPAIR"):
            entry["status"] = "allowance_exhausted"
            entry["allowance_use"] = allowance.state
            raise StagedInferenceError(
                "format repair allowance is exhausted",
                stage="FORMAT_REPAIR", stop_reason="output_invalid",
            )
        entry["allowance_use"] = allowance.state
        repair_response_start = len(responses)
        try:
            wire = await _call_stage(
                "FORMAT_REPAIR", DeliverSelection,
                {**payload, "stage": "FORMAT_REPAIR", "invalid_delivery": raw,
                 "semantic_snapshot": snapshot,
                 "repair_rule": "Repair envelope/version or decimal line formatting only; "
                                "preserve every semantic field and evidence source/range."},
                model=model, config=config, ledger=ledger, responses=responses,
                model_calls=model_calls, deadline=deadline,
                model_call_limit=model_call_limit,
            )
        except asyncio.CancelledError:
            entry["status"] = "cancelled"
            if model_calls and model_calls[-1].get("stage") == "FORMAT_REPAIR":
                entry["repair_call_sequence"] = int(model_calls[-1]["sequence"])
            if len(responses) == repair_response_start + 1:
                try:
                    entry["after"] = _completed_response_json(responses[-1])
                except (ValueError, TypeError) as raw_exc:
                    entry["after_error"] = _safe_external_text(raw_exc, limit=200)
            raise
        except StagedInferenceError as exc:
            entry["status"] = (
                "blocked_budget" if exc.stop_reason == "budget_exhausted" else "failed"
            )
            entry["repair_call_sequence"] = int(model_calls[-1]["sequence"])
            if len(responses) == repair_response_start + 1:
                try:
                    entry["after"] = _completed_response_json(responses[-1])
                except (ValueError, TypeError) as raw_exc:
                    entry["after_error"] = _safe_external_text(raw_exc, limit=200)
            if exc.stop_reason in {"budget_exhausted", "run_timeout", "timeout"}:
                raise
            raise StagedInferenceError(
                "final format repair failed or its shared budget was exhausted",
                stage="FORMAT_REPAIR", stop_reason="output_invalid",
            ) from None
        entry["after"] = wire.model_dump(mode="json")
        entry["repair_call_sequence"] = int(model_calls[-1]["sequence"])
        stage_entry = _wire_stage_entry(
            "FORMAT_REPAIR", wire,
            model_call_sequence=int(model_calls[-1]["sequence"]),
        )
        stages.append(stage_entry)
        if wire.model_dump(mode="json") != expected_wire:
            entry["status"] = "rejected_semantic_change"
            raise StagedInferenceError(
                "FORMAT_REPAIR changed semantics or evidence",
                stage="FORMAT_REPAIR", stop_reason="output_invalid",
            )
        source_store.validate_task(task)
        try:
            delivered = _materialize_delivery(wire, source_store)
        except CitationMaterializationError as exc:
            _record_citation_error(stage_entry, exc)
            raise
        _complete_stage_entry(stage_entry, delivered)
        entry["status"] = "completed"
    if delivered.model_dump(mode="json") != expected:
        raise ValueError("DELIVER changed the constructed semantics")
    if delivered.contract is not None:
        validate_contract(delivered.contract, task, source_store=source_store)
    return delivered


def _validate_constructed(
    constructed: ConstructResult, task: UnifiedTask, source_store: SourceStore,
    *, strict_mapping: bool = False,
) -> ScenarioContract | None:
    """Validate state-specific delivery rules and return a revalidated contract."""
    contract = (
        None if constructed.contract is None
        else validate_contract(constructed.contract, task, source_store=source_store)
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
    if any(
        not item.evidence_ids and not (
            strict_mapping and item.support == "assumption"
        )
        for item in constructed.critical_behaviors
    ):
        raise ValueError("critical behavior requires evidence unless explicitly assumed")
    if strict_mapping:
        assert contract is not None or constructed.result_status == "INCONCLUSIVE"
        known_evidence = set() if contract is None else {
            item.id for item in contract.evidence
        }
        role_claims: dict[str, list[Any]] = {
            "dependency_contract": [] if contract is None else contract.dependency_constraints,
            "mut_path_condition": [] if contract is None else contract.scenario_hypotheses,
            "test_objective": [] if contract is None else contract.test_objectives,
        }

        def check_support(
            *, support: str | None, ids: list[str], reason: str | None, label: str
        ) -> None:
            if support == "evidence":
                if not ids or reason is not None:
                    raise ValueError(f"{label} evidence support requires only evidence IDs")
                if not set(ids) <= known_evidence:
                    raise ValueError(f"{label} references unknown evidence")
            elif support == "assumption":
                if ids or reason is None:
                    raise ValueError(f"{label} assumption requires only an explicit reason")
            else:
                raise ValueError(f"{label} must declare evidence or assumption support")

        for index, basis in enumerate(constructed.claim_basis):
            if basis.semantic_role is None or basis.target is None:
                raise ValueError("claim basis requires semantic role and target mapping")
            check_support(
                support=basis.support, ids=basis.evidence_ids,
                reason=basis.assumption_reason, label=f"claim basis {index}",
            )
            collection_size = {
                "selection_rationale": 1,
                "allowed_variation": len(constructed.allowed_variations),
                "unresolved": len(constructed.unresolved),
                "nonblocking_unknown": len(constructed.nonblocking_unknowns),
                "formal_claim": len(role_claims[basis.semantic_role]),
            }[basis.target]
            if basis.target_index is None or basis.target_index >= collection_size:
                raise ValueError("claim basis target index is outside its mapped collection")

        mapped = {(item.target, item.target_index) for item in constructed.claim_basis}
        required_mappings = {("selection_rationale", 0)}
        required_mappings.update(
            ("allowed_variation", index)
            for index in range(len(constructed.allowed_variations))
        )
        required_mappings.update(
            ("unresolved", index) for index in range(len(constructed.unresolved))
        )
        required_mappings.update(
            ("nonblocking_unknown", index)
            for index in range(len(constructed.nonblocking_unknowns))
        )
        if not required_mappings <= mapped:
            raise ValueError("important constructed judgments lack claim-basis mappings")

        for index, behavior in enumerate(constructed.critical_behaviors):
            if behavior.semantic_role is None or behavior.formal_claim_index is None:
                raise ValueError("critical behavior requires a formal claim mapping")
            if behavior.formal_claim_index >= len(role_claims[behavior.semantic_role]):
                raise ValueError("critical behavior formal claim index is outside its role")
            check_support(
                support=behavior.support, ids=behavior.evidence_ids,
                reason=behavior.assumption_reason, label=f"critical behavior {index}",
            )
    return contract


def _validate_contrast(
    contrast: ContrastResult, contract: ScenarioContract, *, strict_mapping: bool = False,
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
    if strict_mapping:
        if contrast.semantic_role is None:
            raise ValueError("contrast must declare its semantic role")
        if contrast.support == "evidence":
            if not contrast.evidence_ids or contrast.assumption_reason is not None:
                raise ValueError("evidence-supported contrast requires only evidence IDs")
        elif contrast.support == "assumption":
            if contrast.evidence_ids or contrast.assumption_reason is None:
                raise ValueError("assumption contrast requires only an explicit reason")
        else:
            raise ValueError("contrast must declare evidence or assumption support")


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
    model_calls: list[dict] = []
    format_repair_log: list[dict] = []
    format_repair_allowance = FormatRepairAllowance()
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
    reviewed_adapter_language: Literal["python", "java"] | None = None
    repository = None
    source_store: SourceStore | None = None
    model_call_limit = -1
    exploration_stop: ExplorationStop | None = None
    cancelled = False

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
            max_repeated_no_progress=config.repetition_threshold,
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
            try:
                assert reviewed_snapshot is not None
                reviewed_adapter_language = reviewed_live_case_language(requested_case_id)
                if reviewed_adapter_language == "python":
                    from mock_spec.adapters.python import (
                        load_task, load_task_with_repository,
                    )
                else:
                    from mock_spec.adapters.java import (
                        load_task, load_task_with_repository,
                    )
                if config.repository_read:
                    reviewed_task, repository = load_task_with_repository(
                        reviewed_snapshot, case_id=requested_case_id or ""
                    )
                else:
                    reviewed_task = load_task(
                        reviewed_snapshot, case_id=requested_case_id or ""
                    )
                task_payload = reviewed_task.to_dict()
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
                if (
                    isinstance(task_payload, tuple)
                    and len(task_payload) == 2
                    and isinstance(task_payload[0], UnifiedTask)
                    and isinstance(task_payload[1], ReviewedRepository)
                ):
                    reviewed_task, repository = task_payload
                    task_payload = reviewed_task.to_dict()
            except Exception as exc:
                raise StagedInferenceError(
                    f"input review failed: {exc}",
                    stage="INPUT",
                    stop_reason="input_invalid",
                ) from None
        if not isinstance(task_payload, dict):
            raise ValueError("task payload must be an object")
        task = UnifiedTask.from_dict(task_payload)
        source_store = SourceStore(task, repository=repository)
        model_call_limit = -1 if repository is not None else config.max_model_calls
        canonical_task = task.to_dict()
        input_bytes = canonical_json(canonical_task)
        input_hash = sha256_bytes(input_bytes)
        case_id, language = task.case_id, task.language
        current_stage = "ARTIFACT"
        _write_json_exclusive(output_dir / "input_snapshot.json", canonical_task, canonical=True)

        current_stage = "EXTRACT"
        extract_wire = await _call_stage(
            "EXTRACT", ExtractSelection,
            {"stage": "EXTRACT", "visible_task": canonical_task}, model=model,
            config=config, ledger=ledger, responses=responses,
            model_calls=model_calls, deadline=deadline,
            model_call_limit=model_call_limit,
        )
        extract_entry = _wire_stage_entry(
            "EXTRACT", extract_wire,
            model_call_sequence=int(model_calls[-1]["sequence"]),
        )
        stages.append(extract_entry)
        source_store.validate_task(task)
        try:
            extract = _materialize_extract(extract_wire, source_store)
        except CitationMaterializationError as exc:
            _record_citation_error(extract_entry, exc)
            raise
        _complete_stage_entry(extract_entry, extract)
        for reference in extract.original_goals:
            _check_source_reference(reference, source_store)
        for candidate in extract.candidates:
            for reference in candidate.evidence:
                _check_source_reference(reference, source_store)
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
            source_store=source_store,
            max_attempts=(None if repository is not None else config.max_query_attempts),
            max_no_progress_attempts=config.repetition_threshold,
            max_output_chars=config.max_tool_output_chars,
            analysis_timeout_seconds=config.parsing_timeout_seconds,
            deadline=deadline,
        )
        candidate_ids = {item.scenario_id for item in extract.candidates}
        pending_receipt: dict[str, object] | None = None
        while True:
            query_payload = {
                "stage": "QUERY",
                "visible_task": canonical_task,
                "extract": extract.model_dump(mode="json"),
                "query_receipts": query_executor.attempts,
            }
            response_start = len(responses)
            original_attempt = None
            repaired_output = False
            try:
                query_wire = await _call_stage(
                    "QUERY", QuerySelection, query_payload,
                    model=model, config=config, ledger=ledger,
                    responses=responses, model_calls=model_calls, deadline=deadline,
                    model_call_limit=model_call_limit,
                )
            except StagedInferenceError as exc:
                saved_budget_response = (
                    exc.stop_reason == "budget_exhausted"
                    and len(responses) == response_start + 1
                )
                if exc.stop_reason != "output_invalid" and not saved_budget_response:
                    raise
                raw: object = None
                raw_error: str | None = None
                try:
                    if len(responses) != response_start + 1:
                        raise ValueError("QUERY failure did not preserve one response")
                    raw = _completed_response_json(responses[-1])
                except (ValueError, TypeError) as raw_exc:
                    raw_error = _safe_external_text(raw_exc, limit=200)
                original_call_sequence = int(model_calls[-1]["sequence"])
                if saved_budget_response and raw_error is not None:
                    stages.append({
                        "stage": "QUERY", "model_call_sequence": original_call_sequence,
                        "citation_mode": CITATION_MODE, "wire_result": raw, "result": None,
                        "validation_error": raw_error,
                    })
                    format_repair_log.append({
                        "stage": "QUERY_FORMAT_REPAIR", "category": "unavailable_raw",
                        "original_call_sequence": original_call_sequence,
                        "original_attempt_sequence": None,
                        "repaired_attempt_sequence": None, "repair_call_sequence": None,
                        "error_positions": [], "eligible": False, "raw_before": raw,
                        "comparison_snapshot": None,
                        "comparison_snapshot_sha256": None,
                        "pending_receipt_identity": None, "raw_after": None,
                        "outcome": "blocked_budget", "allowance_use": format_repair_allowance.state,
                        "budget_reason": "budget exhausted after the saved response",
                    })
                    raise
                if saved_budget_response:
                    try:
                        QuerySelection.model_validate(raw)
                    except ValidationError:
                        pass
                    else:
                        stages.append({
                            "stage": "QUERY", "model_call_sequence": original_call_sequence,
                            "citation_mode": CITATION_MODE, "unvalidated_result": raw,
                            "result": None,
                        })
                        raise
                stages.append({
                    "stage": "QUERY",
                    "model_call_sequence": original_call_sequence,
                    "citation_mode": CITATION_MODE,
                    "wire_result": raw,
                    "result": None,
                    "validation_error": raw_error or "strict staged-query/v0.3 validation failed",
                })
                if isinstance(raw, dict) and (
                    raw.get("decision") == "request_query"
                    or ("query_attempt" in raw and raw.get("query_attempt") is not None)
                ):
                    original_attempt = query_executor.register(raw.get("query_attempt"))
                    query_executor.reject(
                        original_attempt, status="protocol_error",
                        code="invalid_query_protocol",
                        diagnostic="The original QUERY response failed strict protocol validation.",
                    )
                query_wire = await _repair_query_output(
                    raw,
                    original_call_sequence=original_call_sequence,
                    original_attempt_sequence=(
                        None if original_attempt is None else int(original_attempt["sequence"])
                    ),
                    pending_receipt=pending_receipt, run_id=run_id, case_id=case_id,
                    candidate_ids=candidate_ids, task=task, source_store=source_store,
                    model=model, config=config, ledger=ledger, responses=responses,
                    model_calls=model_calls, stages=stages, repair_log=format_repair_log,
                    allowance=format_repair_allowance, deadline=deadline,
                    model_call_limit=model_call_limit,
                )
                repaired_output = True
            query_entry = _wire_stage_entry(
                "QUERY" if not repaired_output else "QUERY_FORMAT_REPAIR",
                query_wire, model_call_sequence=int(model_calls[-1]["sequence"]),
            )
            if repaired_output:
                stages[-1] = query_entry
            else:
                stages.append(query_entry)
            observed_attempt = None
            if query_wire.decision == "request_query" or query_wire.query_attempt is not None:
                observed_attempt = query_executor.register(
                    None if query_wire.query_attempt is None
                    else query_wire.query_attempt.model_dump(mode="json")
                )
                if repaired_output:
                    format_repair_log[-1]["repaired_attempt_sequence"] = int(
                        observed_attempt["sequence"]
                    )
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
            if pending_receipt is None:
                if query_wire.previous_receipt_assessment is not None:
                    if observed_attempt is not None:
                        query_executor.reject(
                            observed_attempt,
                            status="protocol_error",
                            code="unexpected_previous_receipt_assessment",
                            diagnostic="No prior receipt exists to assess.",
                        )
                    raise ValueError("QUERY assessed a receipt that does not exist")
            else:
                if query_wire.previous_receipt_assessment is None:
                    if observed_attempt is not None:
                        query_executor.reject(
                            observed_attempt,
                            status="protocol_error",
                            code="missing_previous_receipt_assessment",
                            diagnostic="The prior receipt must be assessed before another query.",
                        )
                    raise ValueError("QUERY must assess the previous receipt")
            source_store.validate_task(task)
            assessment_sequence = None
            if pending_receipt is not None:
                pending_sequence = pending_receipt.get("sequence")
                if type(pending_sequence) is not int:
                    raise ValueError("pending receipt has an invalid sequence")
                assessment_sequence = pending_sequence
            try:
                query = _materialize_query(
                    query_wire, source_store,
                    assessment_sequence=assessment_sequence,
                )
            except CitationMaterializationError as exc:
                _record_citation_error(query_entry, exc)
                raise
            if pending_receipt is not None:
                assert query.previous_receipt_assessment is not None
                pending_receipt["assessment"] = (
                    query.previous_receipt_assessment.model_dump(mode="json")
                )
                pending_receipt["assessment_status"] = "complete"
            _complete_stage_entry(query_entry, query)

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
                exploration_stop = query.stop_explanation
                if repository is not None and exploration_stop is None:
                    raise ValueError("repository exploration requires an explicit stop explanation")
                if exploration_stop is not None:
                    source_store.validate_task(task)
                    _validate_exploration_stop(exploration_stop, source_store)
                break
            if query.stop_explanation is not None:
                raise ValueError("request_query cannot declare exploration finished")
            assert observed_attempt is not None
            pending_receipt = await query_executor.execute_registered(
                observed_attempt, candidate_ids=candidate_ids
            )
            category = pending_receipt.get("semantic_category")
            if category in {"repeated_exploration", "content_changed", "identity_mismatch"}:
                raise StagedInferenceError(
                    "exploration stopped by controller protection",
                    stage="QUERY", stop_reason=str(category),
                )
            receipt_result = pending_receipt["result"]
            if (
                isinstance(receipt_result, dict)
                and receipt_result.get("error_code") == "run_deadline_exhausted"
            ):
                raise StagedInferenceError(
                    "run deadline exhausted",
                    stage="QUERY",
                    stop_reason="run_timeout",
                )

        current_stage = "CONSTRUCT"
        construct_wire = await _call_stage(
            "CONSTRUCT", ConstructSelection,
            {"stage": "CONSTRUCT", "visible_task": canonical_task,
             "extract": extract.model_dump(mode="json"),
             "query": query.model_dump(mode="json"),
             "query_receipts": query_executor.attempts}, model=model,
            config=config, ledger=ledger, responses=responses,
            model_calls=model_calls, deadline=deadline,
            model_call_limit=model_call_limit,
        )
        construct_entry = _wire_stage_entry(
            "CONSTRUCT", construct_wire,
            model_call_sequence=int(model_calls[-1]["sequence"]),
        )
        stages.append(construct_entry)
        source_store.validate_task(task)
        try:
            constructed = _materialize_construct(construct_wire, source_store)
        except CitationMaterializationError as exc:
            _record_citation_error(construct_entry, exc)
            raise
        _complete_stage_entry(construct_entry, constructed)
        constructed_contract = _validate_constructed(constructed, task, source_store,
                                                    strict_mapping=repository is not None)
        if exploration_stop is not None and any(
            item.blocking for item in exploration_stop.remaining_unknowns
        ) and constructed.result_status == "SELECTED":
            raise ValueError("blocking exploration unknowns cannot become a selected result")
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
                model_calls=model_calls,
                model_call_limit=model_call_limit,
            )
            stages.append({
                "stage": "CONTRAST", "result": contrast.model_dump(mode="json")
            })
            _validate_contrast(contrast, constructed_contract,
                               strict_mapping=repository is not None)
            if contrast.revision_requested:
                current_stage = "REVISE"
                before_revision = constructed
                revision_wire = await _call_stage(
                    "REVISE", RevisionSelection,
                    {"stage": "REVISE", "visible_task": canonical_task,
                     "extract": extract.model_dump(mode="json"),
                     "query_receipts": query_executor.attempts,
                     "construct": before_revision.model_dump(mode="json"),
                     "contrast": contrast.model_dump(mode="json")}, model=model,
                    config=config, ledger=ledger, responses=responses, deadline=deadline,
                    model_calls=model_calls,
                    model_call_limit=model_call_limit,
                )
                revision_entry = _wire_stage_entry(
                    "REVISE", revision_wire,
                    model_call_sequence=int(model_calls[-1]["sequence"]),
                )
                stages.append(revision_entry)
                source_store.validate_task(task)
                try:
                    revision = _materialize_revision(revision_wire, source_store)
                except CitationMaterializationError as exc:
                    _record_citation_error(revision_entry, exc)
                    raise
                _complete_stage_entry(revision_entry, revision)
                before_dump = before_revision.model_dump(mode="json")
                after_dump = revision.revised_construct.model_dump(mode="json")
                changed_objects = _changed_paths(before_dump, after_dump)
                if not changed_objects:
                    raise ValueError("semantic revision must modify the constructed result")
                revised_contract = _validate_constructed(
                    revision.revised_construct, task, source_store,
                    strict_mapping=repository is not None,
                )
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
            allowance=format_repair_allowance,
            source_store=source_store,
            config=config, ledger=ledger, responses=responses,
            model_calls=model_calls, deadline=deadline,
            model_call_limit=model_call_limit,
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
        error = {"type": type(exc).__name__, "message": _safe_external_text(exc)}
    except StagedInferenceError as exc:
        error_stage, stop_reason = exc.stage, exc.stop_reason
        error = {"type": type(exc).__name__, "message": _safe_external_text(exc)}
        contract = None
        selected_scenario_id = None
        result_status = (
            "INCONCLUSIVE" if exc.stop_reason in {
                "budget_exhausted", "run_timeout", "timeout", "repeated_exploration"
            } else "ERROR"
        )
        early_unresolved = [
            f"Controller stopped at {exc.stage}: {exc.stop_reason}; "
            "prior drafts remain diagnostic and do not establish evidence sufficiency."
        ]
    except asyncio.CancelledError:
        contract = None
        selected_scenario_id = None
        result_status = "INCONCLUSIVE"
        cancelled = True
        error_stage, stop_reason = current_stage, "cancelled"
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
        error_stage, stop_reason = current_stage, (
            "artifact_error" if current_stage == "ARTIFACT" else "backend_error"
        )
        error = {"type": type(exc).__name__, "message": "unexpected staged run failure"}

    if isinstance(config, StagedRunConfig) and config.variant == "C":
        degeneracy_contrast = (
            {
                "status": "unavailable",
                "execution_status": "not_executed",
                "support": None, "assumption_reason": None, "semantic_role": None,
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
            "support": None, "assumption_reason": None, "semantic_role": None,
            "alternative": None,
            "bypass_behavior": None,
            "evidence_ids": [],
            "distinguishing_observation": None,
            "revision_requested": False,
            "revision_reason": None,
        }

    record: dict[str, object] = {
        "schema_version": RECORD_VERSION,
        "citation_mode": CITATION_MODE,
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
        "exploration_stop": (
            None if exploration_stop is None else exploration_stop.model_dump(mode="json")
        ),
        "termination": {
            "initiator": "caller" if cancelled else (
                "agent" if error is None else "controller"
            ),
            "reason": stop_reason,
            "evidence_sufficiency_claimed": error is None and exploration_stop is not None
            and exploration_stop.reason == "evidence_sufficient",
        },
        "query_log": [] if query_executor is None else query_executor.attempts,
        "query_accounting": {
            "observed_attempts": 0 if query_executor is None else query_executor.observed_attempts,
            "budgeted_attempts": 0 if query_executor is None else query_executor.budgeted_attempts,
            "limit": (
                None
                if repository is not None
                else (config.max_query_attempts if isinstance(config, StagedRunConfig) else None)
            ),
        },
        "repetition_policy": {
            "name": "normalized_query_and_returned_evidence",
            "max_no_progress_attempts": (
                config.repetition_threshold if isinstance(config, StagedRunConfig) else None
            ),
            "scope": "consecutive repeated operations without evidence increment",
            "total_query_limit": False,
        },
        "source_observations": (
            [] if source_store is None else list(source_store.observations())
        ),
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
        "format_repair_allowance": {
            "state": format_repair_allowance.state,
            "category": format_repair_allowance.category,
        },
        "unresolved": (
            early_unresolved or ([] if constructed is None else constructed.unresolved)
        ),
        "stages": stages,
        "model_calls": model_calls,
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
                f"reviewed_{reviewed_adapter_language}_adapter" if adapter_reviewed
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
            "citation_mode": CITATION_MODE,
        },
        "configuration": None if valid_config is None else {
            key: str(value) if isinstance(value, Decimal) else value
            for key, value in asdict(valid_config).items()
        },
        "controller_limits": {
            "model_calls": (
                None if repository is not None
                else (None if valid_config is None else valid_config.max_model_calls)
            ),
            "query_attempts": (
                None if repository is not None
                else (None if valid_config is None else valid_config.max_query_attempts)
            ),
            "termination": (
                "shared_deadline_token_cost_and_repetition_protection"
                if repository is not None else "configured_call_limits_and_shared_budgets"
            ),
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
            "limit": (
                None if repository is not None
                else (None if valid_config is None else valid_config.max_query_attempts)
            ),
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
    if repository is not None:
        repository.close()
    if cancelled:
        raise asyncio.CancelledError
    return {"record": record, "contract": contract, "manifest": manifest}


__all__ = [
    "ConstructResult", "ContrastResult", "DeliverResult", "ExtractResult", "QueryResult",
    "RevisionResult",
    "StagedInferenceError", "StagedRunConfig", "canonical_json", "execute_staged_run",
]
