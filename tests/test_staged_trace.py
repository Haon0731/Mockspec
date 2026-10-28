"""Diagnostic trace coverage for staged model calls."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
import sys
import time

from agents.models.interface import Model, ModelResponse
from agents.usage import Usage
import httpx2
from openai import APIStatusError
from openai.types.responses import ResponseOutputMessage, ResponseOutputText
import pytest

from mock_spec.budget import InferenceBudget, UsageLedger


STAGED_ROOT = Path(__file__).parents[1] / "experiments" / "staged-inference"
sys.path.insert(0, str(STAGED_ROOT))

from controller import (  # noqa: E402
    StagedInferenceError, StagedRunConfig, _call_stage, execute_staged_run,
)
from stage_protocol import QueryResult  # noqa: E402
from scripted import FixedScriptModel, scripted_outputs, synthetic_task  # noqa: E402
def _ledger(config: StagedRunConfig) -> UsageLedger:
    return UsageLedger(InferenceBudget(
        max_turns=config.max_model_calls,
        max_tool_calls=config.max_query_attempts,
        max_output_tokens=config.max_output_tokens,
        max_total_tokens=config.max_total_tokens,
        max_tool_output_chars=config.max_tool_output_chars,
        analysis_timeout_seconds=config.parsing_timeout_seconds,
    ))


class _FailingModel(Model):
    async def get_response(self, *args, **kwargs):
        request = httpx2.Request("POST", "https://provider.invalid/v1/responses")
        response = httpx2.Response(
            429,
            request=request,
            headers={"x-request-id": "req-trace-123"},
            json={
                "error": {
                    "message": "rate limited for sk-test-secret",
                    "type": "rate_limit_error",
                    "code": "rate_limit",
                }
            },
        )
        error = APIStatusError(
            "rate limited for sk-test-secret",
            response=response,
            body=response.json()["error"],
        )
        raise error from RuntimeError("provider transport boundary")

    async def stream_response(self, *args, **kwargs):
        raise AssertionError("streaming is not used")
        yield


class _SuccessModel(Model):
    async def get_response(self, *args, **kwargs):
        value = {
            "schema_version": "staged-query/v0.1",
            "decision": "finish_queries",
            "query_attempt": None,
            "previous_receipt_assessment": None,
            "impact": "The current evidence is sufficient.",
        }
        return ModelResponse(
            output=[ResponseOutputMessage(
                id="msg-trace", type="message", role="assistant", status="completed",
                content=[ResponseOutputText(
                    type="output_text", text=json.dumps(value), annotations=[]
                )],
            )],
            usage=Usage(requests=1, input_tokens=12, output_tokens=8, total_tokens=20),
            response_id="resp-trace-123",
            request_id="req-trace-456",
        )

    async def stream_response(self, *args, **kwargs):
        raise AssertionError("streaming is not used")
        yield


class _BlockingModel(Model):
    def __init__(self) -> None:
        self.started = asyncio.Event()

    async def get_response(self, *args, **kwargs):
        self.started.set()
        await asyncio.Event().wait()

    async def stream_response(self, *args, **kwargs):
        raise AssertionError("streaming is not used")
        yield


def test_api_failure_trace_preserves_safe_provider_diagnostics() -> None:
    config = StagedRunConfig()
    calls: list[dict] = []

    with pytest.raises(StagedInferenceError) as caught:
        asyncio.run(_call_stage(
            "QUERY",
            QueryResult,
            {"stage": "QUERY"},
            model=_FailingModel(),
            config=config,
            ledger=_ledger(config),
            responses=[],
            model_calls=calls,
            deadline=time.monotonic() + 5,
        ))

    assert caught.value.stop_reason == "model_error"
    assert len(calls) == 1
    call = calls[0]
    assert {
        key: call[key]
        for key in (
            "sequence", "stage", "status", "model_request_started",
            "model_name", "output_type", "prompt_version", "payload_bytes",
        )
    } == {
        "sequence": 1,
        "stage": "QUERY",
        "status": "failed",
        "model_request_started": True,
        "model_name": "scripted-offline",
        "output_type": "QueryResult",
        "prompt_version": "staged-scenario-inference-prompts/v0.4",
        "payload_bytes": 17,
    }
    assert call["payload_sha256"]
    assert call["payload"] == {"stage": "QUERY"}
    assert call["instructions"].startswith("staged-scenario-inference-prompts/v0.4")
    assert call["requested_output_schema"]["title"] == "QueryResult"
    assert call["request_start_basis"] == "usage_ledger_before_model_call_hook"
    assert call["timeout_seconds"] == pytest.approx(5, abs=0.1)
    assert call["elapsed_seconds"] >= 0
    assert call["response_refs"] == []
    assert call["exception"] == {
        "classification": "api_status",
        "type": "APIStatusError",
        "message": "rate limited for <REDACTED>",
        "status_code": 429,
        "request_id": "req-trace-123",
        "error_code": "rate_limit",
        "cause_types": ["RuntimeError"],
    }


def test_completed_call_trace_links_response_and_usage() -> None:
    config = StagedRunConfig()
    calls: list[dict] = []
    responses: list[dict] = []

    result = asyncio.run(_call_stage(
        "QUERY",
        QueryResult,
        {"stage": "QUERY"},
        model=_SuccessModel(),
        config=config,
        ledger=_ledger(config),
        responses=responses,
        model_calls=calls,
        deadline=time.monotonic() + 5,
    ))

    assert result.decision == "finish_queries"
    assert calls[0]["status"] == "completed"
    assert calls[0]["exception"] is None
    assert calls[0]["model_request_started"] is True
    assert len(calls[0]["response_refs"]) == 1
    response_ref = calls[0]["response_refs"][0]
    assert response_ref["response_index"] == 0
    assert response_ref["response_id"] == "resp-trace-123"
    assert response_ref["request_id"] == "req-trace-456"
    assert response_ref["usage"]["requests"] == 1
    assert response_ref["usage"]["input_tokens"] == 12
    assert response_ref["usage"]["output_tokens"] == 8
    assert response_ref["usage"]["total_tokens"] == 20


def test_timeout_and_cancellation_are_distinct_trace_outcomes() -> None:
    timeout_config = StagedRunConfig(parsing_timeout_seconds=0.01)
    timeout_calls: list[dict] = []
    with pytest.raises(StagedInferenceError) as timed_out:
        asyncio.run(_call_stage(
            "QUERY", QueryResult, {"stage": "QUERY"},
            model=_BlockingModel(), config=timeout_config,
            ledger=_ledger(timeout_config), responses=[], model_calls=timeout_calls,
            deadline=time.monotonic() + 5,
        ))
    assert timed_out.value.stop_reason == "timeout"
    assert timeout_calls[0]["status"] == "failed"
    assert timeout_calls[0]["exception"]["classification"] == "host_timeout"

    async def cancel_call() -> list[dict]:
        model = _BlockingModel()
        config = StagedRunConfig(parsing_timeout_seconds=5)
        calls: list[dict] = []
        task = asyncio.create_task(_call_stage(
            "QUERY", QueryResult, {"stage": "QUERY"},
            model=model, config=config, ledger=_ledger(config),
            responses=[], model_calls=calls, deadline=time.monotonic() + 5,
        ))
        await asyncio.wait_for(model.started.wait(), timeout=1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        return calls

    cancelled_calls = asyncio.run(cancel_call())
    assert cancelled_calls[0]["status"] == "cancelled"
    assert cancelled_calls[0]["exception"]["classification"] == "cancelled"


def test_completed_run_persists_replayable_model_call_trace(tmp_path: Path) -> None:
    output_dir = tmp_path / "trace-run"
    result = asyncio.run(execute_staged_run(
        synthetic_task(),
        output_dir=output_dir,
        model=FixedScriptModel(scripted_outputs("selected")),
        config=StagedRunConfig(),
    ))

    persisted = json.loads(
        (output_dir / "inference_record.json").read_text(encoding="utf-8")
    )
    calls = result["record"]["model_calls"]
    assert persisted["model_calls"] == calls
    assert [call["stage"] for call in calls] == [
        "EXTRACT", "QUERY", "CONSTRUCT", "DELIVER"
    ]
    assert all(call["status"] == "completed" for call in calls)
    assert all(call["model_request_started"] is True for call in calls)
    assert all(call["payload"]["stage"] == call["stage"] for call in calls)
    assert all(call["requested_output_schema"] for call in calls)
    assert result["manifest"]["artifact_sha256"]["inference_record.json"]


def test_pre_request_budget_gate_is_distinct_from_provider_failure() -> None:
    config = StagedRunConfig(max_model_calls=1)
    ledger = _ledger(config)
    ledger.requests_started = 1
    calls: list[dict] = []

    with pytest.raises(StagedInferenceError) as blocked:
        asyncio.run(_call_stage(
            "QUERY", QueryResult, {"stage": "QUERY"},
            model=_SuccessModel(), config=config, ledger=ledger,
            responses=[], model_calls=calls, deadline=time.monotonic() + 5,
        ))

    assert blocked.value.stop_reason == "budget_exhausted"
    assert calls[0]["status"] == "blocked"
    assert calls[0]["model_request_started"] is False
    assert calls[0]["exception"]["classification"] == "host_budget"
    assert calls[0]["exception"]["error_code"] == "model_call_limit"
