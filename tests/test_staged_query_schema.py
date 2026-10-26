"""Exercise the QUERY wire schema through the real SDK provider-call boundary."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
import sys
import time

import httpx2
import pytest
from agents import ModelBehaviorError
from openai import BadRequestError
from openai.types.responses import Response
from types import SimpleNamespace
from agents.models.openai_responses import OpenAIResponsesModel

from mock_spec.budget import InferenceBudget, UsageLedger


sys.path.insert(0, str(Path(__file__).parents[1] / "experiments" / "staged-inference"))
from controller import (  # noqa: E402
    ConstructResult, DeliverResult, QueryResult, RevisionResult,
    StagedRunConfig, _StageOutputSchema, _call_stage,
)


def test_query_schema_is_accepted_by_provider_without_untyped_union_branches() -> None:
    requests: list[dict] = []
    calls: list[dict] = []
    result = {
        "schema_version": "staged-query/v0.1",
        "decision": "finish_queries",
        "query_attempt": None,
        "previous_receipt_assessment": None,
        "impact": "The visible task is sufficient.",
        "stop_explanation": None,
    }

    def untyped_union(value):
        if isinstance(value, dict):
            if any("type" not in branch
                   for branch in value.get("anyOf", [])):
                return True
            return any(untyped_union(child) for child in value.values())
        return isinstance(value, list) and any(untyped_union(child) for child in value)

    async def respond(**body):
        requests.append(body)
        if untyped_union(body["text"]["format"]["schema"]):
            raise BadRequestError(
                "Invalid json schema: field `anyOf`: missing field `type`",
                response=httpx2.Response(400, request=httpx2.Request("POST", "https://provider.invalid/responses")),
                body={"code": "invalid_request_error"},
            )
        return Response.model_validate({
            "id": "resp-query", "object": "response", "created_at": 1,
            "status": "completed", "model": "test-model",
            "parallel_tool_calls": False, "tools": [], "tool_choice": "auto",
            "output": [{"id": "msg-query", "type": "message", "role": "assistant",
                        "status": "completed", "content": [{"type": "output_text",
                        "text": json.dumps(result), "annotations": []}]}],
            "usage": {"input_tokens": 20, "output_tokens": 10, "total_tokens": 30,
                      "input_tokens_details": {"cached_tokens": 0, "cache_write_tokens": 0},
                      "output_tokens_details": {"reasoning_tokens": 0}},
        })

    async def run():
        client = SimpleNamespace(base_url="https://provider.invalid", responses=SimpleNamespace(create=respond))
        return await _call_stage(
            "QUERY", QueryResult, {"stage": "QUERY"},
            model=OpenAIResponsesModel(model="test-model", openai_client=client),
            config=StagedRunConfig(), ledger=UsageLedger(InferenceBudget()),
            responses=[], model_calls=calls, deadline=time.monotonic() + 10,
        )

    try:
        parsed = asyncio.run(run())
    except Exception as exc:
        raise AssertionError(calls[-1]["exception"]) from exc
    assert parsed.model_dump(mode="json") == result
    assert len(requests) == 1
    assert requests[0].get("tools", []) == []  # Host-dispatched protocol remains intact.
    assert calls[0]["requested_output_schema"] == requests[0]["text"]["format"]["schema"]


def test_malformed_query_envelope_remains_available_for_host_attempt_accounting() -> None:
    result = QueryResult.model_validate({
        "schema_version": "staged-query/v0.1", "decision": "request_query",
        "query_attempt": {"tool": "unknown", "arguments_json": "{"},
        "previous_receipt_assessment": "not-an-assessment", "impact": "inspect",
    })
    assert result.query_attempt == {"tool": "unknown", "arguments_json": "{"}
    assert result.previous_receipt_assessment == "not-an-assessment"


@pytest.mark.parametrize("output_type", [QueryResult, ConstructResult, DeliverResult, RevisionResult])
def test_nested_nullable_stage_objects_have_explicit_wire_types(output_type) -> None:
    schema = _StageOutputSchema(output_type)

    def check(value):
        if isinstance(value, dict):
            for branch in value.get("anyOf", []):
                assert "type" in branch
            for child in value.values():
                check(child)
        elif isinstance(value, list):
            for child in value:
                check(child)

    check(schema.json_schema())
    assert schema.is_strict_json_schema()
    with pytest.raises(ModelBehaviorError):
        schema.validate_json('{"unexpected": true}')
