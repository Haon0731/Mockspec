"""Tool-augmented Scenario Inference Agent V0.2."""

from __future__ import annotations

import asyncio
import json
import math
import os
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
from .validation import decode_json


PROMPT_VERSION: Final = "scenario-inference/v0.2"
_PROMPT_PATH: Final = Path(__file__).with_name("prompts") / "scenario_v0.2.txt"


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
        tools=build_source_tools(task, budget=budget, tool_trace=tool_trace),
        handoffs=[],
        output_type=ScenarioContract,
    )


async def infer_scenario(
    task: UnifiedTask,
    *,
    model: str,
    timeout_seconds: float = 120,
    responses: list | None = None,
    tool_trace: list | None = None,
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

    agent = build_agent(model, task, budget=budget, tool_trace=tool_trace)
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

    try:
        result = await asyncio.wait_for(
            Runner.run(
                agent,
                input=payload,
                max_turns=budget.max_turns,
                run_config=run_config,
                hooks=BudgetHooks(usage_ledger, responses),
            ),
            timeout=float(timeout_seconds),
        )
    except asyncio.TimeoutError:
        raise InferenceError("scenario inference timed out", "budget_exhausted") from None
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
        output = result.final_output
        if isinstance(output, ScenarioContract):
            output = output.model_dump()
        if isinstance(output, str):
            output = decode_json(output)
        contract = ScenarioContract.model_validate(output)
    except (ValidationError, ValueError):
        raise InferenceError(
            "scenario inference returned an invalid contract", "structure_error"
        ) from None
    try:
        return validate_contract(contract, task)
    except (ValueError, KeyError):
        raise InferenceError(
            "scenario inference returned an invalid contract", "evidence_error"
        ) from None


__all__ = [
    "InferenceBudget",
    "PROMPT_VERSION",
    "build_agent",
    "infer_scenario",
]
