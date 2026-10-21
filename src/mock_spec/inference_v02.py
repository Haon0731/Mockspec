"""Tool-augmented Scenario Inference Agent V0.2."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
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
    ModelSettings,
    RunConfig,
    RunHooks,
    Runner,
    function_tool,
)
from agents.usage import Usage
from pydantic import TypeAdapter, ValidationError

from .inference import InferenceError
from .program_tools import ProgramTools
from .scenario import ScenarioContract, validate_contract
from .schemas import UnifiedTask
from .validation import decode_json


PROMPT_VERSION: Final = "scenario-inference/v0.2"
_PROMPT_PATH: Final = Path(__file__).with_name("prompts") / "scenario_v0.2.txt"


@dataclass(frozen=True)
class InferenceBudget:
    """Local execution limits for the first V0.2 inference slice."""

    max_turns: int = 6
    max_tool_calls: int = 8
    max_tool_output_chars: int = 16_000

    def __post_init__(self) -> None:
        for name in ("max_turns", "max_tool_calls"):
            value = getattr(self, name)
            if type(value) is not int or value < 1:
                raise InferenceError(f"scenario inference {name} must be a positive integer")
        if type(self.max_tool_output_chars) is not int or self.max_tool_output_chars < 512:
            raise InferenceError(
                "scenario inference max_tool_output_chars must be an integer >= 512"
            )


class ResponseCapture(RunHooks):
    """Capture provider responses before SDK structured-output parsing."""

    def __init__(self, responses: list):
        self.responses = responses

    async def on_llm_end(self, context, agent, response):
        self.responses.append({
            "output": [item.model_dump(mode="json") for item in response.output],
            "usage": TypeAdapter(Usage).dump_python(response.usage, mode="json"),
            "response_id": response.response_id,
            "resolved_model_version": None,
        })


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

    program_tools = ProgramTools(task, max_output_chars=budget.max_tool_output_chars)
    trace = [] if tool_trace is None else tool_trace
    tool_call_count = 0

    @function_tool(failure_error_function=None)
    async def inspect_source(source_id: str, start_line: int, end_line: int) -> dict:
        """Return an allowed source range with exact line numbers and original text.

        Args:
            source_id: An opaque source identifier present in the supplied UnifiedTask.
            start_line: First requested source line, inclusive.
            end_line: Last requested source line, inclusive.
        """
        nonlocal tool_call_count
        started = perf_counter()
        arguments = {
            "source_id": source_id,
            "start_line": start_line,
            "end_line": end_line,
        }
        tool_call_count += 1
        if tool_call_count > budget.max_tool_calls:
            trace.append({
                "sequence": tool_call_count,
                "tool": "inspect_source",
                "arguments": arguments,
                "status": "budget_exhausted",
                "result": None,
                "duration_seconds": perf_counter() - started,
            })
            raise InferenceError(
                "scenario inference tool-call budget exhausted", "budget_exhausted"
            )
        try:
            result = await program_tools.inspect_source(
                source_id=source_id,
                start_line=start_line,
                end_line=end_line,
            )
        except InferenceError:
            raise
        except Exception as exc:
            result = {
                "status": "error",
                "diagnostic": type(exc).__name__,
            }
        status = result.get("status", "error") if isinstance(result, dict) else "error"
        trace.append({
            "sequence": tool_call_count,
            "tool": "inspect_source",
            "arguments": arguments,
            "status": status,
            "result": result,
            "duration_seconds": perf_counter() - started,
        })
        return result

    return Agent(
        name="Scenario Inference Agent V0.2",
        instructions=_load_prompt(),
        model=model.strip(),
        model_settings=ModelSettings(store=False),
        tools=[inspect_source],
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
    payload = json.dumps(task.to_dict(), ensure_ascii=False, separators=(",", ":"))
    run_config = RunConfig(
        tracing_disabled=True,
        trace_include_sensitive_data=False,
        workflow_name=PROMPT_VERSION,
    )

    try:
        capture = {} if responses is None else {"hooks": ResponseCapture(responses)}
        result = await asyncio.wait_for(
            Runner.run(
                agent,
                input=payload,
                max_turns=budget.max_turns,
                run_config=run_config,
                **capture,
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
