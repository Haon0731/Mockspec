"""Exercise controller-owned protective termination for repository query loops."""

from __future__ import annotations

import json

import pytest

from test_staged_repository_exploration import _repository_run


def _repeat_ranges(outputs: list[dict], lines: tuple[int, ...]) -> None:
    for index, output in enumerate(outputs[1:8]):
        line = lines[index % len(lines)]
        output["query_attempt"]["arguments_json"] = json.dumps({
            "source_id": "src/example.py",
            "start_line": line,
            "end_line": line,
        })


@pytest.mark.parametrize(
    ("lines", "expected_attempts"),
    [
        ((1,), 5),
        ((1, 2), 6),
    ],
    ids=["same-read", "alternating-reads"],
)
def test_controller_stops_sustained_repeated_exploration_and_preserves_trace(
    tmp_path, lines: tuple[int, ...], expected_attempts: int,
) -> None:
    result = _repository_run(
        tmp_path,
        transform=lambda outputs: _repeat_ranges(outputs, lines),
    )

    record = result["record"]
    manifest = result["manifest"]
    assert result["contract"] is None
    assert record["result_status"] == "INCONCLUSIVE"
    assert record["stop_reason"] == "repeated_exploration"
    assert record["error_stage"] == "QUERY"
    assert record["termination"] == {
        "initiator": "controller",
        "reason": "repeated_exploration",
        "evidence_sufficiency_claimed": False,
    }
    assert manifest["stop_reason"] == "repeated_exploration"

    receipts = record["query_log"]
    assert len(receipts) == expected_attempts
    assert all(receipt["result"] for receipt in receipts)
    assert all(
        receipt["status"] == "ok" for receipt in receipts[:-1]
    )
    assert receipts[-1]["status"] == "repeated_exploration"
    assert receipts[-1]["backend_status"] == "ok"
    assert receipts[-1]["semantic_category"] == "repeated_exploration"
    assert receipts[-1]["exploration_progress"]["triggered"] is True
    assert receipts[-1]["exploration_progress"]["termination_initiator"] == "controller"
    assert len(record["source_observations"]) == expected_attempts
