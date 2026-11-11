"""Materialize the frozen langflow_189 sources and authenticate its visible loader binding."""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
from pathlib import Path
import sys
from typing import Any, cast

from mock_spec.adapters.frozen_dataset import FrozenDataset

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from criteria import extract_criteria
from source_selection import select_sources


HERE = Path(__file__).resolve().parent
EXPECTATIONS = HERE / "langflow_expectations.json"


def digest(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def validate_binding(binding: dict[str, Any], expected: dict[str, Any]) -> None:
    wanted = {
        "status": "resolved", "loader": expected["loader"]["name"],
        "path": expected["product_path"], "function_key": expected["function"]["name"],
        "return_index": expected["loader"]["return_index"],
    }
    for key, value in wanted.items():
        if binding.get(key) != value:
            raise ValueError(f"dynamic loader binding differs: {key}")
    definition = binding.get("function_definition", {})
    for key, value in {
        "logical_path": expected["product_path"], "name": expected["function"]["name"],
        "start_line": expected["function"]["start_line"], "end_line": expected["function"]["end_line"],
    }.items():
        if definition.get(key) != value:
            raise ValueError(f"dynamic function definition differs: {key}")
    if binding.get("test_bindings") != [{"name": expected["function"]["name"],
                                         "line": expected["loader"]["test_binding_line"]}]:
        raise ValueError("dynamic test binding is missing, ambiguous, or rebound")
    if binding.get("call_sites") != [{"name": expected["function"]["name"],
                                      "line": expected["loader"]["call_line"]}]:
        raise ValueError("dynamic test call is missing, ambiguous, or rebound")


def invalid_token_identity_binding(source: str, expected: dict[str, Any]) -> dict[str, Any]:
    """Authenticate the second loader result and the exact identity assertion, without exec."""
    tree = ast.parse(source)
    loaders = [node for node in tree.body if isinstance(node, ast.FunctionDef)
               and node.name == expected["loader"]["name"]]
    if len(loaders) != 1:
        raise ValueError("InvalidToken loader is missing or ambiguous")
    loader = loaders[0]
    classes = [node for node in loader.body if isinstance(node, ast.ClassDef)
               and node.name == "LocalInvalidToken"]
    namespace_writes = [
        node for node in loader.body if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "namespace" for target in node.targets)
        and isinstance(node.value, ast.Dict)
        and len(node.value.keys) == 1 and isinstance(node.value.keys[0], ast.Constant)
        and node.value.keys[0].value == "InvalidToken" and isinstance(node.value.values[0], ast.Name)
        and node.value.values[0].id == "LocalInvalidToken"
    ]
    returns = [node for node in loader.body if isinstance(node, ast.Return)
               and isinstance(node.value, ast.Tuple) and len(node.value.elts) == 2]
    if len(classes) != 1 or len(namespace_writes) != 1 or len(returns) != 1:
        raise ValueError("InvalidToken namespace or tuple provenance is unsupported")
    return_value = cast(ast.Tuple, returns[0].value)
    first, second = return_value.elts
    if not (isinstance(first, ast.Subscript) and isinstance(first.value, ast.Name)
            and first.value.id == "namespace" and isinstance(first.slice, ast.Constant)
            and first.slice.value == expected["function"]["name"]
            and isinstance(second, ast.Name) and second.id == "LocalInvalidToken"):
        raise ValueError("loader tuple slots differ from authenticated function and exception class")
    namespace_line, return_line = namespace_writes[0].lineno, returns[0].lineno
    loader_writes = [
        node for node in ast.walk(loader)
        if namespace_line < getattr(node, "lineno", -1) < return_line
        and isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign, ast.NamedExpr,
                              ast.For, ast.AsyncFor, ast.Delete))
    ]
    for node in loader_writes:
        targets: list[ast.AST] = []
        if isinstance(node, ast.Assign):
            targets = list(node.targets)
        elif isinstance(node, (ast.AnnAssign, ast.AugAssign, ast.NamedExpr, ast.For, ast.AsyncFor)):
            targets = [node.target]
        elif isinstance(node, ast.Delete):
            targets = list(node.targets)
        if any((isinstance(target, ast.Name) and target.id in {"namespace", "LocalInvalidToken"})
               or (isinstance(target, ast.Subscript) and isinstance(target.value, ast.Name)
                   and target.value.id == "namespace")
               for target in targets):
            raise ValueError("InvalidToken namespace is mutated before loader return")
    if any(isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
           and namespace_line < node.lineno < return_line
           and node.name in {"namespace", "LocalInvalidToken"} for node in ast.walk(loader)):
        raise ValueError("InvalidToken namespace class is rebound before loader return")
    mutating_calls = [node for node in ast.walk(loader)
                      if namespace_line < getattr(node, "lineno", -1) < return_line
                      and isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                      and isinstance(node.func.value, ast.Name) and node.func.value.id == "namespace"
                      and node.func.attr in {"clear", "pop", "popitem", "setdefault", "update", "__setitem__"}]
    if mutating_calls:
        raise ValueError("InvalidToken namespace is mutated before loader return")
    assignments = [node for node in ast.walk(tree) if isinstance(node, ast.Assign)
                   and isinstance(node.value, ast.Call) and isinstance(node.value.func, ast.Name)
                   and node.value.func.id == expected["loader"]["name"]]
    if len(assignments) != 1 or assignments[0].lineno != expected["loader"]["test_binding_line"]:
        raise ValueError("loader result assignment is missing or ambiguous")
    target = assignments[0].targets[0]
    if not (isinstance(target, ast.Tuple) and [item.id if isinstance(item, ast.Name) else None
                                               for item in target.elts]
            == [expected["function"]["name"], "invalid_token_cls"]):
        raise ValueError("loader tuple destructuring differs")
    identity_line = next(item["line"] for item in expected["observations"]
                         if item["id"] == "criterion:1")
    assertions = [node for node in ast.walk(tree)
                  if isinstance(node, ast.Assert) and node.lineno == identity_line]
    if len(assertions) != 1 or not isinstance(assertions[0].test, ast.Compare):
        raise ValueError("InvalidToken identity assertion is missing")
    comparison = assertions[0].test
    right = comparison.comparators[0] if len(comparison.comparators) == 1 else None
    valid_right = (
        isinstance(right, ast.Subscript) and isinstance(right.value, ast.Attribute)
        and right.value.attr == "__globals__" and isinstance(right.value.value, ast.Name)
        and right.value.value.id == expected["function"]["name"]
        and isinstance(right.slice, ast.Constant) and right.slice.value == "InvalidToken"
    )
    if not (len(comparison.ops) == 1 and isinstance(comparison.ops[0], ast.Is)
            and isinstance(comparison.left, ast.Name) and comparison.left.id == "invalid_token_cls"
            and valid_right):
        raise ValueError("line 115 is not the exact InvalidToken identity assertion")
    protected_names = {expected["function"]["name"], "invalid_token_cls"}

    def protected_target(target: ast.AST) -> bool:
        if isinstance(target, ast.Name):
            return target.id in protected_names
        if isinstance(target, (ast.Tuple, ast.List)):
            return any(protected_target(item) for item in target.elts)
        if not isinstance(target, ast.Subscript) or not isinstance(target.value, ast.Attribute):
            return False
        attribute = target.value
        return (attribute.attr == "__globals__" and isinstance(attribute.value, ast.Name)
                and attribute.value.id == expected["function"]["name"]
                and isinstance(target.slice, ast.Constant) and target.slice.value == "InvalidToken")

    rebound: list[ast.AST] = []
    for candidate in ast.walk(tree):
        if not assignments[0].lineno < getattr(candidate, "lineno", -1) < assertions[0].lineno:
            continue
        targets = []
        if isinstance(candidate, ast.Assign):
            targets = list(candidate.targets)
        elif isinstance(candidate, (ast.AnnAssign, ast.AugAssign, ast.NamedExpr, ast.For, ast.AsyncFor)):
            targets = [candidate.target]
        elif isinstance(candidate, ast.Delete):
            targets = list(candidate.targets)
        elif isinstance(candidate, ast.With):
            targets = [item.optional_vars for item in candidate.items if item.optional_vars is not None]
        elif isinstance(candidate, ast.ExceptHandler) and candidate.name in protected_names:
            rebound.append(candidate)
        elif isinstance(candidate, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) \
                and candidate.name in protected_names:
            rebound.append(candidate)
        if any(protected_target(target_node) for target_node in targets):
            rebound.append(candidate)
        if (isinstance(candidate, ast.Call) and isinstance(candidate.func, ast.Attribute)
                and isinstance(candidate.func.value, ast.Attribute)
                and candidate.func.value.attr == "__globals__"
                and isinstance(candidate.func.value.value, ast.Name)
                and candidate.func.value.value.id == expected["function"]["name"]
                and candidate.func.attr in {"clear", "pop", "popitem", "setdefault", "update", "__setitem__"}):
            rebound.append(candidate)
    if rebound:
        raise ValueError("loader function or exception result is rebound before identity observation")
    return {
        "status": "resolved", "loader_class": "LocalInvalidToken",
        "namespace": "namespace", "namespace_key": "InvalidToken",
        "namespace_assignment_line": namespace_writes[0].lineno,
        "loader_return_line": returns[0].lineno,
        "return_slots": [{"index": 0, "test_name": expected["function"]["name"]},
                         {"index": 1, "test_name": "invalid_token_cls"}],
        "test_binding_line": assignments[0].lineno, "observation_line": assertions[0].lineno,
        "comparison": (
            f"invalid_token_cls is {expected['function']['name']}.__globals__['InvalidToken']"
        ),
        "precision": "strict_supported_python_ast",
    }


def prepare(registry_root: Path, output: Path) -> dict[str, Any]:
    output.mkdir(parents=True, exist_ok=False)
    expected = json.loads(EXPECTATIONS.read_text())
    dataset = FrozenDataset(registry_root, dataset=expected["dataset"],
                            inventory_revision=expected["inventory_revision"])
    with dataset.load_case(expected["case_id"]) as case:
        if case.snapshot_id != expected["snapshot_id"] or case.target_selector != expected["target_selector"]:
            raise ValueError("unexpected frozen langflow identity")
        if digest(case.masked_test.text) != expected["masked_test_sha256"]:
            raise ValueError("masked test differs from pre-registration")
        selected = select_sources(case, max_files=8, max_bytes=200_000)
        bindings = selected.get("dynamic_loader_bindings", [])
        if len(bindings) != 1:
            raise ValueError("expected exactly one authenticated dynamic loader binding")
        validate_binding(bindings[0], expected)
        invalid_token_binding = invalid_token_identity_binding(case.masked_test.text, expected)
        if invalid_token_binding != expected["invalid_token_identity"]:
            raise ValueError("InvalidToken identity provenance differs from pre-registration")
        selected_sources = selected.pop("sources")
        product = next((row for row in selected_sources
                        if row["logical_path"] == expected["product_path"]), None)
        if product is None or digest(product["text"]) != expected["product_sha256"]:
            raise ValueError("authenticated product source differs from pre-registration")
        materialized = []
        for source, role in ((selected_sources[0], "masked_test"), (product, "visible_product")):
            target = output / source["logical_path"]
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(source["text"], encoding="utf-8")
            materialized.append({"logical_path": source["logical_path"],
                                 "source_id": source["source_id"], "sha256": digest(source["text"]),
                                 "bytes": len(source["text"].encode()), "role": role})
        manifest = {
            "schema": "mock-spec.langflow-codeql-input/v1", "case_id": expected["case_id"],
            "dataset": expected["dataset"], "inventory_revision": expected["inventory_revision"],
            "snapshot_id": case.snapshot_id, "target_selector": case.target_selector,
            "masking_contract": case._masking_contract,
            "target_extraction": extract_criteria(case.masked_test.text, case.language, case.target_selector),
            "dynamic_loader_binding": bindings[0],
            "invalid_token_identity_binding": invalid_token_binding, "files": materialized,
            "adaptation": (
                "Exact visible sources materialized through FrozenDataset; loader code was not executed. "
                "The authenticated frontend binding is an explicit bridge input, not a CodeQL call-resolution result."
            ),
        }
    (output / "input-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registry-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(prepare(args.registry_root, args.output)))
