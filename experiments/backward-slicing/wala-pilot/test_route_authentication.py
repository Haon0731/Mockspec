"""Regression gates for matched Java call/return and temporal routes."""
from __future__ import annotations

import importlib.util
from pathlib import Path


HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("generic_java_route_subject", HERE / "generic_backend.py")
subject = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(subject)


def transition(modality, caller, callee, instruction, *, normal=(), exceptional=(),
               catch_throwable=False, identity=None, targets=None):
    call_identity = identity or f"{caller}#{instruction}->{callee}"
    return {
        "modality": modality,
        "caller": caller,
        "caller_context": "C",
        "callee": callee,
        "callee_context": "C",
        "instruction_index": instruction,
        "call_identity": call_identity,
        "exact_call_graph_target": True,
        "possible_target_nodes": targets or [{"method": callee, "context": "C"}],
        "caller_cfg": {
            "status": "available",
            "normal_reachable": [
                {"instruction_index": index, "basic_block": index} for index in normal
            ],
            "exceptional_reachable": [
                *([{"instruction_index": None, "basic_block": 2}] if catch_throwable else []),
                *[{"instruction_index": index, "basic_block": index} for index in exceptional],
            ],
            "exceptional_catches": ([
                {"basic_block": 2, "caught_types": ["Ljava/lang/Throwable"]}
            ] if catch_throwable else []),
        },
    }


def test_normal_return_requires_cfg_chronology_and_unique_target():
    product = "p.Product.value()I"
    caller = "p.Test.test()V"
    route = transition("normal_return", caller, product, 10, normal=(12,))
    raw = {
        "seed_index": 3,
        "slice_statements": [
            {"kind": "NORMAL", "method": product, "context": "C", "instruction_index": 1,
             "line": 9},
            {"kind": "NORMAL_RET_CALLEE", "method": product, "context": "C"},
            {"kind": "NORMAL_RET_CALLER", "method": caller, "context": "C",
             "instruction_index": 10},
            {"kind": "NORMAL", "method": caller, "context": "C", "instruction_index": 12,
             "basic_block": 12},
        ],
        "slice_edges": [
            {"from": 0, "to": 1, "labels": ["DATA_DEP"]},
            {"from": 1, "to": 2, "labels": ["DATA_DEP"],
             "interprocedural_transition": route},
            {"from": 2, "to": 3, "labels": ["DATA_DEP"]},
        ],
        "product_body_evidence": [{"kind": "value_return", "method": product,
                                   "context": "C", "instruction_index": 1, "line": 9}],
    }
    paths, boundaries = subject._product_body_paths(
        raw, {"p.Product": "src/main/java/p/Product.java"})
    assert [(row["kind"], row["source_line"]) for row in paths] == [
        ("normal_return_dependence", 9)
    ]
    assert boundaries == []

    raw["slice_edges"][1]["interprocedural_transition"]["caller_cfg"][
        "normal_reachable"
    ] = [{"instruction_index": 8, "basic_block": 8}]
    paths, boundaries = subject._product_body_paths(
        raw, {"p.Product": "src/main/java/p/Product.java"})
    assert paths == []
    assert boundaries[0]["reason"] == "no_authenticated_matched_call_return_path"

    raw["slice_edges"][1]["interprocedural_transition"] = route
    route["possible_target_nodes"].append({"method": "p.Other.value()I", "context": "C"})
    paths, boundaries = subject._product_body_paths(
        raw, {"p.Product": "src/main/java/p/Product.java"})
    assert paths == []
    assert boundaries[0]["route_evidence"]["pruned_ambiguous"] == 1

    route["possible_target_nodes"] = [{"method": product, "context": "C"}]
    route["caller"] = "p.Other.test()V"
    paths, boundaries = subject._product_body_paths(
        raw, {"p.Product": "src/main/java/p/Product.java"})
    assert paths == []
    assert boundaries[0]["route_evidence"]["pruned_transition"] == 1


def test_exception_route_rejects_different_call_identity_and_later_exception():
    product = "p.Product.fail()V"
    helper = "p.Helper.size()I"
    caller = "p.Caller.run()V"
    test = "p.Test.test()V"
    initial_exception = transition(
        "exceptional_return", caller, product, 10, exceptional=(20,),
        catch_throwable=True, identity="caller#10->product")
    helper_call = transition("call", caller, helper, 20, identity="caller#20->helper")
    wrong_return = transition("normal_return", test, helper, 30, normal=(31,),
                              identity="test#30->helper")
    raw = {
        "seed_index": 8,
        "slice_statements": [
            {"kind": "NORMAL", "method": product, "context": "C", "instruction_index": 1,
             "line": 7},
            {"kind": "EXC_RET_CALLEE", "method": product, "context": "C"},
            {"kind": "EXC_RET_CALLER", "method": caller, "context": "C",
             "instruction_index": 10},
            {"kind": "PARAM_CALLER", "method": caller, "context": "C",
             "instruction_index": 20, "basic_block": 20},
            {"kind": "PARAM_CALLEE", "method": helper, "context": "C"},
            {"kind": "NORMAL_RET_CALLEE", "method": helper, "context": "C"},
            {"kind": "NORMAL_RET_CALLER", "method": test, "context": "C",
             "instruction_index": 30},
            {"kind": "NORMAL", "method": test, "context": "C", "instruction_index": 31,
             "basic_block": 31},
            {"kind": "NORMAL", "method": test, "context": "C", "instruction_index": 32,
             "basic_block": 32},
        ],
        "slice_edges": [
            {"from": 0, "to": 1, "labels": ["DATA_DEP"]},
            {"from": 1, "to": 2, "labels": ["DATA_DEP"],
             "interprocedural_transition": initial_exception},
            {"from": 2, "to": 3, "labels": ["CONTROL_DEP"]},
            {"from": 3, "to": 4, "labels": ["DATA_DEP"],
             "interprocedural_transition": helper_call},
            {"from": 4, "to": 5, "labels": ["DATA_DEP"]},
            {"from": 5, "to": 6, "labels": ["DATA_DEP"],
             "interprocedural_transition": wrong_return},
            {"from": 6, "to": 7, "labels": ["DATA_DEP"]},
            {"from": 7, "to": 8, "labels": ["DATA_DEP"]},
        ],
        "product_body_evidence": [{"kind": "exceptional_exit", "method": product,
                                   "context": "C", "instruction_index": 1, "line": 7}],
    }
    paths, boundaries = subject._product_body_paths(
        raw, {"p.Product": "src/main/java/p/Product.java"})
    assert paths == []
    assert boundaries[0]["reason"] == "no_authenticated_matched_call_return_path"
    assert boundaries[0]["route_evidence"]["pruned_transition"] >= 1


def test_exception_route_preserves_caught_handler_observation():
    product = "p.Product.fail()V"
    caller = "p.Caller.run()V"
    exceptional_return = transition(
        "exceptional_return", caller, product, 10, exceptional=(20,),
        catch_throwable=True, identity="caller#10->product")
    raw = {
        "seed_index": 3,
        "slice_statements": [
            {"kind": "NORMAL", "method": product, "context": "C",
             "instruction_index": 1, "line": 7},
            {"kind": "EXC_RET_CALLEE", "method": product, "context": "C"},
            {"kind": "EXC_RET_CALLER", "method": caller, "context": "C",
             "instruction_index": 10},
            {"kind": "NORMAL", "method": caller, "context": "C",
             "instruction_index": 20, "basic_block": 20},
        ],
        "slice_edges": [
            {"from": 0, "to": 1, "labels": ["DATA_DEP"]},
            {"from": 1, "to": 2, "labels": ["DATA_DEP"],
             "interprocedural_transition": exceptional_return},
            {"from": 2, "to": 3, "labels": ["CONTROL_DEP"]},
        ],
        "product_body_evidence": [{
            "kind": "exceptional_exit", "method": product, "context": "C",
            "instruction_index": 1, "line": 7,
        }],
    }
    paths, boundaries = subject._product_body_paths(
        raw, {"p.Product": "src/main/java/p/Product.java"})
    assert [(row["kind"], row["source_line"]) for row in paths] == [
        ("exception_dependence", 7)
    ]
    assert boundaries == []


def test_truncated_base_slice_stays_unknown_instead_of_proving_absence():
    raw = {
        "seed_index": 1,
        "traversal_truncated": True,
        "traversal_frontier_count": 4,
        "slice_statements": [
            {"kind": "NORMAL", "method": "p.Product.value()I", "context": "C",
             "instruction_index": 1, "line": 9},
            {"kind": "NORMAL", "method": "p.Test.test()V", "context": "C",
             "instruction_index": 2, "line": 20},
        ],
        "slice_edges": [{"from": 0, "to": 1, "labels": ["DATA_DEP"]}],
        "product_body_evidence": [{"kind": "value_return", "method": "p.Product.value()I",
                                   "context": "C", "instruction_index": 1, "line": 9}],
    }
    paths, boundaries = subject._product_body_paths(
        raw, {"p.Product": "src/main/java/p/Product.java"})
    assert paths == []
    assert boundaries[0]["reason"] == "base_slice_or_realizable_path_frontier"
    assert boundaries[0]["route_evidence"]["exhaustive"] is False


def test_authenticated_search_state_cap_is_an_explicit_frontier():
    raw = {
        "slice_statements": [
            {"method": "p.Product.value()I", "context": "C", "instruction_index": index}
            for index in range(4)
        ],
        "slice_edges": [
            {"from": index, "to": index + 1, "labels": ["DATA_DEP"]}
            for index in range(3)
        ],
    }
    path, evidence = subject._authenticated_dependency_path(
        raw, 0, 3, {"DATA_DEP"}, max_states=2)
    assert path is None
    assert evidence["reason"] == "realizable_path_search_frontier"
    assert evidence["exhaustive"] is False
    assert evidence["searched_states"] == 2
    assert evidence["state_limit"] == 2


def two_call_value_route(wrong_return):
    """Six-node call/return fixture: enter call 10, return via 10 or 20."""
    caller, callee, test = "p.Cut.run()I", "p.Helper.relay(I)I", "p.Test.observe()V"
    returned_call = 20 if wrong_return else 10
    raw = {
        "seed_index": 5,
        "slice_statements": [
            {"kind": "PARAM_CALLER", "method": caller, "context": "C",
             "instruction_index": 10, "line": 10},
            {"kind": "PARAM_CALLEE", "method": callee, "context": "C"},
            {"kind": "NORMAL_RET_CALLEE", "method": callee, "context": "C"},
            {"kind": "NORMAL_RET_CALLER", "method": caller, "context": "C",
             "instruction_index": returned_call},
            {"kind": "NORMAL_RET_CALLEE", "method": caller, "context": "C",
             "basic_block": 25},
            {"kind": "NORMAL_RET_CALLER", "method": test, "context": "C",
             "instruction_index": 30},
        ],
        "slice_edges": [
            {"from": 0, "to": 1, "labels": ["DATA_DEP"],
             "interprocedural_transition": transition("call", caller, callee, 10)},
            {"from": 1, "to": 2, "labels": ["DATA_DEP"]},
            {"from": 2, "to": 3, "labels": ["DATA_DEP"],
             "interprocedural_transition": transition(
                 "normal_return", caller, callee, returned_call, normal=(25,))},
            {"from": 3, "to": 4, "labels": ["DATA_DEP"]},
            {"from": 4, "to": 5, "labels": ["DATA_DEP"],
             "interprocedural_transition": transition("normal_return", test, caller, 30)},
        ],
    }
    return raw, {"p.Cut": "src/main/java/p/Cut.java"}


def test_typed_value_chain_preserves_matched_call_return():
    raw, classes = two_call_value_route(False)
    paths, _ = subject._typed_product_paths(raw, classes, {})
    assert any(path["source_statement"] == 0 for path in paths)


def test_typed_value_chain_rejects_return_to_other_call_site():
    raw, classes = two_call_value_route(True)
    paths, _ = subject._typed_product_paths(raw, classes, {})
    assert not any(path["source_statement"] == 0 for path in paths)


def test_typed_value_chain_requires_every_edge():
    raw, classes = two_call_value_route(False)
    for index in range(len(raw["slice_edges"])):
        cut = {**raw, "slice_edges": raw["slice_edges"][:index] + raw["slice_edges"][index + 1:]}
        paths, _ = subject._typed_product_paths(cut, classes, {})
        assert not any(path["source_statement"] == 0 for path in paths)
