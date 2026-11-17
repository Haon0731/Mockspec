"""Tests for the bounded reviewed-artifact Java slice gate."""
from __future__ import annotations

import importlib.util
from pathlib import Path


HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("java_slice_gate", HERE / "run_java_slice_gate.py")
gate = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(gate)


def test_gate_preserves_positive_jib_clue_and_excludes_known_bad_routes():
    result = gate.run_gate()
    assert result["status"] == "passed_bounded_reviewed_artifacts"
    assert result["target_executed"] is False
    assert result["full_graph_rebuilt"] is False
    assert result["semantic_claim"] == "clues_only_not_semantic_proof"
    assert result["positive_relations"] == [result["positive_relations"][0]]
    positive = result["positive_relations"][0]
    assert positive["case"] == "Jib"
    assert positive["kind"] == "state"
    assert positive["certainty"] == "conditional"
    assert positive["unknown_dependencies"]
    assert len(result["exclusions"]) == 38
    assert {row["reason"] for row in result["exclusions"]} == {
        "no_authenticated_matched_call_return_path"
    }
    assert {row["classification"] for row in result["exclusions"]} == {
        "excluded_unknown_not_relation"
    }
    assert {row["archived_relation_kind"] for row in result["exclusions"]} == {
        "control_dependence", "exception_dependence"
    }
