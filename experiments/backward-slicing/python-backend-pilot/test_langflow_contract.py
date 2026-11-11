from __future__ import annotations

from copy import deepcopy

import pytest

from langflow_contract import LangflowContractError, build_case_result


TEST_HASH = "1" * 64
PRODUCT_HASH = "2" * 64


def expectations():
    return {
        "schema": "mock-spec.langflow-backend-expectations/v1",
        "case_id": "langflow_189", "dataset": "python-benchmark-new",
        "inventory_revision": "revision", "snapshot_id": "snapshot",
        "target_selector": "tests/test_189is_encrypted.py::test_is_encrypted",
        "masked_test_sha256": TEST_HASH, "product_path": "product.py",
        "product_sha256": PRODUCT_HASH,
        "observations": [
            {"id": "criterion:0", "line": 106, "kind": "return_value_assertion"},
            {"id": "criterion:1", "line": 115,
             "kind": "dynamic_global_identity_assertion"},
        ],
        "unknown": ["path feasibility remains unknown"],
    }


def manifest():
    return {
        "schema": "mock-spec.langflow-codeql-input/v1",
        "case_id": "langflow_189", "dataset": "python-benchmark-new",
        "inventory_revision": "revision", "snapshot_id": "snapshot",
        "target_selector": "tests/test_189is_encrypted.py::test_is_encrypted",
        "masking_contract": "masked-stub-region/v1",
        "files": [
            {"source_id": "test", "logical_path": "test.py", "sha256": TEST_HASH,
             "bytes": 100, "role": "masked_test"},
            {"source_id": "product", "logical_path": "product.py", "sha256": PRODUCT_HASH,
             "bytes": 200, "role": "visible_product"},
        ],
        "target_extraction": {
            "target_scope": {"locations": [{"start_line": 70, "end_line": 116}]},
            "criteria": [
                {"criterion_id": "criterion:0", "line": 106},
                {"criterion_id": "criterion:1", "line": 115},
            ],
        },
        "dynamic_loader_binding": {"status": "resolved", "path": "product.py",
                                   "function_key": "is_encrypted"},
    }


def relation(kind, modality="analysis_may", observation_ids=None):
    return {
        "kind": kind, "modality": modality,
        "from": {"path": "product.py", "line": 106},
        "to": {"path": "test.py", "line": 106},
        "detail": kind + " evidence", "observation_ids": observation_ids or [],
        "provenance": "bounded CodeQL query", "assumptions": [],
    }


def study():
    return {
        "schema": "mock-spec.langflow-backend-study/v1",
        "status": "verified_incomplete",
        "identity": {
            "case_id": "langflow_189", "dataset": "python-benchmark-new",
            "snapshot_id": "snapshot",
            "target_selector": "tests/test_189is_encrypted.py::test_is_encrypted",
        },
        "entry_point": {"status": "authenticated"},
        "observations": expectations()["observations"],
        "relations": [
            relation("test_dynamic_call_syntax", "authenticated_external_bridge", ["criterion:0"]),
            relation("function_origin", "certain", ["criterion:1"]),
            relation("value_flow", observation_ids=["criterion:0"]),
            relation("control_dependence", observation_ids=["criterion:0"]),
            relation("normal_return_dependence", observation_ids=["criterion:0"]),
            relation("exception_dependence", observation_ids=["criterion:0"]),
            relation("state_flow", "unknown_effect", ["criterion:0"]),
        ],
        "unknown_effects": [{
            "boundary": {"path": "product.py", "line": 109, "call": "decrypt_api_key"},
            "normal_return": "may reach comparison",
            "exception": "listed exception is caught; other exception may prevent return",
            "state_alias_scope": ["arguments", "service", "auth state"],
        }],
        "raw_backend_relations": [{"relation": "raw retained evidence"}],
        "checks": [{"name": "exact_provenance", "status": "passed"}],
        "unknowns": ["dynamic rebinding outside the authenticated shape is unsupported"],
    }


def cost():
    return {
        "timings_seconds": {"database": 1.25, "query": 0.5},
        "total_seconds": 1.75,
        "peak_memory": {"status": "measured", "bytes": 1024,
                        "reason": "per-process maximum RSS"},
        "cache_state": {"status": "unknown", "reason": "cache not isolated"},
        "unavailable_phases": [
            {"phase": "path_feasibility", "reason": "not evaluated"}
        ],
    }


def test_builds_valid_contract_with_all_typed_relation_kinds():
    result = build_case_result(manifest(), study(), expectations(), cost())

    assert result["schema_version"] == "mock-spec.backward-analysis-case/v1"
    assert [row["observation_id"] for row in result["observations"]] == [
        "criterion:0", "criterion:1"
    ]
    assert {row["kind"] for row in result["relations"]} == {
        "call_binding", "function_origin", "value_flow", "control_dependence",
        "normal_return_dependence", "exception_dependence", "state_flow",
    }
    bridge = next(row for row in result["relations"] if row["kind"] == "call_binding")
    assert bridge["modality"] == "analysis_may"
    assert bridge["provenance"]["raw_modality"] == "authenticated_external_bridge"
    assert "authenticated_external_bridge" in bridge["precision"]
    assert result["completion"]["status"] == "partial"
    assert result["semantic_validation"]["status"] == "verified_incomplete"


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("test_source", "masked test identity"),
        ("product_source", "product identity"),
        ("observation_id", "criterion identity"),
        ("observation_line", "observation positions"),
    ],
)
def test_changed_source_or_observation_identity_fails_closed(mutation, message):
    current_manifest = manifest()
    if mutation == "test_source":
        current_manifest["files"][0]["sha256"] = "3" * 64
    elif mutation == "product_source":
        current_manifest["files"][1]["sha256"] = "3" * 64
    elif mutation == "observation_id":
        current_manifest["target_extraction"]["criteria"][0]["criterion_id"] = "criterion:x"
    else:
        current_manifest["target_extraction"]["criteria"][1]["line"] = 116
    with pytest.raises(LangflowContractError, match=message):
        build_case_result(current_manifest, study(), expectations(), cost())


def test_unknown_relation_kind_is_retained_only_as_unmapped_boundary():
    current = study()
    current["relations"].append(relation("backend_future_relation", "analysis_may"))
    result = build_case_result(manifest(), current, expectations(), cost())

    assert "backend_future_relation" not in {row["kind"] for row in result["relations"]}
    boundary = next(
        row for row in result["analysis_boundaries"]["unanalysed_regions"]
        if row["scope"] == "backend_relation"
    )
    assert boundary["reason"] == "unmapped relation kind"
    assert boundary["raw_relation"]["kind"] == "backend_future_relation"


def test_unknown_modality_is_not_forced_into_a_typed_relation():
    current = study()
    current["relations"].append(relation("value_flow", "backend_magic"))
    result = build_case_result(manifest(), current, expectations(), cost())
    boundary = next(
        row for row in result["analysis_boundaries"]["unanalysed_regions"]
        if row.get("reason") == "unmapped relation modality"
    )
    assert boundary["raw_relation"]["modality"] == "backend_magic"


def test_structured_unknown_is_copied_without_inventing_effects():
    current = study()
    expected_unknown = deepcopy(current["unknown_effects"][0])
    result = build_case_result(manifest(), current, expectations(), {})
    unknown = result["analysis_boundaries"]["unknown_effects"][0]

    assert unknown["effect"] == {
        "normal_return": expected_unknown["normal_return"],
        "exception": expected_unknown["exception"],
        "state_alias_scope": expected_unknown["state_alias_scope"],
    }
    assert result["cost"]["peak_memory"]["status"] == "unavailable"
    assert result["cost"]["total_seconds"] is None


def test_flat_phase_cost_dicts_and_additional_raw_kinds_are_supported():
    current = study()
    current["relations"] += [
        {
            "relation": kind, "modality": "analysis_may",
            "sourcePath": "product.py", "sourceLine": "106",
            "targetPath": "product.py", "targetLine": "109",
            "detail": "backend row", "observation_ids": ["criterion:0"],
            "assumptions": [],
        }
        for kind in (
            "settings_result_to_decrypt_argument",
            "decrypt_result_to_downstream_use",
            "decrypt_exception_to_handler_return",
        )
    ]
    flat_cost = {
        "database": {"seconds": 2.0, "peak_rss_bytes": 2000},
        "query": {"seconds": 0.5, "peak_rss_bytes": 3000},
        "decode": {"seconds": 0.1, "peak_rss_bytes": 1000},
        "total_seconds": 2.6,
        "cache_state": {"status": "unknown", "reason": "not isolated"},
    }
    result = build_case_result(manifest(), current, expectations(), flat_cost)
    raw_kinds = {
        "settings_result_to_decrypt_argument", "decrypt_result_to_downstream_use",
        "decrypt_exception_to_handler_return",
    }
    mapped = [row["kind"] for row in result["relations"]
              if row["provenance"]["raw_relation_kind"] in raw_kinds]
    assert mapped == [
        "value_flow", "value_flow", "exception_dependence"
    ]
    assert result["cost"]["timings_seconds"] == {
        "database": 2.0, "query": 0.5, "decode": 0.1
    }
    assert result["cost"]["peak_memory"]["bytes"] == 3000
    assert result["cost"]["peak_memory"]["status"] == "measured_per_process_max"


def test_relation_with_unknown_observation_id_fails_closed():
    current = study()
    current["relations"][0]["observation_ids"] = ["criterion:wrong"]
    with pytest.raises(LangflowContractError, match="unknown observation"):
        build_case_result(manifest(), current, expectations(), cost())


def test_missing_required_typed_kind_or_observation_coverage_fails_gate():
    current = study()
    current["relations"] = [
        row for row in current["relations"] if row["kind"] != "exception_dependence"
    ]
    with pytest.raises(LangflowContractError, match="required typed relation"):
        build_case_result(manifest(), current, expectations(), cost())

    current = study()
    current["relations"] = [
        {**row, "observation_ids": []} for row in current["relations"]
    ]
    with pytest.raises(LangflowContractError, match="observation coverage"):
        build_case_result(manifest(), current, expectations(), cost())
