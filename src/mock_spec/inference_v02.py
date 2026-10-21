"""Tool-augmented Scenario Inference Agent V0.2."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import json
import math
import os
from pathlib import Path
from time import perf_counter
from typing import Final

from agents import (
    Agent,
    MaxTurnsExceeded,
    ModelBehaviorError,
    ModelRetrySettings,
    ModelSettings,
    RunConfig,
    Runner,
    function_tool,
)
from pydantic import StrictInt, ValidationError

from .budget import BudgetHooks, UsageLedger
from .inference import InferenceError
from .program_tools import ProgramTools
from .scenario import ScenarioContract, validate_contract
from .schemas import UnifiedTask
from .validation import decode_json


PROMPT_VERSION: Final = "scenario-inference/v0.2"
_PROMPT_PATH: Final = Path(__file__).with_name("prompts") / "scenario_v0.2.txt"


@dataclass(frozen=True)
class InferenceBudget:
    """Host-configured limits for one V0.2 inference run."""

    max_turns: int = 6
    max_tool_calls: int = 8
    max_output_tokens: int = 4_096
    max_total_tokens: int = 24_000
    max_tool_output_chars: int = 16_000
    analysis_timeout_seconds: float = 15
    max_cost_usd: Decimal | None = None
    input_cost_per_million: Decimal | None = None
    output_cost_per_million: Decimal | None = None

    def __post_init__(self) -> None:
        for name in ("max_turns", "max_tool_calls", "max_output_tokens", "max_total_tokens"):
            value = getattr(self, name)
            if type(value) is not int or value < 1:
                raise InferenceError(f"scenario inference {name} must be a positive integer")
        if self.max_output_tokens > self.max_total_tokens:
            raise InferenceError(
                "scenario inference max_output_tokens cannot exceed max_total_tokens"
            )
        if type(self.max_tool_output_chars) is not int or self.max_tool_output_chars < 512:
            raise InferenceError(
                "scenario inference max_tool_output_chars must be an integer >= 512"
            )
        if (
            not isinstance(self.analysis_timeout_seconds, (int, float))
            or isinstance(self.analysis_timeout_seconds, bool)
            or not math.isfinite(self.analysis_timeout_seconds)
            or self.analysis_timeout_seconds <= 0
        ):
            raise InferenceError(
                "scenario inference analysis_timeout_seconds must be positive"
            )
        decimal_fields = (
            "max_cost_usd", "input_cost_per_million", "output_cost_per_million"
        )
        for name in decimal_fields:
            value = getattr(self, name)
            if value is None:
                continue
            if isinstance(value, bool):
                raise InferenceError(f"scenario inference {name} must be positive")
            try:
                parsed = Decimal(str(value))
            except (InvalidOperation, ValueError):
                raise InferenceError(
                    f"scenario inference {name} must be positive"
                ) from None
            if not parsed.is_finite() or parsed <= 0:
                raise InferenceError(f"scenario inference {name} must be positive")
            object.__setattr__(self, name, parsed)
        prices = (self.input_cost_per_million, self.output_cost_per_million)
        if (prices[0] is None) != (prices[1] is None):
            raise InferenceError(
                "scenario inference input and output prices must be configured together"
            )
        if self.max_cost_usd is not None and prices[0] is None:
            raise InferenceError(
                "scenario inference cost limit requires explicit input and output prices"
            )


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

    program_tools = ProgramTools(
        task,
        max_output_chars=budget.max_tool_output_chars,
        analysis_timeout_seconds=budget.analysis_timeout_seconds,
    )
    trace = [] if tool_trace is None else tool_trace
    tool_call_count = 0

    async def invoke_tool(name: str, arguments: dict, call) -> dict:
        nonlocal tool_call_count
        sequence = tool_call_count + 1
        tool_call_count = sequence
        started = perf_counter()
        record = {
            "sequence": sequence,
            "tool": name,
            "arguments": arguments,
            "status": "running",
            "result": None,
            "duration_seconds": None,
        }
        trace.append(record)
        if sequence > budget.max_tool_calls:
            record.update(
                status="budget_exhausted",
                duration_seconds=perf_counter() - started,
            )
            raise InferenceError(
                "scenario inference tool-call budget exhausted", "budget_exhausted"
            )
        try:
            result = await call()
        except asyncio.CancelledError:
            record.update(
                status="cancelled",
                duration_seconds=perf_counter() - started,
            )
            raise
        except InferenceError:
            record.update(
                status="budget_exhausted",
                duration_seconds=perf_counter() - started,
            )
            raise
        except Exception as exc:
            result = {
                "status": "error",
                "error_code": "analysis_failure",
                "diagnostics": [type(exc).__name__],
                "limitations": [
                    "Analysis failure does not establish absence of behavior."
                ],
            }
        status = result.get("status", "error") if isinstance(result, dict) else "error"
        record.update(
            status=status,
            result=result,
            duration_seconds=perf_counter() - started,
        )
        return result

    @function_tool(failure_error_function=None)
    async def inspect_source(
        source_id: str, start_line: StrictInt, end_line: StrictInt
    ) -> dict:
        """Return an allowed source range with exact line numbers and original text.

        Args:
            source_id: An opaque source identifier present in the supplied UnifiedTask.
            start_line: First requested source line, inclusive.
            end_line: Last requested source line, inclusive.
        """
        arguments = {
            "source_id": source_id,
            "start_line": start_line,
            "end_line": end_line,
        }
        return await invoke_tool(
            "inspect_source",
            arguments,
            lambda: program_tools.inspect_source(**arguments),
        )

    @function_tool(failure_error_function=None)
    async def inspect_program_structure(source_id: str) -> dict:
        """Return syntax-level calls, conditions, branches, returns, and handlers.

        Args:
            source_id: An opaque source identifier present in the supplied UnifiedTask.
        """
        arguments = {"source_id": source_id}
        return await invoke_tool(
            "inspect_program_structure",
            arguments,
            lambda: program_tools.inspect_program_structure(**arguments),
        )

    @function_tool(failure_error_function=None)
    async def inspect_dependency_usage(source_id: str) -> dict:
        """Return syntax-level dependency calls and reliable local usage relations.

        Args:
            source_id: An opaque source identifier present in the supplied UnifiedTask.
        """
        arguments = {"source_id": source_id}
        return await invoke_tool(
            "inspect_dependency_usage",
            arguments,
            lambda: program_tools.inspect_dependency_usage(**arguments),
        )

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
        tools=[inspect_source, inspect_program_structure, inspect_dependency_usage],
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
