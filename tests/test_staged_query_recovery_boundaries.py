"""Lock the narrow QUERY recovery boundary before any provider retry."""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import sys

import pytest


sys.path.insert(0, str(Path(__file__).parents[1] / "experiments" / "staged-inference"))
from controller import _completed_response_json, _query_repair_snapshot  # noqa: E402


def _query_with_extra(*, extra_name: str = "attempt_sequence_note", value=None) -> dict:
    return {
        "schema_version": "staged-query/v0.4",
        "decision": "request_query",
        "query_attempt": {
            "question": "What behavior constrains this candidate?",
            "scenario_id": "candidate",
            "tool": "inspect_source",
            "arguments_json": '{ "source_id": "mut:0", "start_line": 1, "end_line": 1 }',
        },
        "previous_receipt_assessment": {
            "impact": "no_new_information",
            "rationale": "The declaration does not establish runtime behavior.",
            extra_name: value,
        },
        "impact": "A behavior read remains necessary.",
        "stop_explanation": None,
        "question_updates": [],
        "candidate_updates": [],
    }


def _response(text: str) -> dict:
    return {
        "output": [
            {"type": "reasoning", "summary": []},
            {
                "type": "message",
                "status": "completed",
                "content": [{"type": "output_text", "text": text}],
            },
        ]
    }


def test_completed_response_accepts_one_completed_text_plus_reasoning() -> None:
    raw = _query_with_extra()

    assert _completed_response_json(_response(json.dumps(raw))) == raw


@pytest.mark.parametrize(
    "mutation",
    [
        "missing_status",
        "incomplete_status",
        "refusal",
        "multiple_texts",
        "multiple_messages",
        "tool_output",
        "mixed_content",
        "missing_output",
    ],
)
def test_completed_response_rejects_ambiguous_or_incomplete_output(mutation: str) -> None:
    response = _response(json.dumps(_query_with_extra()))
    message = response["output"][1]
    if mutation == "missing_status":
        del message["status"]
    elif mutation == "incomplete_status":
        message["status"] = "in_progress"
    elif mutation == "refusal":
        message["content"] = [{"type": "refusal", "refusal": "no"}]
    elif mutation == "multiple_texts":
        message["content"].append(deepcopy(message["content"][0]))
    elif mutation == "multiple_messages":
        response["output"].append(deepcopy(message))
    elif mutation == "tool_output":
        response["output"].append({"type": "function_call", "name": "inspect_source"})
    elif mutation == "mixed_content":
        message["content"].append({"type": "refusal", "refusal": "no"})
    elif mutation == "missing_output":
        del response["output"]

    with pytest.raises(ValueError):
        _completed_response_json(response)


def test_completed_response_rejects_duplicate_json_keys() -> None:
    response = _response(
        '{"schema_version":"staged-query/v0.4",'
        '"schema_version":"staged-query/v0.4"}'
    )

    with pytest.raises(ValueError):
        _completed_response_json(response)


def test_snapshot_removes_only_unknown_null_assessment_children() -> None:
    raw = _query_with_extra()
    raw["previous_receipt_assessment"]["provider_note"] = None

    wire, snapshot, locations = _query_repair_snapshot(raw)

    assert wire.model_dump(mode="json") == snapshot
    assert snapshot["query_attempt"]["arguments_json"] == (
        raw["query_attempt"]["arguments_json"]
    )
    assert snapshot["previous_receipt_assessment"] == {
        "impact": "no_new_information",
        "rationale": "The declaration does not establish runtime behavior.",
    }
    assert locations == [
        ["previous_receipt_assessment", "attempt_sequence_note"],
        ["previous_receipt_assessment", "provider_note"],
    ]


@pytest.mark.parametrize(
    "mutation",
    [
        "nonnull_extra",
        "host_sequence",
        "top_level_extra",
        "old_version",
        "missing_top_level",
        "missing_known_assessment",
        "wrong_known_assessment_type",
        "nested_query_extra",
        "nested_stop_error",
        "assessment_null",
    ],
)
def test_snapshot_rejects_every_error_outside_null_assessment_extras(
    mutation: str,
) -> None:
    raw = _query_with_extra()
    assessment = raw["previous_receipt_assessment"]
    if mutation == "nonnull_extra":
        assessment["attempt_sequence_note"] = "host 1"
    elif mutation == "host_sequence":
        del assessment["attempt_sequence_note"]
        assessment["attempt_sequence"] = None
    elif mutation == "top_level_extra":
        raw["provider_note"] = None
    elif mutation == "old_version":
        raw["schema_version"] = "staged-query/v0.2"
    elif mutation == "missing_top_level":
        del raw["stop_explanation"]
    elif mutation == "missing_known_assessment":
        del assessment["rationale"]
    elif mutation == "wrong_known_assessment_type":
        assessment["impact"] = None
    elif mutation == "nested_query_extra":
        raw["query_attempt"]["provider_note"] = None
    elif mutation == "nested_stop_error":
        raw["decision"] = "finish_queries"
        raw["query_attempt"] = None
        raw["stop_explanation"] = {
            "reason": "evidence_sufficient",
            "rationale": "done",
            "evidence": [{"source_id": "mut:0", "start_line": 1.0, "end_line": 1}],
            "remaining_unknowns": [],
        }
    elif mutation == "assessment_null":
        raw["previous_receipt_assessment"] = None

    with pytest.raises(ValueError):
        _query_repair_snapshot(raw)
