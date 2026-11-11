from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path

import pytest

from state_observation_mapping import (
    HERE,
    StateMappingEvidenceError,
    build_state_study,
    has_unique_call_at,
)


EXPECTATIONS = HERE / "state_observation_expectations.json"


def write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    fields = sorted({key for row in rows for key in row})
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_source_call_identity_rejects_two_calls_on_one_line(tmp_path: Path) -> None:
    source = tmp_path / "probe.py"
    source.write_text("def target(dep):\n    dep.first(); dep.second()\n")
    assert not has_unique_call_at(source, "target", 2)


def prepare(tmp_path: Path) -> None:
    expectations = json.loads(EXPECTATIONS.read_text())
    source = HERE / "mechanism_probe.py"
    mapper = HERE / "state_observation_mapping.py"
    query = HERE / "state-boundary-flow.ql"
    visible_query = HERE / "visible-state-flow.ql"
    (tmp_path / "state-pre-registration.json").write_text(json.dumps({
        "schema": "mock-spec.python-state-observation-pre-registration/v1",
        "expectations_sha256": sha256(EXPECTATIONS),
        "source_sha256": sha256(source),
        "query_sha256": sha256(query),
        "visible_query_sha256": sha256(visible_query),
        "mapper_sha256": sha256(mapper),
    }))
    checks = {
        "receiver_effect_reaches_receiver_read": True,
        "receiver_loop_prewrite_not_false_kill": True,
        "global_effect_reaches_global_read": True,
        "escaped_argument_effect_reaches_same_argument_read": True,
        "receiver_fresh_object_excluded": True,
        "receiver_parameter_alias_recorded_unknown": True,
        "receiver_overwrite_kills_unknown_effect": True,
        "local_object_not_promoted_to_global": True,
        "global_overwrite_kills_unknown_effect": True,
        "global_rebind_recorded_as_unknown": True,
        "global_rebind_not_falsely_linked": True,
        "escaped_fresh_object_excluded": True,
        "escaped_parameter_alias_recorded_unknown": True,
        "escaped_overwrite_kills_unknown_effect": True,
        "visible_pure_call_has_no_unknown_effect": True,
        "negative_fixtures_present": True,
        "unknown_fixtures_present": True,
    }
    (tmp_path / "verdict.json").write_text(json.dumps({
        "state_boundary_evidence": {"status": "passed", "checks": checks},
    }))
    by_function = {row["function"]: row for row in expectations["observations"]}
    write_csv(tmp_path / "probe-visible-state-flow.csv", [{
        "relation": "visible_write_to_read", "functionName": "visible_write",
        "fieldName": "ready", "writeLine": "59",
        "observedLine": str(by_function["visible_write"]["line"]),
    }])
    write_csv(tmp_path / "probe-unknown-effect-flow.csv", [{
        "relation": "unknown_may_write_to_read", "functionName": "unknown_may_write",
        "fieldName": "ready", "callLine": "80",
        "observedLine": str(by_function["unknown_may_write"]["line"]),
    }, {
        "relation": "return_alias_write_to_argument_read",
        "functionName": "returned_alias_write", "fieldName": "ready", "callLine": "85",
        "observedLine": str(by_function["returned_alias_write"]["line"]),
    }])
    write_csv(tmp_path / "probe-state-boundary-flow.csv", [{
        "relation": "unknown_receiver_write_to_read", "functionName": "receiver_may_write",
        "fieldName": "configured", "callLine": "131",
        "observedLine": str(by_function["receiver_may_write"]["line"]),
    }, {
        "relation": "unknown_receiver_write_to_read",
        "functionName": "receiver_loop_prewrite_is_not_a_kill",
        "fieldName": "configured", "callLine": "149",
        "observedLine": str(by_function["receiver_loop_prewrite_is_not_a_kill"]["line"]),
    }, {
        "relation": "unknown_global_write_to_read", "functionName": "global_may_write",
        "fieldName": "active", "callLine": "147",
        "observedLine": str(by_function["global_may_write"]["line"]),
    }, {
        "relation": "unknown_escaped_argument_write_to_read",
        "functionName": "escaped_argument_may_write", "fieldName": "sealed", "callLine": "165",
        "observedLine": str(by_function["escaped_argument_may_write"]["line"]),
    }, {
        "relation": "global_rebind_boundary_unknown", "caseKind": "unknown",
        "scopeKind": "module_global_rebind",
        "functionName": "global_rebind_boundary", "fieldName": "active",
        "pathId": "global_rebind_boundary:204:206", "regionKey": "global-region",
        "callLine": "204", "regionLine": "206", "observedLine": "206",
    }, {
        "relation": "parameter_alias_boundary_unknown", "caseKind": "unknown",
        "scopeKind": "parameter_alias", "functionName": "receiver_other_object",
        "fieldName": "configured", "pathId": "receiver_other_object:136:137",
        "regionKey": "receiver-other-region", "callLine": "136", "regionLine": "137",
        "observedLine": "137",
    }, {
        "relation": "parameter_alias_boundary_unknown", "caseKind": "unknown",
        "scopeKind": "parameter_alias", "functionName": "escaped_other_object",
        "fieldName": "sealed", "pathId": "escaped_other_object:182:183",
        "regionKey": "escaped-other-region", "callLine": "182", "regionLine": "183",
        "observedLine": "183",
    }])
    write_csv(tmp_path / "probe-unknown-exception-state.csv", [{
        "relation": "write_effect_exception_path_to_read", "functionName": "caught_and_finally",
        "caseKind": "handler", "fieldName": "ready", "callLine": "48",
        "observedLine": str(next(row["line"] for row in expectations["observations"]
                                 if row["scope"] == "exception_handler")),
    }, {
        "relation": "write_effect_exception_path_to_read", "functionName": "caught_and_finally",
        "caseKind": "finally", "fieldName": "ready", "callLine": "48",
        "observedLine": str(next(row["line"] for row in expectations["observations"]
                                 if row["scope"] == "finally")),
    }])
    (tmp_path / "probe-db.time").write_text(
        "\tElapsed (wall clock) time (h:mm:ss or m:ss): 0:01.00\n"
        "\tMaximum resident set size (kbytes): 100\n"
    )


def test_builds_shared_contract_with_every_registered_state_observation(tmp_path: Path) -> None:
    prepare(tmp_path)
    result = build_state_study(tmp_path, EXPECTATIONS)

    case = result["case_result"]
    assert case["schema_version"] == "mock-spec.backward-analysis-case/v1"
    assert len(case["observations"]) == 9
    assert len(case["relations"]) == 9
    assert {item for relation in case["relations"] for item in relation["observation_ids"]} == {
        observation["observation_id"] for observation in case["observations"]
    }
    assert case["semantic_validation"]["status"] == "verified_incomplete"
    assert {item["scope"] for item in case["analysis_boundaries"]["unanalysed_regions"]} >= {
        "module_global_rebind", "parameter_alias",
    }
    assert result["unknown_cases"][0]["function"] == "global_rebind_boundary"
    boundary_metadata_by_scope = {
        item["scope"]: (item["effect"], item["provenance"], item["precision"])
        for item in case["analysis_boundaries"]["unknown_effects"]
        if item["scope"] in {"module_global_rebind", "parameter_alias"}
    }
    assert boundary_metadata_by_scope == {
        "module_global_rebind": (
            "unresolved_effect_after_global_rebind",
            "backend global-binding boundary",
            "post-rebind identity unresolved",
        ),
        "parameter_alias": (
            "unresolved_alias_relation",
            "backend parameter-alias boundary",
            "runtime parameter alias unresolved",
        ),
    }
    assert result["negative_functions"] == sorted(
        row["function"] for row in result["pre_registered_expectations"]["exclude"]
    )


def test_multiple_exception_paths_from_one_call_map_to_one_observation(tmp_path: Path) -> None:
    prepare(tmp_path)
    path = tmp_path / "probe-unknown-exception-state.csv"
    rows = list(csv.DictReader(path.open(newline="", encoding="utf-8")))
    finally_row = next(row for row in rows if row["caseKind"] == "finally")
    rows.append({**finally_row, "middleLine": "52"})
    write_csv(path, rows)

    result = build_state_study(tmp_path, EXPECTATIONS)

    finally_relations = [
        relation for relation in result["case_result"]["relations"]
        if relation["observation_ids"] == ["state:finally"]
    ]
    assert len(finally_relations) == 1


def test_corrupt_extra_exception_path_fails_closed(tmp_path: Path) -> None:
    prepare(tmp_path)
    path = tmp_path / "probe-unknown-exception-state.csv"
    rows = list(csv.DictReader(path.open(newline="", encoding="utf-8")))
    finally_row = next(row for row in rows if row["caseKind"] == "finally")
    rows.append({**finally_row, "callLine": "invalid", "middleLine": "52"})
    write_csv(path, rows)

    with pytest.raises(StateMappingEvidenceError, match="finally"):
        build_state_study(tmp_path, EXPECTATIONS)


def test_missing_registered_state_path_fails_closed(tmp_path: Path) -> None:
    prepare(tmp_path)
    path = tmp_path / "probe-state-boundary-flow.csv"
    rows = [row for row in csv.DictReader(path.open(newline="", encoding="utf-8"))
            if row["functionName"] != "global_may_write"]
    write_csv(path, rows)
    with pytest.raises(StateMappingEvidenceError, match="global_may_write"):
        build_state_study(tmp_path, EXPECTATIONS)


def test_negative_state_path_fails_closed(tmp_path: Path) -> None:
    prepare(tmp_path)
    path = tmp_path / "probe-state-boundary-flow.csv"
    rows = list(csv.DictReader(path.open(newline="", encoding="utf-8")))
    rows.append({
        "relation": "unknown_receiver_write_to_read", "functionName": "receiver_fresh_other",
        "callLine": "212", "observedLine": "214",
    })
    write_csv(path, rows)
    with pytest.raises(StateMappingEvidenceError, match="negative state cases"):
        build_state_study(tmp_path, EXPECTATIONS)


def test_unknown_state_label_without_path_identity_fails_closed(tmp_path: Path) -> None:
    prepare(tmp_path)
    path = tmp_path / "probe-state-boundary-flow.csv"
    rows = list(csv.DictReader(path.open(newline="", encoding="utf-8")))
    for row in rows:
        if row["relation"] == "global_rebind_boundary_unknown":
            row["pathId"] = ""
    write_csv(path, rows)
    with pytest.raises(StateMappingEvidenceError, match="global_rebind_boundary"):
        build_state_study(tmp_path, EXPECTATIONS)


def test_unknown_state_path_rejects_out_of_order_region(tmp_path: Path) -> None:
    prepare(tmp_path)
    path = tmp_path / "probe-state-boundary-flow.csv"
    rows = list(csv.DictReader(path.open(newline="", encoding="utf-8")))
    for row in rows:
        if row["relation"] == "global_rebind_boundary_unknown":
            row["regionLine"] = "999"
    write_csv(path, rows)
    with pytest.raises(StateMappingEvidenceError, match="global_rebind_boundary"):
        build_state_study(tmp_path, EXPECTATIONS)


def test_changed_pre_registration_fails_closed(tmp_path: Path) -> None:
    prepare(tmp_path)
    prereg = json.loads((tmp_path / "state-pre-registration.json").read_text())
    prereg["query_sha256"] = "0" * 64
    (tmp_path / "state-pre-registration.json").write_text(json.dumps(prereg))
    with pytest.raises(StateMappingEvidenceError, match="pre-registration"):
        build_state_study(tmp_path, EXPECTATIONS)
