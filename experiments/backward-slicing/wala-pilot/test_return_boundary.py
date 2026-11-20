"""Regression gates using actual WALA exports, including unlocated return boundaries."""
from __future__ import annotations

import json
from pathlib import Path
import importlib.util


HERE = Path(__file__).resolve().parent
FIXTURES = HERE / "fixtures/return-boundary"
SNAPSHOT = FIXTURES / "cross-caller/raw.json"
SAME_CALLER_SNAPSHOT = FIXTURES / "same-caller/raw.json"
SPEC = importlib.util.spec_from_file_location("return_boundary_subject", HERE / "generic_backend.py")
subject = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(subject)


def _snapshot():
    return json.loads(SNAPSHOT.read_text())


def _classes():
    return {
        "sample.Cut": "src/sample/Cut.java",
        "sample.Relay": "src/sample/Relay.java",
    }


def _transition(modality, caller, callee, instruction, *, identity, normal=()):
    return {
        "modality": modality,
        "caller": caller,
        "caller_context": "C",
        "callee": callee,
        "callee_context": "C",
        "instruction_index": instruction,
        "call_identity": identity,
        "exact_call_graph_target": True,
        "possible_target_nodes": [{"method": callee, "context": "C"}],
        "caller_cfg": {
            "status": "available",
            "normal_reachable": [
                {"instruction_index": index, "basic_block": index}
                for index in normal
            ],
            "exceptional_reachable": [],
            "exceptional_catches": [],
        },
    }


def _relay_relation(raw):
    relations, boundaries = subject._product_body_paths(raw, _classes())
    relay = [row for row in relations
             if row["kind"] == "normal_return_dependence"
             and row["source_path"] == "src/sample/Relay.java"
             and row["source_line"] == 5]
    return relay, boundaries


def test_real_wala_snapshot_accepts_unlocated_normal_return_boundary():
    raw = _snapshot()
    boundary = next(row for row in raw["slice_statements"]
                    if row["kind"] == "NORMAL_RET_CALLEE"
                    and row["method"] == "sample.Cut.second(I)I")
    assert boundary["instruction_index"] is None
    assert boundary["basic_block"] is None

    relay, boundaries = _relay_relation(raw)

    assert len(relay) == 1, boundaries
    return_nodes = {
        index for index, row in enumerate(raw["slice_statements"])
        if row["method"] == "sample.Cut.second(I)I"
        and row["kind"] == "NORMAL"
        and row["instruction_index"] in {9, 13}
    }
    return_boundary = next(
        index for index, row in enumerate(raw["slice_statements"])
        if row["method"] == "sample.Cut.second(I)I"
        and row["kind"] == "NORMAL_RET_CALLEE"
    )
    assert any(edge["from"] in return_nodes and edge["to"] == return_boundary
               for edge in relay[0]["dependency_path"])


def test_real_cross_caller_snapshot_preserves_negative_and_cut_evidence():
    raw = _snapshot()
    calls = [row for row in raw["interprocedural_transitions"]
             if row["modality"] == "call"
             and row["callee"] == "sample.Relay.relay(I)I"]
    assert len(calls) == 2
    assert len({row["call_identity"] for row in calls}) == 2
    assert {row["caller"] for row in calls} == {
        "sample.Cut.first(I)I", "sample.Cut.second(I)I",
    }
    first = next(index for index, row in enumerate(raw["slice_statements"])
                 if row["kind"] == "PARAM_CALLER"
                 and row["method"] == "sample.Cut.first(I)I")
    assert first in subject._path_indexes(raw)["data"][1]

    typed, suppressed = subject._typed_product_paths(raw, _classes(), {})
    body, _ = subject._product_body_paths(raw, _classes())

    assert not any(row["kind"] == "value_flow"
                   and row["source_statement"] == first for row in typed)
    assert any(row.get("route_evidence", {}).get("pruned_transition", 0) > 0
               for row in suppressed)
    assert any(row["kind"] == "control_dependence" and row["source_line"] == 10
               for row in body)
    assert {row["source_line"] for row in body
            if row["kind"] == "normal_return_dependence"
            and row["source_path"] == "src/sample/Cut.java"} == {11, 13}


def test_real_cross_caller_snapshot_state_budget_is_unknown():
    raw = _snapshot()
    start = next(index for index, row in enumerate(raw["slice_statements"])
                 if row["method"] == "sample.Relay.relay(I)I"
                 and row["kind"] == "NORMAL")

    path, evidence = subject._authenticated_dependency_path(
        raw, start, raw["seed_index"], {"DATA_DEP"}, max_states=2)

    assert path is None
    assert evidence["reason"] == "realizable_path_search_frontier"
    assert evidence["exhaustive"] is False


def test_unlocated_boundary_requires_concrete_value_return_witness():
    raw = _snapshot()
    raw["product_body_evidence"] = [
        row for row in raw["product_body_evidence"]
        if not (row["kind"] == "value_return"
                and row["method"] == "sample.Cut.second(I)I")
    ]

    relay, boundaries = _relay_relation(raw)

    assert relay == []
    boundary = next(row for row in boundaries
                    if row["source_path"] == "src/sample/Relay.java")
    assert boundary["reason"] == "return_boundary_evidence_unavailable"
    assert boundary["route_evidence"]["exhaustive"] is False
    assert boundary["route_evidence"]["pruned_boundary"] > 0


def test_invalid_boundary_shortcut_cannot_poison_valid_witness_state():
    """An invalid route reaching the same node first must not occupy seen."""
    raw = _snapshot()
    caller_return = next(
        index for index, row in enumerate(raw["slice_statements"])
        if row["method"] == "sample.Cut.second(I)I"
        and row["kind"] == "NORMAL_RET_CALLER"
    )
    return_boundary = next(
        index for index, row in enumerate(raw["slice_statements"])
        if row["method"] == "sample.Cut.second(I)I"
        and row["kind"] == "NORMAL_RET_CALLEE"
    )
    raw["slice_edges"].append({
        "from": caller_return, "to": return_boundary, "labels": ["DATA_DEP"],
    })

    relay, boundaries = _relay_relation(raw)

    assert len(relay) == 1, boundaries
    assert not any(edge["from"] == caller_return and edge["to"] == return_boundary
                   for edge in relay[0]["dependency_path"])


def test_unlocated_boundary_rejects_non_return_predecessor():
    """A DATA_DEP edge and kind alone cannot authenticate a method exit."""
    product = "p.Product.value()I"
    caller = "p.Caller.run()I"
    test = "p.Test.observe()V"
    inner_return = _transition(
        "normal_return", caller, product, 10,
        identity="caller#10->product", normal=(12, 13))
    outer_return = _transition(
        "normal_return", test, caller, 20,
        identity="test#20->caller", normal=(21,))
    raw = {
        "seed_index": 6,
        "slice_statements": [
            {"kind": "NORMAL", "method": product, "context": "C",
             "instruction_index": 1, "line": 5, "instruction": "return 1"},
            {"kind": "NORMAL_RET_CALLEE", "method": product, "context": "C"},
            {"kind": "NORMAL_RET_CALLER", "method": caller, "context": "C",
             "instruction_index": 10},
            {"kind": "NORMAL", "method": caller, "context": "C",
             "instruction_index": 12, "basic_block": 12,
             "instruction": "4 = binaryop(add) 2,3"},
            {"kind": "NORMAL_RET_CALLEE", "method": caller, "context": "C"},
            {"kind": "NORMAL_RET_CALLER", "method": test, "context": "C",
             "instruction_index": 20},
            {"kind": "NORMAL", "method": test, "context": "C",
             "instruction_index": 21, "basic_block": 21},
        ],
        "slice_edges": [
            {"from": 0, "to": 1, "labels": ["DATA_DEP"]},
            {"from": 1, "to": 2, "labels": ["DATA_DEP"],
             "interprocedural_transition": inner_return},
            {"from": 2, "to": 3, "labels": ["DATA_DEP"]},
            {"from": 3, "to": 4, "labels": ["DATA_DEP"]},
            {"from": 4, "to": 5, "labels": ["DATA_DEP"],
             "interprocedural_transition": outer_return},
            {"from": 5, "to": 6, "labels": ["DATA_DEP"]},
        ],
        "product_body_evidence": [{
            "kind": "value_return", "method": product, "context": "C",
            "instruction_index": 1, "retained_instruction_index": 1,
            "line": 5, "instruction": "return 1",
        }],
    }

    relations, boundaries = subject._product_body_paths(
        raw, {"p.Product": "src/p/Product.java"})

    assert relations == []
    assert boundaries[0]["reason"] == "return_boundary_evidence_unavailable"


def test_real_same_caller_snapshot_keeps_only_observation_feeding_call():
    raw = json.loads(SAME_CALLER_SNAPSHOT.read_text())
    calls = [row for row in raw["interprocedural_transitions"]
             if row["modality"] == "call"
             and row["caller"] == "sample.Cut.second(I)I"
             and row["callee"] == "sample.Relay.relay(I)I"]
    returns = [row for row in raw["interprocedural_transitions"]
               if row["modality"] == "normal_return"
               and row["caller"] == "sample.Cut.second(I)I"
               and row["callee"] == "sample.Relay.relay(I)I"]
    assert {row["instruction_index"] for row in calls} == {3, 6}
    assert [row["instruction_index"] for row in returns] == [6]
    first = next(index for index, row in enumerate(raw["slice_statements"])
                 if row["kind"] == "PARAM_CALLER"
                 and row["method"] == "sample.Cut.second(I)I"
                 and row["instruction_index"] == 3)
    second = next(index for index, row in enumerate(raw["slice_statements"])
                  if row["kind"] == "PARAM_CALLER"
                  and row["method"] == "sample.Cut.second(I)I"
                  and row["instruction_index"] == 6)
    assert first in subject._path_indexes(raw)["data"][1]

    typed, _ = subject._typed_product_paths(raw, _classes(), {})

    assert not any(row["source_statement"] == first for row in typed)
    assert any(row["source_statement"] == second for row in typed)


def test_edge_budget_reports_non_exhaustive_frontier():
    raw = {
        "slice_statements": [
            {"kind": "NORMAL", "method": "p.C.m()V", "context": "C",
             "instruction_index": index, "basic_block": index}
            for index in range(3)
        ],
        "slice_edges": [
            {"from": 0, "to": 1, "labels": ["DATA_DEP"]},
            {"from": 1, "to": 2, "labels": ["DATA_DEP"]},
        ],
    }

    path, evidence = subject._authenticated_dependency_path(
        raw, 0, 2, {"DATA_DEP"}, max_edges=1)

    assert path is None
    assert evidence["reason"] == "realizable_path_search_frontier"
    assert evidence["exhaustive"] is False
    assert evidence["pruned_depth"] == 1


def test_call_depth_budget_reports_non_exhaustive_frontier():
    first_caller = "p.A.run()V"
    second_caller = "p.B.run()V"
    leaf = "p.C.run()V"
    raw = {
        "slice_statements": [
            {"kind": "PARAM_CALLER", "method": first_caller, "context": "C",
             "instruction_index": 1, "basic_block": 1},
            {"kind": "PARAM_CALLEE", "method": second_caller, "context": "C"},
            {"kind": "PARAM_CALLER", "method": second_caller, "context": "C",
             "instruction_index": 2, "basic_block": 2},
            {"kind": "PARAM_CALLEE", "method": leaf, "context": "C"},
            {"kind": "NORMAL", "method": leaf, "context": "C",
             "instruction_index": 3, "basic_block": 3},
        ],
        "slice_edges": [
            {"from": 0, "to": 1, "labels": ["DATA_DEP"],
             "interprocedural_transition": _transition(
                 "call", first_caller, second_caller, 1,
                 identity="a#1->b")},
            {"from": 1, "to": 2, "labels": ["DATA_DEP"]},
            {"from": 2, "to": 3, "labels": ["DATA_DEP"],
             "interprocedural_transition": _transition(
                 "call", second_caller, leaf, 2,
                 identity="b#2->c")},
            {"from": 3, "to": 4, "labels": ["DATA_DEP"]},
        ],
    }

    path, evidence = subject._authenticated_dependency_path(
        raw, 0, 4, {"DATA_DEP"}, max_depth=1)

    assert path is None
    assert evidence["reason"] == "realizable_path_search_frontier"
    assert evidence["exhaustive"] is False
    assert evidence["pruned_depth"] == 1
