"""Provider response capture through the fixed OpenAI SDK transport path."""

from __future__ import annotations

import asyncio
from decimal import Decimal
import json
from pathlib import Path
import sys
import time

from agents import ModelBehaviorError, ModelSettings
from agents.models.interface import ModelTracing
from agents.models.openai_responses import OpenAIResponsesModel
import httpx2
from openai import APIConnectionError, AsyncOpenAI, DefaultAsyncHttpxClient
import pytest

from mock_spec.budget import InferenceBudget, UsageLedger


STAGED_ROOT = Path(__file__).parents[1] / "experiments" / "staged-inference"
sys.path.insert(0, str(STAGED_ROOT))

from controller import StagedInferenceError, StagedRunConfig, _call_stage  # noqa: E402
from stage_protocol import QuerySelection  # noqa: E402
from provider_capture import ProviderCaptureError, ResponseCapture  # noqa: E402


def _body(*, status: str = "completed", usage: object = ...,
          text: str = "ok") -> dict[str, object]:
    body: dict[str, object] = {
        "id": f"resp-{status}", "object": "response", "created_at": 1,
        "status": status, "model": "test-model", "parallel_tool_calls": False,
        "tools": [], "tool_choice": "auto",
        "output": [{
            "id": "msg-1", "type": "message", "role": "assistant",
            "status": "incomplete" if status == "incomplete" else "completed",
            "content": [{"type": "output_text", "text": text, "annotations": []}],
        }],
    }
    if status == "incomplete":
        body["incomplete_details"] = {"reason": "max_output_tokens"}
    if usage is ...:
        body["usage"] = {
            "input_tokens": 20, "output_tokens": 10, "total_tokens": 30,
            "input_tokens_details": {"cached_tokens": 0},
            "output_tokens_details": {"reasoning_tokens": 3},
        }
    elif usage is not None:
        body["usage"] = usage
    return body


def _model(body: dict[str, object], capture: ResponseCapture,
           *, authorization_seen: list[str] | None = None) -> tuple[OpenAIResponsesModel, object]:
    def respond(request: httpx2.Request) -> httpx2.Response:
        if authorization_seen is not None:
            authorization_seen.append(request.headers["authorization"])
        return httpx2.Response(
            200, request=request, headers={"x-request-id": "req-provider-1"}, json=body,
        )

    client = DefaultAsyncHttpxClient(
        transport=httpx2.MockTransport(respond),
        event_hooks={"response": [capture.on_response]},
    )
    api = AsyncOpenAI(
        api_key="sk-test-provider-secret", base_url="https://provider.invalid/v1",
        max_retries=0, http_client=client,
    )
    return OpenAIResponsesModel(model="test-model", openai_client=api), client


async def _call(model: OpenAIResponsesModel):
    return await model.get_response(
        system_instructions="return text", input="{}", model_settings=ModelSettings(),
        tools=[], output_schema=None, handoffs=[], tracing=ModelTracing.DISABLED,
    )


def _ledger() -> UsageLedger:
    ledger = UsageLedger(InferenceBudget(
        max_total_tokens=1_000, max_output_tokens=100,
        input_cost_per_million=Decimal("1"),
        output_cost_per_million=Decimal("2"),
    ))
    ledger.requests_started = 1
    return ledger


def test_incomplete_response_preserves_body_and_recovers_failed_usage() -> None:
    capture = ResponseCapture()
    model, client = _model(_body(
        status="incomplete", text='{"otherwise":"valid structured text"}'
    ), capture)
    event: dict[str, object] = {"sequence": 1, "status": "failed"}
    ledger = _ledger()

    async def run() -> None:
        capture.begin_call(1)
        with pytest.raises(ModelBehaviorError, match="response.incomplete"):
            await _call(model)
        capture.finish_call(event, ledger, [])
        await client.aclose()

    asyncio.run(run())

    assert event["provider_response_capture"] == "captured"
    provider = event["provider_responses"][0]
    assert provider["body"]["status"] == "incomplete"
    assert provider["body"]["incomplete_details"] == {"reason": "max_output_tokens"}
    assert provider["body"]["output"][0]["content"][0]["text"].startswith("{")
    assert event["provider_usage_recovery"] == "recovered_from_http_response"
    assert event["provider_usage"] == {
        "input_tokens": {"presence": "reported", "value": 20},
        "output_tokens": {"presence": "reported", "value": 10},
        "total_tokens": {"presence": "reported", "value": 30},
        "reasoning_tokens": {"presence": "reported", "value": 3},
    }
    assert ledger.requests_completed == 1
    assert ledger.total_tokens == 30
    assert ledger.usage_complete is True
    assert ledger.usage_records[0]["raw_usage_available"] is True


def test_completed_response_is_not_double_counted() -> None:
    capture = ResponseCapture()
    model, client = _model(_body(), capture)
    event: dict[str, object] = {"sequence": 1, "status": "completed"}
    ledger = _ledger()

    async def run() -> None:
        capture.begin_call(1)
        response = await _call(model)
        ledger.after_model_call(response)
        capture.finish_call(event, ledger, [{"usage": {"total_tokens": 30}}])
        await client.aclose()

    asyncio.run(run())

    assert event["provider_usage_recovery"] == "not_needed_sdk_accounted"
    assert ledger.requests_completed == 1
    assert ledger.total_tokens == 30
    assert len(ledger.usage_records) == 1


@pytest.mark.parametrize("body", [_body(status="incomplete", usage=None)])
def test_missing_failed_usage_is_unknown_and_blocks_next_budget_gate(body) -> None:
    capture = ResponseCapture()
    model, client = _model(body, capture)
    event: dict[str, object] = {"sequence": 1, "status": "failed"}
    ledger = _ledger()

    async def run() -> None:
        capture.begin_call(1)
        with pytest.raises(ModelBehaviorError):
            await _call(model)
        capture.finish_call(event, ledger, [])
        await client.aclose()

    asyncio.run(run())

    assert event["provider_usage_recovery"] == "unknown"
    assert all(
        item == {"presence": "not_reported", "value": None}
        for item in event["provider_usage"].values()
    )
    assert ledger.requests_completed == 1
    assert ledger.usage_complete is False
    with pytest.raises(Exception, match="usage is unavailable"):
        ledger.before_model_call(system_prompt=None, input_items=[], agent=object())


def test_no_response_marks_usage_unknown_and_capture_never_records_credentials(
    monkeypatch,
) -> None:
    capture = ResponseCapture()
    event: dict[str, object] = {"sequence": 1, "status": "failed"}
    ledger = _ledger()

    def disconnect(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("offline", request=request)

    client = DefaultAsyncHttpxClient(
        transport=httpx2.MockTransport(disconnect),
        event_hooks={"response": [capture.on_response]},
    )
    model = OpenAIResponsesModel(
        model="test-model",
        openai_client=AsyncOpenAI(
            api_key="unused", base_url="https://provider.invalid/v1",
            max_retries=0, http_client=client,
        ),
    )

    async def fail_without_response() -> None:
        capture.begin_call(1)
        with pytest.raises(APIConnectionError):
            await _call(model)
        capture.finish_call(event, ledger, [])
        await client.aclose()

    asyncio.run(fail_without_response())
    assert event["provider_response_capture"] == "unavailable"
    assert event["provider_usage_recovery"] == "unknown"
    assert all(
        item == {"presence": "unavailable", "value": None}
        for item in event["provider_usage"].values()
    )
    assert ledger.usage_complete is False

    seen: list[str] = []
    capture = ResponseCapture()
    monkeypatch.setenv("OPENAI_API_KEY", "provider-environment-value")
    body = _body(
        status="incomplete",
        text=(
            "Bearer token-value and sk-echoed-secret provider-environment-value "
            "api_key=plain-value password='password-value'"
        ),
    )
    model, client = _model(body, capture, authorization_seen=seen)
    event = {"sequence": 1, "status": "failed"}
    ledger = _ledger()

    async def run() -> None:
        capture.begin_call(1)
        with pytest.raises(ModelBehaviorError):
            await _call(model)
        capture.finish_call(event, ledger, [])
        await client.aclose()

    asyncio.run(run())
    serialized = str(event["provider_responses"])
    assert seen == ["Bearer sk-test-provider-secret"]
    assert "sk-test-provider-secret" not in serialized
    assert "token-value" not in serialized
    assert "provider-environment-value" not in serialized
    assert "plain-value" not in serialized
    assert "password-value" not in serialized
    assert "authorization" not in serialized.lower()


def test_preflight_rejection_does_not_invent_a_completed_request() -> None:
    capture = ResponseCapture()
    ledger = _ledger()
    ledger.requests_started = 0
    event: dict[str, object] = {
        "sequence": 1, "status": "failed", "model_request_started": False,
    }
    capture.begin_call(1)
    capture.finish_call(event, ledger, [])

    assert event["provider_usage_recovery"] == "not_applicable_request_not_started"
    assert ledger.requests_started == ledger.requests_completed == 0
    assert ledger.usage_records == []


def test_explicit_zero_usage_is_reported_but_ledger_remains_conservative() -> None:
    capture = ResponseCapture()
    body = _body(status="incomplete", usage={
        "input_tokens": 0, "output_tokens": 0, "total_tokens": 0,
        "output_tokens_details": {"reasoning_tokens": 0},
    })
    model, client = _model(body, capture)
    event: dict[str, object] = {"sequence": 1, "status": "failed"}
    ledger = _ledger()

    async def run() -> None:
        capture.begin_call(1)
        with pytest.raises(ModelBehaviorError):
            await _call(model)
        capture.finish_call(event, ledger, [])
        await client.aclose()

    asyncio.run(run())

    assert event["provider_usage"] == {
        name: {"presence": "reported", "value": 0}
        for name in ("input_tokens", "output_tokens", "total_tokens", "reasoning_tokens")
    }
    assert ledger.usage_complete is False
    assert ledger.usage_records[0]["known"] is False


def test_capture_truncation_turns_completed_sdk_response_into_failure() -> None:
    capture = ResponseCapture(max_body_bytes=8)
    model, client = _model(_body(), capture)
    event: dict[str, object] = {
        "sequence": 1, "status": "completed", "model_request_started": True,
    }
    ledger = _ledger()

    async def run() -> None:
        capture.begin_call(1)
        response = await _call(model)
        ledger.after_model_call(response)
        with pytest.raises(ProviderCaptureError, match="truncated"):
            capture.finish_call(event, ledger, [{"usage": {"total_tokens": 30}}])
        await client.aclose()

    asyncio.run(run())

    assert event["status"] == "failed"
    assert event["provider_response_capture"] == "capture_failed"
    assert event["exception"]["error_code"] == "provider_body_truncated"
    assert all(
        item == {"presence": "unavailable", "value": None}
        for item in event["provider_usage"].values()
    )


def test_call_stage_records_incomplete_response_without_format_repair() -> None:
    value = {
        "schema_version": "staged-query/v0.4",
        "decision": "finish_queries",
        "query_attempt": None,
        "previous_receipt_assessment": None,
        "impact": "The visible evidence is sufficient.",
        "stop_explanation": None,
        "question_updates": [],
        "candidate_updates": [],
    }
    capture = ResponseCapture()
    model, client = _model(_body(
        status="incomplete", text=json.dumps(value)
    ), capture)
    model.response_capture = capture
    calls: list[dict] = []
    responses: list[dict] = []
    ledger = UsageLedger(InferenceBudget(max_total_tokens=10_000))

    async def run() -> None:
        with pytest.raises(StagedInferenceError) as caught:
            await _call_stage(
                "QUERY", QuerySelection, {"stage": "QUERY"}, model=model,
                config=StagedRunConfig(), ledger=ledger, responses=responses,
                model_calls=calls, deadline=time.monotonic() + 5,
            )
        assert caught.value.stop_reason == "output_invalid"
        await client.aclose()

    asyncio.run(run())

    assert responses == []
    assert len(calls) == 1
    assert calls[0]["stage"] == "QUERY"
    assert calls[0]["status"] == "failed"
    assert calls[0]["provider_response_capture"] == "captured"
    assert calls[0]["provider_usage"]["total_tokens"] == {
        "presence": "reported", "value": 30,
    }
    assert ledger.requests_started == ledger.requests_completed == 1
    assert ledger.total_tokens == 30


@pytest.mark.parametrize("truncated", [False, True])
def test_unexpected_ledger_failure_is_not_hidden(truncated: bool) -> None:
    class BrokenLedger:
        def after_model_call(self, response) -> None:
            raise RuntimeError("ledger invariant broken")

    capture = ResponseCapture(max_body_bytes=1 if truncated else 2_000_000)
    body = _body(status="incomplete")
    model, client = _model(body, capture)
    event: dict[str, object] = {
        "sequence": 1, "status": "failed", "model_request_started": True,
    }

    async def run() -> None:
        capture.begin_call(1)
        with pytest.raises(ModelBehaviorError):
            await _call(model)
        with pytest.raises(RuntimeError, match="ledger invariant broken"):
            capture.finish_call(event, BrokenLedger(), [])
        await client.aclose()

    asyncio.run(run())

    assert event["provider_responses"]
    assert event["provider_usage"]
    assert event["provider_response_capture"] == (
        "capture_failed" if truncated else "captured"
    )
