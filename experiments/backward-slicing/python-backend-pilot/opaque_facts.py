"""Build authenticated opaque-call facts from visible masked-test syntax."""

from __future__ import annotations

import ast
from collections.abc import Iterable
from dataclasses import dataclass


class OpaqueFactError(ValueError):
    pass


@dataclass(frozen=True)
class OpaqueCallFact:
    line: int
    column: int
    origin: str


def _direct_assigned_names(node: ast.AST) -> set[str]:
    names: set[str] = set()
    targets: list[ast.AST] = []
    if isinstance(node, ast.Assign):
        targets.extend(node.targets)
    elif isinstance(node, (ast.AnnAssign, ast.AugAssign, ast.NamedExpr)):
        targets.append(node.target)
    elif isinstance(node, ast.Delete):
        targets.extend(node.targets)
    elif isinstance(node, (ast.Import, ast.ImportFrom)):
        for alias in node.names:
            names.add(alias.asname or alias.name.split(".", 1)[0])
    for target in targets:
        for child in ast.walk(target):
            if isinstance(child, ast.Name) and isinstance(child.ctx, (ast.Store, ast.Del)):
                names.add(child.id)
    return names


def _contains(root: ast.AST, target: ast.AST) -> bool:
    return any(node is target for node in ast.walk(root))


def _target_binds(target: ast.AST | None, name: str) -> bool:
    return target is not None and any(
        isinstance(node, ast.Name) and node.id == name
        and isinstance(node.ctx, (ast.Store, ast.Del)) for node in ast.walk(target))


def _enclosing_binder_precedes(function: ast.FunctionDef | ast.AsyncFunctionDef,
                               name: str, call: ast.Call) -> bool:
    for node in ast.walk(function):
        if isinstance(node, (ast.For, ast.AsyncFor)):
            if _target_binds(node.target, name) and any(_contains(child, call)
                                                        for child in node.body):
                return True
        elif isinstance(node, (ast.With, ast.AsyncWith)):
            for index, item in enumerate(node.items):
                if _contains(item.context_expr, call) and any(
                        _target_binds(previous.optional_vars, name)
                        for previous in node.items[:index]):
                    return True
            if any(_target_binds(item.optional_vars, name) for item in node.items) and any(
                    _contains(child, call) for child in node.body):
                return True
        elif isinstance(node, ast.ExceptHandler):
            if node.name == name and any(_contains(child, call) for child in node.body):
                return True
        elif isinstance(node, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
            generators = node.generators
            for index, generator in enumerate(generators):
                if not _target_binds(generator.target, name):
                    continue
                later_regions: list[ast.AST] = [*generator.ifs]
                if isinstance(node, ast.DictComp):
                    later_regions.extend([node.key, node.value])
                else:
                    later_regions.append(node.elt)
                for later in generators[index + 1:]:
                    later_regions.extend([later.iter, *later.ifs])
                if any(_contains(region, call) for region in later_regions):
                    return True
    return False


def _statement_blocks(statement: ast.stmt) -> list[list[ast.stmt]]:
    blocks: list[list[ast.stmt]] = []
    for field in ("body", "orelse", "finalbody"):
        value = getattr(statement, field, None)
        if isinstance(value, list) and all(isinstance(child, ast.stmt) for child in value):
            blocks.append(value)
    if isinstance(statement, ast.Try):
        blocks.extend(handler.body for handler in statement.handlers)
    return blocks


def _ordered_block_rebinds(statements: list[ast.stmt], name: str, call: ast.Call) -> bool:
    """Find definite assignments on the executed block path leading to ``call``."""
    for statement in statements:
        if not _contains(statement, call):
            if name in _direct_assigned_names(statement):
                return True
            continue
        for child_block in _statement_blocks(statement):
            if any(_contains(child, call) for child in child_block):
                return _ordered_block_rebinds(child_block, name, call)
        return False
    return False


class _SelectedCalls(ast.NodeVisitor):
    def __init__(self) -> None:
        self.calls: list[ast.Call] = []

    def visit_Call(self, node: ast.Call) -> None:  # noqa: N802 - ast visitor API
        self.calls.append(node)
        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # noqa: N802
        return

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:  # noqa: N802
        return

    def visit_Lambda(self, node: ast.Lambda) -> None:  # noqa: N802
        return

    def visit_ClassDef(self, node: ast.ClassDef) -> None:  # noqa: N802
        return


def _calls_in_function(function: ast.FunctionDef | ast.AsyncFunctionDef) -> list[ast.Call]:
    visitor = _SelectedCalls()
    for statement in function.body:
        visitor.visit(statement)
    return visitor.calls


def _definitely_rebound_before(
    function: ast.FunctionDef | ast.AsyncFunctionDef, name: str, call: ast.Call
) -> bool:
    if _enclosing_binder_precedes(function, name, call):
        return True
    return _ordered_block_rebinds(function.body, name, call)


def build_parameter_boundary_facts(
    source: str,
    *,
    function_name: str,
    function_start_line: int,
    resolved_call_sites: Iterable[tuple[int, int]] = (),
) -> list[OpaqueCallFact]:
    """Authenticate unresolved calls through selected-function parameters.

    The visible formal declaration and exact call syntax are the provenance;
    no dataset annotation or case label is required. A parameter definitely
    rebound or deleted before the call is rejected. Calls with a visible
    resolved target are also rejected.
    """

    tree = ast.parse(source)
    matches = [
        node for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == function_name and node.lineno == function_start_line
    ]
    if len(matches) != 1:
        raise OpaqueFactError(f"selected function matches: {len(matches)}")
    function = matches[0]
    parameter_names = {
        argument.arg for argument in [
            *function.args.posonlyargs, *function.args.args, *function.args.kwonlyargs
        ]
    }
    if function.args.vararg:
        parameter_names.add(function.args.vararg.arg)
    if function.args.kwarg:
        parameter_names.add(function.args.kwarg.arg)
    resolved = set(resolved_call_sites)
    facts: list[OpaqueCallFact] = []
    for node in _calls_in_function(function):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name):
            dependency_name = node.func.value.id
        elif isinstance(node.func, ast.Name):
            dependency_name = node.func.id
        else:
            continue
        if dependency_name not in parameter_names:
            continue
        site = (node.lineno, node.col_offset + 1)
        if site in resolved:
            continue
        if _definitely_rebound_before(function, dependency_name, node):
            continue
        facts.append(OpaqueCallFact(*site, "unresolved_parameter_boundary"))
    return sorted(set(facts), key=lambda fact: (fact.line, fact.column, fact.origin))
