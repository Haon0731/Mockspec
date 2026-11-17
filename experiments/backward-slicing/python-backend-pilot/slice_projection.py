"""Project the accepted local AST subset into conservative slice clues."""
from __future__ import annotations

import ast
from copy import deepcopy
import hashlib
import importlib
from pathlib import Path
import sys
from typing import Any


HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))

from slice_contract import SliceClueContract, validate_source_quotes  # noqa: E402


analyze_local_constraints = importlib.import_module(
    "local_constraints",
).analyze_local_constraints


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _function(text: str, name: str, line: int) -> ast.FunctionDef:
    tree = ast.parse(text)
    matches = [
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == name and node.lineno == line
    ]
    if len(matches) != 1:
        raise ValueError("selected function is not unique")
    return matches[0]


def _parameter_rows(function: ast.FunctionDef) -> tuple[list[str], list[ast.expr]]:
    matches = [
        decorator for decorator in function.decorator_list
        if isinstance(decorator, ast.Call)
        and isinstance(decorator.func, ast.Attribute)
        and decorator.func.attr == "parametrize"
    ]
    if len(matches) != 1 or len(matches[0].args) < 2:
        raise ValueError("one explicit parameterization is required")
    names_node, rows = matches[0].args[:2]
    if not isinstance(names_node, ast.Constant) or not isinstance(names_node.value, str):
        raise ValueError("parameter names are not explicit")
    if not isinstance(rows, (ast.List, ast.Tuple)):
        raise ValueError("parameter rows are not explicit")
    names = [name.strip() for name in names_node.value.split(",")]
    if not names or any(not name for name in names):
        raise ValueError("parameter names are invalid")
    return names, list(rows.elts)


def _matching_call(function: ast.FunctionDef, location: dict[str, int]) -> ast.Call:
    start_line, start_col, end_line, end_col = _span(location)
    candidates = [
        node for node in ast.walk(function)
        if isinstance(node, ast.Call)
        and (node.lineno, node.col_offset) >= (start_line, start_col)
        and (node.end_lineno, node.end_col_offset) <= (end_line, end_col)
    ]
    matches = [
        node for node in candidates
        if not any(
            other is not node
            and (other.lineno, other.col_offset) <= (node.lineno, node.col_offset)
            and (other.end_lineno, other.end_col_offset)
            >= (node.end_lineno, node.end_col_offset)
            for other in candidates
        )
    ]
    if len(matches) != 1:
        raise ValueError("selected call is not unique")
    return matches[0]


def _parameter_value(row: ast.expr, index: int, width: int) -> ast.expr:
    if width == 1:
        if index != 0:
            raise ValueError("parameter index is outside row")
        return row
    if not isinstance(row, (ast.Tuple, ast.List)) or len(row.elts) != width:
        raise ValueError("parameter row width does not match names")
    return row.elts[index]


def _target_names(statement: ast.Assign | ast.AnnAssign) -> set[str]:
    targets = statement.targets if isinstance(statement, ast.Assign) else [statement.target]
    return {
        node.id for target in targets for node in ast.walk(target)
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store)
    }


def _value_names(statement: ast.Assign | ast.AnnAssign) -> set[str]:
    value = statement.value
    if value is None:
        return set()
    return {
        node.id for node in ast.walk(value)
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)
    }


def _executed_assignments(
    function: ast.FunctionDef, conditions: list[dict[str, Any]], selected_return: ast.Return,
) -> list[tuple[ast.Assign | ast.AnnAssign, int | None]]:
    """Recover assignments on the authenticated selected local path."""
    polarity_by_line = {
        item["location"]["start_line"]: item["polarity"] for item in conditions
    }
    assignments: list[tuple[ast.Assign | ast.AnnAssign, int | None]] = []

    def walk(statements: list[ast.stmt], controller: int | None) -> bool:
        for statement in statements:
            if isinstance(statement, (ast.Assign, ast.AnnAssign)):
                assignments.append((statement, controller))
            elif isinstance(statement, ast.If):
                if statement.lineno not in polarity_by_line:
                    raise ValueError("selected path omits a visible condition")
                selected = statement.body if polarity_by_line[statement.lineno] else statement.orelse
                if walk(selected, statement.lineno):
                    return True
            elif isinstance(statement, ast.Return):
                return statement is selected_return
        return False

    if not walk(function.body, None):
        raise ValueError("selected return is outside authenticated path")
    return assignments


def _normalize_external_observation_ids(
    analysis: dict[str, Any], expected_ids: dict[str, str],
) -> dict[str, Any]:
    """Restore analyzer-local IDs while retaining task observation IDs for output."""
    normalized = deepcopy(analysis)
    id_map: dict[str, str] = {}
    for observation in normalized.get("observations", []):
        external_id = observation.get("observation_id")
        local_id = observation.pop("local_observation_id", external_id)
        if not isinstance(external_id, str) or not isinstance(local_id, str):
            raise ValueError("observation identity is invalid")
        if expected_ids.get(local_id) != external_id:
            raise ValueError("observation identity differs from authenticated receipt")
        id_map[external_id] = local_id
    if len(id_map) != len(normalized.get("observations", [])):
        raise ValueError("observation identities are not unique")
    if set(expected_ids) != set(id_map.values()):
        raise ValueError("authenticated observation identity set is incomplete")

    def replace(value: Any) -> None:
        if isinstance(value, dict):
            if "observation_id" in value and value["observation_id"] in id_map:
                value["observation_id"] = id_map[value["observation_id"]]
            for child in value.values():
                replace(child)
        elif isinstance(value, list):
            for child in value:
                replace(child)

    replace(normalized)
    return normalized


def _span(location: dict[str, int]) -> tuple[int, int, int, int]:
    return (
        location["start_line"], location["start_column"],
        location["end_line"], location["end_column"],
    )


def project_local_slice(
    *, identity: dict[str, str], sources: list[dict[str, Any]],
    local_analysis: dict[str, Any], expected_observation_ids: dict[str, str],
    wall_seconds: float | None = None,
) -> SliceClueContract:
    """Create slice clues without promoting candidate relations or Mock requirements."""
    by_role = {source["role"]: source for source in sources}
    if set(by_role) != {"test", "product"} or len(sources) != 2:
        raise ValueError("exactly one test and one product source are required")
    for source in sources:
        if source["sha256"] != _digest(source["text"]):
            raise ValueError("source digest mismatch")
    test, product = by_role["test"], by_role["product"]
    binding = local_analysis["binding"]
    test_function = _function(
        test["text"], binding["test_function"],
        binding["test_function_location"]["start_line"],
    )
    product_function = _function(
        product["text"], binding["product_function"],
        binding["product_function_location"]["start_line"],
    )
    recomputed = analyze_local_constraints(
        test_source=test["text"], product_source=product["text"],
        test_function=binding["test_function"],
        test_function_line=binding["test_function_location"]["start_line"],
        product_function=binding["product_function"],
        product_function_line=binding["product_function_location"]["start_line"],
        call_line=binding["call_location"]["start_line"],
    )
    if recomputed != _normalize_external_observation_ids(
        local_analysis, expected_observation_ids,
    ):
        raise ValueError("local analysis differs from authenticated source recomputation")
    parameter_names, row_nodes = _parameter_rows(test_function)
    branches = local_analysis["branches"]
    if len(row_nodes) != len(branches) or any(branch.get("blockers") for branch in branches):
        raise ValueError("only complete local parameter rows can be projected")
    selected_call = _matching_call(test_function, binding["call_location"])
    starred_names = [
        argument.value.id for argument in selected_call.args
        if isinstance(argument, ast.Starred) and isinstance(argument.value, ast.Name)
    ]
    if len(starred_names) != 1 or starred_names[0] not in parameter_names:
        raise ValueError("one explicit parameter-backed variadic argument is required")
    variadic_parameter_index = parameter_names.index(starred_names[0])

    source_models = [{
        "source_id": source["source_id"], "logical_path": source["logical_path"],
        "sha256": source["sha256"], "start_line": 1,
        "end_line": len(source["text"].splitlines()),
    } for source in sources]
    source_by_id = {source["source_id"]: source for source in sources}
    evidence: list[dict[str, Any]] = [
        {"evidence_id": "rule:local-ast", "kind": "rule", "source": None,
         "rule": "restricted local AST parameter, assignment, branch and return propagation",
         "provenance": "python-backend-pilot/local_constraints.py"},
        {"evidence_id": "binding:task-provided", "kind": "binding", "source": None,
         "rule": "caller supplied the test call and product function binding",
         "provenance": "projection input; automatic binding not evaluated"},
    ]
    evidence_keys: dict[tuple[str, int, int, int, int], str] = {}

    def add_source(source_id: str, location: dict[str, int], provenance: str) -> str:
        start, start_col, end, end_col = _span(location)
        key = (source_id, start, start_col, end, end_col)
        if key in evidence_keys:
            return evidence_keys[key]
        source = source_by_id[source_id]
        lines = source["text"].splitlines()
        chosen = [line.encode() for line in lines[start - 1:end]]
        if start == end:
            quote = chosen[0][start_col:end_col].decode()
        else:
            chosen[0] = chosen[0][start_col:]
            chosen[-1] = chosen[-1][:end_col]
            quote = b"\n".join(chosen).decode()
        evidence_id = f"src:{source_id}:{start}:{start_col}-{end}:{end_col}"
        evidence.append({
            "evidence_id": evidence_id, "kind": "source",
            "source": {"source_id": source_id, "source_sha256": source["sha256"],
                       "start_line": start, "start_column": start_col,
                       "end_line": end, "end_column": end_col, "quote": quote},
            "rule": None, "provenance": provenance,
        })
        evidence_keys[key] = evidence_id
        return evidence_id

    contexts = []
    context_input_evidence: dict[str, str] = {}
    for row, branch in zip(row_nodes, branches, strict=True):
        parameter_value = _parameter_value(row, variadic_parameter_index, len(parameter_names))
        value_location = {
            "start_line": parameter_value.lineno, "start_column": parameter_value.col_offset,
            "end_line": parameter_value.end_lineno or parameter_value.lineno,
            "end_column": parameter_value.end_col_offset or parameter_value.col_offset,
        }
        row_evidence = add_source(
            test["source_id"], value_location, "literal variadic input for parameter row",
        )
        contexts.append({
            "context_id": branch["branch_id"],
            "label": str(branch["parameter_row"].get("scenario", branch["branch_id"])),
            "input_facts": {
                "actual_variadic_arguments_literal": branch["normalized_call"][
                    "actual_variadic_arguments_literal"
                ],
            },
            "evidence_ids": [row_evidence, "rule:local-ast"],
        })
        context_input_evidence[branch["branch_id"]] = row_evidence
    context_ids = [item["context_id"] for item in contexts]

    observations, nodes, observation_nodes = [], [], {}
    for observation in local_analysis["observations"]:
        evidence_id = add_source(
            test["source_id"], observation["location"], "original test observation",
        )
        observations.append({"observation_id": observation["observation_id"], "kind": "value",
                             "expression": observation["expression"],
                             "evidence_id": evidence_id})
        node_id = f"node:observation:{observation['observation_id']}"
        observation_nodes[observation["observation_id"]] = node_id
        nodes.append({"node_id": node_id, "kind": "observation",
                      "context_ids": context_ids, "evidence_id": evidence_id,
                      "relevance": "criterion"})

    test_call_evidence = add_source(
        test["source_id"], binding["call_location"], "selected test call",
    )
    entry_location = {"start_line": product_function.lineno,
                      "start_column": product_function.col_offset,
                      "end_line": product_function.lineno,
                      "end_column": len(product["text"].splitlines()[
                          product_function.lineno - 1
                      ].encode())}
    entry_evidence = add_source(product["source_id"], entry_location,
                                "selected product declaration")
    nodes.extend([
        {"node_id": "node:test-call", "kind": "call", "context_ids": context_ids,
         "evidence_id": test_call_evidence, "relevance": "call_boundary"},
        {"node_id": "node:product-entry", "kind": "statement", "context_ids": context_ids,
         "evidence_id": entry_evidence, "relevance": "value_source"},
    ])
    premises = [{
        "premise_id": "premise:task-binding", "kind": "binding",
        "expression": "selected test call resolves to selected product function",
        "polarity": None, "context_ids": context_ids,
        "evidence_ids": ["binding:task-provided", test_call_evidence, entry_evidence],
        "status": "conditional",
    }]
    relations, unknowns = [], []
    if_nodes = {node.lineno: node for node in ast.walk(product_function) if isinstance(node, ast.If)}

    for branch in branches:
        context_id = branch["branch_id"]
        observation_ids = [item["observation_id"] for item in observations]
        argument_premise = f"premise:{context_id}:argument-binding"
        premises.append({
            "premise_id": argument_premise, "kind": "binding",
            "expression": "authenticated parameter literal binds to product variadic formal",
            "polarity": None, "context_ids": [context_id],
            "evidence_ids": [context_input_evidence[context_id], test_call_evidence,
                             entry_evidence, "rule:local-ast"],
            "status": "conditional",
        })
        condition_premises: list[str] = []
        condition_records: list[tuple[dict[str, Any], ast.If | None, str, str, str]] = []
        for index, condition in enumerate(branch["conditions"]):
            selected_if = if_nodes.get(condition["location"]["start_line"])
            evidence_id = add_source(product["source_id"], condition["location"],
                                     "evaluated local condition")
            node_id = f"node:{context_id}:condition:{index}"
            nodes.append({"node_id": node_id, "kind": "condition", "context_ids": [context_id],
                          "evidence_id": evidence_id, "relevance": "control_guard"})
            premise_id = f"premise:{context_id}:condition:{index}"
            premises.append({"premise_id": premise_id, "kind": "condition",
                             "expression": condition["expression"],
                             "polarity": condition["polarity"], "context_ids": [context_id],
                             "evidence_ids": [context_input_evidence[context_id], evidence_id,
                                              "rule:local-ast"],
                             "status": "conditional"})
            condition_premises.append(premise_id)
            condition_records.append((condition, selected_if, node_id, premise_id, evidence_id))

        return_evidence = add_source(product["source_id"], branch["return_site"],
                                     "selected return statement")
        return_node = f"node:{context_id}:return"
        nodes.append({"node_id": return_node, "kind": "return", "context_ids": [context_id],
                      "evidence_id": return_evidence, "relevance": "value_source"})
        boundary = branch["boundary_return"]
        if boundary.get("kind") != "opaque_external_call_return":
            raise ValueError("visible literal returns are outside this clue projection")
        boundary_evidence = add_source(product["source_id"], boundary["call_location"],
                                       "opaque call directly returned by product")
        boundary_node = f"node:{context_id}:boundary-call"
        nodes.append({"node_id": boundary_node, "kind": "call", "context_ids": [context_id],
                      "evidence_id": boundary_evidence, "relevance": "call_boundary"})
        return_matches = [
            node for node in ast.walk(product_function)
            if isinstance(node, ast.Return) and node.lineno == branch["return_site"]["start_line"]
        ]
        if len(return_matches) != 1 or return_matches[0].value is None:
            raise ValueError("selected return is not unique")
        selected_return = return_matches[0]
        executed_assignments = _executed_assignments(
            product_function, branch["conditions"], selected_return,
        )
        assignment_candidates = [item[0] for item in executed_assignments]
        condition_by_line = {
            selected_if.lineno: (node_id, premise_id, condition_evidence)
            for _condition, selected_if, node_id, premise_id, condition_evidence
            in condition_records if selected_if is not None
        }
        assignment_controls = {
            statement.lineno: condition_by_line[controller]
            for statement, controller in executed_assignments if controller is not None
        }
        return_control_records: list[tuple[str, str, str]] = []
        for condition, selected_if, node_id, premise_id, condition_evidence in condition_records:
            if selected_if is None:
                continue
            in_body = any(
                child is selected_return for statement in selected_if.body
                for child in ast.walk(statement)
            )
            in_orelse = any(
                child is selected_return for statement in selected_if.orelse
                for child in ast.walk(statement)
            )
            body_returns = any(isinstance(statement, ast.Return) for statement in selected_if.body)
            orelse_returns = any(
                isinstance(statement, ast.Return) for statement in selected_if.orelse
            )
            controls_return = (
                (condition["polarity"] and in_body)
                or (not condition["polarity"] and in_orelse)
                or (not condition["polarity"] and body_returns
                    and selected_return.lineno > (selected_if.end_lineno or selected_if.lineno))
                or (condition["polarity"] and orelse_returns
                    and selected_return.lineno > (selected_if.end_lineno or selected_if.lineno))
            )
            if controls_return:
                return_control_records.append((node_id, premise_id, condition_evidence))
        needed_names = {
            node.id for node in ast.walk(return_matches[0].value)
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)
        }
        selected_assignments: list[ast.Assign | ast.AnnAssign] = []
        for statement in reversed(assignment_candidates):
            targets = _target_names(statement)
            if not targets.intersection(needed_names):
                continue
            selected_assignments.append(statement)
            needed_names = (needed_names - targets) | _value_names(statement)
        formal_name = product_function.args.vararg.arg if product_function.args.vararg else None
        assignment_node_by_line: dict[int, str] = {}
        for statement in sorted(selected_assignments, key=lambda item: item.lineno):
            location = {
                "start_line": statement.lineno, "start_column": statement.col_offset,
                "end_line": statement.end_lineno or statement.lineno,
                "end_column": statement.end_col_offset or statement.col_offset,
            }
            assignment_evidence = add_source(
                product["source_id"], location, "backward-relevant executed local assignment",
            )
            assignment_node = f"node:{context_id}:assignment:{statement.lineno}"
            nodes.append({"node_id": assignment_node, "kind": "statement",
                          "context_ids": [context_id], "evidence_id": assignment_evidence,
                          "relevance": "value_source"})
            assignment_node_by_line[statement.lineno] = assignment_node
            if statement.lineno in assignment_controls:
                control_node, control_premise, control_evidence = assignment_controls[
                    statement.lineno
                ]
                relations.append({
                    "relation_id": (
                        f"relation:{context_id}:control-assignment:{statement.lineno}"
                    ),
                    "kind": "control", "from_node_id": control_node,
                    "to_node_id": assignment_node, "observation_ids": observation_ids,
                    "context_id": context_id,
                    "premise_ids": ["premise:task-binding", argument_premise,
                                    control_premise],
                    "evidence_ids": [control_evidence, assignment_evidence,
                                     "rule:local-ast"],
                    "status": "conditional",
                })
        normal_premise = f"premise:{context_id}:normal-completion"
        premises.append({"premise_id": normal_premise, "kind": "normal_completion",
                         "expression": f"{boundary['target_expression']} completes normally",
                         "polarity": None, "context_ids": [context_id],
                         "evidence_ids": [boundary_evidence], "status": "conditional"})
        relations.append({
            "relation_id": f"relation:{context_id}:call-binding", "kind": "call",
            "from_node_id": "node:test-call", "to_node_id": "node:product-entry",
            "observation_ids": observation_ids, "context_id": context_id,
            "premise_ids": ["premise:task-binding"],
            "evidence_ids": [test_call_evidence, entry_evidence, "binding:task-provided"],
            "status": "conditional",
        })
        return_control_premises = [item[1] for item in return_control_records]
        for control_index, (control_node, control_premise, control_evidence) in enumerate(
            return_control_records
        ):
            relations.append({
                "relation_id": f"relation:{context_id}:control-return:{control_index}",
                "kind": "control", "from_node_id": control_node, "to_node_id": return_node,
                "observation_ids": observation_ids, "context_id": context_id,
                    "premise_ids": ["premise:task-binding", argument_premise,
                                    control_premise],
                "evidence_ids": [control_evidence, return_evidence, "rule:local-ast"],
                "status": "conditional",
            })
        node_evidence = {node["node_id"]: node["evidence_id"] for node in nodes}
        reaching_definitions: dict[str, str] = {}
        value_edges: list[tuple[str, str]] = []
        for statement in sorted(selected_assignments, key=lambda item: item.lineno):
            target_node = assignment_node_by_line[statement.lineno]
            for name in sorted(_value_names(statement)):
                source_node = reaching_definitions.get(name)
                if source_node is None and name == formal_name:
                    source_node = "node:test-call"
                if source_node is not None:
                    value_edges.append((source_node, target_node))
            for name in _target_names(statement):
                reaching_definitions[name] = target_node
        boundary_call = _matching_call(product_function, boundary["call_location"])
        boundary_argument_names = {
            node.id
            for value in [*boundary_call.args, *(item.value for item in boundary_call.keywords)]
            for node in ast.walk(value)
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)
        }
        for name in sorted(boundary_argument_names):
            source_node = reaching_definitions.get(name)
            if source_node is None and name == formal_name:
                source_node = "node:test-call"
            if source_node is not None:
                value_edges.append((source_node, boundary_node))
        value_edges = list(dict.fromkeys(value_edges))
        for index, (source_node, target_node) in enumerate(value_edges):
            relations.append({
                "relation_id": f"relation:{context_id}:argument-flow:{index}", "kind": "data",
                "from_node_id": source_node, "to_node_id": target_node,
                "observation_ids": observation_ids, "context_id": context_id,
                "premise_ids": list(dict.fromkeys([
                    "premise:task-binding", argument_premise,
                    *return_control_premises, *condition_premises,
                ])),
                "evidence_ids": [node_evidence[source_node], node_evidence[target_node],
                                 "rule:local-ast"],
                "status": "conditional",
            })
        relations.append({
            "relation_id": f"relation:{context_id}:call-result-return", "kind": "data",
            "from_node_id": boundary_node, "to_node_id": return_node,
            "observation_ids": observation_ids, "context_id": context_id,
            "premise_ids": [normal_premise, *return_control_premises],
            "evidence_ids": [boundary_evidence, return_evidence, "rule:local-ast"],
            "status": "conditional",
        })
        for observation in observations:
            relations.append({
                "relation_id": f"relation:{context_id}:to:{observation['observation_id']}",
                "kind": "data", "from_node_id": return_node,
                "to_node_id": observation_nodes[observation["observation_id"]],
                "observation_ids": [observation["observation_id"]], "context_id": context_id,
                "premise_ids": ["premise:task-binding", normal_premise,
                                *return_control_premises],
                "evidence_ids": [return_evidence, observation["evidence_id"], "rule:local-ast"],
                "status": "conditional",
            })
        unknowns.append({
            "unknown_id": f"unknown:{context_id}:external-effects",
            "kind": "external_call_effects",
            "description": "runtime dispatch, exceptions, state changes and concrete return are unknown",
            "affects_observation_ids": observation_ids, "context_ids": [context_id],
            "evidence_ids": [boundary_evidence],
        })

    for index, unknown in enumerate(local_analysis.get("unknowns", [])):
        location = unknown.get("location")
        evidence_id = (
            add_source(test["source_id"], location, "visible unanalysed test region")
            if location else "rule:local-ast"
        )
        unknowns.append({
            "unknown_id": f"unknown:analysis:{index}", "kind": unknown["kind"],
            "description": unknown.get("impact", "outside declared local scope"),
            "affects_observation_ids": [item["observation_id"] for item in observations],
            "context_ids": context_ids, "evidence_ids": [evidence_id],
        })

    payload = {
        "schema_version": "mock-spec.slice-evidence/v1",
        "identity": {**identity, "entry_source": binding["origin"],
                     "analysis_version": "restricted-local-ast/v1"},
        "sources": source_models, "contexts": contexts, "evidence": evidence,
        "observations": observations, "slice_nodes": nodes, "premises": premises,
        "relations": relations, "unknowns": unknowns,
        "exclusions": [
            {"exclusion_id": "exclusion:mock-behavior", "kind": "unsupported_inference",
             "description": "no concrete return, Mock identity, patch target, verification or cardinality",
             "evidence_ids": ["rule:local-ast"]},
            {"exclusion_id": "exclusion:semantic-proof", "kind": "contract_limit",
             "description": "well-formed clues do not prove chronology, feasibility or completeness",
             "evidence_ids": ["rule:local-ast"]},
        ],
        "coverage": {"status": "partial",
                     "declared_scope": "literal parameter rows and task-provided local direct return",
                     "included_context_ids": context_ids,
                     "unknown_ids": [item["unknown_id"] for item in unknowns]},
        "cost": {"wall_seconds": wall_seconds, "model_requests": 0, "target_executions": 0,
                 "note": "projection only; no backend rebuild, model request or target execution"},
        "semantic_claim": "clues_only_not_semantic_proof",
    }
    contract = SliceClueContract.model_validate(payload)
    validate_source_quotes(contract, {source["source_id"]: source["text"] for source in sources})
    return contract


__all__ = ["project_local_slice"]
