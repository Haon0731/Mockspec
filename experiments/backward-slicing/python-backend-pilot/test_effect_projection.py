from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

import pytest


HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("effect_projection", HERE / "effect_projection.py")
assert SPEC and SPEC.loader
projection = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = projection
SPEC.loader.exec_module(projection)


def row(relation: str, certainty: str, *, route: str = "normal", source: int = 4,
        observation: int = 5, detail: str = "evidence") -> dict[str, str]:
    has_call = relation != "visible_write_to_observation"
    return {
        "relation": relation, "certainty": certainty, "route": route,
        "sourcePath": "tests/test_box.py", "sourceLine": str(source),
        "sourceEndLine": str(source), "sourceColumn": "5", "sourceEndColumn": "17",
        "targetPath": "tests/test_box.py",
        "targetLine": str(observation), "targetEndLine": str(observation),
        "targetColumn": "12", "targetEndColumn": "21",
        "observationLine": str(observation), "fieldName": "ready",
        "regionKey": "box-allocation", "argumentIndex": "0", "detail": detail,
        "callPath": "tests/test_box.py" if has_call else "",
        "callLine": str(source) if has_call else "0",
        "callEndLine": str(source) if has_call else "0",
        "callColumn": "5" if has_call else "0", "callEndColumn": "17" if has_call else "0",
        "productPath": "", "productLine": "0", "productEndLine": "0",
        "productName": "",
    }


def test_fill_box_is_unknown_effect_attached_to_exact_assertion():
    result = projection.project_effect_rows(
        [projection.Observation("criterion:ready", 5)],
        [row("unknown_region_effect_to_observation", "unknown_effect")],
        query_sha256="a" * 64,
    )

    assert result["relations"] == []
    assert result["unknown_effects"][0]["effect"] == "call_may_modify_observed_region"
    assert result["unknown_effects"][0]["scope"]["observation_ids"] == ["criterion:ready"]
    assert result["unknown_effects"][0]["scope"]["field"] == "ready"
    assert result["unknown_effects"][0]["provenance"]["query_relation"] == (
        "unknown_region_effect_to_observation")
    assert result["unknown_effects"][0]["assumptions"]


def test_visible_write_is_candidate_state_flow_not_definite_value():
    result = projection.project_effect_rows(
        [projection.Observation("criterion:ready", 8)],
        [row("visible_write_to_observation", "candidate", source=6, observation=8)],
        query_sha256="b" * 64,
    )

    assert result["relations"] == [{
        "kind": "state_flow", "modality": "analysis_may",
        "observation_ids": ["criterion:ready"],
        "from": {"logical_path": "tests/test_box.py", "start_line": 6, "end_line": 6},
        "to": {"logical_path": "tests/test_box.py", "start_line": 8, "end_line": 8},
        "field": "ready", "region": "box-allocation", "route": "normal",
        "argument_index": 0,
        "effect_location": {"start_column": 5, "end_column": 17},
        "observation_location": {"start_column": 12, "end_column": 21},
        "call": None,
        "product_target": None,
        "assumptions": [
            "ordinary Python attribute semantics; descriptor, __getattribute__, and "
            "__setattr__ effects are not modelled"
        ],
        "provenance": {"backend": "CodeQL", "query_relation": "visible_write_to_observation",
                       "query_detail": "evidence", "query_sha256": "b" * 64},
        "precision": ("same unique local-source region and field with CFG reachability; "
                      "path feasibility and runtime dispatch remain unreviewed"),
        "assumptions": [
            "CodeQL local-source identity represents the same runtime object region",
            "ordinary Python attribute semantics; descriptor, __getattribute__, and "
            "__setattr__ effects are not modelled",
        ],
    }]


def test_dominating_overwrite_is_exclusion_not_influence():
    result = projection.project_effect_rows(
        [projection.Observation("criterion:ready", 7)],
        [row("region_effect_excluded_by_overwrite", "excluded", observation=7,
             detail="dominating_same_region_same_field_overwrite:6")],
        query_sha256="c" * 64,
    )

    assert result["relations"] == []
    assert result["unknown_effects"] == []
    assert result["exclusions"][0]["reason"] == "dominating_same_region_same_field_overwrite"


def test_alias_without_unique_region_proof_stays_unknown():
    result = projection.project_effect_rows(
        [projection.Observation("criterion:ready", 8)],
        [row("possible_alias_effect_to_observation", "unknown_effect",
             source=4, observation=8)],
        query_sha256="1" * 64,
    )
    assert result["unknown_effects"][0]["effect"] == (
        "call_may_modify_possible_alias_of_observed_region"
    )


@pytest.mark.parametrize("relation,route,effect", [
    ("unknown_exception_path_to_observation", "exception",
     "call_exception_may_reach_observation_handler"),
    ("unknown_exception_to_pytest_observation", "pytest_raises",
     "call_exception_may_satisfy_pytest_raises_type_compatibility_unknown"),
])
def test_exception_evidence_stays_unknown_and_observation_linked(relation, route, effect):
    result = projection.project_effect_rows(
        [projection.Observation("criterion:exception", 12)],
        [row(relation, "unknown_effect", route=route, source=9, observation=12)],
        query_sha256="d" * 64,
    )

    unknown = result["unknown_effects"][0]
    assert unknown["effect"] == effect
    assert unknown["scope"]["route"] == route
    assert unknown["scope"]["observation_ids"] == ["criterion:exception"]


def test_ambiguous_observation_line_is_rejected():
    observations = [
        projection.Observation("criterion:a", 5),
        projection.Observation("criterion:b", 5),
    ]
    with pytest.raises(projection.EffectProjectionError, match="maps to 2 ids"):
        projection.project_effect_rows(
            observations,
            [row("unknown_region_effect_to_observation", "unknown_effect")],
            query_sha256="e" * 64,
        )


def test_unknown_effect_cannot_be_promoted_to_typed_relation():
    with pytest.raises(projection.EffectProjectionError, match="cannot be promoted"):
        projection.project_effect_rows(
            [projection.Observation("criterion:ready", 5)],
            [row("unknown_region_effect_to_observation", "candidate")],
            query_sha256="f" * 64,
        )


def test_duplicate_backend_rows_are_deduplicated():
    backend_row = row("unknown_region_effect_to_observation", "unknown_effect")
    result = projection.project_effect_rows(
        [projection.Observation("criterion:ready", 5)],
        [backend_row, dict(backend_row)], query_sha256="0" * 64,
    )
    assert len(result["unknown_effects"]) == 1


def test_same_boundary_keeps_distinct_original_observations():
    first = row("unknown_region_effect_to_observation", "unknown_effect", observation=5)
    second = row("unknown_region_effect_to_observation", "unknown_effect", observation=9)
    result = projection.project_effect_rows(
        [projection.Observation("criterion:first", 5),
         projection.Observation("criterion:second", 9)],
        [first, second], query_sha256="2" * 64,
    )
    assert [item["scope"]["observation_ids"] for item in result["unknown_effects"]] == [
        ["criterion:first"], ["criterion:second"],
    ]
    assert len({item["boundary_id"] for item in result["unknown_effects"]}) == 2


def test_same_line_effects_keep_field_region_argument_and_route_identity():
    base = row("unknown_region_effect_to_observation", "unknown_effect")
    other_field = {**base, "fieldName": "closed"}
    other_region = {**base, "regionKey": "alias-region"}
    other_argument = {**base, "argumentIndex": "1"}
    other_route = {**base, "route": "exception",
                   "relation": "unknown_exception_path_to_observation"}
    result = projection.project_effect_rows(
        [projection.Observation("criterion:ready", 5)],
        [base, other_field, other_region, other_argument, other_route],
        query_sha256="3" * 64,
    )
    assert len(result["unknown_effects"]) == 5
    assert len({item["boundary_id"] for item in result["unknown_effects"]}) == 5
    assert all(item["observation_ids"] == ["criterion:ready"]
               for item in result["unknown_effects"])


def test_same_line_calls_keep_distinct_column_identity_and_boundary_ids():
    first = row("unknown_region_effect_to_observation", "unknown_effect")
    second = {**first, "callColumn": "22", "callEndColumn": "34"}
    result = projection.project_effect_rows(
        [projection.Observation("criterion:ready", 5)], [first, second],
        query_sha256="4" * 64)
    assert len(result["unknown_effects"]) == 2
    assert len({item["boundary_id"] for item in result["unknown_effects"]}) == 2
    assert {item["evidence_identity"]["call"]["start_column"]
            for item in result["unknown_effects"]} == {5, 22}


def test_product_effect_preserves_concrete_call_and_target_identity():
    backend_row = row("visible_product_write_to_observation", "candidate",
                      source=40, observation=8)
    backend_row.update({"callLine": "6", "callEndLine": "6", "callColumn": "9",
                        "callEndColumn": "25", "productPath": "src/product.py",
                        "productLine": "35", "productEndLine": "44",
                        "productName": "fill"})
    result = projection.project_effect_rows(
        [projection.Observation("criterion:ready", 8)], [backend_row],
        query_sha256="5" * 64)

    relation = result["relations"][0]
    assert relation["call"] == {"logical_path": "tests/test_box.py", "start_line": 6,
                                "end_line": 6, "start_column": 9, "end_column": 25}
    assert relation["product_target"] == {"logical_path": "src/product.py",
                                           "start_line": 35, "end_line": 44,
                                           "symbol": "fill"}


def test_query_keeps_conditional_overwrite_and_distinct_fresh_object_rules_separate():
    query = (HERE / "observation-effects.ql").read_text()
    assert "overwrite.asCfgNode().dominates(read.asCfgNode())" in query
    assert "possibleAliasRegion(region, read.getObject())" in query
    assert "DataFlow::localFlow(left, right)" in query
    assert "sameUniqueRegion(region, read.getObject())" in query
    assert "not normalStrictlyReaches(overwrite.asCfgNode(), call.asCfgNode())" in query
    assert "authenticatedOpaqueCall(call, _)" in query
    assert "rebind.defines(variable)" in query
    assert "normalReaches(successor, read.asCfgNode())" in query
    assert "independentLiteralWrite(overwrite)" in query
    assert "lastIndependentLiteralWrite" in query
    assert "normalStrictlyReaches(write.asCfgNode(), target.getANormalExit())" in query
    assert "visibleLoaderBindingFact" in query
    assert "actualMapsToFormal" in query
    assert "callee.getArgumentForCall(callNode, formalIndex)" in query
