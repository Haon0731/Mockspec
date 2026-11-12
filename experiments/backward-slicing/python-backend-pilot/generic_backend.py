"""Run a generic, bounded CodeQL observation analysis for one frozen Python case.

The adapter is driven by the admitted case identity, visible source selection,
selected test function and extracted observations.  It contains no benchmark
case allow-list and never executes the target or dynamic loader.
"""
from __future__ import annotations

import argparse
import ast
from collections import Counter
import csv
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

from criteria import _FunctionBodyVisitor, extract_criteria  # noqa: E402
from result_contract import RELATION_KINDS, RESULT_SCHEMA_VERSION, validate_result_contract  # noqa: E402
from source_selection import select_sources  # noqa: E402
from mock_spec.adapters.frozen_dataset import FrozenDataset  # noqa: E402
from effect_projection import Observation as EffectObservation, project_effect_rows  # noqa: E402
from opaque_facts import build_parameter_boundary_facts  # noqa: E402

SCHEMA = "mockspec.generic-python-backend/v1"
QUERY = HERE / "generic-observation-flow.ql"
EFFECT_QUERY = HERE / "observation-effects.ql"
QLPACK = HERE / "qlpack.yml"
DRIVER_ENV = {"LANG": "C.UTF-8", "LC_ALL": "C.UTF-8", "PATH": os.defpath}
MAX_EFFECT_OUTPUTS = 2_000


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n")


def run_tool(command: list[str], *, cwd: Path, stdout: Path, stderr: Path,
             timeout: int = 180) -> dict[str, Any]:
    started = time.monotonic()
    try:
        with stdout.open("wb") as out, stderr.open("wb") as err:
            process = subprocess.Popen(command, cwd=cwd, env=DRIVER_ENV, stdout=out, stderr=err,
                                       start_new_session=True)
            try:
                code = process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
                raise
        return {"status": "complete", "exit_code": code,
                "seconds": time.monotonic() - started}
    except subprocess.TimeoutExpired:
        return {"status": "timeout", "exit_code": None, "seconds": time.monotonic() - started,
                "timeout_seconds": timeout}


def write_external_column(path: Path, values: list[str | int]) -> None:
    with path.open("w", newline="") as stream:
        writer = csv.writer(stream)
        for value in values:
            writer.writerow([value])


def endpoint(path: str, line: int, end_line: int) -> dict[str, Any]:
    return {"logical_path": path, "start_line": line, "end_line": max(line, end_line)}


def selector_locations(text: str, criteria: list[dict[str, Any]]) -> list[list[Any]]:
    """Resolve each unambiguous original root to an exact AST expression location."""
    nodes = list(ast.walk(ast.parse(text)))
    counts = Counter(item["line"] for item in criteria)
    facts = []
    for item in criteria:
        if counts[item["line"]] != 1:
            continue
        candidates = []
        for node in nodes:
            if getattr(node, "lineno", None) != item["line"]:
                continue
            if isinstance(node, ast.Assert) and not item.get("call"):
                candidates.append(node.test)
            elif isinstance(node, ast.Call) and item.get("call"):
                name = node.func.id if isinstance(node.func, ast.Name) else (
                    node.func.attr if isinstance(node.func, ast.Attribute) else None)
                if name == item["call"]:
                    candidates.append(node)
        if len(candidates) == 1:
            node = candidates[0]
            # Python AST offsets count UTF-8 bytes; CodeQL columns count characters.
            prefix = text.splitlines()[node.lineno - 1].encode()[:node.col_offset].decode()
            facts.append([item["line"], node.lineno, len(prefix) + 1,
                          "call" if item.get("call") else "assert"])
    return facts


def resolved_call_positions(text: str, scope: dict[str, Any],
                            bindings: list[list[Any]]) -> list[tuple[int, int]]:
    """Locate only the exact visible calls authenticated by a binding frontend."""
    tree = ast.parse(text)
    targets = [node for node in ast.walk(tree)
               if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
               and node.name == scope["symbol"] and node.lineno == scope["start_line"]]
    if len(targets) != 1:
        return []
    visitor = _FunctionBodyVisitor()
    for statement in targets[0].body:
        visitor.visit(statement)
    requested = {(int(row[0]), str(row[1])) for row in bindings}
    matches: dict[tuple[int, str], list[ast.Call]] = {key: [] for key in requested}
    for node in visitor.nodes:
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
            continue
        key = (node.lineno, node.func.id)
        if key in matches:
            matches[key].append(node)
    return sorted((nodes[0].lineno, nodes[0].col_offset + 1)
                  for nodes in matches.values() if len(nodes) == 1)


def _module_statement_binds(statement: ast.stmt, name: str) -> bool:
    class BindingVisitor(ast.NodeVisitor):
        found = False

        def visit_Name(self, node: ast.Name) -> None:  # noqa: N802
            if node.id == name and isinstance(node.ctx, (ast.Store, ast.Del)):
                self.found = True

        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # noqa: N802
            self.found = self.found or node.name == name

        def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:  # noqa: N802
            self.found = self.found or node.name == name

        def visit_ClassDef(self, node: ast.ClassDef) -> None:  # noqa: N802
            self.found = self.found or node.name == name

        def visit_Import(self, node: ast.Import) -> None:  # noqa: N802
            self.found = self.found or any(
                (alias.asname or alias.name.split(".")[0]) == name for alias in node.names)

        def visit_ImportFrom(self, node: ast.ImportFrom) -> None:  # noqa: N802
            self.found = self.found or any(
                (alias.asname or alias.name) == name for alias in node.names)

        def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:  # noqa: N802
            self.found = self.found or node.name == name
            for child in node.body:
                self.visit(child)

    visitor = BindingVisitor()
    visitor.visit(statement)
    return visitor.found


def _async_runner_names(tree: ast.Module, target_nodes: list[ast.AST],
                        target: ast.FunctionDef | ast.AsyncFunctionDef) -> set[str]:
    imports = [(statement, alias.asname or alias.name) for statement in tree.body
               if isinstance(statement, ast.Import) for alias in statement.names
               if alias.name == "asyncio"]
    names = {name for imported_statement, name in imports
             if not any(statement is not imported_statement
                        and _module_statement_binds(statement, name) for statement in tree.body)}
    parameters = {argument.arg for argument in (*target.args.posonlyargs, *target.args.args,
                                                 *target.args.kwonlyargs)}
    if target.args.vararg:
        parameters.add(target.args.vararg.arg)
    if target.args.kwarg:
        parameters.add(target.args.kwarg.arg)
    return {name for name in names if name not in parameters and not any(
        isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)) and node.id == name
        for node in target_nodes)}


def _call_result_mode(call: ast.Call, *, is_async: bool, tree: ast.Module,
                      target_nodes: list[ast.AST],
                      target: ast.FunctionDef | ast.AsyncFunctionDef) -> tuple[int, str]:
    """Describe the only wrappers that turn a coroutine call into an observed value."""
    if not is_async:
        return 0, ""
    for node in target_nodes:
        if isinstance(node, ast.Await) and node.value is call:
            return 1, ""
    runners = _async_runner_names(tree, target_nodes, target)
    wrappers: list[str] = []
    for node in target_nodes:
        if (not isinstance(node, ast.Call) or len(node.args) != 1 or node.args[0] is not call
                or node.keywords or isinstance(node.args[0], ast.Starred)):
            continue
        function = node.func
        if not isinstance(function, ast.Attribute) or function.attr != "run":
            continue
        receiver = function.value
        if isinstance(receiver, ast.Name) and receiver.id in runners:
            wrappers.append(receiver.id)
    if len(wrappers) == 1:
        return 2, wrappers[0]
    return -1, ""


def _assigned_name(statement: ast.stmt) -> tuple[str, ast.AST] | None:
    if isinstance(statement, ast.Assign) and len(statement.targets) == 1:
        target = statement.targets[0]
        if isinstance(target, ast.Name):
            return target.id, statement.value
    if (isinstance(statement, ast.AnnAssign) and isinstance(statement.target, ast.Name)
            and statement.value is not None):
        return statement.target.id, statement.value
    return None


def _name(node: ast.AST | None, expected: str) -> bool:
    return isinstance(node, ast.Name) and node.id == expected


def _call_attr(node: ast.AST, receiver: str, attribute: str) -> bool:
    return (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and node.func.attr == attribute and _name(node.func.value, receiver))


def _support_loader_shape(
        function: ast.FunctionDef | ast.AsyncFunctionDef,
        module: ast.Module) -> tuple[bool, str, bool]:
    """Recognize one connected, side-effect-bounded source-loader dataflow chain."""
    parameters = [argument.arg for argument in (*function.args.posonlyargs, *function.args.args)]
    if len(parameters) < 2 or function.decorator_list:
        return False, "helper_signature_or_decorator", False
    module_path, function_name = parameters[:2]
    ast_imports = [statement for statement in module.body if isinstance(statement, ast.Import)
                   and any(alias.name == "ast" and alias.asname is None
                           for alias in statement.names)]
    if len(ast_imports) != 1 or any(statement is not ast_imports[0]
                                    and _module_statement_binds(statement, "ast")
                                    for statement in module.body):
        return False, "helper_ast_import_not_stable", False
    critical_builtins = {"open", "compile", "exec", "dict"}
    if (critical_builtins.intersection(parameters)
            or any(_module_statement_binds(statement, name) for statement in module.body
                   for name in critical_builtins)
            or any(
            isinstance(node, ast.Name) and node.id in critical_builtins
            and isinstance(node.ctx, (ast.Store, ast.Del)) for node in ast.walk(function))):
        return False, "helper_builtin_rebound", False
    if any(isinstance(statement, ast.AnnAssign) and statement.value is None
           for statement in function.body):
        return False, "helper_annotation_without_value", False
    if any(isinstance(statement, (ast.For, ast.AsyncFor, ast.While, ast.Try,
                                  ast.With, ast.AsyncWith, ast.Match))
           for statement in function.body):
        return False, "helper_control_flow_unsupported", False
    indexed_assignments = [(index, row[0], row[1]) for index, statement in enumerate(function.body)
                           if (row := _assigned_name(statement))]
    assignments = [(name, value) for _, name, value in indexed_assignments]
    if len({name for name, _ in assignments}) != len(assignments):
        return False, "helper_critical_name_overwritten", False
    assigned_values = dict(assignments)
    assignment_positions = {name: index for index, name, _ in indexed_assignments}

    for index, _, value in indexed_assignments:
        locally_bound = {generator.target.id for generator in ast.walk(value)
                         if isinstance(generator, ast.comprehension)
                         and isinstance(generator.target, ast.Name)}
        if any(part.id not in locally_bound and part.id in assignment_positions
               and assignment_positions[part.id] >= index
               for part in ast.walk(value) if isinstance(part, ast.Name)):
            return False, "helper_dataflow_out_of_order", False

    def depends_on(node: ast.AST, name: str, seen: frozenset[str] = frozenset()) -> bool:
        for part in ast.walk(node):
            if not isinstance(part, ast.Name):
                continue
            if part.id == name:
                return True
            if part.id in assigned_values and part.id not in seen and depends_on(
                    assigned_values[part.id], name, seen | {part.id}):
                return True
        return False

    reads = [(name, value) for name, value in assignments
             if isinstance(value, ast.Call) and isinstance(value.func, ast.Attribute)
             and value.func.attr == "read" and isinstance(value.func.value, ast.Call)
             and _name(value.func.value.func, "open") and value.func.value.args
             and _name(value.func.value.args[0], module_path)]
    if len(reads) != 1:
        return False, "helper_read_chain", False
    source_name = reads[0][0]

    parses = [(name, value) for name, value in assignments
              if isinstance(value, ast.Call) and isinstance(value.func, ast.Attribute)
              and value.func.attr == "parse" and _name(value.func.value, "ast")
              and len(value.args) == 1
              and _name(value.args[0], source_name)]
    if len(parses) != 1:
        return False, "helper_parse_chain", False
    tree_name = parses[0][0]
    if assignment_positions[tree_name] <= assignment_positions[source_name]:
        return False, "helper_parse_before_read", False

    comprehensions = [(name, value) for name, value in assignments
                      if isinstance(value, ast.ListComp) and len(value.generators) == 1]
    valid_comprehensions: list[tuple[str, ast.ListComp, str]] = []
    for name, value in comprehensions:
        generator = value.generators[0]
        if (not isinstance(generator.target, ast.Name)
                or not isinstance(generator.iter, ast.Call)
                or not isinstance(generator.iter.func, ast.Attribute)
                or generator.iter.func.attr != "walk" or not _name(generator.iter.func.value, "ast")
                or len(generator.iter.args) != 1
                or not _name(generator.iter.args[0], tree_name)
                or not _name(value.elt, generator.target.id)):
            continue
        node_name = generator.target.id
        has_type = any(
            isinstance(part, ast.Call) and _name(part.func, "isinstance")
            and part.args and _name(part.args[0], node_name)
            and {attribute.attr for attribute in ast.walk(part)
                 if isinstance(attribute, ast.Attribute)} >= {"FunctionDef", "AsyncFunctionDef"}
            for condition in generator.ifs for part in ast.walk(condition))
        has_name = any(
            isinstance(part, ast.Compare) and isinstance(part.left, ast.Attribute)
            and _name(part.left.value, node_name) and part.left.attr == "name"
            and any(_name(comparator, function_name) for comparator in part.comparators)
            for condition in generator.ifs for part in ast.walk(condition))
        if has_type and has_name:
            valid_comprehensions.append((name, value, node_name))
    if len(valid_comprehensions) != 1:
        return False, "helper_walk_and_match_chain", False
    functions_name = valid_comprehensions[0][0]
    if assignment_positions[functions_name] <= assignment_positions[tree_name]:
        return False, "helper_walk_before_parse", False

    selected = [(name, value) for name, value in assignments
                if isinstance(value, ast.Subscript) and _name(value.value, functions_name)
                and isinstance(value.slice, ast.UnaryOp) and isinstance(value.slice.op, ast.USub)
                and isinstance(value.slice.operand, ast.Constant) and value.slice.operand.value == 1]
    if len(selected) != 1:
        return False, "helper_unique_selected_node_chain", False
    selected_name = selected[0][0]
    if assignment_positions[selected_name] <= assignment_positions[functions_name]:
        return False, "helper_select_before_walk", False
    guards = [(index, statement) for index, statement in enumerate(function.body)
              if isinstance(statement, ast.If)]
    for index, guard in guards:
        if not (assignment_positions[functions_name] < index
                < assignment_positions[selected_name]
                and isinstance(guard.test, ast.UnaryOp) and isinstance(guard.test.op, ast.Not)
                and _name(guard.test.operand, functions_name)
                and not guard.orelse and len(guard.body) == 1
                and isinstance(guard.body[0], ast.Raise)):
            return False, "helper_empty_list_guard_unsupported", False
    if len(guards) > 1:
        return False, "helper_empty_list_guard_not_unique", False

    split_names = {name for name, value in assignments
                   if isinstance(value, ast.Call)
                   and _call_attr(value, source_name, "splitlines") and not value.args}

    def selected_lineno(node: ast.AST) -> bool:
        if isinstance(node, ast.Name) and node.id in assigned_values:
            return selected_lineno(assigned_values[node.id])
        if (isinstance(node, ast.Attribute) and node.attr == "lineno"
                and _name(node.value, selected_name)):
            return True
        if not isinstance(node, ast.IfExp):
            return False
        decorator_lineno = any(
            isinstance(part, ast.Attribute) and part.attr == "lineno"
            and isinstance(part.value, ast.Subscript)
            and isinstance(part.value.value, ast.Attribute)
            and part.value.value.attr == "decorator_list"
            and _name(part.value.value.value, selected_name)
            for part in ast.walk(node.body))
        fallback_lineno = (isinstance(node.orelse, ast.Attribute)
                           and node.orelse.attr == "lineno"
                           and _name(node.orelse.value, selected_name))
        condition_mentions_decorators = any(
            isinstance(part, ast.Attribute) and part.attr == "decorator_list"
            and _name(part.value, selected_name) for part in ast.walk(node.test))
        return decorator_lineno and fallback_lineno and condition_mentions_decorators

    def supported_body_slice(value: ast.AST) -> bool:
        candidate = value
        if (isinstance(value, ast.Call) and isinstance(value.func, ast.Attribute)
                and _name(value.func.value, "textwrap") and value.func.attr == "dedent"
                and len(value.args) == 1):
            textwrap_imports = [statement for statement in module.body
                                if isinstance(statement, ast.Import)
                                and any(alias.name == "textwrap" and alias.asname is None
                                        for alias in statement.names)]
            if len(textwrap_imports) != 1 or any(
                    statement is not textwrap_imports[0]
                    and _module_statement_binds(statement, "textwrap")
                    for statement in module.body):
                return False
            candidate = value.args[0]
        if (not isinstance(candidate, ast.Call)
                or not isinstance(candidate.func, ast.Attribute)
                or candidate.func.attr != "join" or len(candidate.args) != 1
                or not isinstance(candidate.args[0], ast.Subscript)):
            return False
        subscript = candidate.args[0]
        receiver = candidate.func.value
        if not isinstance(receiver, ast.Constant) or receiver.value != "\n":
            return False
        if not isinstance(subscript.value, ast.Name) or subscript.value.id not in split_names:
            return False
        if not isinstance(subscript.slice, ast.Slice):
            return False
        lower, upper = subscript.slice.lower, subscript.slice.upper
        return (isinstance(lower, ast.BinOp) and isinstance(lower.op, ast.Sub)
                and isinstance(lower.right, ast.Constant) and lower.right.value == 1
                and selected_lineno(lower.left)
                and isinstance(upper, ast.Attribute) and upper.attr == "end_lineno"
                and _name(upper.value, selected_name))

    body_values = [(name, value) for name, value in assignments if supported_body_slice(value)]
    if len(body_values) != 1:
        return False, "helper_source_slice_chain", False
    body_name = body_values[0][0]
    if assignment_positions[body_name] <= assignment_positions[selected_name]:
        return False, "helper_slice_before_select", False

    namespace_values = [(name, value) for name, value in assignments
                        if isinstance(value, (ast.Dict, ast.Call))
                        and (isinstance(value, ast.Dict) or _name(value.func, "dict"))]
    if len(namespace_values) != 1:
        return False, "helper_namespace_chain", False
    namespace_name = namespace_values[0][0]

    expression_calls = [node.value for node in function.body if isinstance(node, ast.Expr)
                        and isinstance(node.value, ast.Call)]
    namespace_initializers = [call for call in expression_calls
                              if isinstance(call.func, ast.Attribute)
                              and _name(call.func.value, namespace_name)
                              and call.func.attr == "setdefault"]
    if len(namespace_initializers) > 1 or any(
            not (len(call.args) == 2 and not call.keywords
                 and isinstance(call.args[0], ast.Constant)
                 and call.args[0].value == "__name__"
                 and isinstance(call.args[1], ast.Constant)
                 and isinstance(call.args[1].value, str))
            for call in namespace_initializers):
        return False, "helper_namespace_initializer_unsupported", False

    exec_calls = [node.value for node in function.body if isinstance(node, ast.Expr)
                  and isinstance(node.value, ast.Call) and _name(node.value.func, "exec")]
    if any(call not in exec_calls and call not in namespace_initializers
           for call in expression_calls):
        return False, "helper_side_effect_call_unsupported", False
    if len(exec_calls) != 1 or len(exec_calls[0].args) != 2:
        return False, "helper_exec_shape", False
    exec_index = next(index for index, statement in enumerate(function.body)
                      if isinstance(statement, ast.Expr) and statement.value is exec_calls[0])
    if exec_index <= max(assignment_positions[body_name], assignment_positions[namespace_name]):
        return False, "helper_exec_before_inputs", False
    compiled = exec_calls[0].args[0]
    def supported_prefix(node: ast.AST) -> bool:
        if isinstance(node, ast.Name) and node.id in assigned_values:
            return supported_prefix(assigned_values[node.id])
        if not isinstance(node, ast.BinOp) or not isinstance(node.op, ast.Add):
            return False
        padding = node.right
        return (isinstance(node.left, ast.Constant)
                and node.left.value == "from __future__ import annotations\n"
                and isinstance(padding, ast.BinOp) and isinstance(padding.op, ast.Mult)
                and isinstance(padding.left, ast.Constant) and padding.left.value == "\n"
                and isinstance(padding.right, ast.BinOp)
                and isinstance(padding.right.op, ast.Sub)
                and selected_lineno(padding.right.left)
                and isinstance(padding.right.right, ast.Constant)
                and padding.right.right.value == 2)

    def supported_compile_input(node: ast.AST) -> bool:
        if _name(node, body_name):
            return True
        return (isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add)
                and supported_prefix(node.left) and _name(node.right, body_name))

    if (not isinstance(compiled, ast.Call) or not _name(compiled.func, "compile")
            or len(compiled.args) != 3 or not _name(compiled.args[1], module_path)
            or not isinstance(compiled.args[2], ast.Constant) or compiled.args[2].value != "exec"
            or not supported_compile_input(compiled.args[0])):
        return False, "helper_compile_chain", False
    if not _name(exec_calls[0].args[1], namespace_name):
        return False, "helper_exec_namespace_chain", False

    returns = [node for node in function.body if isinstance(node, ast.Return)]
    if len(returns) != 1 or not isinstance(returns[0].value, ast.Subscript):
        return False, "helper_return_shape", False
    returned = returns[0].value
    return_index = next(index for index, statement in enumerate(function.body)
                        if statement is returns[0])
    if return_index <= exec_index:
        return False, "helper_return_before_exec", False
    if not _name(returned.value, namespace_name) or not _name(returned.slice, function_name):
        return False, "helper_return_namespace_chain", False
    start_line_names = {argument.arg for argument in (*function.args.posonlyargs,
                                                       *function.args.args,
                                                       *function.args.kwonlyargs)
                        if argument.arg == "start_line"}
    supports_start_line = bool(start_line_names) and any(
        isinstance(part, ast.Compare) and any(_name(item, "start_line")
                                              for item in ast.walk(part))
        and any(isinstance(item, ast.Attribute) and item.attr == "lineno"
                and _name(item.value, valid_comprehensions[0][2]) for item in ast.walk(part))
        for condition in valid_comprehensions[0][1].generators[0].ifs
        for part in ast.walk(condition))
    return True, "authenticated", supports_start_line


def _module_constants(tree: ast.Module) -> dict[str, str | int]:
    values: dict[str, str | int] = {}
    for statement in tree.body:
        if not isinstance(statement, (ast.Assign, ast.AnnAssign)):
            continue
        value = statement.value
        targets = statement.targets if isinstance(statement, ast.Assign) else [statement.target]
        literal = value.value if isinstance(value, ast.Constant) else None
        if isinstance(literal, (str, int)) and not isinstance(literal, bool):
            for target in targets:
                if isinstance(target, ast.Name) and not any(
                        other is not statement and _module_statement_binds(other, target.id)
                        for other in tree.body):
                    values[target.id] = literal
    return values


def _constant_argument(node: ast.AST, constants: dict[str, str | int]) -> str | int | None:
    literal = node.value if isinstance(node, ast.Constant) else None
    if isinstance(literal, (str, int)) and not isinstance(literal, bool):
        return literal
    if isinstance(node, ast.Name):
        return constants.get(node.id)
    return None


def support_loader_facts(sources: list[dict[str, Any]], selection: dict[str, Any],
                         scope: dict[str, Any],
                         diagnostics: list[dict[str, Any]] | None = None) -> list[list[Any]]:
    """Bind an explicitly imported, structurally verified visible loader helper."""
    test = next(source for source in sources if source["source_id"] == "test")
    tree = ast.parse(test["text"])
    targets = [node for node in ast.walk(tree)
               if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
               and node.lineno == scope["start_line"] and node.name == scope["symbol"]]
    if len(targets) != 1:
        return []
    target = targets[0]
    visitor = _FunctionBodyVisitor()
    for statement in target.body:
        visitor.visit(statement)
    target_nodes = visitor.nodes
    constants = _module_constants(tree)
    selected_by_path = {source["logical_path"]: source for source in sources}
    support_paths = {row["candidates"][0] for row in selection.get("evidence", [])
                     if row.get("stage") == "loader_support"
                     and len(row.get("candidates", [])) == 1}
    facts: list[list[Any]] = []
    for support_path in support_paths:
        support = selected_by_path.get(support_path)
        if support is None or support.get("selection_role") != "loader_support":
            continue
        support_tree = ast.parse(support["text"])
        imported_names: list[tuple[ast.ImportFrom, str]] = []
        for statement in tree.body:
            if not isinstance(statement, ast.ImportFrom):
                continue
            module_suffix = (statement.module or "").replace(".", "/") + ".py"
            if statement.level:
                parent = Path(test["logical_path"]).parent
                for _ in range(statement.level - 1):
                    parent = parent.parent
                imported_path = (parent / module_suffix).as_posix()
                if imported_path != support_path:
                    continue
            elif not support_path.endswith("/" + module_suffix) and support_path != module_suffix:
                continue
            for imported in statement.names:
                local_name = imported.asname or imported.name
                definitions = [node for node in support_tree.body
                               if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                               and node.name == imported.name]
                shape = (_support_loader_shape(definitions[0], support_tree)
                         if len(definitions) == 1
                         else (False, "helper_definition_not_unique", False))
                if shape[0]:
                    imported_names.append((statement, local_name))
                elif diagnostics is not None:
                    for call in target_nodes:
                        if (isinstance(call, ast.Call) and isinstance(call.func, ast.Name)
                                and call.func.id == local_name):
                            diagnostics.append({"scope": "visible_loader_binding",
                                                "path": test["logical_path"],
                                                "line": call.lineno, "name": local_name,
                                                "stage": shape[1]})
        for authenticating_import, local_name in imported_names:
            loader_calls = [node for node in target_nodes if isinstance(node, ast.Call)
                            and isinstance(node.func, ast.Name) and node.func.id == local_name]
            def reject(call: ast.Call, stage: str) -> None:
                if diagnostics is not None:
                    diagnostics.append({"scope": "visible_loader_binding",
                                        "path": test["logical_path"], "line": call.lineno,
                                        "name": local_name, "stage": stage})
            if any(_module_statement_binds(statement, local_name)
                   for statement in tree.body
                   if statement is not authenticating_import):
                for call in loader_calls:
                    reject(call, "helper_import_name_rebound_at_module_scope")
                continue
            if any(isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del))
                   and node.id == local_name for node in target_nodes):
                for call in loader_calls:
                    reject(call, "helper_import_name_rebound_in_target_scope")
                continue
            for loader_call in loader_calls:
                if (len(loader_call.args) != 2
                        or any(isinstance(argument, ast.Starred)
                               for argument in loader_call.args)):
                    reject(loader_call, "loader_call_positional_shape_unsupported")
                    continue
                path = _constant_argument(loader_call.args[0], constants)
                function_name = _constant_argument(loader_call.args[1], constants)
                start_line: int | None = None
                unsupported_keyword = False
                seen_keywords: set[str | None] = set()
                for keyword in loader_call.keywords:
                    if keyword.arg in seen_keywords:
                        unsupported_keyword = True
                        continue
                    seen_keywords.add(keyword.arg)
                    if keyword.arg == "start_line":
                        value = _constant_argument(keyword.value, constants)
                        if type(value) is not int:
                            unsupported_keyword = True
                        else:
                            start_line = value
                    elif keyword.arg not in {"namespace"}:
                        unsupported_keyword = True
                if unsupported_keyword or type(path) is not str or type(function_name) is not str:
                    reject(loader_call, "loader_call_arguments_not_static")
                    continue
                helper_definition = next(
                    definition for definition in support_tree.body
                    if isinstance(definition, (ast.FunctionDef, ast.AsyncFunctionDef))
                    and definition.name == next(
                        imported.name for imported in authenticating_import.names
                        if (imported.asname or imported.name) == local_name))
                if start_line is not None and not _support_loader_shape(
                        helper_definition, support_tree)[2]:
                    if diagnostics is not None:
                        diagnostics.append({"scope": "visible_loader_binding",
                                            "path": test["logical_path"],
                                            "line": loader_call.lineno, "name": local_name,
                                            "stage": "helper_start_line_not_authenticated"})
                    continue
                product = selected_by_path.get(path)
                if product is None or product.get("selection_role") != "candidate_product":
                    reject(loader_call, "loader_product_source_not_selected")
                    continue
                product_tree = ast.parse(product["text"])
                definitions = [node for node in ast.walk(product_tree)
                               if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                               and node.name == function_name
                               and (start_line is None or node.lineno == start_line)
                               and not node.decorator_list]
                if len(definitions) != 1:
                    reject(loader_call, "loader_product_definition_not_unique")
                    continue
                definition = definitions[0]
                assignments = [node for node in target_nodes
                               if isinstance(node, (ast.Assign, ast.AnnAssign))
                               and node.value is loader_call]
                if len(assignments) != 1:
                    reject(loader_call, "loader_result_assignment_not_unique")
                    continue
                assigned = assignments[0]
                assignment_target = (assigned.targets[0] if isinstance(assigned, ast.Assign)
                                     and len(assigned.targets) == 1 else assigned.target
                                     if isinstance(assigned, ast.AnnAssign) else None)
                if not isinstance(assignment_target, ast.Name):
                    reject(loader_call, "loader_result_assignment_target_unsupported")
                    continue
                bound_name = assignment_target.id
                calls = [node for node in target_nodes if isinstance(node, ast.Call)
                         and isinstance(node.func, ast.Name) and node.func.id == bound_name
                         and node.lineno > assigned.lineno]
                rebound = any(isinstance(node, ast.Name)
                              and isinstance(node.ctx, (ast.Store, ast.Del))
                              and node.id == bound_name and node is not assignment_target
                              and assigned.lineno < node.lineno <= max(
                                  (call.lineno for call in calls), default=assigned.lineno)
                              for node in target_nodes)
                if rebound:
                    reject(loader_call, "loader_result_callable_rebound")
                    continue
                for call in calls:
                    mode, runner = _call_result_mode(
                        call, is_async=isinstance(definition, ast.AsyncFunctionDef), tree=tree,
                        target_nodes=target_nodes, target=target)
                    if mode == -1 and diagnostics is not None:
                        diagnostics.append({"scope": "visible_loader_binding",
                                            "path": test["logical_path"], "line": call.lineno,
                                            "name": bound_name,
                                            "stage": "coroutine_completion_unsupported"})
                    facts.append([call.lineno, bound_name, product["logical_path"], definition.lineno,
                                  definition.name, getattr(definition, "end_lineno", definition.lineno),
                                  mode, runner])
    counts = Counter((row[0], row[1]) for row in facts)
    if diagnostics is not None:
        for row in facts:
            if counts[(row[0], row[1])] != 1:
                diagnostics.append({"scope": "visible_loader_binding",
                                    "path": test["logical_path"], "line": row[0],
                                    "name": row[1], "stage": "loader_call_binding_not_unique"})
    return [row for row in facts if counts[(row[0], row[1])] == 1]


def loader_facts(sources: list[dict[str, Any]], selection: dict[str, Any],
                 scope: dict[str, Any],
                 diagnostics: list[dict[str, Any]] | None = None) -> list[list[Any]]:
    """Qualify existing authenticated loader evidence at unique selected-test calls."""
    test = next(source for source in sources if source["source_id"] == "test")
    tree = ast.parse(test["text"])
    targets = [node for node in ast.walk(tree)
               if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
               and node.lineno == scope["start_line"] and node.name == scope["symbol"]]
    if len(targets) != 1:
        return []
    target = targets[0]
    visitor = _FunctionBodyVisitor()
    for statement in target.body:
        visitor.visit(statement)
    target_nodes = visitor.nodes
    facts = []
    for binding in selection.get("dynamic_loader_bindings", []):
        def reject(stage: str) -> None:
            if diagnostics is not None:
                diagnostics.extend({"scope": "visible_loader_binding",
                                    "path": test["logical_path"], "line": call["line"],
                                    "name": call["name"], "stage": stage}
                                   for call in binding.get("call_sites", []))
        if binding["status"] != "resolved":
            reject(f"loader_selection_{binding['status']}")
            continue
        loader_name = binding.get("loader")
        loaders = [node for node in tree.body if isinstance(node, ast.FunctionDef)
                   and node.name == loader_name and not node.decorator_list]
        if len(loaders) != 1 or any(
                isinstance(node, ast.Name) and node.id == loader_name
                and isinstance(node.ctx, (ast.Store, ast.Del)) for node in ast.walk(tree)):
            reject("loader_definition_missing_or_rebound")
            continue
        definition = binding["function_definition"]
        product = next((source for source in sources
                        if source["source_id"] == definition["source_id"]
                        and source["logical_path"] == definition["logical_path"]), None)
        if product is None:
            reject("loader_product_source_missing")
            continue
        functions = [node for node in ast.parse(product["text"]).body
                     if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                     and node.name == definition["name"]
                     and node.lineno == definition["start_line"]
                     and node.end_lineno == definition["end_line"]]
        if len(functions) != 1 or functions[0].decorator_list:
            reject("loader_product_definition_mismatch")
            continue
        is_async = isinstance(functions[0], ast.AsyncFunctionDef)
        # Existing loader recovery scans the module. Only accept names initialized
        # inside this target, never a same-name binding in another test.
        bound: set[str] = set()
        for item in sorted(binding["test_bindings"], key=lambda row: row["line"]):
            stores = [node for node in target_nodes if isinstance(node, ast.Name)
                      and isinstance(node.ctx, (ast.Store, ast.Del)) and node.id == item["name"]]
            if len(stores) != 1 or stores[0].lineno != item["line"]:
                continue
            assignments = [node for node in target_nodes
                           if isinstance(node, (ast.Assign, ast.AnnAssign)) and node.lineno == item["line"]
                           and stores[0] in ast.walk(node)]
            if len(assignments) != 1:
                continue
            assignment = assignments[0]
            targets_at_line = assignment.targets if isinstance(assignment, ast.Assign) else [assignment.target]
            if len(targets_at_line) != 1:
                continue
            target_at_line = targets_at_line[0]
            rhs = assignment.value
            if isinstance(rhs, ast.Name) and rhs.id in bound and isinstance(target_at_line, ast.Name):
                bound.add(item["name"])
            elif isinstance(rhs, ast.Call) and isinstance(rhs.func, ast.Name) and rhs.func.id == loader_name:
                slots = list(target_at_line.elts) if isinstance(target_at_line, (ast.Tuple, ast.List)) else [target_at_line]
                index = binding.get("return_index", 0)
                if index < len(slots) and isinstance(slots[index], ast.Name) and slots[index].id == item["name"]:
                    bound.add(item["name"])
        allowed_stores = {(item["name"], item["line"]) for item in binding["test_bindings"]}
        if any(isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del))
               and node.id in bound and (node.id, node.lineno) not in allowed_stores
               for node in target_nodes):
            reject("loader_callable_rebound")
            continue
        for call in binding["call_sites"]:
            if call["name"] not in bound:
                if diagnostics is not None:
                    diagnostics.append({"scope": "visible_loader_binding",
                                        "path": test["logical_path"], "line": call["line"],
                                        "name": call["name"],
                                        "stage": "loader_test_binding_not_authenticated"})
                continue
            matches = [node for node in target_nodes if isinstance(node, ast.Call)
                       and isinstance(node.func, ast.Name) and node.func.id == call["name"]
                       and node.lineno == call["line"]]
            if len(matches) == 1:
                mode, runner = _call_result_mode(matches[0], is_async=is_async, tree=tree,
                                                 target_nodes=target_nodes, target=target)
                facts.append([call["line"], call["name"], definition["logical_path"],
                              definition["start_line"], definition["name"], definition["end_line"],
                              mode, runner])
            elif diagnostics is not None:
                diagnostics.append({"scope": "visible_loader_binding",
                                    "path": test["logical_path"], "line": call["line"],
                                    "name": call["name"],
                                    "stage": "loader_call_site_not_unique"})
    counts = Counter((row[0], row[1]) for row in facts)
    return [row for row in facts if counts[(row[0], row[1])] == 1]


def _module_relative_paths(module: str) -> tuple[str, str]:
    relative = module.replace(".", "/")
    return f"{relative}.py", f"{relative}/__init__.py"


def _authenticated_source_roots(selection: dict[str, Any]) -> set[str]:
    roots: set[str] = set()
    for row in selection.get("evidence", []):
        module = row.get("hint")
        candidates = row.get("candidates", [])
        if (row.get("stage") != "masked_test" or row.get("hint_kind") != "python_import"
                or not isinstance(module, str) or "." not in module
                or len(candidates) != 1):
            continue
        candidate = candidates[0]
        for relative in _module_relative_paths(module):
            suffix = "/" + relative
            if candidate.endswith(suffix):
                roots.add(candidate[:-len(suffix)])
    return roots


def _module_path_authenticated(module: str, logical_path: str,
                               source_roots: set[str]) -> bool:
    return any(
        logical_path == relative
        or any(logical_path == f"{root}/{relative}" for root in source_roots)
        for relative in _module_relative_paths(module)
    )


def import_facts(sources: list[dict[str, Any]], selection: dict[str, Any],
                 scope: dict[str, Any],
                 diagnostics: list[dict[str, Any]] | None = None) -> list[list[Any]]:
    """Bind unique direct ``from module import function`` calls using visible source only."""
    test = next(source for source in sources if source["source_id"] == "test")
    tree = ast.parse(test["text"])
    targets = [node for node in ast.walk(tree)
               if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
               and node.lineno == scope["start_line"] and node.name == scope["symbol"]]
    if len(targets) != 1:
        return []
    target = targets[0]
    visitor = _FunctionBodyVisitor()
    for statement in target.body:
        visitor.visit(statement)
    target_nodes = visitor.nodes
    selected_by_path = {source["logical_path"]: source for source in sources}
    source_roots = _authenticated_source_roots(selection)
    exact_modules: dict[str, str] = {}
    rejected_modules: set[str] = set()
    for row in selection.get("evidence", []):
        candidates = row.get("candidates", [])
        module = row.get("hint")
        if (row.get("stage") != "masked_test" or row.get("hint_kind") != "python_import"
                or not isinstance(module, str) or len(candidates) != 1
                or candidates[0] not in selected_by_path):
            continue
        if _module_path_authenticated(module, candidates[0], source_roots):
            exact_modules[module] = candidates[0]
        else:
            rejected_modules.add(module)
    facts: list[list[Any]] = []
    parameters = {argument.arg for argument in (*target.args.posonlyargs, *target.args.args,
                                                 *target.args.kwonlyargs)}
    if target.args.vararg:
        parameters.add(target.args.vararg.arg)
    if target.args.kwarg:
        parameters.add(target.args.kwarg.arg)
    for statement in tree.body:
        if not isinstance(statement, ast.ImportFrom) or statement.level or statement.module is None:
            continue
        if statement.module in rejected_modules:
            if diagnostics is not None:
                for imported in statement.names:
                    local_name = imported.asname or imported.name
                    diagnostics.extend({"scope": "visible_import_binding",
                                        "path": test["logical_path"], "line": call.lineno,
                                        "name": local_name,
                                        "stage": "import_module_path_not_authenticated"}
                                       for call in target_nodes if isinstance(call, ast.Call)
                                       and isinstance(call.func, ast.Name)
                                       and call.func.id == local_name)
            continue
        product_path = exact_modules.get(statement.module)
        if product_path is None:
            continue
        product = selected_by_path[product_path]
        product_tree = ast.parse(product["text"])
        for imported in statement.names:
            if imported.name == "*":
                continue
            local_name = imported.asname or imported.name
            calls = [node for node in target_nodes if isinstance(node, ast.Call)
                     and isinstance(node.func, ast.Name) and node.func.id == local_name]
            def reject(stage: str) -> None:
                if diagnostics is not None:
                    diagnostics.extend({"scope": "visible_import_binding",
                                        "path": test["logical_path"], "line": call.lineno,
                                        "name": local_name, "stage": stage} for call in calls)
            if product.get("selection_role") != "candidate_product":
                reject("import_product_source_not_candidate")
                continue
            if local_name in parameters:
                reject("import_name_is_parameter")
                continue
            if any(other is not statement and _module_statement_binds(other, local_name)
                   for other in tree.body):
                reject("import_name_rebound_at_module_scope")
                continue
            definitions = [node for node in product_tree.body
                           if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                           and node.name == imported.name and not node.decorator_list]
            if len(definitions) != 1:
                reject("imported_definition_not_unique")
                continue
            definition = definitions[0]
            if any(
                (isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del))
                 and node.id == local_name)
                or (isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                    and node.name == local_name)
                or (isinstance(node, (ast.Import, ast.ImportFrom))
                    and any((alias.asname or alias.name.split(".")[-1]) == local_name
                            for alias in node.names))
                for node in target_nodes
            ):
                reject("import_name_rebound_in_target_scope")
                continue
            for call in calls:
                mode, runner = _call_result_mode(
                    call, is_async=isinstance(definition, ast.AsyncFunctionDef), tree=tree,
                    target_nodes=target_nodes, target=target)
                if mode == -1 and isinstance(definition, ast.AsyncFunctionDef):
                    if diagnostics is not None:
                        diagnostics.append({"scope": "visible_import_binding",
                                            "path": test["logical_path"], "line": call.lineno,
                                            "name": local_name,
                                            "stage": "coroutine_completion_unsupported"})
                facts.append([call.lineno, local_name, product_path, definition.lineno,
                              definition.name, getattr(definition, "end_lineno", definition.lineno),
                              mode, runner])
    counts = Counter((row[0], row[1]) for row in facts)
    return [row for row in facts if counts[(row[0], row[1])] == 1]


def summarize_entry_evidence(criteria: list[dict[str, Any]], rows: list[dict[str, str]]) -> dict[str, Any]:
    mapped_counts = Counter(int(row["observationLine"]) for row in rows
                            if row["relation"] == "observation")
    criterion_counts = Counter(item["line"] for item in criteria)
    mapped_lines = {line for line, count in mapped_counts.items()
                    if count == 1 and criterion_counts[line] == 1}
    observations_by_line: dict[int, list[str]] = {}
    for criterion in criteria:
        observations_by_line.setdefault(criterion["line"], []).append(criterion["criterion_id"])
    call_binding_relations = {"resolved_test_product_call", "visible_loader_product_call",
                              "resolved_async_test_product_call",
                              "resolved_raw_async_test_product_call",
                              "visible_async_loader_product_call", "visible_import_product_call",
                              "visible_async_import_product_call",
                              "visible_raw_async_loader_product_call",
                              "visible_raw_async_import_product_call"}
    influence_relations = {"resolved_product_result_to_observation",
                           "async_resolved_product_result_to_observation",
                           "loader_product_result_to_observation",
                           "async_loader_product_result_to_observation",
                           "import_product_result_to_observation",
                           "async_import_product_result_to_observation",
                           "product_return_to_observation",
                           "product_guard_controls_return_to_observation"}
    product_call_bindings = sum(row["relation"] in call_binding_relations for row in rows)
    cross_source_relations = sum(
        row["relation"] != "observation" and row["sourcePath"] != row["targetPath"]
        for row in rows
    )
    observation_linked = sum(
        row["relation"] in influence_relations
        and row["sourcePath"] != row["targetPath"]
        and int(row["observationLine"]) in mapped_lines
        for row in rows
    )
    observations_mapped = sum(len(ids) for line, ids in observations_by_line.items()
                              if line in mapped_lines)
    if observation_linked:
        entry_status = "observation_linked_to_product"
    elif product_call_bindings:
        entry_status = "product_call_binding_only"
    elif observations_mapped:
        entry_status = "observations_mapped_only"
    else:
        entry_status = "no_observation_mapping"
    return {
        "observations_mapped": observations_mapped,
        "product_call_bindings": product_call_bindings,
        "cross_source_relations": cross_source_relations,
        "observation_linked_cross_source_relations": observation_linked,
        "product_body_returns": sum(row["relation"] == "product_return_to_observation"
                                    for row in rows),
        "product_body_controls": sum(
            row["relation"] == "product_guard_controls_return_to_observation" for row in rows),
        "product_body_exceptions": sum(
            row["relation"] in {"product_exception_exit_unknown",
                                "product_rethrow_endpoint_unknown"} for row in rows),
        "product_unknown_effect_boundaries": sum(row["relation"] in {
            "product_call_unknown_effect", "product_attribute_write_unknown_effect",
            "product_external_state_unanalysed"}
            for row in rows),
        "missing_observations": [criterion["criterion_id"] for criterion in criteria
                                 if criterion["line"] not in mapped_lines],
        "ambiguous_observation_lines": sorted({line for line, ids in observations_by_line.items()
                                               if len(ids) > 1 or mapped_counts[line] > 1}),
        "analysis_entry_status": entry_status,
    }


def build_contract(entry: dict[str, Any], context: dict[str, Any], sources: list[dict[str, Any]],
                   criteria: list[dict[str, Any]], rows: list[dict[str, str]],
                   costs: dict[str, Any], boundaries: list[dict[str, Any]]) -> dict[str, Any]:
    observations = [{
        "observation_id": criterion["criterion_id"],
        "location": {"source_id": "test", "start_line": criterion["line"],
                     "end_line": criterion["line"]},
        "kind": criterion["kind"],
        "origin": "explicit" if criterion["confidence"] == "explicit" else "candidate",
        "reason": criterion["reason"],
    } for criterion in criteria]
    evidence = summarize_entry_evidence(criteria, rows)
    observation_ids: dict[int, list[str]] = {}
    for observation in observations:
        if observation["observation_id"] in evidence["missing_observations"]:
            continue
        observation_ids.setdefault(observation["location"]["start_line"], []).append(
            observation["observation_id"]
        )
    relations: list[dict[str, Any]] = []
    candidates: dict[tuple[str, int, str], dict[str, Any]] = {}
    unknown_effects: dict[tuple[Any, ...], dict[str, Any]] = {}

    def add_unknown_effect(key: tuple[Any, ...], effect: dict[str, Any],
                           refs: list[str], observation: dict[str, Any] | None = None) -> None:
        current = unknown_effects.setdefault(key, effect)
        if refs:
            known = current.setdefault("observation_ids", [])
            known.extend(reference for reference in refs if reference not in known)
        if observation is not None:
            scope = current["scope"]
            if "from" not in scope:
                current["scope"] = {"from": scope, "observations": []}
                scope = current["scope"]
            observed = scope.setdefault("observations", [])
            if observation not in observed:
                observed.append(observation)

    for row in rows:
        kind = row["relation"]
        source_line, source_end = int(row["sourceLine"]), int(row["sourceEndLine"])
        target_line, target_end = int(row["targetLine"]), int(row["targetEndLine"])
        refs = list(observation_ids.get(int(row["observationLine"]), []))
        source_endpoint = endpoint(row["sourcePath"], source_line, source_end)
        target_endpoint = endpoint(row["targetPath"], target_line, target_end)
        common = {
            "from": source_endpoint,
            "to": target_endpoint,
            "provenance": {"backend": "CodeQL 2.27.4", "query_relation": kind,
                           "query_detail": row["detail"],
                           "query_sha256": sha256_bytes(QUERY.read_bytes())},
            "precision": "generic bounded CodeQL relation; semantic correctness not independently reviewed",
            "observation_ids": refs, "assumptions": [],
        }
        if kind in {"resolved_test_product_call", "visible_loader_product_call",
                    "resolved_async_test_product_call",
                    "resolved_raw_async_test_product_call",
                    "visible_async_loader_product_call", "visible_import_product_call",
                    "visible_async_import_product_call",
                    "visible_raw_async_loader_product_call",
                    "visible_raw_async_import_product_call"}:
            relations.append({"kind": "call_binding", "modality": "analysis_may", **common})
            key = (row["targetPath"], target_line, row["detail"])
            candidates[key] = {"logical_path": key[0], "start_line": key[1], "symbol": key[2],
                               "evidence": common["from"]}
            raw_coroutine = "raw_async" in kind
            add_unknown_effect((row["sourcePath"], source_line, kind), {
                "boundary_id": f"call:{row['sourcePath']}:{source_line}",
                "effect": ("coroutine_object_created_product_body_not_executed"
                           if raw_coroutine else
                           "return_exception_and_reachable_state_effects_not_fully_modelled"),
                "scope": common["from"], "provenance": (
                    "generic CodeQL resolved call" if kind.startswith("resolved_")
                    else "authenticated visible-source frontend"),
                "precision": "unknown_effect", "assumptions": [],
            }, refs)
        elif kind == "actual_to_formal":
            relations.append({"kind": "value_flow", "modality": "analysis_may", **common})
        elif kind in {"call_result_to_observation", "resolved_product_result_to_observation",
                      "async_resolved_product_result_to_observation",
                      "loader_product_result_to_observation",
                      "async_loader_product_result_to_observation",
                      "import_product_result_to_observation",
                      "async_import_product_result_to_observation"}:
            relations.append({"kind": "value_flow", "modality": "analysis_may", **common})
        elif kind == "guard_controls_observation":
            relations.append({"kind": "control_dependence", "modality": "analysis_may", **common})
        elif kind == "product_return_to_observation":
            common["precision"] = (
                "authenticated call/result-to-observation chain composed with a reachable "
                "product return-value endpoint; path feasibility remains unreviewed"
            )
            relations.append({"kind": "normal_return_dependence", "modality": "analysis_may",
                              **common})
        elif kind == "product_guard_controls_return_to_observation":
            common["precision"] = (
                "CodeQL product CFG guard controls a return candidate linked to the observation; "
                "the branch is possible, not necessarily mandatory"
            )
            relations.append({"kind": "control_dependence", "modality": "analysis_may", **common})
        elif kind in {"product_exception_exit_unknown", "product_rethrow_endpoint_unknown"}:
            detail_hash = sha256_bytes(row["detail"].encode())[:12]
            endpoint_label = ("exception_exit" if kind == "product_exception_exit_unknown"
                              else "bare_rethrow")
            add_unknown_effect((row["sourcePath"], source_line, kind, row["detail"]), {
                "boundary_id": (f"product:{row['sourcePath']}:{source_line}:"
                                f"{endpoint_label}:{detail_hash}"),
                "effect": ("product_exceptional_exit_expected_type_compatibility_unknown"
                           if kind == "product_exception_exit_unknown" else
                           "bare_rethrow_endpoint_outer_handler_and_type_compatibility_unknown"),
                "scope": {"from": common["from"], "observations": [],
                          "call_evidence": row["detail"]},
                "provenance": common["provenance"],
                "precision": ("unknown_effect; entry-reachable product endpoint is an "
                              "observation-scoped frontier, not a proven influence"),
                "assumptions": (["outer product handlers and expected exception type "
                                 "compatibility are not proven"]
                                if kind == "product_rethrow_endpoint_unknown" else
                                ["same product execution route from endpoint to observation is "
                                 "not proven"]),
            }, refs, target_endpoint)
        elif kind in {"product_call_unknown_effect", "product_attribute_write_unknown_effect",
                      "product_external_state_unanalysed"}:
            effect = ("unknown_product_call_return_exception_and_reachable_state_effects"
                      if kind == "product_call_unknown_effect"
                      else "visible_attribute_write_alias_region_and_overwrite_not_proven"
                      if kind == "product_attribute_write_unknown_effect"
                      else "visible_external_state_endpoint_to_observed_region_unproven")
            detail_hash = sha256_bytes(row["detail"].encode())[:12]
            add_unknown_effect((row["sourcePath"], source_line, kind, row["detail"]), {
                "boundary_id": f"product:{row['sourcePath']}:{source_line}:{kind}:{detail_hash}",
                "effect": effect, "scope": {"from": common["from"], "observations": [],
                                             "call_evidence": row["detail"]},
                "provenance": common["provenance"],
                "precision": "unknown_effect",
                "assumptions": (["receiver type and runtime region are unproven; the endpoint "
                                 "does not establish an effect on the observed path"]
                                if kind == "product_external_state_unanalysed" else []),
            }, refs, target_endpoint)
    candidate_values = list(candidates.values())
    binding_status = "unique" if len(candidate_values) == 1 else "ambiguous" if candidate_values else "unresolved"
    coverage = {kind: "not_evaluated" for kind in RELATION_KINDS}
    failure_categories = ["semantic_validation_not_evaluated",
                          "generic_interprocedural_effects_incomplete"]
    if not candidate_values:
        failure_categories.append("mut_binding_unresolved")
    if evidence["missing_observations"]:
        failure_categories.append("observation_mapping_incomplete")
    if evidence["ambiguous_observation_lines"]:
        failure_categories.append("observation_mapping_ambiguous")
    for relation in relations:
        origin = relation["provenance"]["query_relation"]
        frontend_origin = ("loader" if "loader" in origin else
                           "import" if "import" in origin else None)
        if origin.startswith("visible_") or frontend_origin is not None:
            relation["provenance"]["binding_origin"] = (
                "authenticated_visible_loader_frontend" if frontend_origin == "loader"
                else "authenticated_visible_import_frontend")
            relation["provenance"]["selector_facts_sha256"] = context.get("selector_facts_sha256")
            relation["assumptions"] = (["visible loader namespace and runtime imports retain their shown bindings"]
                                       if frontend_origin == "loader" else
                                       ["visible direct import retains its shown binding"])
        if origin.endswith("_product_result_to_observation"):
            relation["precision"] = "composed function binding and CodeQL test-local result flow; function span is provenance scope, not an internal return-site edge"
            if origin.startswith("async_"):
                relation["precision"] += "; async result is connected only through an explicit await or authenticated asyncio.run wrapper"
    contract = {
        "schema_version": RESULT_SCHEMA_VERSION,
        "identity": {"case_id": entry["case_id"], "dataset": entry["dataset"],
                     "language": "python", "snapshot_id": entry["snapshot_id"],
                     "masking_contract": context["masking_contract"],
                     "masked_test_sha256": context["masked_test_sha256"],
                     "target_selector": context["target_selector"]},
        "source_scope": [{"source_id": source["source_id"], "logical_path": source["logical_path"],
                          "sha256": source["sha256"], "bytes": source["bytes"],
                          "selection_role": source.get("selection_role", "masked_test"
                                                     if source["source_id"] == "test"
                                                     else "candidate_product")} for source in sources],
        "observations": observations,
        "parameterization": {"status": "not_expanded", "coverage": "selected_test_definition",
                             "reason": "generic backend does not enumerate parameter rows"},
        "entry_point": {
            "test_selection": {"origin": "task_provided", "status": "unique",
                               "selector": context["target_selector"], "evidence": context["target_scope"]},
            "mut_binding": {"origin": "automatic", "status": binding_status,
                            "candidates": candidate_values,
                            "evidence": [row["evidence"] for row in candidate_values], "assumptions": []},
        },
        "relations": relations,
        "relation_coverage": [{"kind": kind, "status": coverage[kind]} for kind in sorted(coverage)],
        "analysis_boundaries": {
            "assumptions": ["selected source paths are candidates, not proof of full runtime module resolution"],
            "unknown_effects": list(unknown_effects.values()), "unanalysed_regions": boundaries,
        },
        "completion": {"status": "partial", "stage": "generic_codeql_mapping",
                       "failure_categories": failure_categories, "error": None},
        "semantic_validation": {"status": "not_evaluated", "reviewer": None, "evidence": [],
                                "notes": "relations are backend candidates; no case-specific gold or review used"},
        "cost": costs,
    }
    validate_result_contract(contract)
    return contract


def integrate_effect_rows(contract: dict[str, Any], criteria: list[dict[str, Any]],
                          rows: list[dict[str, str]],
                          *, product_paths: set[str],
                          limit: int = MAX_EFFECT_OUTPUTS) -> dict[str, Any]:
    """Append projected effect candidates without strengthening their modality."""
    if limit < 1:
        raise ValueError("effect output limit must be positive")
    ordered_rows = sorted(rows, key=lambda row: tuple(sorted(row.items())))
    projection = project_effect_rows(
        [EffectObservation(criterion["criterion_id"], criterion["line"])
         for criterion in criteria],
        ordered_rows,
        query_sha256=sha256_bytes(EFFECT_QUERY.read_bytes()),
    )
    grouped: dict[str, list[tuple[str, dict[str, Any]]]] = {}
    for bucket in ("relations", "unknown_effects", "exclusions"):
        for item in projection[bucket]:
            observation_id = item.get("observation_ids", ["unattached"])[0]
            grouped.setdefault(observation_id, []).append((bucket, item))
    for items in grouped.values():
        items.sort(key=lambda row: json.dumps(row[1], sort_keys=True, separators=(",", ":")))
    published: list[tuple[str, dict[str, Any]]] = []
    positions = {observation_id: 0 for observation_id in grouped}
    while len(published) < limit:
        progressed = False
        for observation_id in sorted(grouped):
            position = positions[observation_id]
            if position >= len(grouped[observation_id]):
                continue
            published.append(grouped[observation_id][position])
            positions[observation_id] += 1
            progressed = True
            if len(published) == limit:
                break
        if not progressed:
            break
    selected = {bucket: [item for item_bucket, item in published if item_bucket == bucket]
                for bucket in ("relations", "unknown_effects", "exclusions")}
    contract["relations"].extend(selected["relations"])
    contract["analysis_boundaries"]["unknown_effects"].extend(
        selected["unknown_effects"])
    contract["analysis_boundaries"]["unanalysed_regions"].extend({
        "scope": "effect_exclusion", "reason": exclusion["reason"],
        "evidence": exclusion,
    } for exclusion in selected["exclusions"])
    projected_outputs = sum(len(items) for items in grouped.values())
    omitted = projected_outputs - len(published)
    if omitted:
        omitted_by_observation = {
            observation_id: len(items) - positions[observation_id]
            for observation_id, items in sorted(grouped.items())
            if len(items) > positions[observation_id]
        }
        contract["analysis_boundaries"]["unanalysed_regions"].append({
            "scope": "effect_output_budget",
            "reason": "effect_row_budget_exhausted",
            "limit": limit,
            "omitted_outputs": omitted,
            "affected_observation_ids": list(omitted_by_observation),
            "omitted_by_observation": omitted_by_observation,
        })
    validate_result_contract(contract)
    cross_source = sum(
        relation["from"]["logical_path"] in product_paths
        and relation["from"]["logical_path"] != relation["to"]["logical_path"]
        for relation in selected["relations"])
    observation_linked = sum(
        relation["from"]["logical_path"] in product_paths
        and relation["from"]["logical_path"] != relation["to"]["logical_path"]
        and bool(relation.get("observation_ids")) for relation in selected["relations"])
    return {"relations": len(selected["relations"]),
            "unknown_effects": len(selected["unknown_effects"]),
            "exclusions": len(selected["exclusions"]),
            "input_rows": len(ordered_rows), "projected_outputs": projected_outputs,
            "published_outputs": len(published),
            "omitted_outputs": omitted, "cross_source_relations": cross_source,
            "observation_linked_cross_source_relations": observation_linked}


def update_effect_evidence(entry_evidence: dict[str, Any], counts: dict[str, Any]) -> None:
    entry_evidence["effect_projection"] = counts
    entry_evidence["cross_source_relations"] += counts["cross_source_relations"]
    entry_evidence["observation_linked_cross_source_relations"] += counts[
        "observation_linked_cross_source_relations"]
    if entry_evidence["observation_linked_cross_source_relations"]:
        entry_evidence["analysis_entry_status"] = "observation_linked_to_product"


def normalize_selected_sources(selected: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Attach the authoritative source role before any binding consumer runs."""
    return [{"source_id": row["source_id"], "logical_path": row["logical_path"],
             "sha256": sha256_bytes(row["text"].encode()),
             "bytes": len(row["text"].encode()), "text": row["text"],
             "selection_role": row.get(
                 "selection_role", "masked_test" if row["source_id"] == "test"
                 else "candidate_product")}
            for row in selected]


def execute(entry: dict[str, Any], registry_root: Path, output: Path, codeql: Path,
            *, max_files: int = 8, max_bytes: int = 200_000,
            database_timeout: int = 180, query_timeout: int = 360) -> dict[str, Any]:
    output = output.resolve()
    if output.exists():
        raise FileExistsError(f"output must not exist: {output}")
    output.mkdir(parents=True)
    started = time.monotonic()
    entry_evidence: dict[str, Any] = {
        "input_prepared": False, "database_created": False,
        "observations_total": 0, "observations_mapped": 0,
        "product_call_bindings": 0, "cross_source_relations": 0,
        "observation_linked_cross_source_relations": 0,
        "missing_observations": [], "ambiguous_observation_lines": [],
        "analysis_entry_status": "not_started",
        "current_stage": "admission", "tool_runs": {},
    }
    try:
        if entry.get("language") != "python":
            raise ValueError("generic Python backend requires a Python inventory entry")
        if not codeql.is_file() or not os.access(codeql, os.X_OK):
            raise ValueError("CodeQL path must be an executable file")
        dataset = FrozenDataset(registry_root, dataset=entry["dataset"],
                                inventory_revision=entry["inventory_revision"])
        with dataset.load_case(entry["case_id"]) as case:
            if case.snapshot_id != entry["snapshot_id"] or case.language != "python":
                raise ValueError("frozen case identity differs from inventory entry")
            extraction = extract_criteria(case.masked_test.text, case.language, case.target_selector)
            criteria = extraction["criteria"]
            if not criteria:
                raise ValueError("selected test has no visible observation candidate")
            for index, criterion in enumerate(criteria):
                criterion["criterion_id"] = f"criterion:{index}"
            entry_evidence["observations_total"] = len(criteria)
            entry_evidence["missing_observations"] = [item["criterion_id"] for item in criteria]
            entry_evidence["current_stage"] = "source_selection"
            selection = select_sources(case, max_files=max_files, max_bytes=max_bytes)
            selected = selection.pop("sources")
            context = {"target_selector": case.target_selector,
                       "target_scope": extraction["target_scope"]["locations"],
                       "masking_contract": case._masking_contract,
                       "masked_test_sha256": sha256_bytes(case.masked_test.text.encode())}
        sources = normalize_selected_sources(selected)
        entry_evidence["current_stage"] = "input_provenance"
        write_json(output / "input-provenance.json", {
            "identity": {key: entry[key] for key in ("case_id", "dataset", "language", "snapshot_id")},
            "input_context": context, "criteria": criteria,
            "sources": [{key: row[key] for key in (
                "source_id", "logical_path", "sha256", "bytes", "selection_role")}
                        for row in sources],
            "implementation_sha256": {str(path.relative_to(ROOT)): sha256_bytes(path.read_bytes())
                                      for path in (Path(__file__), QUERY, EFFECT_QUERY,
                                                   HERE / "effect_projection.py",
                                                   HERE / "opaque_facts.py",
                                                   HERE.parent / "source_selection.py",
                                                   HERE.parent / "criteria.py")},
        })
        test_source = next(row for row in sources if row["source_id"] == "test")
        entry_evidence["input_prepared"] = True
        entry_evidence["current_stage"] = "source_materialization"
        with tempfile.TemporaryDirectory(prefix="mockspec-generic-python-") as temporary:
            work = Path(temporary)
            input_root, query_root = work / "input", work / "query"
            input_root.mkdir(); query_root.mkdir()
            for source in sources:
                destination = input_root / source["logical_path"]
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_text(source["text"])
            shutil.copy2(QLPACK, query_root / "qlpack.yml")
            entry_evidence["current_stage"] = "selector_fact_preparation"
            query_text = QUERY.read_text()
            binding_diagnostics: list[dict[str, Any]] = []
            loader_bindings = [list(row) for row in sorted({
                tuple(row) for row in [
                    *loader_facts(sources, selection, context["target_scope"][0],
                                  binding_diagnostics),
                    *support_loader_facts(sources, selection, context["target_scope"][0],
                                          binding_diagnostics),
                ]
            })]
            import_bindings = import_facts(
                sources, selection, context["target_scope"][0], binding_diagnostics)
            resolved_sites = resolved_call_positions(
                test_source["text"], context["target_scope"][0],
                [*loader_bindings, *import_bindings])
            opaque_bindings = [[fact.line, fact.column, fact.origin] for fact in
                               build_parameter_boundary_facts(
                                   test_source["text"],
                                   function_name=case.target_selector.rsplit("::", 1)[-1],
                                   function_start_line=context["target_scope"][0]["start_line"],
                                   resolved_call_sites=resolved_sites)]
            selector_facts = {
                "test_path": test_source["logical_path"],
                "test_function": case.target_selector.rsplit("::", 1)[-1],
                "test_start_line": context["target_scope"][0]["start_line"],
                "observations": [{"observation_id": criterion["criterion_id"],
                                  "line": criterion["line"]} for criterion in criteria],
                "observation_expressions": selector_locations(test_source["text"], criteria),
                "visible_loader_bindings": loader_bindings,
                "visible_import_bindings": import_bindings,
                "opaque_call_bindings": opaque_bindings,
                "binding_diagnostics": binding_diagnostics,
                "loader_support_sources": [
                    {"logical_path": source["logical_path"],
                     "sha256": sha256_bytes(source["text"].encode())}
                    for source in sources if source["selection_role"] == "loader_support"
                ],
            }
            write_json(output / "selector-facts.json", selector_facts)
            context["selector_facts_sha256"] = sha256_bytes((output / "selector-facts.json").read_bytes())
            fact_files = {
                "selectedTestPathFact": output / "selector-test-path.csv",
                "selectedTestFunctionFact": output / "selector-test-function.csv",
                "selectedTestStartLineFact": output / "selector-test-start-line.csv",
                "selectedObservationLineFact": output / "selector-observation-lines.csv",
                "selectedObservationExprFact": output / "selector-observation-expressions.csv",
                "selectedSupportPathFact": output / "selector-support-paths.csv",
                "visibleLoaderBindingFact": output / "selector-loader-bindings.csv",
                "visibleImportBindingFact": output / "selector-import-bindings.csv",
                "selectedOpaqueCallFact": output / "selector-opaque-calls.csv",
            }
            write_external_column(fact_files["selectedTestPathFact"], [selector_facts["test_path"]])
            write_external_column(fact_files["selectedTestFunctionFact"],
                                  [selector_facts["test_function"]])
            write_external_column(fact_files["selectedTestStartLineFact"],
                                  [selector_facts["test_start_line"]])
            write_external_column(fact_files["selectedObservationLineFact"],
                                  sorted({row["line"] for row in selector_facts["observations"]}))
            write_external_column(fact_files["selectedSupportPathFact"],
                                  [source["logical_path"] for source in sources
                                   if source["selection_role"] == "loader_support"])
            for key, predicate in (("observation_expressions", "selectedObservationExprFact"),
                                   ("visible_loader_bindings", "visibleLoaderBindingFact"),
                                   ("visible_import_bindings", "visibleImportBindingFact"),
                                   ("opaque_call_bindings", "selectedOpaqueCallFact")):
                with fact_files[predicate].open("w", newline="") as stream:
                    csv.writer(stream).writerows(selector_facts[key])
            query = query_root / QUERY.name
            query.write_text(query_text)
            effect_query_text = EFFECT_QUERY.read_text()
            effect_query = query_root / EFFECT_QUERY.name
            effect_query.write_text(effect_query_text)
            version = json.loads(subprocess.check_output(
                [str(codeql), "version", "--format=json"], text=True, env=DRIVER_ENV, timeout=30))
            database, bqrs = work / "database", work / "result.bqrs"
            entry_evidence["current_stage"] = "database_create"
            db_cost = run_tool([str(codeql), "database", "create", str(database), "--language=python",
                                f"--source-root={input_root}", "--threads=2", "--ram=4096", "--overwrite"],
                               cwd=work, stdout=output / "database.stdout", stderr=output / "database.stderr",
                               timeout=database_timeout)
            entry_evidence["tool_runs"]["database_create"] = db_cost
            if db_cost["status"] != "complete" or db_cost["exit_code"]:
                raise RuntimeError("CodeQL database creation failed")
            entry_evidence["database_created"] = True
            entry_evidence["current_stage"] = "query"
            query_cost = run_tool([str(codeql), "query", "run", f"--database={database}", "--threads=2",
                                   "--ram=4096", f"--output={bqrs}",
                                   *[f"--external={name}={fact_files[name]}" for name in (
                                       "selectedTestPathFact", "selectedTestFunctionFact",
                                       "selectedTestStartLineFact", "selectedObservationLineFact",
                                       "selectedObservationExprFact", "selectedSupportPathFact",
                                       "visibleLoaderBindingFact", "visibleImportBindingFact")],
                                   str(query)], cwd=work,
                                  stdout=output / "query.stdout", stderr=output / "query.stderr",
                                  timeout=query_timeout)
            entry_evidence["tool_runs"]["query"] = query_cost
            if query_cost["status"] != "complete" or query_cost["exit_code"]:
                raise RuntimeError("generic CodeQL query failed")
            entry_evidence["current_stage"] = "decode"
            csv_path = output / "relations.csv"
            decode_cost = run_tool([str(codeql), "bqrs", "decode", "--format=csv",
                                    f"--output={csv_path}", str(bqrs)], cwd=work,
                                   stdout=output / "decode.stdout", stderr=output / "decode.stderr")
            entry_evidence["tool_runs"]["decode"] = decode_cost
            if decode_cost["status"] != "complete" or decode_cost["exit_code"]:
                raise RuntimeError("generic CodeQL result decode failed")
            with csv_path.open(newline="") as stream:
                rows = list(csv.DictReader(stream))
            entry_evidence["current_stage"] = "effect_query"
            effect_bqrs = work / "effect-result.bqrs"
            effect_query_cost = run_tool([
                str(codeql), "query", "run", f"--database={database}", "--threads=2",
                "--ram=4096", f"--output={effect_bqrs}",
                *[f"--external={name}={fact_files[name]}" for name in (
                    "selectedTestPathFact", "selectedTestFunctionFact",
                    "selectedTestStartLineFact", "selectedObservationLineFact",
                    "selectedObservationExprFact", "selectedSupportPathFact",
                    "selectedOpaqueCallFact",
                    "visibleLoaderBindingFact", "visibleImportBindingFact")],
                str(effect_query)], cwd=work, stdout=output / "effect-query.stdout",
                stderr=output / "effect-query.stderr", timeout=query_timeout)
            entry_evidence["tool_runs"]["effect_query"] = effect_query_cost
            if effect_query_cost["status"] != "complete" or effect_query_cost["exit_code"]:
                raise RuntimeError("effect CodeQL query failed")
            entry_evidence["current_stage"] = "effect_decode"
            effect_csv_path = output / "effect-relations.csv"
            effect_decode_cost = run_tool([
                str(codeql), "bqrs", "decode", "--format=csv",
                f"--output={effect_csv_path}", str(effect_bqrs)], cwd=work,
                stdout=output / "effect-decode.stdout", stderr=output / "effect-decode.stderr")
            entry_evidence["tool_runs"]["effect_decode"] = effect_decode_cost
            if effect_decode_cost["status"] != "complete" or effect_decode_cost["exit_code"]:
                raise RuntimeError("effect CodeQL result decode failed")
            with effect_csv_path.open(newline="") as stream:
                effect_rows = list(csv.DictReader(stream))
        mapping_started = time.monotonic()
        entry_evidence.update(summarize_entry_evidence(criteria, rows))
        entry_evidence["current_stage"] = "mapped"
        source_records = [{key: row[key] for key in (
            "source_id", "logical_path", "sha256", "bytes", "selection_role")}
                          for row in sources]
        costs: dict[str, Any] = {"timings_seconds": {"database": db_cost["seconds"],
                                     "query": query_cost["seconds"],
                                     "decode": decode_cost["seconds"],
                                     "effect_query": effect_query_cost["seconds"],
                                     "effect_decode": effect_decode_cost["seconds"]},
                 "total_seconds": 0.0,
                 "total_scope": ("frozen admission, bounded selection, one CodeQL database, "
                                 "generic/effect queries, decodes and mapping"),
                 "peak_memory": {"status": "unavailable", "bytes": None,
                                 "reason": "generic runner does not yet collect per-process peak RSS"},
                 "cache_state": {"status": "cold", "reason": "fresh temporary database; OS/tool distribution caches not isolated"},
                 "input_size": {"files": len(sources), "bytes": sum(row["bytes"] for row in sources)},
                 "graph_size": {"nodes": None, "edges": len(rows) + len(effect_rows)},
                 "unavailable_phases": []}
        boundaries = [{"scope": "source_selection", "reason": value}
                      for value in selection["diagnostics"]]
        boundaries.extend(binding_diagnostics)
        boundaries.extend([
            {"scope": "path_feasibility", "reason": "not evaluated"},
            {"scope": "interprocedural_return_exception_state", "reason": "generic query is incomplete"},
        ])
        contract = build_contract(entry, context, source_records, criteria, rows, costs, boundaries)
        entry_evidence["current_stage"] = "effect_projection"
        projection_started = time.monotonic()
        product_paths = {
            source["logical_path"] for source in source_records
            if source["selection_role"] == "candidate_product"
        }
        effect_counts = integrate_effect_rows(
            contract, criteria, effect_rows, product_paths=product_paths)
        projection_seconds = time.monotonic() - projection_started
        entry_evidence["effect_rows"] = len(effect_rows)
        update_effect_evidence(entry_evidence, effect_counts)
        costs["timings_seconds"]["mapping"] = projection_started - mapping_started
        costs["timings_seconds"]["effect_projection"] = projection_seconds
        costs["total_seconds"] = time.monotonic() - started
        entry_evidence["current_stage"] = "mapped"
        result = {"schema": SCHEMA, "status": "complete", "backend": "generic_codeql_python",
                  "identity": contract["identity"], "result_contract": contract,
                  "source_selection": selection, "raw_relation_count": len(rows),
                  "raw_effect_relation_count": len(effect_rows),
                  "entry_evidence": entry_evidence,
                  "provenance": {"query_template_sha256": sha256_bytes(QUERY.read_bytes()),
                                 "rendered_query_sha256": sha256_bytes(query_text.encode()),
                                 "effect_query_template_sha256": sha256_bytes(
                                     EFFECT_QUERY.read_bytes()),
                                 "rendered_effect_query_sha256": sha256_bytes(
                                     effect_query_text.encode()),
                                 "effect_projection_sha256": sha256_bytes(
                                     (HERE / "effect_projection.py").read_bytes()),
                                 "opaque_facts_sha256": sha256_bytes(
                                     (HERE / "opaque_facts.py").read_bytes()),
                                 "selector_facts_sha256": sha256_bytes(
                                     (output / "selector-facts.json").read_bytes()),
                                 "codeql_version": version["version"], "driver_environment": DRIVER_ENV,
                                 "target_executed": False, "loader_executed": False,
                                 "selection": "case identity + visible imports/paths + extracted observations; no case allow-list"}}
    except Exception as exc:
        failed_run = entry_evidence["tool_runs"].get(entry_evidence["current_stage"], {})
        result = {"schema": SCHEMA, "status": "failed", "backend": "generic_codeql_python",
                  "identity": {key: entry.get(key) for key in ("dataset", "case_id", "language", "snapshot_id")},
                  "result_contract": None,
                  "error": {"type": type(exc).__name__, "message": str(exc),
                            "stage": entry_evidence["current_stage"],
                            "classification": "timeout" if failed_run.get("status") == "timeout"
                            else "tool_failure" if failed_run else "pre_tool_failure"},
                  "entry_evidence": entry_evidence,
                  "provenance": {"target_executed": False, "loader_executed": False},
                  "elapsed_seconds": time.monotonic() - started}
    write_json(output / "batch-result.json", result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--entry", type=Path, required=True)
    parser.add_argument("--registry-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--codeql", type=Path, required=True)
    parser.add_argument("--max-files", type=int, default=8)
    parser.add_argument("--max-bytes", type=int, default=200_000)
    parser.add_argument("--database-timeout", type=int, default=180)
    parser.add_argument("--query-timeout", type=int, default=360)
    args = parser.parse_args()
    entry = json.loads(args.entry.read_text())
    result = execute(entry, args.registry_root, args.output, args.codeql,
                     max_files=args.max_files, max_bytes=args.max_bytes,
                     database_timeout=args.database_timeout, query_timeout=args.query_timeout)
    print(json.dumps({"status": result["status"], "case_id": entry.get("case_id"),
                      "relations": len((result.get("result_contract") or {}).get("relations", []))}))
    return 0 if result["status"] == "complete" else 1


if __name__ == "__main__":
    raise SystemExit(main())
