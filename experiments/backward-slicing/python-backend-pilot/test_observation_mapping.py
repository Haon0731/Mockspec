from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from observation_mapping import MappingEvidenceError, build_study_result, parse_time_files


def write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    fields = sorted({key for row in rows for key in row})
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def test_pilot_verdict_without_per_observation_backend_rows_is_rejected(tmp_path: Path) -> None:
    (tmp_path / "verdict.json").write_text(json.dumps({"gate_passed": True}), encoding="utf-8")
    with pytest.raises(MappingEvidenceError, match="evidence"):
        build_study_result(tmp_path, Path(__file__).with_name("observation_expectations.json"))


def test_only_backend_connected_relations_receive_observation_ids(tmp_path: Path) -> None:
    manifest = {
        "case_id": "flask_308", "dataset": "python-benchmark-new",
        "snapshot_id": "723639bebf43aa4f8a5c007ee21c31b0647d61488945905921fb2351be69b78c",
        "masking_contract": "python-slot-v1", "target_selector": "tests/test_308abort.py::test_abort_scenarios",
        "target_extraction": {
            "target_scope": {"status": "located", "locations": [{"symbol": "test_abort_scenarios", "start_line": 48, "end_line": 126}]},
            "criteria": [
                {"line": 102, "kind": "exception_observation", "reason": "raises"},
                {"line": 120, "kind": "value_assertion", "reason": "assert"},
                {"line": 126, "kind": "value_assertion", "reason": "assert"},
            ],
        },
        "files": [
            {"logical_path": "tests/test_308abort.py", "source_id": "test", "sha256": "a" * 64, "bytes": 10},
            {"logical_path": "src/src/flask/helpers.py", "source_id": "source:helper", "sha256": "b" * 64, "bytes": 20},
        ],
    }
    (tmp_path / "input-manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    expectations = Path(__file__).with_name("observation_expectations.json")
    pytest_model = Path(__file__).with_name("pytest_model_assumptions.json")
    (tmp_path / "pre-registration.json").write_text(json.dumps({
        "schema": "mock-spec.python-observation-pre-registration/v1",
        "expectations_sha256": __import__("hashlib").sha256(expectations.read_bytes()).hexdigest(),
        "pytest_model_sha256": __import__("hashlib").sha256(pytest_model.read_bytes()).hexdigest(),
        "input_manifest_sha256": __import__("hashlib").sha256((tmp_path / "input-manifest.json").read_bytes()).hexdigest(),
    }), encoding="utf-8")
    (tmp_path / "pytest-model-source-receipt.json").write_text(json.dumps({
        "schema": "mock-spec.pytest-model-source-receipt/v1",
        "status": "passed",
        "source_role": "host_verified_reference",
        "frozen_case_dependency_version": "unknown",
        "assumptions_sha256": __import__("hashlib").sha256(pytest_model.read_bytes()).hexdigest(),
        "actual_pytest_version": "9.1.1",
        "actual_sources": [],
        "failures": [],
    }), encoding="utf-8")
    write_csv(tmp_path / "flask-flask-observation-flow.csv", [
        {"relation": "observation", "sourcePath": "tests/test_308abort.py", "sourceLine": "102", "branch": "none", "guardLine": "0"},
        {"relation": "observation", "sourcePath": "tests/test_308abort.py", "sourceLine": "120", "branch": "true", "guardLine": "111"},
        {"relation": "observation", "sourcePath": "tests/test_308abort.py", "sourceLine": "126", "branch": "false", "guardLine": "111"},
        {"relation": "resolved_test_product_call", "sourcePath": "tests/test_308abort.py", "sourceLine": "103", "targetPath": "src/src/flask/helpers.py", "targetLine": "265", "targetEndLine": "285"},
        {"relation": "actual_to_formal", "sourcePath": "tests/test_308abort.py", "sourceLine": "103", "targetPath": "src/src/flask/helpers.py", "targetLine": "265", "targetEndLine": "285", "argumentIndex": "0"},
    ])
    write_csv(tmp_path / "flask-flask-abort.csv", [
        {"relation": "aborter_normal_path_reaches_fallback", "col4": "src/src/flask/helpers.py", "col5": "283", "col6": "src/src/flask/helpers.py", "col7": "285"},
    ])
    write_csv(tmp_path / "flask-exceptions.csv", [
        {"relation": "exceptional_exit", "label": "aborter", "col3": "src/src/flask/helpers.py", "col4": "283"},
        {"relation": "exceptional_exit", "label": "_wz_abort", "col3": "src/src/flask/helpers.py", "col4": "285"},
    ])
    write_csv(tmp_path / "flask-pytest-exception-flow.csv", [
        {"relation": "captured_exception_may_reach_membership_observation", "sourcePath": "src/src/flask/helpers.py", "sourceLine": "283", "calleePath": "src/src/flask/helpers.py", "calleeLine": "265", "testCallPath": "tests/test_308abort.py", "testCallLine": "103", "withLine": "102", "observationLine": "120", "captureLine": "102", "typeCompatibility": "unknown_may_match", "suppressedContinuationLine": "107"},
        {"relation": "captured_exception_may_reach_membership_observation", "sourcePath": "src/src/flask/helpers.py", "sourceLine": "285", "calleePath": "src/src/flask/helpers.py", "calleeLine": "265", "testCallPath": "tests/test_308abort.py", "testCallLine": "103", "withLine": "102", "observationLine": "126", "captureLine": "102", "typeCompatibility": "unknown_may_match", "suppressedContinuationLine": "107"},
    ])
    write_csv(tmp_path / "probe-unknown-effect-flow.csv", [
        {"relation": "unknown_may_write_to_read", "caseKind": "positive"},
        {"relation": "trusted_fresh_argument_region_disjoint", "caseKind": "negative"},
    ])
    write_csv(tmp_path / "probe-unknown-exception-state.csv", [
        {"relation": "write_effect_exception_path_to_read", "caseKind": "handler"},
    ])
    write_csv(tmp_path / "probe-pytest-exception-flow.csv", [
        {"sourceLine": "10", "testCallLine": "19", "withLine": "18", "observationLine": "22", "typeCompatibility": "known_subtype_match", "suppressedContinuationLine": "20"},
        {"sourceLine": "10", "testCallLine": "34", "withLine": "33", "observationLine": "35", "typeCompatibility": "known_subtype_match", "suppressedContinuationLine": "35"},
        {"sourceLine": "14", "testCallLine": "37", "withLine": "36", "observationLine": "38", "typeCompatibility": "known_subtype_match", "suppressedContinuationLine": "38"},
    ])
    (tmp_path / "verdict.json").write_text(json.dumps({
        "schema": "mock-spec.issue24-verdict/v2", "gate_passed": True,
        "model_evidence": {"status": "passed"},
    }), encoding="utf-8")

    result = build_study_result(tmp_path, expectations)

    assert result["case_result"]["semantic_validation"]["status"] == "verified_incomplete"
    attached = {relation["kind"]: relation["observation_ids"]
                for relation in result["case_result"]["relations"] if relation["observation_ids"]}
    assert set(attached) == {"control_dependence", "exception_dependence"}
    modeled = [relation for relation in result["case_result"]["relations"]
               if relation["provenance"] == "CodeQL pytest.raises exception capture model"]
    assert {item for relation in modeled for item in relation["observation_ids"]} == {
        "criterion:0", "criterion:1", "criterion:2",
    }
    assert result["unknown_observation_mappings"] == []
    assert {row["observation_id"] for row in result["unknown_value_constraints"]} == {
        "criterion:0", "criterion:1", "criterion:2",
    }


def test_missing_actual_formal_row_blocks_value_flow_claim(tmp_path: Path) -> None:
    test_only_backend_connected_relations_receive_observation_ids(tmp_path)
    path = tmp_path / "flask-flask-observation-flow.csv"
    rows = [row for row in csv.DictReader(path.open(newline="", encoding="utf-8"))
            if row["relation"] != "actual_to_formal"]
    write_csv(path, rows)
    with pytest.raises(MappingEvidenceError, match="actual-to-formal"):
        build_study_result(tmp_path, Path(__file__).with_name("observation_expectations.json"))


def test_failed_mechanism_verdict_blocks_mechanism_claim(tmp_path: Path) -> None:
    test_only_backend_connected_relations_receive_observation_ids(tmp_path)
    (tmp_path / "verdict.json").write_text(json.dumps({
        "schema": "mock-spec.issue24-verdict/v2", "gate_passed": False,
        "model_evidence": {"status": "blocked"},
    }), encoding="utf-8")
    with pytest.raises(MappingEvidenceError, match="mechanism verdict"):
        build_study_result(tmp_path, Path(__file__).with_name("observation_expectations.json"))


def test_elapsed_cost_parser_preserves_minutes(tmp_path: Path) -> None:
    (tmp_path / "flask-query.time").write_text(
        "\tElapsed (wall clock) time (h:mm:ss or m:ss): 1:23.45\n"
        "\tMaximum resident set size (kbytes): 123\n",
        encoding="utf-8",
    )
    timings, peak = parse_time_files(tmp_path, "flask-")
    assert timings == {"flask-query": 83.45}
    assert peak["bytes"] == 123 * 1024


def test_missing_suppressed_continuation_blocks_observation_link(tmp_path: Path) -> None:
    test_only_backend_connected_relations_receive_observation_ids(tmp_path)
    path = tmp_path / "flask-pytest-exception-flow.csv"
    rows = list(csv.DictReader(path.open(newline="", encoding="utf-8")))
    rows[0]["suppressedContinuationLine"] = ""
    write_csv(path, rows)
    with pytest.raises(MappingEvidenceError, match="observation 120"):
        build_study_result(tmp_path, Path(__file__).with_name("observation_expectations.json"))


def test_unverified_pytest_source_receipt_blocks_mapping(tmp_path: Path) -> None:
    test_only_backend_connected_relations_receive_observation_ids(tmp_path)
    receipt = json.loads((tmp_path / "pytest-model-source-receipt.json").read_text())
    receipt["status"] = "blocked"
    (tmp_path / "pytest-model-source-receipt.json").write_text(
        json.dumps(receipt), encoding="utf-8"
    )
    with pytest.raises(MappingEvidenceError, match="source receipt"):
        build_study_result(tmp_path, Path(__file__).with_name("observation_expectations.json"))
