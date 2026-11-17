"""Tool-augmented Scenario Inference Agent V0.2."""

from __future__ import annotations

import asyncio
import json
import math
import os
import time
from pathlib import Path
from typing import Final

from agents import (
    Agent,
    MaxTurnsExceeded,
    ModelBehaviorError,
    ModelRetrySettings,
    ModelSettings,
    RunConfig,
    Runner,
)
from pydantic import ValidationError
from .source_tools import build_source_tools
from .budget import BudgetHooks, InferenceBudget, UsageLedger
from .inference import InferenceError
from .scenario import ScenarioContract, validate_contract
from .schemas import UnifiedTask
from .structured_output import (
    PydanticOutputSchema,
    StructuredOutputValidationError,
    StructuredOutputEnvelopeError,
    parse_pydantic_json,
    retry_validation_errors,
    validation_error_summary,
)
from .validation import SourceStore, InputValidationError


PROMPT_VERSION: Final = "scenario-inference/v0.2"
_PROMPT_PATH: Final = Path(__file__).with_name("prompts") / "scenario_v0.2.txt"


class InferenceTimeoutError(InferenceError):
    """Identify the host deadline without changing serialized failure metadata."""

    def __init__(self, message: str = "scenario inference timed out"):
        super().__init__(message, "budget_exhausted")


class _UnexpectedFinalOutputError(TypeError):
    pass


def _load_prompt() -> str:
    try:
        prompt = _PROMPT_PATH.read_text(encoding="utf-8")
    except OSError as exc:
        raise InferenceError("scenario inference prompt is unavailable") from exc
    if PROMPT_VERSION not in prompt:
        raise InferenceError("scenario inference prompt version is invalid")
    return prompt


def _wrapped_inference_error(exc: BaseException) -> InferenceError | None:
    """Recover a host budget error wrapped by the SDK's tool boundary."""

    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, InferenceError):
            return current
        current = current.__cause__ or current.__context__
    return None


def build_agent(
    model: str,
    task: UnifiedTask,
    *,
    budget: InferenceBudget | None = None,
    tool_trace: list | None = None,
    source_store: SourceStore | None = None,
) -> Agent[None]:
    """Build one task-scoped V0.2 agent with read-only source access."""

    if not isinstance(model, str) or not model.strip():
        raise InferenceError("scenario inference model must be configured")
    if not isinstance(task, UnifiedTask):
        raise InferenceError("scenario inference requires a UnifiedTask")
    if budget is None:
        budget = InferenceBudget()
    if not isinstance(budget, InferenceBudget):
        raise InferenceError("scenario inference budget is invalid")

    return Agent(
        name="Scenario Inference Agent V0.2",
        instructions=_load_prompt(),
        model=model.strip(),
        model_settings=ModelSettings(
            store=False,
            max_tokens=budget.max_output_tokens,
            preserve_raw_usage=True,
            retry=ModelRetrySettings(max_retries=0),
        ),
        tools=build_source_tools(
            task, budget=budget, tool_trace=tool_trace, source_store=source_store
        ),
        handoffs=[],
        output_type=PydanticOutputSchema(ScenarioContract),
    )


async def infer_scenario(
    task: UnifiedTask,
    *,
    model: str,
    timeout_seconds: float = 120,
    responses: list | None = None,
    tool_trace: list | None = None,
    source_store: SourceStore | None = None,
    budget: InferenceBudget | None = None,
    usage_ledger: UsageLedger | None = None,
) -> ScenarioContract:
    """Infer and validate a V0.2 scenario contract from one reviewed task."""

    if not isinstance(task, UnifiedTask):
        raise InferenceError("scenario inference requires a UnifiedTask")
    if not isinstance(timeout_seconds, (int, float)) or isinstance(timeout_seconds, bool):
        raise InferenceError("scenario inference timeout must be positive")
    if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
        raise InferenceError("scenario inference timeout must be positive")
    if budget is None:
        budget = InferenceBudget()
    if not isinstance(budget, InferenceBudget):
        raise InferenceError("scenario inference budget is invalid")
    if not os.environ.get("OPENAI_API_KEY", "").strip():
        raise InferenceError("OPENAI_API_KEY is required for scenario inference")

    source_store = source_store if source_store is not None else SourceStore(task)
    run_deadline = time.monotonic() + float(timeout_seconds)
    source_store.limit_deadline(run_deadline)
    source_store.validate_task(task)
    effective_tool_trace = [] if tool_trace is None else tool_trace
    agent = build_agent(
        model,
        task,
        budget=budget,
        tool_trace=effective_tool_trace,
        source_store=source_store,
    )
    if usage_ledger is None:
        usage_ledger = UsageLedger(budget)
    elif not isinstance(usage_ledger, UsageLedger) or usage_ledger.budget != budget:
        raise InferenceError("scenario inference usage ledger is invalid")
    payload = json.dumps(task.to_dict(), ensure_ascii=False, separators=(",", ":"))
    run_config = RunConfig(
        tracing_disabled=True,
        trace_include_sensitive_data=False,
        workflow_name=PROMPT_VERSION,
    )
    captured_responses = [] if responses is None else responses
    last_validation_summary: str | None = None

    async def run_attempt(attempt: int) -> ScenarioContract:
        nonlocal last_validation_summary
        remaining = run_deadline - time.monotonic()
        if remaining <= 0:
            raise asyncio.TimeoutError
        request = payload
        active_agent = agent
        if attempt:
            active_agent = agent.clone(tools=[])
            repair_context = {
                "task": task.to_dict(),
                "acquired_tool_results": effective_tool_trace,
                "validation_errors": last_validation_summary,
                "repair_instruction": (
                    "The previous final response failed the declared output schema. Generate a "
                    "new candidate JSON object from the task and acquired tool results, matching "
                    "the native response schema. Do not request tools; the host will validate the "
                    "new candidate independently."
                ),
            }
            request = json.dumps(
                repair_context, ensure_ascii=False, separators=(",", ":")
            )
        try:
            result = await asyncio.wait_for(
                Runner.run(
                    active_agent,
                    input=request,
                    max_turns=(
                        1 if attempt
                        else (None if source_store.repository is not None else budget.max_turns)
                    ),
                    run_config=run_config,
                    hooks=BudgetHooks(usage_ledger, captured_responses),
                ),
                timeout=remaining,
            )
        except StructuredOutputValidationError as exc:
            last_validation_summary = validation_error_summary(exc)
            raise
        output = result.final_output
        try:
            if isinstance(output, str):
                return parse_pydantic_json(ScenarioContract, output)
            if isinstance(output, ScenarioContract):
                return parse_pydantic_json(ScenarioContract, output.model_dump_json())
        except ValidationError as exc:
            wrapped = StructuredOutputValidationError(exc)
            last_validation_summary = validation_error_summary(wrapped)
            raise wrapped from exc
        except InputValidationError as exc:
            raise StructuredOutputEnvelopeError("model output has ambiguous JSON fields") from exc
        raise _UnexpectedFinalOutputError(
            "scenario inference final output has an unexpected type"
        )

    try:
        contract = await retry_validation_errors(run_attempt, max_retries=1)
        if time.monotonic() >= run_deadline:
            raise asyncio.TimeoutError
    except asyncio.TimeoutError:
        raise InferenceTimeoutError() from None
    except MaxTurnsExceeded:
        raise InferenceError(
            "scenario inference turn budget exhausted", "budget_exhausted"
        ) from None
    except InferenceError:
        raise
    except ModelBehaviorError:
        raise InferenceError(
            "scenario inference output structure failed", "structure_error"
        ) from None
    except _UnexpectedFinalOutputError:
        raise InferenceError(
            "scenario inference output structure failed", "structure_error"
        ) from None
    except Exception as exc:
        wrapped = _wrapped_inference_error(exc)
        if wrapped is not None:
            raise wrapped from None
        raise InferenceError(
            "scenario inference model call failed",
            "model_call_failure",
            type(exc).__name__,
        ) from None

    try:
        return validate_contract(contract, task, source_store=source_store)
    except (ValueError, KeyError):
        raise InferenceError(
            "scenario inference returned an invalid contract", "evidence_error"
        ) from None


__all__ = [
    "InferenceBudget",
    "InferenceTimeoutError",
    "PROMPT_VERSION",
    "build_agent",
    "infer_scenario",
]
