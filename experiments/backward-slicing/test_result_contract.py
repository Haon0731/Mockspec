import copy

import pytest

import result_contract


IDENTITY = {
    "case_id": "case-1",
    "dataset": "python-benchmark-new",
    "language": "python",
    "snapshot_id": "a" * 64,
}


def _partial_result():
    return {
        "status": "partial",
        "stage": "complete",
        "failure_categories": ["unresolved_calls", "semantic_analysis_gap"],
        "input_context": {
            "target_selector": "test_module.py::test_behavior",
            "masked_test_logical_path": "test_module.py",
            "masked_test_sha256": "b" * 64,
            "masking_contract": "python-slot-v1",
        },
        "target_extraction": {
            "target_scope": {
                "selector": "test_module.py::test_behavior",
                "status": "located",
                "precision": "python_ast",
                "locations": [{"symbol": "test_behavior", "start_line": 3, "end_line": 8}],
            },
            "criteria": [{
                "criterion_id": "criterion:0", "line": 8, "kind": "value_assertion",
                "confidence": "explicit", "reason": "Python assert",
            }],
        },
        "sources": [{
            "source_id": "test", "logical_path": "test_module.py", "sha256": "b" * 64,
            "bytes": 100,
        }, {
            "source_id": "repository:1", "logical_path": "module.py", "sha256": "c" * 64,
            "bytes": 200,
        }],
        "source_selection": {"truncated": False},
        "evidence": {"nodes": 4, "edges": 3, "criteria": [{
            "seed": "n4", "unknown_call_nodes": ["n2"],
        }]},
        "timings_seconds": {"criterion_extraction": 0.001, "source_selection": 0.002,
                            "dependency_analysis": 0.01, "source_mapping": 0.001},
        "elapsed_seconds": 0.02,
    }


def test_contract_preserves_identity_observations_unknowns_and_unverified_semantics():
    contract = result_contract.build_result_contract(_partial_result(), IDENTITY)
    result_contract.validate_result_contract(contract)

    assert contract["identity"] == {
        "case_id": "case-1", "dataset": "python-benchmark-new", "language": "python",
        "snapshot_id": "a" * 64, "masking_contract": "python-slot-v1",
        "masked_test_sha256": "b" * 64, "target_selector": "test_module.py::test_behavior",
    }
    assert contract["observations"] == [{
        "observation_id": "criterion:0",
        "location": {"source_id": "test", "start_line": 8, "end_line": 8},
        "kind": "value_assertion", "origin": "explicit", "reason": "Python assert",
    }]
    assert contract["entry_point"]["test_selection"]["status"] == "unique"
    assert contract["entry_point"]["mut_binding"]["status"] == "not_evaluated"
    assert contract["relations"] == []
    assert {row["kind"] for row in contract["relation_coverage"]} == result_contract.RELATION_KINDS
    assert all(row["status"] == "not_evaluated" for row in contract["relation_coverage"])
    assert contract["analysis_boundaries"]["unknown_effects"][0]["boundary_id"] == "n2"
    assert contract["analysis_boundaries"]["unanalysed_regions"] == [{
        "scope": "semantic_relations",
        "reason": "candidate_graph_has_no_validated_dependency_semantics",
    }]
    assert contract["semantic_validation"]["status"] == "not_evaluated"
    assert contract["cost"]["peak_memory"]["status"] == "unavailable"
    assert contract["cost"]["graph_size"] == {"nodes": 4, "edges": 3}


def test_failure_contract_retains_stage_budget_and_unanalysed_boundary():
    result = {
        "status": "timeout", "stage": "backward_graph",
        "failure_categories": ["source_budget", "timeout"],
        "source_selection": {"truncated": True}, "sources": [],
        "timings_seconds": {"criterion_extraction": 0.001}, "elapsed_seconds": 1.0,
    }
    contract = result_contract.build_result_contract(result, IDENTITY)
    result_contract.validate_result_contract(contract)

    assert contract["completion"]["failure_categories"] == ["source_budget", "timeout"]
    assert contract["identity"]["masked_test_sha256"] is None
    assert contract["identity"]["target_selector"] is None
    assert contract["analysis_boundaries"]["unanalysed_regions"] == [
        {"scope": "source_selection", "reason": "source_budget"},
        {"scope": "backward_graph", "reason": "source_budget,timeout"},
    ]
    assert {row["phase"] for row in contract["cost"]["unavailable_phases"]} >= {
        "dependency_analysis", "source_mapping",
    }


def test_contract_rejects_missing_required_field_and_invalid_relation_semantics():
    contract = result_contract.build_result_contract(_partial_result(), IDENTITY)
    missing = copy.deepcopy(contract)
    del missing["semantic_validation"]
    with pytest.raises(result_contract.ResultContractError, match="fields"):
        result_contract.validate_result_contract(missing)

    invalid = copy.deepcopy(contract)
    invalid["relations"] = [{
        "kind": "value_flow", "modality": "must", "from": {}, "to": {},
        "provenance": "backend", "assumptions": [],
    }]
    with pytest.raises(result_contract.ResultContractError, match="modality"):
        result_contract.validate_result_contract(invalid)


def test_contract_digest_is_stable_for_independent_readers():
    contract = result_contract.build_result_contract(_partial_result(), IDENTITY)
    assert result_contract.contract_digest(contract) == result_contract.contract_digest(
        copy.deepcopy(contract)
    )


@pytest.mark.parametrize("field", ["parameterization", "relation_coverage"])
def test_contract_rejects_missing_coverage_details(field):
    contract = result_contract.build_result_contract(_partial_result(), IDENTITY)
    contract[field] = None
    with pytest.raises(result_contract.ResultContractError):
        result_contract.validate_result_contract(contract)


def test_contract_rejects_duplicate_observation_and_relation_without_precision():
    contract = result_contract.build_result_contract(_partial_result(), IDENTITY)
    contract["observations"].append(copy.deepcopy(contract["observations"][0]))
    with pytest.raises(result_contract.ResultContractError, match="duplicate observation"):
        result_contract.validate_result_contract(contract)
    contract["observations"].pop()
    contract["relations"] = [{"kind": "value_flow", "modality": "analysis_may",
                              "from": "n1", "to": "n2", "provenance": "backend",
                              "assumptions": []}]
    with pytest.raises(result_contract.ResultContractError, match="precision"):
        result_contract.validate_result_contract(contract)


def test_combined_backend_timer_is_not_a_separate_dependency_measurement():
    result = _partial_result()
    result["timings_seconds"] = {"backend_total": 0.1, "evidence_summary": 0.01}
    cost = result_contract.build_result_contract(result, IDENTITY)["cost"]
    assert {row["phase"] for row in cost["unavailable_phases"]} >= {
        "front_end", "dependency_analysis", "slice", "source_mapping",
    }
    assert "excludes input admission" in cost["total_scope"]


@pytest.mark.parametrize("observation_ids", [
    ["missing-observation"], ["criterion:0", "criterion:0"], "criterion:0", [None],
])
def test_relation_rejects_invalid_observation_references(observation_ids):
    contract = result_contract.build_result_contract(_partial_result(), IDENTITY)
    contract["relations"] = [{
        "kind": "value_flow", "modality": "analysis_may", "from": "n1", "to": "n2",
        "provenance": "backend", "precision": "bounded", "assumptions": [],
        "observation_ids": observation_ids,
    }]
    with pytest.raises(result_contract.ResultContractError, match="observation"):
        result_contract.validate_result_contract(contract)


@pytest.mark.parametrize("observation_ids", [[], ["criterion:0"]])
def test_relation_accepts_registered_or_unassociated_observation_references(observation_ids):
    contract = result_contract.build_result_contract(_partial_result(), IDENTITY)
    contract["relations"] = [{
        "kind": "value_flow", "modality": "analysis_may", "from": "n1", "to": "n2",
        "provenance": "backend", "precision": "bounded", "assumptions": [],
        "observation_ids": observation_ids,
    }]
    result_contract.validate_result_contract(contract)
    assert contract["semantic_validation"]["status"] == "not_evaluated"
