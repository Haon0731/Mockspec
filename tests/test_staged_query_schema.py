"""Exercise the QUERY wire schema through the real SDK provider-call boundary."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
import sys
import time
from typing import Literal

import httpx2
import pytest
from agents import ModelBehaviorError
from openai import BadRequestError
from pydantic import ValidationError, model_validator
from openai.types.responses import Response
from types import SimpleNamespace
from agents.models.openai_responses import OpenAIResponsesModel

from mock_spec.budget import InferenceBudget, UsageLedger


sys.path.insert(0, str(Path(__file__).parents[1] / "experiments" / "staged-inference"))
from controller import (  # noqa: E402
    StagedInferenceError, StagedRunConfig, _StageOutputSchema, _call_stage,
)
from scripted import FixedScriptModel  # noqa: E402
from stage_protocol import (  # noqa: E402
    ConstructSelection, ExtractSelection, QuerySelection, ReferencedContractSelection,
    RevisionSelection, StageModel,
)


class _SemanticSelection(StageModel):
    schema_version: Literal["semantic-test/v0.1"]
    decision: str

    @model_validator(mode="after")
    def require_supported_decision(self):
        if self.decision != "supported":
            raise ValueError("decision lacks semantic support")
        return self


def test_query_schema_is_accepted_by_provider_without_untyped_union_branches() -> None:
    requests: list[dict] = []
    calls: list[dict] = []
    result = {
        "schema_version": "staged-query/v0.6",
        "decision": "finish_queries",
        "query_attempt": None,
        "previous_receipt_assessment": None,
        "impact": "The visible task is sufficient.",
        "stop_explanation": None,
        "question_updates": [],
        "candidate_updates": [],
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


def _call_scripted_stage(stage, output_type, outputs):
    model = FixedScriptModel(outputs)
    calls: list[dict] = []
    responses: list[dict] = []
    ledger = UsageLedger(InferenceBudget(max_total_tokens=10_000))

    async def run():
        result = await _call_stage(
            stage, output_type, {"stage": stage}, model=model,
            config=StagedRunConfig(), ledger=ledger, responses=responses,
            model_calls=calls, deadline=time.monotonic() + 10,
        )
        return result

    return run, model, calls, responses, ledger


def test_non_query_structure_error_retries_once_and_retains_both_attempts() -> None:
    valid = {
        "schema_version": "staged-extract/v0.3", "result_status": "CONTINUE",
        "original_goals": [], "candidates": [], "unresolved": [],
    }
    run, model, calls, responses, ledger = _call_scripted_stage(
        "EXTRACT", ExtractSelection, [{"schema_version": "staged-extract/v0.3"}, valid]
    )

    result = asyncio.run(run())

    assert result.model_dump(mode="json") == valid
    assert model.calls == ledger.requests_started == ledger.requests_completed == 2
    assert len(responses) == len(calls) == 2
    assert calls[0]["status"] == "failed"
    assert calls[0]["structured_output_attempt"] == 1
    assert calls[1]["status"] == "completed"
    assert calls[1]["structured_output_attempt"] == 2
    assert calls[1]["retry_of_model_call_sequence"] == calls[0]["sequence"]
    retry_input = model.inputs[1]
    if isinstance(retry_input, list):
        assert len(retry_input) == 1
        retry_input = retry_input[0]["content"]
    assert isinstance(retry_input, str)
    retry_payload = json.loads(retry_input)
    assert retry_payload["structured_output_retry"] == {
        "attempt": 2,
        "validation_error": (
            "original_goals: missing; candidates: missing; unresolved: missing"
        ),
        "instruction": (
            "Regenerate the same stage result under the declared schema. "
            "Do not treat this format retry as new program evidence."
        ),
    }


def test_non_query_structure_retry_exhaustion_stops_after_two_attempts() -> None:
    invalid = {"schema_version": "staged-extract/v0.3"}
    run, model, calls, responses, ledger = _call_scripted_stage(
        "EXTRACT", ExtractSelection, [invalid, invalid]
    )

    with pytest.raises(StagedInferenceError) as caught:
        asyncio.run(run())

    assert caught.value.stop_reason == "output_invalid"
    assert model.calls == ledger.requests_started == ledger.requests_completed == 2
    assert len(responses) == len(calls) == 2
    assert [call["status"] for call in calls] == ["failed", "failed"]


def test_query_structure_error_does_not_use_generic_retry() -> None:
    valid = {
        "schema_version": "staged-query/v0.6", "decision": "finish_queries",
        "query_attempt": None, "previous_receipt_assessment": None,
        "impact": "done", "stop_explanation": None,
        "question_updates": [], "candidate_updates": [],
    }
    run, model, calls, responses, ledger = _call_scripted_stage(
        "QUERY", QuerySelection, [{"schema_version": "staged-query/v0.6"}, valid]
    )

    with pytest.raises(StagedInferenceError) as caught:
        asyncio.run(run())

    assert caught.value.stop_reason == "output_invalid"
    assert model.calls == ledger.requests_started == ledger.requests_completed == 1
    assert len(responses) == len(calls) == 1


def test_non_query_semantic_value_error_is_not_retried() -> None:
    run, model, calls, responses, ledger = _call_scripted_stage(
        "CONSTRUCT", _SemanticSelection,
        [
            {"schema_version": "semantic-test/v0.1", "decision": "guessed"},
            {"schema_version": "semantic-test/v0.1", "decision": "supported"},
        ],
    )

    with pytest.raises(StagedInferenceError) as caught:
        asyncio.run(run())

    assert caught.value.stop_reason == "output_invalid"
    assert model.calls == ledger.requests_started == ledger.requests_completed == 1
    assert len(responses) == len(calls) == 1


def test_question_update_schema_assigns_acquisition_only_to_open_questions() -> None:
    schema = _StageOutputSchema(QuerySelection).json_schema()
    branches = schema["properties"]["question_updates"]["items"]["anyOf"]

    assert len(branches) == 2
    assert all(branch["type"] == "object" for branch in branches)
    assert all(branch["additionalProperties"] is False for branch in branches)
    open_branch = next(branch for branch in branches
                       if branch["properties"]["state"].get("const") == "open")
    closed_branch = next(branch for branch in branches
                         if branch["properties"]["state"].get("enum") == ["resolved", "nonblocking"])
    assert open_branch["properties"]["acquisition"]["enum"] == ["available", "unavailable"]
    assert "acquisition" in open_branch["required"]
    assert "acquisition" not in closed_branch["properties"]
    assert "acquisition" not in closed_branch["required"]


@pytest.mark.parametrize("invalid", [
    {"schema_version": "staged-query/v0.2"},
    {"previous_receipt_assessment": {"attempt_sequence": 1, "impact": "supports",
                                     "rationale": "old host-owned field"}},
    {"query_attempt": {"question": "q", "scenario_id": "s", "tool": "unknown",
                       "arguments_json": "{}", "extra": None}},
])
def test_query_wire_strictly_rejects_legacy_or_unknown_fields(invalid) -> None:
    value = {
        "schema_version": "staged-query/v0.6", "decision": "finish_queries",
        "query_attempt": None, "previous_receipt_assessment": None,
        "impact": "inspect", "stop_explanation": None,
        "question_updates": [], "candidate_updates": [],
    }
    value.update(invalid)
    with pytest.raises(ValidationError):
        QuerySelection.model_validate(value)


@pytest.mark.parametrize(
    "output_type",
    [QuerySelection, ConstructSelection, RevisionSelection],
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
    schema = _StageOutputSchema(ReferencedContractSelection)
    contract = {
        "schema_version": "staged-contract-selection/v0.1",
        "test_objectives": [{
            "statement": "goal", "status": "required", "scenario_ids": ["s"],
            "condition": "always", "evidence_ids": ["e"], "ref": "goal-1",
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
    assert schema.validate_json(json.dumps(contract)).test_objectives

    contract["evidence"][0]["quote"] = "legacy"
    with pytest.raises(ModelBehaviorError):
        schema.validate_json(json.dumps(contract))
    contract["evidence"][0].pop("quote")
    contract["test_objectives"][0]["scenario_ids"] = "s"
    with pytest.raises(ModelBehaviorError):
        schema.validate_json(json.dumps(contract))
    contract["test_objectives"][0]["scenario_ids"] = ["s"]
