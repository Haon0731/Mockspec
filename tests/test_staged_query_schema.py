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
    ConstructSelection, DeliverSelection, QuerySelection, RevisionSelection,
    StagedRunConfig, _StageOutputSchema, _call_stage,
)


def test_query_schema_is_accepted_by_provider_without_untyped_union_branches() -> None:
    requests: list[dict] = []
    calls: list[dict] = []
    result = {
        "schema_version": "staged-query/v0.2",
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
            "QUERY", QuerySelection, {"stage": "QUERY"},
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
    result = QuerySelection.model_validate({
        "schema_version": "staged-query/v0.2", "decision": "request_query",
        "query_attempt": {"tool": "unknown", "arguments_json": "{"},
        "previous_receipt_assessment": "not-an-assessment", "impact": "inspect",
    })
    assert result.query_attempt == {"tool": "unknown", "arguments_json": "{"}
    assert result.previous_receipt_assessment == "not-an-assessment"


@pytest.mark.parametrize(
    "output_type",
    [QuerySelection, ConstructSelection, DeliverSelection, RevisionSelection],
)
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


def test_contract_selection_schema_strictly_rejects_legacy_quote_and_bad_nested_types() -> None:
    schema = _StageOutputSchema(DeliverSelection)
    contract = {
        "schema_version": "mock-spec.scenario/v0.1",
        "case_id": "case", "language": "python",
        "test_objectives": [{
            "statement": "goal", "status": "required", "scenario_ids": ["s"],
            "condition": "always", "evidence_ids": ["e"],
        }],
        "scenario_hypotheses": [{
            "id": "s", "description": "scenario", "condition": "always",
            "status": "candidate", "evidence_ids": ["e"],
        }],
        "dependency_constraints": [], "expected_interactions": [],
        "verification_candidates": [],
        "evidence": [{
            "id": "e", "source_id": "mut:0", "start_line": 1, "end_line": 1,
            "rationale": "source",
        }],
        "uncertainty": [],
    }
    output = {
        "schema_version": "staged-deliver/v0.2", "result_status": "SELECTED",
        "selected_scenario_id": "s", "contract": contract,
    }
    assert schema.validate_json(json.dumps(output)).contract is not None

    contract["evidence"][0]["quote"] = "legacy"
    with pytest.raises(ModelBehaviorError):
        schema.validate_json(json.dumps(output))
    contract["evidence"][0].pop("quote")
    contract["test_objectives"][0]["scenario_ids"] = "s"
    with pytest.raises(ModelBehaviorError):
        schema.validate_json(json.dumps(output))
    contract["test_objectives"][0]["scenario_ids"] = ["s"]
    contract["scenario_hypotheses"][0]["evidence_ids"] = ["unknown"]
    with pytest.raises(ModelBehaviorError):
        schema.validate_json(json.dumps(output))
