from __future__ import annotations

import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from java_demand_analysis import (
    SCHEMA, Unit, cache_key, canonical_observation, canonical_summary, method_footprint, plan,
    valid_cache_record,
)


def test_plan_retains_budget_frontier_instead_of_dropping_units():
    units = {
        "all": Unit("all", None, ("a", "b")),
        "a": Unit("a", "sink.a"),
        "b": Unit("b", "sink.b"),
    }
    result = plan(["all"], units=units, max_units=1, max_depth=2)
    assert result["selected_units"] == ["a"]
    assert result["unexpanded_boundaries"] == [
        {"unit": "b", "reason": "unit_budget", "depth": 1}
    ]
    assert result["complete"] is False


def test_plan_marks_recursive_dependency_and_terminates():
    units = {
        "a": Unit("a", "sink.a", ("b",)),
        "b": Unit("b", "sink.b", ("a",)),
    }
    result = plan(["a"], units=units, max_units=5, max_depth=5)
    assert result["selected_units"] == ["b", "a"]
    assert result["unexpanded_boundaries"] == [
        {"unit": "a", "reason": "recursive_dependency", "depth": 2}
    ]


def test_cache_key_invalidates_source_config_context_and_summary_version():
    unit = Unit("a", "sink.a")
    base = cache_key(unit=unit, source_identity="source", configuration_identity="config",
                     context="Everywhere", summary_version="v1")
    variants = {
        cache_key(unit=unit, source_identity="changed", configuration_identity="config",
                  context="Everywhere", summary_version="v1"),
        cache_key(unit=unit, source_identity="source", configuration_identity="changed",
                  context="Everywhere", summary_version="v1"),
        cache_key(unit=unit, source_identity="source", configuration_identity="config",
                  context="Other", summary_version="v1"),
        cache_key(unit=unit, source_identity="source", configuration_identity="config",
                  context="Everywhere", summary_version="v2"),
    }
    assert base not in variants
    assert len(variants) == 4


def test_cache_validation_rejects_tampered_observation(tmp_path: Path):
    path = tmp_path / "unit.json"
    path.write_text(json.dumps({
        "schema": SCHEMA, "cache_key": "key", "observation_sha256": "wrong",
        "observation": {"criterion": "sink.a"},
    }))
    assert valid_cache_record(path, key="key") is None


def test_canonical_observation_only_ignores_runtime_duration():
    value = {"criterion": "sink.a", "slice_millis": 123,
             "statements": [{"method": "A.m", "kind": "NORMAL"}]}
    assert canonical_observation(value) == {
        "criterion": "sink.a", "statements": [{"method": "A.m", "kind": "NORMAL"}]
    }


def test_method_footprint_counts_only_materialized_dependency_statements():
    footprint = method_footprint([
        {"statements": [{"method": "A.m"}, {"method": "A.m"}]},
        {"statements": [{"method": "B.n"}]},
    ])
    assert footprint["demanded_observations"] == 2
    assert footprint["retained_dependency_statements"] == 3
    assert footprint["retained_dependency_methods"] == 2


def summary_fixture() -> dict:
    relation = {"kind": "value_flow", "from": {"v": 1}, "to": {"v": 2},
                "modality": "analysis_may", "observation_ids": ["criterion:0"]}
    return {
        "observations": [{
            "observed_expression_witness": {"status": "complete"},
            "excluded_from_state_support": {"count": 2, "examples": ["sample-b", "sample-a"]},
        }],
        "outcome": {"status": "partial"}, "control_evidence": {}, "optional_evidence": {},
        "optional_setter_bytecode_evidence": {
            "entries": [], "inputs": [{"path": "/run/a", "sha256": "abc"}],
        },
        "result_contract": {
            "identity": {}, "source_scope": [], "observations": [], "entry_point": {},
            "relations": [relation], "relation_coverage": [], "analysis_boundaries": {},
            "completion": {}, "semantic_validation": {},
        },
    }


def test_summary_equivalence_ignores_only_order_metadata_and_truncated_examples():
    left = summary_fixture()
    right = summary_fixture()
    right["optional_setter_bytecode_evidence"]["inputs"][0]["path"] = "/other/run/a"
    right["observations"][0]["excluded_from_state_support"]["examples"] = ["different-sample"]
    assert canonical_summary(left) == canonical_summary(right)


def test_summary_equivalence_rejects_removed_relation_and_exact_to_opaque_change():
    baseline = summary_fixture()
    removed = summary_fixture()
    removed["result_contract"]["relations"] = []
    assert canonical_summary(baseline) != canonical_summary(removed)
    opaque = summary_fixture()
    opaque["observations"][0]["observed_expression_witness"]["status"] = (
        "complete_with_opaque_may"
    )
    assert canonical_summary(baseline) != canonical_summary(opaque)
