from __future__ import annotations

import ast
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest


HERE = Path(__file__).parent


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


local = load("local_constraints_for_slice", HERE / "local_constraints.py")
projection = load("slice_projection_subject", HERE / "slice_projection.py")


TEST = '''import pytest
@pytest.mark.parametrize("scenario,args_input", [
    ("empty", ()),
    ("single", ("hello",)),
])
def test_case(scenario, args_input):
    result = make_response(*args_input)
    assert result is not None
    assert hasattr(result, "headers")
'''

PRODUCT = '''def make_response(*args):
    """Unrelated documentation must not enter the slice."""
    if not args:
        return current_app.response_class()
    if len(args) == 1:
        ignored = 42
        args = args[0]
    return current_app.make_response(args)
'''


def function_line(text: str, name: str) -> int:
    return next(node.lineno for node in ast.parse(text).body
                if isinstance(node, ast.FunctionDef) and node.name == name)


def build():
    analysis = local.analyze_local_constraints(
        test_source=TEST, product_source=PRODUCT,
        test_function="test_case", test_function_line=function_line(TEST, "test_case"),
        product_function="make_response",
        product_function_line=function_line(PRODUCT, "make_response"), call_line=7,
    )
    sources = [
        {"role": "test", "source_id": "test", "logical_path": "tests/test_x.py",
         "sha256": hashlib.sha256(TEST.encode()).hexdigest(), "text": TEST},
        {"role": "product", "source_id": "product", "logical_path": "src/x.py",
         "sha256": hashlib.sha256(PRODUCT.encode()).hexdigest(), "text": PRODUCT},
    ]
    return projection.project_local_slice(
        identity={"case_id": "case", "dataset": "dataset", "language": "python",
                  "snapshot_id": "snapshot"},
        sources=sources, local_analysis=analysis,
        expected_observation_ids={
            item["observation_id"]: item["observation_id"]
            for item in analysis["observations"]
        },
        wall_seconds=0.01,
    )


def test_projection_contains_only_relevant_code_and_raw_observations() -> None:
    contract = build()
    quotes = [item.source.quote for item in contract.evidence if item.source is not None]
    assert any("make_response(*args_input)" in quote for quote in quotes)
    assert "not args" in quotes
    assert "args = args[0]" in quotes
    assert "current_app.make_response(args)" in quotes
    assert all("Unrelated documentation" not in quote for quote in quotes)
    assert all("ignored = 42" not in quote for quote in quotes)
    assert [item.expression for item in contract.observations] == [
        "result is not None", "hasattr(result, 'headers')",
    ]
    serialized = contract.model_dump_json()
    assert "return_non_null" not in serialized
    assert "return_has_attribute" not in serialized
    assert "call_cardinality" not in serialized
    assert "patch_target" not in serialized


def test_parameter_rows_and_relations_never_merge_contexts() -> None:
    contract = build()
    assert [item.context_id for item in contract.contexts] == ["row:0:empty", "row:1:single"]
    for relation in contract.relations:
        assert relation.context_id in {"row:0:empty", "row:1:single"}
        endpoints = {item.node_id: item for item in contract.slice_nodes}
        assert relation.context_id in endpoints[relation.from_node_id].context_ids
        assert relation.context_id in endpoints[relation.to_node_id].context_ids
    single_quotes = {
        next(item.source.quote for item in contract.evidence
             if item.evidence_id == node.evidence_id)
        for node in contract.slice_nodes if node.context_ids == ["row:1:single"]
    }
    assert "args = args[0]" in single_quotes
    empty_quotes = {
        next(item.source.quote for item in contract.evidence
             if item.evidence_id == node.evidence_id)
        for node in contract.slice_nodes if node.context_ids == ["row:0:empty"]
    }
    assert "args = args[0]" not in empty_quotes


def test_projected_relations_have_source_evidence_and_no_invented_temporal_order() -> None:
    contract = build()
    evidence = {item.evidence_id: item for item in contract.evidence}
    for relation in contract.relations:
        assert any(evidence[item].source is not None for item in relation.evidence_ids)
        assert relation.kind in {"call", "control", "data", "state"}

    for context in contract.contexts:
        edges = [
            (item.from_node_id, item.to_node_id) for item in contract.relations
            if item.context_id == context.context_id
        ]
        adjacency: dict[str, set[str]] = {}
        for source, target in edges:
            adjacency.setdefault(source, set()).add(target)
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(node: str) -> None:
            assert node not in visiting, "accepted local clues must not imply a cyclic order"
            if node in visited:
                return
            visiting.add(node)
            for target in adjacency.get(node, set()):
                visit(target)
            visiting.remove(node)
            visited.add(node)

        for node in adjacency:
            visit(node)


def test_projection_keeps_external_effects_and_analysis_gaps_unknown() -> None:
    contract = build()
    assert contract.coverage.status == "partial"
    assert contract.semantic_claim == "clues_only_not_semantic_proof"
    assert {item.kind for item in contract.unknowns} >= {
        "external_call_effects", "external_call_runtime_behavior_not_modelled",
        "call_binding_not_automatically_recovered",
    }
    assert all(relation.status == "conditional" for relation in contract.relations
               if relation.kind in {"call", "data"})
    json.loads(contract.model_dump_json())


def test_control_and_argument_edges_follow_structure_and_def_use() -> None:
    contract = build()
    node_quotes = {
        node.node_id: next(item.source.quote for item in contract.evidence
                           if item.evidence_id == node.evidence_id)
        for node in contract.slice_nodes
    }
    empty_relations = [
        item for item in contract.relations if item.context_id == "row:0:empty"
    ]
    assert not any(item.relation_id.startswith("relation:row:0:empty:argument-flow")
                   for item in empty_relations)

    single_controls = [
        item for item in contract.relations
        if item.context_id == "row:1:single" and item.kind == "control"
    ]
    return_controls = [item for item in single_controls
                       if item.to_node_id == "node:row:1:single:return"]
    assert [node_quotes[item.from_node_id] for item in return_controls] == ["not args"]
    assignment_controls = [item for item in single_controls
                           if ":control-assignment:" in item.relation_id]
    assert [node_quotes[item.from_node_id] for item in assignment_controls] == ["len(args) == 1"]
    assert all(item.status == "conditional" for item in single_controls)


@pytest.mark.parametrize(
    ("product_text", "expected_edges", "forbidden_edges", "absent_quote"),
    [
        (
            """def make_response(*args):
    if len(args) == 1:
        left = args[0]
        right = args[0]
    return external(left, right)
""",
            {
                ("result = make_response(*args_input)", "left = args[0]"),
                ("result = make_response(*args_input)", "right = args[0]"),
                ("left = args[0]", "external(left, right)"),
                ("right = args[0]", "external(left, right)"),
            },
            {("left = args[0]", "right = args[0]")},
            None,
        ),
        (
            """def make_response(*args):
    if len(args) == 1:
        value = args[0]
        value = "constant"
    return external(value)
""",
            {('value = "constant"', "external(value)")},
            {("result = make_response(*args_input)", 'value = "constant"')},
            "value = args[0]",
        ),
        (
            """def make_response(*args):
    value = args[0]
    return external(value)
""",
            {
                ("result = make_response(*args_input)", "value = args[0]"),
                ("value = args[0]", "external(value)"),
            },
            set(),
            None,
        ),
        (
            """def make_response(*args):
    if not args:
        return external()
    else:
        value = args[0]
        return external(value)
""",
            {
                ("result = make_response(*args_input)", "value = args[0]"),
                ("value = args[0]", "external(value)"),
            },
            set(),
            None,
        ),
    ],
)
def test_data_edges_use_reaching_definitions_not_a_linear_assignment_chain(
    product_text: str,
    expected_edges: set[tuple[str, str]],
    forbidden_edges: set[tuple[str, str]],
    absent_quote: str | None,
) -> None:
    test_text = '''import pytest
@pytest.mark.parametrize("args_input", [(("input",),)])
def test_case(args_input):
    result = make_response(*args_input)
    assert result is not None
'''
    analysis = local.analyze_local_constraints(
        test_source=test_text, product_source=product_text,
        test_function="test_case", test_function_line=3,
        product_function="make_response", product_function_line=1, call_line=4,
    )
    sources = [
        {"role": "test", "source_id": "test", "logical_path": "test.py",
         "sha256": hashlib.sha256(test_text.encode()).hexdigest(), "text": test_text},
        {"role": "product", "source_id": "product", "logical_path": "product.py",
         "sha256": hashlib.sha256(product_text.encode()).hexdigest(), "text": product_text},
    ]
    contract = projection.project_local_slice(
        identity={"case_id": "case", "dataset": "data", "language": "python",
                  "snapshot_id": "snapshot"},
        sources=sources, local_analysis=analysis,
        expected_observation_ids={
            item["observation_id"]: item["observation_id"]
            for item in analysis["observations"]
        },
    )
    node_quotes = {
        node.node_id: next(item.source.quote for item in contract.evidence
                           if item.evidence_id == node.evidence_id)
        for node in contract.slice_nodes
    }
    edges = {
        (node_quotes[item.from_node_id], node_quotes[item.to_node_id])
        for item in contract.relations if item.kind == "data"
    }
    assert expected_edges <= edges
    assert not forbidden_edges.intersection(edges)
    if absent_quote is not None:
        assert absent_quote not in node_quotes.values()


@pytest.mark.parametrize("mutation", ["observation", "input_fact", "external_id"])
def test_projection_recomputes_source_facts_and_rejects_tampered_analysis(
    mutation: str,
) -> None:
    analysis = local.analyze_local_constraints(
        test_source=TEST, product_source=PRODUCT,
        test_function="test_case", test_function_line=function_line(TEST, "test_case"),
        product_function="make_response", product_function_line=1, call_line=7,
    )
    expected_observation_ids = {
        item["observation_id"]: item["observation_id"]
        for item in analysis["observations"]
    }
    if mutation == "observation":
        analysis["observations"][0]["expression"] = "invented(result)"
    elif mutation == "external_id":
        analysis["observations"][0]["observation_id"] = "criterion:invented"
    else:
        analysis["branches"][0]["normalized_call"][
            "actual_variadic_arguments_literal"
        ] = {"kind": "tuple", "items": [{"kind": "str", "value": "invented"}]}
    sources = [
        {"role": "test", "source_id": "test", "logical_path": "test.py",
         "sha256": hashlib.sha256(TEST.encode()).hexdigest(), "text": TEST},
        {"role": "product", "source_id": "product", "logical_path": "product.py",
         "sha256": hashlib.sha256(PRODUCT.encode()).hexdigest(), "text": PRODUCT},
    ]
    with pytest.raises(ValueError, match="authenticated"):
        projection.project_local_slice(
            identity={"case_id": "case", "dataset": "data", "language": "python",
                      "snapshot_id": "snapshot"},
            sources=sources, local_analysis=analysis,
            expected_observation_ids=expected_observation_ids,
        )


def test_projection_rejects_blocked_rows_instead_of_publishing_partial_edges() -> None:
    analysis = local.analyze_local_constraints(
        test_source=TEST,
        product_source=PRODUCT.replace("if not args:", "if external(args):"),
        test_function="test_case", test_function_line=function_line(TEST, "test_case"),
        product_function="make_response", product_function_line=1, call_line=7,
    )
    sources = [
        {"role": "test", "source_id": "test", "logical_path": "test.py",
         "sha256": hashlib.sha256(TEST.encode()).hexdigest(), "text": TEST},
        {"role": "product", "source_id": "product", "logical_path": "product.py",
         "sha256": hashlib.sha256(PRODUCT.replace("if not args:", "if external(args):").encode()).hexdigest(),
         "text": PRODUCT.replace("if not args:", "if external(args):")},
    ]
    try:
        projection.project_local_slice(
            identity={"case_id": "case", "dataset": "data", "language": "python",
                      "snapshot_id": "snapshot"},
            sources=sources, local_analysis=analysis,
            expected_observation_ids={
                item["observation_id"]: item["observation_id"]
                for item in analysis["observations"]
            },
        )
    except ValueError as exc:
        assert "complete local parameter rows" in str(exc)
    else:  # pragma: no cover - explicit fail-closed assertion
        raise AssertionError("blocked analysis was projected")
