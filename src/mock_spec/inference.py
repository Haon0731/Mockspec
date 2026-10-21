"""Scenario inference through one tool-free OpenAI Agents SDK agent."""

from __future__ import annotations

import asyncio
import json
import math
import os
from pathlib import Path
from typing import Final

from agents import Agent, ModelSettings, RunConfig, Runner, RunHooks, ModelBehaviorError
from pydantic import TypeAdapter, ValidationError
from agents.usage import Usage

from .scenario import ScenarioContract, validate_contract
from .schemas import UnifiedTask
from .validation import decode_json


PROMPT_VERSION: Final = "scenario-inference/v0.1"
_PROMPT_PATH: Final = Path(__file__).with_name("prompts") / "scenario_v0.1.txt"


class InferenceError(RuntimeError):
    """A safe, user-facing scenario inference failure."""

    def __init__(self, message: str, category: str = "configuration_error",
                 diagnostic: str | None = None):
        super().__init__(message)
        self.category = category
        self.diagnostic = diagnostic


class ResponseCapture(RunHooks):
    """Host-only capture before SDK structured-output parsing; never a model tool."""

    def __init__(self, responses: list):
        self.responses = responses

    async def on_llm_end(self, context, agent, response):
        self.responses.append({
            "output": [item.model_dump(mode="json") for item in response.output],
            "usage": TypeAdapter(Usage).dump_python(response.usage, mode="json"),
            "response_id": response.response_id,
            # ModelResponse does not expose the provider's resolved model version.
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


def build_agent(model: str) -> Agent[None]:
    """Build the shared Java/Python scenario inference agent."""

    if not isinstance(model, str) or not model.strip():
        raise InferenceError("scenario inference model must be configured")
    return Agent(
        name="Scenario Inference Agent",
        instructions=_load_prompt(),
        model=model.strip(),
        model_settings=ModelSettings(store=False),
        tools=[],
        handoffs=[],
        output_type=ScenarioContract,
    )


async def infer_scenario(
    task: UnifiedTask,
    *,
    model: str,
    timeout_seconds: float = 120,
    responses: list | None = None,
) -> ScenarioContract:
    """Infer and validate a scenario contract from one reviewed UnifiedTask."""

    if not isinstance(task, UnifiedTask):
        raise InferenceError("scenario inference requires a UnifiedTask")
    if not isinstance(timeout_seconds, (int, float)) or isinstance(timeout_seconds, bool):
        raise InferenceError("scenario inference timeout must be positive")
    if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
        raise InferenceError("scenario inference timeout must be positive")
    if not os.environ.get("OPENAI_API_KEY", "").strip():
        raise InferenceError("OPENAI_API_KEY is required for scenario inference")

    agent = build_agent(model)
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
                max_turns=1,
                run_config=run_config,
                **capture,
            ),
            timeout=float(timeout_seconds),
        )
    except asyncio.TimeoutError:
        raise InferenceError("scenario inference timed out", "model_call_failure") from None
    except ModelBehaviorError:
        raise InferenceError("scenario inference output structure failed",
                             "structure_error") from None
    except Exception as exc:
        # Type only, never provider exception text/request headers/body.
        raise InferenceError("scenario inference model call failed", "model_call_failure",
                             type(exc).__name__) from None

    try:
        output = result.final_output
        if isinstance(output, ScenarioContract):
            output = output.model_dump()
        if isinstance(output, str):
            output = decode_json(output)
        contract = ScenarioContract.model_validate(output)
    except (ValidationError, ValueError):
        raise InferenceError("scenario inference returned an invalid contract",
                             "structure_error") from None
    try:
        return validate_contract(contract, task)
    except (ValueError, KeyError):
        raise InferenceError("scenario inference returned an invalid contract",
                             "evidence_error") from None


__all__ = [
    "InferenceError",
    "PROMPT_VERSION",
    "build_agent",
    "infer_scenario",
]
