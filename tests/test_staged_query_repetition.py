"""Protect staged repository exploration from sustained no-progress query loops."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
import sys
import time

import pytest

from mock_spec.schemas import UnifiedTask
from test_schemas import valid_task_data


sys.path.insert(0, str(Path(__file__).parents[1] / "experiments" / "staged-inference"))
from query import QueryExecutor  # noqa: E402


def _request(*, question: str, start_line: int = 1, cursor: str | None = None) -> dict:
    arguments: dict[str, object]
    if cursor is None:
        arguments = {
            "operation": "read",
            "source_id": "mut:0",
            "start_line": start_line,
            "end_line": start_line,
        }
    else:
        arguments = {
            "operation": "search",
            "text": "dependency",
            "cursor": cursor,
        }
    return {
        "question": question,
        "scenario_id": "candidate",
        "tool": "inspect_source",
        "arguments_json": json.dumps(arguments),
    }


def _executor(*, threshold: int) -> QueryExecutor:
    executor = QueryExecutor(
        UnifiedTask.from_dict(valid_task_data()),
        max_attempts=None,
        max_output_chars=16_000,
        analysis_timeout_seconds=2,
        deadline=time.monotonic() + 10,
        max_no_progress_attempts=threshold,
    )
    calls = 0

    async def fixed_result(**arguments: object) -> dict:
        nonlocal calls
        calls += 1
        return {
            "status": "ok",
            "backend": "fixture",
            "source_id": "mut:0",
            "facts": [{"kind": "line", "value": "same evidence"}],
            "next_cursor": f"opaque-{calls}",
            "usage": {"requested": arguments},
        }

    executor.analysis.inspect_source = fixed_result  # type: ignore[method-assign]
    return executor


async def _execute(executor: QueryExecutor, requests: list[dict]) -> list[dict]:
    records = []
    for request in requests:
        record = executor.register(request)
        records.append(
            await executor.execute_registered(record, candidate_ids={"candidate"})
        )
    return records


def test_same_query_stops_only_after_configured_sustained_no_progress() -> None:
    executor = _executor(threshold=2)
    request = _request(question="What result does the dependency require?")

    records = asyncio.run(_execute(executor, [request, request, request]))

    assert [record["status"] for record in records] == [
        "ok", "ok", "repeated_exploration",
    ]
    assert records[-1]["backend_status"] == "ok"
    assert records[-1]["result"]["facts"]
    assert records[-1]["exploration_progress"] == {
        "made_progress": False,
        "new_operation_or_position": False,
        "new_evidence": False,
        "new_question_review": False,
        "operation_digest": records[0]["exploration_progress"]["operation_digest"],
        "evidence_digest": records[0]["exploration_progress"]["evidence_digest"],
        "consecutive_no_progress": 2,
        "threshold": 2,
        "triggered": True,
        "termination_initiator": "controller",
        "termination_reason": "repeated_exploration",
    }
    assert executor.repeated_exploration_triggered is True


def test_alternating_queries_with_repeated_results_are_also_stopped() -> None:
    executor = _executor(threshold=2)
    first = _request(question="Check the first path", start_line=1)
    second = _request(question="Check the second path", start_line=2)

    records = asyncio.run(_execute(executor, [first, second, first, second]))

    assert [record["status"] for record in records] == [
        "ok", "ok", "ok", "repeated_exploration",
    ]
    assert records[2]["exploration_progress"]["consecutive_no_progress"] == 1
    assert records[3]["exploration_progress"]["consecutive_no_progress"] == 2


def test_repeated_invalid_requests_are_stopped_before_they_can_loop_forever() -> None:
    executor = _executor(threshold=2)
    request = _request(question="Try an unavailable tool")
    request["tool"] = "unknown_tool"

    records = asyncio.run(_execute(executor, [request, request, request]))

    assert [record["status"] for record in records] == [
        "invalid_request", "invalid_request", "repeated_exploration",
    ]
    assert records[-1]["backend_status"] == "invalid_request"
    assert records[-1]["result"]["error_code"] == "unknown_tool"


def test_question_paraphrases_allow_one_review_but_cannot_evade_the_guard() -> None:
    executor = _executor(threshold=2)
    requests = [
        _request(question=question)
        for question in ("Initial check", "Review alternate", "Reword three", "Reword four")
    ]

    records = asyncio.run(_execute(executor, requests))

    assert [record["status"] for record in records] == [
        "ok", "ok", "ok", "repeated_exploration",
    ]
    assert records[1]["exploration_progress"]["new_question_review"] is True
    assert records[2]["exploration_progress"]["new_question_review"] is False


def test_new_positions_pagination_and_explicit_review_are_progress() -> None:
    executor = _executor(threshold=2)
    requests = [
        _request(question="Inspect additional context", start_line=line)
        for line in range(1, 8)
    ]
    requests.extend([
        _request(question="Follow the next page", cursor="page-one"),
        _request(question="Follow the next page", cursor="page-two"),
        _request(question="Recheck this evidence for the alternate scenario", start_line=1),
    ])

    records = asyncio.run(_execute(executor, requests))

    assert len(records) == 10
    assert all(record["status"] == "ok" for record in records)
    assert all(
        record["exploration_progress"]["made_progress"] for record in records
    )
    assert executor.repeated_exploration_triggered is False


@pytest.mark.parametrize(
    ("status", "result", "category"),
    [
        ("error", {"error_code": "analysis_error", "failure_kind": "parse_error"}, "unparseable"),
        ("error", {"error_code": "analysis_error", "failure_kind": "input_limit"}, "scope_limit"),
        ("error", {"error_code": "analysis_error", "failure_kind": "backend_failure"}, "backend_error"),
        ("incomplete", {"diagnostics": [{"category": "unsupported"}]}, "unresolved"),
        ("error", {"error_code": "source_changed"}, "content_changed"),
        ("error", {"error_code": "identity_mismatch"}, "identity_mismatch"),
        ("error", {"error_code": "path_violation"}, "path_violation"),
        ("incomplete", {"limit_reason": "entry_limit"}, "scope_limit"),
        ("incomplete", {"error_code": "output_limit"}, "output_limit"),
        ("error", {"error_code": "unsupported"}, "unparseable"),
        ("unresolved", {}, "unresolved"),
        ("backend_error", {"error_code": "malformed_result"}, "analysis_error"),
        ("budget_exhausted", {"error_code": "total_token_limit"}, "budget_exhausted"),
        ("timeout", {"error_code": "run_deadline_exhausted"}, "run_timeout"),
    ],
)
def test_receipt_semantic_category_maps_parent_error_classes_without_rewriting_result(
    status: str, result: dict, category: str,
) -> None:
    record: dict = {}

    _executor(threshold=2)._finish(record, time.monotonic(), status, result)

    assert record["status"] == status
    assert record["semantic_category"] == category
    assert record["result"] is result
