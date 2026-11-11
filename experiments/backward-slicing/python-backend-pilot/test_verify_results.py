from __future__ import annotations

import csv
from pathlib import Path

from verify_results import (
    contains,
    evaluate_model_evidence,
    evaluate_state_boundary_evidence,
    load_rows,
)


def test_contains_requires_all_requested_columns(tmp_path: Path) -> None:
    path = tmp_path / "rows.csv"
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["relation", "modality", "functionName"])
        writer.writeheader()
        writer.writerow({
            "relation": "unknown_call_may_write_observed_object",
            "modality": "conservative_unknown",
            "functionName": "unknown_write",
        })

    rows = load_rows(path)
    assert contains(
        rows,
        relation="unknown_call_may_write_observed_object",
        modality="conservative_unknown",
        functionName="unknown_write",
    )
    assert not contains(rows, relation="unknown_call_may_write_observed_object", modality="certain")


def write_rows(path: Path, rows: list[dict[str, str]]) -> None:
    fieldnames = sorted({key for row in rows for key in row}) or ["relation"]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def test_candidate_labels_cannot_satisfy_propagation_gate(tmp_path: Path) -> None:
    write_rows(tmp_path / "probe-effects.csv", [{
        "relation": "unknown_call_may_write_observed_object",
        "modality": "conservative_unknown",
    }])

    result = evaluate_model_evidence(tmp_path)

    assert result["status"] == "unknown"
    assert not result["checks"]["unknown_effect_model_propagation_verified"]
    assert not result["checks"]["exception_path_state_propagation_verified"]


def test_complete_backend_paths_satisfy_propagation_gate(tmp_path: Path) -> None:
    def path(relation: str, case_kind: str, function: str, exit_kind: str,
             call: int, middle: int, observed: int, region: str = "box-region") -> dict[str, str]:
        return {
            "relation": relation, "caseKind": case_kind, "functionName": function,
            "pathId": f"{function}:{call}:{observed}", "fieldName": "ready",
            "regionKey": region, "exitKind": exit_kind, "callLine": str(call),
            "middleLine": str(middle), "observedLine": str(observed),
        }

    write_rows(tmp_path / "probe-unknown-effect-flow.csv", [
        path("unknown_may_write_to_read", "positive", "unknown_may_write", "state", 10, 10, 11),
        path("return_alias_write_to_argument_read", "positive", "returned_alias_write", "state", 20, 21, 22),
        path("trusted_fresh_argument_region_disjoint", "negative", "fresh_argument_does_not_reach", "excluded", 30, 30, 31, "fresh-1"),
        path("trusted_fresh_return_alias_channel_disjoint", "negative", "fresh_return_alias_does_not_reach", "excluded", 40, 41, 42, "fresh-2"),
        path("unknown_state_killed_by_definite_write", "kill", "definite_state_kill", "state", 50, 51, 52),
        path("execution_effect_after_state_kill", "normal", "definite_state_kill", "normal", 50, 51, 52),
        path("execution_effect_after_state_kill", "exception", "definite_state_kill", "exception", 50, 51, 52),
        path("conditional_write_not_a_kill", "negative", "conditional_write_is_not_a_kill", "state", 60, 62, 63),
        path("unknown_may_write_to_read", "positive", "conditional_write_is_not_a_kill", "state", 60, 60, 63),
        path(
            "unknown_may_write_to_read", "positive",
            "direct_argument_loop_prewrite_is_not_a_kill", "state", 75, 75, 80,
        ),
        path("unknown_state_killed_by_definite_write", "kill", "conditional_then_definite_kill", "state", 65, 68, 69),
        path("return_alias_other_field_excluded", "negative", "returned_alias_other_field", "state", 70, 71, 72),
    ])
    write_rows(tmp_path / "probe-unknown-exception-state.csv", [
        path("write_effect_exception_path_to_read", "handler", "caught_and_finally", "exception", 80, 81, 82),
        path("write_effect_exception_path_to_read", "finally", "caught_and_finally", "exception", 80, 83, 84),
    ])

    result = evaluate_model_evidence(tmp_path)

    assert result["status"] == "passed"
    assert all(result["checks"].values())


def test_correct_labels_without_path_identity_are_blocked(tmp_path: Path) -> None:
    write_rows(tmp_path / "probe-unknown-effect-flow.csv", [
        {"relation": "unknown_may_write_to_read", "caseKind": "positive"},
    ])
    write_rows(tmp_path / "probe-unknown-exception-state.csv", [
        {"relation": "write_effect_exception_path_to_read", "caseKind": "handler"},
    ])
    assert evaluate_model_evidence(tmp_path)["status"] == "blocked"


def test_rows_from_different_calls_cannot_form_a_path_pair(tmp_path: Path) -> None:
    test_complete_backend_paths_satisfy_propagation_gate(tmp_path)
    rows = load_rows(tmp_path / "probe-unknown-effect-flow.csv")
    for row in rows:
        if row["relation"] == "execution_effect_after_state_kill" and row["caseKind"] == "exception":
            row["callLine"] = "999"
            row["pathId"] = "definite_state_kill:999:52"
    write_rows(tmp_path / "probe-unknown-effect-flow.csv", rows)
    assert evaluate_model_evidence(tmp_path)["status"] == "blocked"


def test_state_boundary_paths_require_exact_regions_and_reject_negative_functions(
    tmp_path: Path,
) -> None:
    def path(relation: str, function: str, scope: str, field: str, call: int, region: int,
             observed: int) -> dict[str, str]:
        return {
            "relation": relation, "caseKind": "positive", "scopeKind": scope,
            "functionName": function, "pathId": f"{function}:{call}:{observed}",
            "fieldName": field, "regionKey": f"{scope}-region",
            "callLine": str(call), "regionLine": str(region),
            "observedLine": str(observed),
        }

    write_rows(tmp_path / "probe-state-boundary-flow.csv", [
        path("unknown_receiver_write_to_read", "receiver_may_write", "receiver",
             "configured", 10, 10, 11),
        path("unknown_receiver_write_to_read", "receiver_loop_prewrite_is_not_a_kill",
             "receiver", "configured", 15, 14, 16),
        path("unknown_global_write_to_read", "global_may_write", "module_global",
             "active", 20, 21, 21),
        path(
            "unknown_escaped_argument_write_to_read", "escaped_argument_may_write",
            "escaped_argument", "sealed", 31, 30, 32,
        ),
        {
            **path(
                "global_rebind_boundary_unknown", "global_rebind_boundary",
                "module_global_rebind", "active", 40, 42, 43,
            ),
            "caseKind": "unknown",
        },
        {
            **path(
                "parameter_alias_boundary_unknown", "receiver_other_object",
                "parameter_alias", "configured", 50, 50, 51,
            ),
            "caseKind": "unknown",
        },
        {
            **path(
                "parameter_alias_boundary_unknown", "escaped_other_object",
                "parameter_alias", "sealed", 60, 60, 62,
            ),
            "caseKind": "unknown",
        },
    ])

    result = evaluate_state_boundary_evidence(tmp_path)

    assert result["status"] == "passed"
    assert all(result["checks"].values())


def test_state_boundary_labels_without_path_identity_are_blocked(tmp_path: Path) -> None:
    write_rows(tmp_path / "probe-state-boundary-flow.csv", [{
        "relation": "unknown_receiver_write_to_read",
        "caseKind": "positive",
        "scopeKind": "receiver",
        "functionName": "receiver_may_write",
    }])
    assert evaluate_state_boundary_evidence(tmp_path)["status"] == "blocked"


def test_state_boundary_negative_function_row_fails_closed(tmp_path: Path) -> None:
    test_state_boundary_paths_require_exact_regions_and_reject_negative_functions(tmp_path)
    rows = load_rows(tmp_path / "probe-state-boundary-flow.csv")
    negative = dict(rows[0])
    negative["functionName"] = "receiver_fresh_other"
    negative["pathId"] = (
        f"receiver_fresh_other:{negative['callLine']}:{negative['observedLine']}"
    )
    write_rows(tmp_path / "probe-state-boundary-flow.csv", [*rows, negative])
    assert evaluate_state_boundary_evidence(tmp_path)["status"] == "blocked"


def test_state_boundary_unknown_label_without_path_identity_fails_closed(
    tmp_path: Path,
) -> None:
    test_state_boundary_paths_require_exact_regions_and_reject_negative_functions(tmp_path)
    rows = load_rows(tmp_path / "probe-state-boundary-flow.csv")
    for row in rows:
        if row["relation"] == "parameter_alias_boundary_unknown":
            row["pathId"] = ""
            break
    write_rows(tmp_path / "probe-state-boundary-flow.csv", rows)
    assert evaluate_state_boundary_evidence(tmp_path)["status"] == "blocked"


def test_state_boundary_unknown_path_rejects_out_of_order_region(tmp_path: Path) -> None:
    test_state_boundary_paths_require_exact_regions_and_reject_negative_functions(tmp_path)
    rows = load_rows(tmp_path / "probe-state-boundary-flow.csv")
    for row in rows:
        if row["relation"] == "global_rebind_boundary_unknown":
            row["regionLine"] = "999"
            break
    write_rows(tmp_path / "probe-state-boundary-flow.csv", rows)
    assert evaluate_state_boundary_evidence(tmp_path)["status"] == "blocked"
