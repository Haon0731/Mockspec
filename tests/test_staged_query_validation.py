"""Keep QUERY repair qualification and normal dispatch on one pure validator."""

from __future__ import annotations

import asyncio
from copy import deepcopy
import json
from pathlib import Path
import sys
import time

import pytest

from mock_spec.schemas import UnifiedTask
from test_schemas import valid_task_data


sys.path.insert(0, str(Path(__file__).parents[1] / "experiments" / "staged-inference"))
from query import QueryExecutor, validate_query_request  # noqa: E402


def _request(**updates: object) -> dict[str, object]:
    request: dict[str, object] = {
        "question": "Which source line establishes the conditional behavior?",
        "scenario_id": "candidate",
        "tool": "inspect_source",
        "arguments_json": json.dumps({
            "source_id": "mut:0",
            "start_line": 1,
            "end_line": 1,
        }),
    }
    request.update(updates)
    return request


@pytest.mark.parametrize(
    "query_request,expected_code",
    [
        (None, "wrong_type"),
        ({"question": "missing fields"}, "invalid_request_fields"),
        (_request(question=" "), "missing_association"),
        (_request(scenario_id="other"), "unknown_candidate"),
        (_request(tool="write_source"), "unknown_tool"),
        (_request(arguments_json="{"), "malformed_json"),
        (
            _request(
                arguments_json=(
                    '{"source_id":"mut:0","source_id":"other",'
                    '"start_line":1,"end_line":1}'
                )
            ),
            "malformed_json",
        ),
        (_request(arguments_json="[]"), "wrong_type"),
        (_request(arguments_json=json.dumps({"source_id": "mut:0"})), "invalid_arguments"),
    ],
    ids=[
        "envelope-type",
        "envelope-fields",
        "blank-association",
        "candidate",
        "tool",
        "json",
        "duplicate-json-key",
        "argument-type",
        "argument-shape",
    ],
)
def test_pure_validator_and_dispatch_reject_with_the_same_code(
    query_request: object, expected_code: str
) -> None:
    candidate_ids = {"candidate"}
    request_before = deepcopy(query_request)
    candidates_before = set(candidate_ids)

    validation = validate_query_request(query_request, candidate_ids=candidate_ids)
    executor = _executor()
    record = executor.register(query_request)
    asyncio.run(executor.execute_registered(record, candidate_ids=candidate_ids))

    assert validation.valid is False
    assert validation.error_code == expected_code
    assert validation.diagnostic
    assert record["status"] == "invalid_request"
    assert record["result"]["error_code"] == expected_code
    assert query_request == request_before
    assert candidate_ids == candidates_before


def test_pure_validator_normalizes_exactly_the_arguments_used_by_dispatch() -> None:
    request = _request(arguments_json=json.dumps({
        "operation": "read",
        "source_id": "mut:0",
        "start_line": 1,
        "end_line": 1,
    }))
    validation = validate_query_request(request, candidate_ids={"candidate"})
    executor = _executor()
    dispatched: list[dict[str, object]] = []

    async def inspect_source(**arguments: object) -> dict[str, object]:
        dispatched.append(arguments)
        return {"status": "ok", "facts": []}

    executor.analysis.inspect_source = inspect_source  # type: ignore[method-assign]
    record = executor.register(request)
    asyncio.run(executor.execute_registered(record, candidate_ids={"candidate"}))

    assert validation.valid is True
    assert validation.tool == "inspect_source"
    assert validation.arguments == {
        "operation": "read",
        "source_id": "mut:0",
        "start_line": 1,
        "end_line": 1,
    }
    assert dispatched == [{
        **validation.arguments,
        "response_id": record["response_id"],
    }]
    assert record["status"] == "ok"


def test_pure_validator_has_no_executor_or_repository_side_effects() -> None:
    executor = _executor()
    before_attempts = deepcopy(executor.attempts)
    store = executor.analysis.store

    validation = validate_query_request(_request(), candidate_ids={"candidate"})

    assert validation.valid is True
    assert executor.attempts == before_attempts
    assert executor.analysis.store is store


def test_protocol_rejection_does_not_create_exploration_progress() -> None:
    executor = _executor()
    record = executor.register(_request())

    executor.reject(
        record,
        status="protocol_error",
        code="output_invalid",
        diagnostic="The model output violated the QUERY wire protocol.",
    )

    assert record["status"] == "protocol_error"
    assert record["exploration_progress"] is None
    assert executor._seen_operations == set()
    assert executor._seen_evidence == set()
    assert executor._consecutive_no_progress == 0


def test_protocol_rejection_preserves_over_limit_accounting_without_dispatch() -> None:
    executor = _executor(max_attempts=0)
    record = executor.register(_request())
    assert record["status"] == "budget_exhausted"

    executor.reject(
        record,
        status="protocol_error",
        code="output_invalid",
        diagnostic="The raw QUERY output violated the wire protocol.",
    )

    assert record["status"] == "protocol_error"
    assert record["within_budget"] is False
    assert record["result"]["error_code"] == "output_invalid"
    assert record["exploration_progress"] is None


def _executor(*, max_attempts: int | None = None) -> QueryExecutor:
    return QueryExecutor(
        UnifiedTask.from_dict(valid_task_data()),
        max_attempts=max_attempts,
        max_output_chars=16_000,
        analysis_timeout_seconds=2,
        deadline=time.monotonic() + 10,
    )
