"""Bounded, evidence-preserving source selection for slicing research.

This module never scans source contents to discover a target.  It derives path
candidates from the already admitted masked test, matches them against
``FrozenCase.source_paths``, and reads only the selected candidates through the
case's authenticated ``read_source`` API.
"""
from __future__ import annotations

import ast
from collections import deque
import posixpath
import re
from typing import Any

from criteria import extract_criteria
from mock_spec.validation import InputValidationError

PYTHON_FOCAL_FILE_BYTES = 512_000
PYTHON_LOADER_SUPPORT_FILE_BYTES = 32_000

_PYTHON_LOADER_NAMES = {
    "load_function_from_source_ast",
    "load_function_and_class_from_source_ast",
    "spec_from_file_location",
}
_JAVA_IMPORT = re.compile(r"(?m)^\s*import\s+(?:static\s+)?([\w.]+)(?:\.\*)?\s*;")
_JAVA_PACKAGE = re.compile(r"(?m)^\s*package\s+([\w.]+)\s*;")
_JAVA_TYPE_NAME = re.compile(r"\b[A-Z][A-Za-z0-9_]*\b")
_JAVA_CONSTRUCTOR_OWNER = re.compile(r"\bnew\s+([A-Z][A-Za-z0-9_]*)\b")
_JAVA_CALL_OWNER = re.compile(
    r"\b([A-Z][A-Za-z0-9_]*)\s*\.\s*[A-Za-z_$][A-Za-z0-9_$]*\s*\(")
_JAVA_VARIABLE_DECLARATION = re.compile(
    r"\b([A-Z][A-Za-z0-9_]*(?:\.[A-Z][A-Za-z0-9_]*)*)\s+"
    r"([a-z_$][A-Za-z0-9_$]*)\s*=")
_JAVA_VARIABLE_RECEIVER = re.compile(
    r"\b([a-z_$][A-Za-z0-9_$]*)\s*\.")
_JAVA_IDENTIFIER = re.compile(r"\b[A-Za-z_$][A-Za-z0-9_$]*\b")
_JAVA_CALL_NAME = re.compile(r"\b([A-Za-z_$][A-Za-z0-9_$]*)\s*\(")
_JAVA_MEMBER_NAME = re.compile(r"\.\s*([A-Za-z_$][A-Za-z0-9_$]*)\b")
_JAVA_KEYWORDS = {
    "abstract", "assert", "boolean", "break", "byte", "case", "catch", "char",
    "class", "const", "continue", "default", "do", "double", "else", "enum",
    "extends", "false", "final", "finally", "float", "for", "goto", "if",
    "implements", "import", "instanceof", "int", "interface", "long", "native",
    "new", "null", "package", "private", "protected", "public", "return", "short",
    "static", "strictfp", "super", "switch", "synchronized", "this", "throw",
    "throws", "transient", "true", "try", "void", "volatile", "while",
}


def _strip_java_comments_and_literals(text: str) -> str:
    """Blank Java comments and literals while preserving line positions."""
    return re.sub(
        r'//[^\n]*|/\*.*?\*/|""".*?"""|"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'',
        lambda match: "".join("\n" if char == "\n" else " " for char in match.group()),
        text,
        flags=re.DOTALL,
    )


def _is_product_source(path: str, test_path: str) -> bool:
    if path == test_path:
        return False
    parts = path.lower().split("/")
    name = parts[-1]
    return not (
        "test" in parts
        or "tests" in parts
        or name.startswith(("test_", "test-"))
        or name.endswith(("test.py", "test.java", "tests.java"))
    )


def _literal_bindings(tree: ast.AST) -> dict[str, str]:
    values: dict[str, str] = {}
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        value = node.value
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            for target in targets:
                if isinstance(target, ast.Name):
                    values[target.id] = value.value
    return values


def _string_value(node: ast.AST, bindings: dict[str, str]) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Name):
        return bindings.get(node.id)
    if isinstance(node, ast.JoinedStr):
        chunks: list[str] = []
        for value in node.values:
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                chunks.append(value.value)
            elif isinstance(value, ast.FormattedValue):
                resolved = _string_value(value.value, bindings)
                if resolved is None:
                    return None
                chunks.append(resolved)
            else:
                return None
        return "".join(chunks)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = _string_value(node.left, bindings)
        right = _string_value(node.right, bindings)
        return left + right if left is not None and right is not None else None
    return None


def _path_value(node: ast.AST, bindings: dict[str, str], logical_path: str,
                constructors: set[str] | None = None) -> str | None:
    """Evaluate a narrow, visible pathlib expression without touching the filesystem."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Name):
        if node.id == "__file__":
            return logical_path
        return bindings.get(node.id)
    if isinstance(node, ast.Call):
        if (isinstance(node.func, ast.Name)
                and (node.func.id == "str" or node.func.id in (constructors or set()))
                and len(node.args) == 1):
            return _path_value(node.args[0], bindings, logical_path, constructors)
        if (isinstance(node.func, ast.Attribute) and node.func.attr in {"resolve", "absolute"}
                and not node.args):
            return _path_value(node.func.value, bindings, logical_path, constructors)
    if isinstance(node, ast.Attribute) and node.attr == "parent":
        value = _path_value(node.value, bindings, logical_path, constructors)
        return posixpath.dirname(value) if value is not None else None
    if (isinstance(node, ast.Subscript) and isinstance(node.value, ast.Attribute)
            and node.value.attr == "parents" and isinstance(node.slice, ast.Constant)
            and type(node.slice.value) is int and node.slice.value >= 0):
        value = _path_value(node.value.value, bindings, logical_path, constructors)
        if value is None:
            return None
        for _ in range(node.slice.value + 1):
            value = posixpath.dirname(value)
        return value
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
        left = _path_value(node.left, bindings, logical_path, constructors)
        right = _path_value(node.right, bindings, logical_path, constructors)
        if left is not None and right is not None:
            return posixpath.normpath(posixpath.join(left, right))
    return None


def _path_bindings(tree: ast.Module, logical_path: str) -> tuple[dict[str, str], dict[str, int]]:
    values: dict[str, str] = {}
    lines: dict[str, int] = {}
    constructors = {
        alias.asname or alias.name
        for node in tree.body
        if isinstance(node, ast.ImportFrom) and node.module == "pathlib"
        for alias in node.names
        if alias.name in {"Path", "PurePath"}
    }
    for node in tree.body:
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        if node.value is None:
            continue
        value = _path_value(node.value, values, logical_path, constructors)
        if value is None:
            for target in targets:
                if isinstance(target, ast.Name):
                    values.pop(target.id, None)
                    lines.pop(target.id, None)
            continue
        for target in targets:
            if isinstance(target, ast.Name):
                values[target.id] = value
                lines[target.id] = node.lineno
    return values, lines


def _python_hints(text: str, logical_path: str = "test.py", *,
                  target_symbol: str | None = None) -> tuple[list[dict[str, str]], list[str]]:
    diagnostics: list[str] = []
    try:
        tree = ast.parse(text)
    except SyntaxError as exc:
        return [], [f"python_parse_error:{exc.lineno}"]
    bindings = _literal_bindings(tree)
    path_bindings, _ = _path_bindings(tree, logical_path)
    relevant_names: set[str] = set()
    rebound_names: set[str] = set()
    relevant_helpers: set[str] = set()
    observation_names: set[str] = set()
    observation_node_ids: set[int] = set()
    containing_function: dict[int, str] = {}
    if target_symbol is not None:
        targets = [node for node in ast.walk(tree)
                   if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                   and node.name == target_symbol]
        if len(targets) == 1:
            relevant_names = {node.id for node in ast.walk(targets[0])
                              if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)}
            rebound_names = {node.id for node in ast.walk(targets[0])
                             if isinstance(node, ast.Name)
                             and isinstance(node.ctx, (ast.Store, ast.Del))}
            observation_lines = {item["line"] for item in extract_criteria(
                text, "python", f"{logical_path}::{target_symbol}")["criteria"]}
            observation_names = {name.id for node in ast.walk(targets[0])
                                 if getattr(node, "lineno", None) in observation_lines
                                 for name in ast.walk(node)
                                 if isinstance(name, ast.Name) and isinstance(name.ctx, ast.Load)}
            # Ranking only: follow visible assignments back from the observations.
            # This is not a call binding or a proof that a value survives a write.
            assignments = [node for node in [*tree.body, *ast.walk(targets[0])]
                           if isinstance(node, (ast.Assign, ast.AnnAssign))]
            changed = True
            while changed:
                before = len(observation_names)
                for assignment in assignments:
                    assigned = {name.id for target in (
                        assignment.targets if isinstance(assignment, ast.Assign)
                        else [assignment.target]) for name in ast.walk(target)
                        if isinstance(name, ast.Name)}
                    if assigned & observation_names and assignment.value is not None:
                        observation_names.update(name.id for name in ast.walk(assignment.value)
                                                 if isinstance(name, ast.Name)
                                                 and isinstance(name.ctx, ast.Load))
                changed = len(observation_names) != before
            # Only observation expressions and their assignment dependencies receive
            # the focal-path priority/allowance. Mere presence in the test is not
            # evidence: setup may read an unrelated large file before the assertion.
            observation_node_ids.update(
                id(child) for node in ast.walk(targets[0])
                if getattr(node, "lineno", None) in observation_lines
                for child in ast.walk(node))
            for assignment in assignments:
                assigned = {name.id for target in (
                    assignment.targets if isinstance(assignment, ast.Assign)
                    else [assignment.target]) for name in ast.walk(target)
                    if isinstance(name, ast.Name)}
                if assigned & observation_names and assignment.value is not None:
                    observation_node_ids.update(id(node) for node in ast.walk(assignment.value))
            relevant_helpers.update(observation_names)
            for statement in tree.body:
                if not isinstance(statement, (ast.Assign, ast.AnnAssign)):
                    continue
                assigned = {node.id for target in (
                    statement.targets if isinstance(statement, ast.Assign) else [statement.target]
                ) for node in ast.walk(target) if isinstance(node, ast.Name)}
                value = statement.value
                if assigned & observation_names and isinstance(value, ast.Call):
                    helper = (value.func.id if isinstance(value.func, ast.Name) else
                              value.func.attr if isinstance(value.func, ast.Attribute) else None)
                    if helper is not None:
                        relevant_helpers.add(helper)
        for statement in tree.body:
            if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for descendant in ast.walk(statement):
                    containing_function[id(descendant)] = statement.name
    ranked_hints: list[tuple[int, int, dict[str, str]]] = []
    ordinal = 0
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                bound = alias.asname or alias.name.split(".", 1)[0]
                priority = (0 if bound in observation_names else 1) if (
                    bound in relevant_names and bound not in rebound_names) else 2
                ranked_hints.append((priority, ordinal,
                                     {"kind": "python_import", "value": alias.name}))
                ordinal += 1
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            imported = {alias.asname or alias.name for alias in node.names}
            priority = (0 if any(name in observation_names and name not in rebound_names
                                for name in imported) else 1) if any(
                name in relevant_names and name not in rebound_names for name in imported) else 2
            ranked_hints.append((priority, ordinal,
                                 {"kind": "python_import", "value": node.module}))
            ordinal += 1
        elif isinstance(node, ast.Call):
            name = node.func.attr if isinstance(node.func, ast.Attribute) else (
                node.func.id if isinstance(node.func, ast.Name) else ""
            )
            if name in _PYTHON_LOADER_NAMES and node.args:
                path = (_path_value(node.args[0], path_bindings, logical_path)
                        or _string_value(node.args[0], bindings))
                if path is not None:
                    priority = (-1 if id(node) in observation_node_ids
                                or containing_function.get(id(node)) in relevant_helpers else 3)
                    ranked_hints.append((priority, ordinal,
                                         {"kind": "python_dynamic_path", "value": path,
                                          "focal": str(priority == -1)}))
                    ordinal += 1
                else:
                    diagnostics.append(f"unresolved_dynamic_path:{getattr(node, 'lineno', 0)}")
            elif isinstance(node.func, ast.Attribute) and node.func.attr in {"read_text", "read_bytes"}:
                path = _path_value(node.func.value, path_bindings, logical_path)
                if path is not None:
                    priority = (-1 if id(node) in observation_node_ids
                                or containing_function.get(id(node)) in relevant_helpers else 3)
                    ranked_hints.append((priority, ordinal,
                                         {"kind": "python_visible_source_path", "value": path,
                                          "focal": str(priority == -1)}))
                    ordinal += 1
                else:
                    diagnostics.append(f"unresolved_visible_source_path:{getattr(node, 'lineno', 0)}")
    return [hint for _, _, hint in sorted(ranked_hints)], diagnostics


def _java_priority_types(text: str, target_selector: str | None) -> list[str]:
    """Rank visible constructor/call owners on the observation dependency chain.

    This is source-intake priority only. It neither binds a call nor proves that
    a selected type reaches an observation.
    """
    if target_selector is None:
        return []
    extraction = extract_criteria(text, "java", target_selector)
    locations = extraction["target_scope"]["locations"]
    if len(locations) != 1 or not extraction["criteria"]:
        return []
    location = locations[0]
    code_lines = _strip_java_comments_and_literals(text).splitlines(keepends=True)
    start_line, end_line = location["start_line"], location["end_line"]
    method = "".join(code_lines[start_line - 1:end_line])
    raw_statements: list[tuple[int, int, str]] = []
    cursor = 0
    for match in re.finditer(r"[^;]*;", method):
        statement = match.group()
        begin = start_line + method.count("\n", 0, match.start())
        finish = begin + statement.count("\n")
        raw_statements.append((begin, finish, statement))
        cursor = match.end()
    if cursor < len(method) and method[cursor:].strip():
        statement = method[cursor:]
        begin = start_line + method.count("\n", 0, cursor)
        raw_statements.append((begin, begin + statement.count("\n"), statement))

    declaration_types: dict[str, set[str]] = {}
    for _, _, statement in raw_statements:
        for type_name, variable in _JAVA_VARIABLE_DECLARATION.findall(statement):
            declaration_types.setdefault(variable, set()).add(type_name.rsplit(".", 1)[-1])
    ambiguous_variables = {
        variable for variable, types in declaration_types.items() if len(types) != 1}

    def variable_references(statement: str) -> set[str]:
        excluded = {
            *_JAVA_CALL_NAME.findall(statement),
            *_JAVA_MEMBER_NAME.findall(statement),
            *_JAVA_KEYWORDS,
        }
        return {
            name for name in _JAVA_IDENTIFIER.findall(statement)
            if not name[0].isupper() and name not in excluded
            and name not in ambiguous_variables
        }

    statements = [
        (begin, finish, statement, variable_references(statement))
        for begin, finish, statement in raw_statements
    ]

    observation_lines = {criterion["line"] for criterion in extraction["criteria"]}
    relevant_statements = {
        index for index, (begin, finish, _, _) in enumerate(statements)
        if any(begin <= line <= finish for line in observation_lines)
    }
    relevant_names = set().union(*(
        statements[index][3] for index in relevant_statements
    )) if relevant_statements else set()
    changed = True
    while changed:
        changed = False
        for index, (_, _, _, names) in enumerate(statements):
            if index in relevant_statements or not names & relevant_names:
                continue
            relevant_statements.add(index)
            before = len(relevant_names)
            relevant_names.update(names)
            changed = changed or len(relevant_names) != before

    declared_receivers = {
        variable: next(iter(types))
        for variable, types in declaration_types.items() if len(types) == 1
    }
    ranked: list[str] = []
    for index, (_, _, statement, _) in enumerate(statements):
        if index not in relevant_statements:
            continue
        for receiver in _JAVA_VARIABLE_RECEIVER.findall(statement):
            name = declared_receivers.get(receiver)
            if name is not None and name not in ranked:
                ranked.append(name)
        for pattern in (_JAVA_CONSTRUCTOR_OWNER, _JAVA_CALL_OWNER):
            for name in pattern.findall(statement):
                if name not in ranked:
                    ranked.append(name)
    return ranked


def _java_hints(text: str, *, target_selector: str | None = None
                ) -> tuple[list[dict[str, str]], list[str]]:
    code = _strip_java_comments_and_literals(text)
    hints = [{"kind": "java_import", "value": name}
             for name in _JAVA_IMPORT.findall(code) if not name.endswith(".*")]
    package = _JAVA_PACKAGE.search(code)
    if package:
        imported = {hint["value"].rsplit(".", 1)[-1] for hint in hints}
        for name in sorted(set(_JAVA_TYPE_NAME.findall(code)) - imported):
            hints.append({"kind": "java_same_package_type",
                          "value": package.group(1) + "." + name})
    priority = _java_priority_types(text, target_selector)
    priority_index = {name: index for index, name in enumerate(priority)}
    for hint in hints:
        simple_name = hint["value"].rsplit(".", 1)[-1]
        if simple_name in priority_index:
            hint["selection_priority"] = "selected_method_lexical_priority_candidate"
    hints.sort(key=lambda hint: (
        priority_index.get(hint["value"].rsplit(".", 1)[-1], len(priority)),
        0 if hint["kind"] == "java_import" else 1,
    ))
    return hints, []


def _python_source_roots(hints: list[dict[str, str]], paths: tuple[str, ...]) -> set[str]:
    roots: set[str] = set()
    for hint in hints:
        value = hint["value"].replace("\\", "/").lstrip("./")
        if hint["kind"] != "python_import" or "." not in value:
            continue
        module = value.replace(".", "/")
        for suffix in (module + ".py", module + "/__init__.py"):
            marker = "/" + suffix
            roots.update(path[:-len(marker)] for path in paths if path.endswith(marker))
    return roots


def _matches(hint: dict[str, str], paths: tuple[str, ...], language: str,
             python_source_roots: set[str] | None = None) -> list[str]:
    value = hint["value"].replace("\\", "/").lstrip("./")
    if hint["kind"] in {"python_dynamic_path", "python_visible_source_path"}:
        return [path for path in paths if path == value or path.endswith("/" + value)]
    if language == "python":
        module = value.replace(".", "/")
        suffixes = (module + ".py", module + "/__init__.py")
        roots = python_source_roots or set()
        return [path for path in paths if any(
            path == suffix or any(path == f"{root}/{suffix}" for root in roots)
            or not roots and path.endswith("/" + suffix)
            for suffix in suffixes)]
    suffix = value.replace(".", "/") + ".java"
    return [path for path in paths if path == suffix or path.endswith("/" + suffix)]


def _precision(hint: dict[str, str], candidates: list[str]) -> str:
    if len(candidates) > 1:
        return "candidate"
    if hint["kind"] in {"python_dynamic_path", "python_visible_source_path"}:
        return "exact_visible_path"
    return "unique_path_match_candidate"


def _python_opaque(source_id: str, text: str) -> list[dict[str, Any]]:
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return []
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) or len(node.body) != 1:
            continue
        statement = node.body[0]
        if not isinstance(statement, ast.Raise) or statement.exc is None:
            continue
        exc = statement.exc.func if isinstance(statement.exc, ast.Call) else statement.exc
        name = exc.id if isinstance(exc, ast.Name) else (
            exc.attr if isinstance(exc, ast.Attribute) else ""
        )
        if name in {"NotImplementedError", "UnsupportedOperationException"}:
            found.append({"source_id": source_id, "name": node.name,
                          "start_line": node.lineno,
                          "end_line": getattr(node, "end_lineno", node.lineno)})
    return found


def _java_opaque(source_id: str, text: str) -> list[dict[str, Any]]:
    # Find the small throw-only body first; do not apply a permissive declaration
    # regex across the whole file (overlapping whitespace can backtrack badly).
    code = _strip_java_comments_and_literals(text)
    bodies = re.compile(
        r"\{\s*throw\s+new\s+(?:UnsupportedOperationException|NotImplementedException)"
        r"\s*\([^;{}]*\)\s*;\s*\}")
    headers = re.compile(
        r"([A-Za-z_$][\w$]*)\s*\([^()]*\)\s*(?:throws\s+[\w.$,\s]+)?$")
    found = []
    for body in bodies.finditer(code):
        # Only inspect the bounded declaration segment after the preceding Java
        # statement/body delimiter. Unsupported signatures remain unclassified.
        start = max(code.rfind(delimiter, max(0, body.start() - 2000), body.start())
                    for delimiter in ";{}") + 1
        start = max(start, body.start() - 2000)
        header = code[start:body.start()].strip()
        signature = headers.search(header)
        if signature is None:
            continue
        name = signature.group(1)
        prefix = header[:signature.start()].strip()
        if (name in {"if", "for", "while", "switch", "catch", "synchronized"}
                or not prefix or re.search(r"[^\w$<>?,.\[\]\s@]", prefix)):
            continue
        declaration_start = start + len(code[start:body.start()]) - len(code[start:body.start()].lstrip())
        found.append({"source_id": source_id, "name": name,
                      "start_line": text.count("\n", 0, declaration_start) + 1,
                      "end_line": text.count("\n", 0, body.end()) + 1})
    return found


def _call_name(node: ast.Call) -> str | None:
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return None


def _namespace_item(node: ast.AST, aliases: dict[str, tuple[str, str]]) -> tuple[str, str] | None:
    if isinstance(node, ast.Name):
        return aliases.get(node.id)
    if not isinstance(node, ast.Subscript) or not isinstance(node.value, ast.Name):
        return None
    if isinstance(node.slice, ast.Constant) and isinstance(node.slice.value, str):
        return node.value.id, node.slice.value
    return None


def _assigned_slots(target: ast.AST) -> list[str | None]:
    if isinstance(target, ast.Name):
        return [target.id]
    if isinstance(target, (ast.Tuple, ast.List)):
        return [item.id if isinstance(item, ast.Name) else None for item in target.elts]
    return []


def _exec_source_span(loader: ast.FunctionDef | ast.AsyncFunctionDef, execution: ast.Call,
                      read_line: int, source_lines: int) -> tuple[int, int] | None:
    """Trace the supported straight-line read/split/slice/join/compile idiom.

    A read elsewhere in the loader is not evidence for the text passed to exec.
    Unsupported expressions or writes kill provenance instead of guessing it.
    """
    values: dict[str, Any] = {}

    def value(node: ast.AST) -> Any:
        if isinstance(node, ast.Constant) and type(node.value) in {str, int}:
            return node.value
        if isinstance(node, ast.Name):
            return values.get(node.id)
        if isinstance(node, ast.BinOp):
            left, right = value(node.left), value(node.right)
            if type(left) is int and type(right) is int and isinstance(node.op, ast.Sub):
                return left - right
            if isinstance(node.op, ast.Mult) and isinstance(left, str) and type(right) is int:
                if left.strip() == '' and 0 <= right <= source_lines:
                    return left * right
            if isinstance(node.op, ast.Add) and isinstance(left, str) and not left.strip():
                if isinstance(right, tuple) and right[0] == 'text':
                    return right
            return None
        if isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Slice):
            original = value(node.value)
            if not isinstance(original, tuple) or original[0] != 'lines' or node.slice.step:
                return None
            lower = value(node.slice.lower) if node.slice.lower else 0
            upper = value(node.slice.upper) if node.slice.upper else original[2] - original[1] + 1
            if type(lower) is int and type(upper) is int and 0 <= lower <= upper:
                return ('lines', original[1] + lower, min(original[1] + upper - 1, original[2]))
            return None
        if not isinstance(node, ast.Call):
            return None
        if isinstance(node.func, ast.Name) and node.func.id == 'compile':
            if len(node.args) != 3 or value(node.args[2]) != 'exec' or node.keywords:
                return None
            return value(node.args[0])
        if not isinstance(node.func, ast.Attribute):
            return None
        if node.func.attr == 'read_text' and node.lineno == read_line:
            return ('text', 1, source_lines)
        receiver = value(node.func.value)
        if node.func.attr == 'splitlines' and not node.args and not node.keywords:
            if isinstance(receiver, tuple) and receiver[0] == 'text':
                return ('lines', receiver[1], receiver[2])
        if node.func.attr == 'join' and receiver == '\n' and len(node.args) == 1 and not node.keywords:
            lines = value(node.args[0])
            if isinstance(lines, tuple) and lines[0] == 'lines':
                return ('text', lines[1], lines[2])
        return None

    for statement in loader.body:
        if isinstance(statement, ast.Expr) and statement.value is execution:
            text = value(execution.args[0]) if execution.args else None
            if isinstance(text, tuple) and text[0] == 'text':
                return text[1], text[2]
            return None
        if isinstance(statement, (ast.Assign, ast.AnnAssign)):
            result = value(statement.value) if statement.value else None
            targets = statement.targets if isinstance(statement, ast.Assign) else [statement.target]
            for target in targets:
                if isinstance(target, ast.Name):
                    values[target.id] = result
        elif isinstance(statement, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            values[statement.name] = None
        elif not isinstance(statement, ast.Pass):
            return None
    return None


def python_dynamic_loader_evidence(sources: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Recover visible file/function-object provenance; never execute loader code."""
    test = next((source for source in sources if source["source_id"] == "test"), None)
    if test is None:
        return []
    try:
        tree = ast.parse(test["text"], filename=test["logical_path"])
    except SyntaxError:
        return []
    path_bindings, path_lines = _path_bindings(tree, test["logical_path"])
    product_trees: dict[str, tuple[dict[str, Any], ast.Module]] = {}
    for source in sources:
        if source is test:
            continue
        try:
            product_trees[source["logical_path"]] = (source, ast.parse(source["text"]))
        except SyntaxError:
            continue

    results: list[dict[str, Any]] = []
    loaders = [node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))]
    for loader in loaders:
        read_paths: list[tuple[str | None, int, str | None]] = []
        execs: list[tuple[ast.Call, str]] = []
        aliases: dict[str, tuple[str, str]] = {}
        for node in ast.walk(loader):
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                origin = _namespace_item(node.value, aliases) if node.value is not None else None
                if origin is not None:
                    for target in targets:
                        if isinstance(target, ast.Name):
                            aliases[target.id] = origin
                else:
                    for target in targets:
                        if isinstance(target, ast.Name):
                            aliases.pop(target.id, None)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                    and node.func.attr in {"read_text", "read_bytes"}:
                path = _path_value(node.func.value, path_bindings, test["logical_path"])
                path_name = node.func.value.id if isinstance(node.func.value, ast.Name) else None
                read_paths.append((path, node.lineno, path_name))
            if isinstance(node, ast.Call) and _call_name(node) == "exec" and len(node.args) >= 2 \
                    and isinstance(node.args[1], ast.Name):
                execs.append((node, node.args[1].id))
        if not read_paths or not execs:
            continue
        path, read_line, path_name = read_paths[0]
        exec_call, namespace = execs[0]
        returns: list[ast.Return] = [
            node for node in ast.walk(loader)
            if isinstance(node, ast.Return) and node.value is not None
        ]
        for returned in returns:
            if returned.value is None:
                continue
            values: list[ast.AST] = (list(returned.value.elts)
                                     if isinstance(returned.value, (ast.Tuple, ast.List))
                                     else [returned.value])
            for index, value in enumerate(values):
                origin = _namespace_item(value, aliases)
                base = {"loader": loader.name, "path": path,
                        "path_line": path_lines.get(path_name or "", read_line),
                        "namespace": namespace, "exec_line": exec_call.lineno,
                        "return_line": returned.lineno}
                if isinstance(value, ast.Subscript) and isinstance(value.value, ast.Name) \
                        and value.value.id == namespace and origin is None:
                    results.append({"status": "unknown", **base,
                                    "reason": "dynamic_namespace_key"})
                    continue
                if origin is None or origin[0] != namespace:
                    continue
                key = origin[1]
                if path is None:
                    results.append({"status": "unknown", **base, "function_key": key,
                                    "reason": "unresolved_visible_source_path"})
                    continue
                overwritten = any(
                    isinstance(node, (ast.Assign, ast.AnnAssign))
                    and returned.lineno > node.lineno > exec_call.lineno
                    and any(
                        (isinstance(target, ast.Name) and target.id == namespace)
                        or (isinstance(target, ast.Subscript)
                            and isinstance(target.value, ast.Name) and target.value.id == namespace
                            and (not isinstance(target.slice, ast.Constant) or target.slice.value == key))
                        for target in (node.targets if isinstance(node, ast.Assign) else [node.target])
                    )
                    for node in ast.walk(loader)
                )
                if overwritten:
                    results.append({"status": "unknown", **base, "function_key": key,
                                    "reason": "namespace_key_rebound"})
                    continue
                selected = product_trees.get(path)
                if selected is None:
                    results.append({"status": "unknown", **base, "function_key": key,
                                    "reason": "visible_source_not_selected"})
                    continue
                product, product_tree = selected
                definitions = [node for node in product_tree.body
                               if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                               and node.name == key]
                span = _exec_source_span(loader, exec_call, read_line, len(product['text'].splitlines()))
                if span is None or len(read_paths) != 1 or len(execs) != 1:
                    results.append({"status": "unknown", **base, "function_key": key,
                                    "reason": "exec_source_provenance_not_supported"})
                    continue
                start, end = span
                definitions = [node for node in definitions
                               if start <= node.lineno
                               and getattr(node, "end_lineno", node.lineno) <= end]
                if len(definitions) != 1:
                    results.append({"status": "unknown", **base, "function_key": key,
                                    "reason": "function_definition_not_unique"})
                    continue
                definition = definitions[0]
                test_bindings: list[dict[str, Any]] = []
                bound_names: set[str] = set()
                for assignment in (node for node in ast.walk(tree)
                                   if isinstance(node, (ast.Assign, ast.AnnAssign))):
                    target = assignment.targets[0] if isinstance(assignment, ast.Assign) else assignment.target
                    call = assignment.value
                    if isinstance(call, ast.Call) and _call_name(call) == loader.name:
                        names = _assigned_slots(target)
                        if index < len(names) and names[index] is not None:
                            bound_name = names[index]
                            assert bound_name is not None
                            bound_names.add(bound_name)
                            test_bindings.append({"name": bound_name, "line": assignment.lineno})
                changed = True
                while changed:
                    changed = False
                    for assignment in (node for node in ast.walk(tree) if isinstance(node, ast.Assign)):
                        if isinstance(assignment.value, ast.Name) and assignment.value.id in bound_names:
                            for name in _assigned_slots(assignment.targets[0]):
                                if name is not None and name not in bound_names:
                                    bound_names.add(name)
                                    test_bindings.append({"name": name, "line": assignment.lineno})
                                    changed = True
                call_sites = sorted({(node.func.id, node.lineno) for node in ast.walk(tree)
                                     if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                                     and node.func.id in bound_names})
                binding_lines = {row["line"] for row in test_bindings}
                last_call_line = max((line for _, line in call_sites), default=-1)
                rebound = any(
                    assignment.lineno not in binding_lines
                    and min(binding_lines) < assignment.lineno < last_call_line
                    and any(name is not None and name in bound_names for target in (
                        assignment.targets if isinstance(assignment, ast.Assign) else [assignment.target]
                    ) for name in _assigned_slots(target))
                    for assignment in ast.walk(tree)
                    if isinstance(assignment, (ast.Assign, ast.AnnAssign))
                ) if test_bindings else False
                if rebound:
                    results.append({"status": "unknown", **base, "function_key": key,
                                    "reason": "test_callable_rebound"})
                    continue
                results.append({"status": "resolved", **base, "read_line": read_line,
                                "function_key": key, "return_index": index,
                                "function_definition": {
                                    "source_id": product["source_id"], "logical_path": path,
                                    "name": definition.name, "start_line": definition.lineno,
                                    "end_line": getattr(definition, "end_lineno", definition.lineno),
                                },
                                "test_bindings": sorted(test_bindings, key=lambda row: row["line"]),
                                "call_sites": [{"name": name, "line": line}
                                               for name, line in call_sites]})
    return sorted(results, key=lambda row: (row["loader"], row["return_line"],
                                            row.get("function_key", "")))


def _python_loader_support_paths(text: str, logical_path: str, paths: tuple[str, ...],
                                 target_symbol: str | None
                                 ) -> list[str]:
    """Find explicitly imported local loader helpers, without scanning other tests."""
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return []
    targets = [node for node in ast.walk(tree)
               if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
               and node.name == target_symbol]
    if len(targets) != 1:
        return []
    relevant_nodes = list(ast.walk(targets[0]))
    names = {node.id for node in relevant_nodes if isinstance(node, ast.Name)}
    for statement in tree.body:
        if isinstance(statement, (ast.Assign, ast.AnnAssign)):
            assigned = {name.id for target in (
                statement.targets if isinstance(statement, ast.Assign) else [statement.target])
                for name in ast.walk(target) if isinstance(name, ast.Name)}
            if assigned & names:
                relevant_nodes.extend(ast.walk(statement))
    calls = {node.func.id for node in relevant_nodes
             if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)}
    results = []
    for node in tree.body:
        if not isinstance(node, ast.ImportFrom) or not node.module or node.level < 1:
            continue
        if not any(alias.name in _PYTHON_LOADER_NAMES and (alias.asname or alias.name) in calls
                   for alias in node.names):
            continue
        directory = posixpath.dirname(logical_path)
        for _ in range(node.level - 1):
            directory = posixpath.dirname(directory)
        candidate = posixpath.normpath(posixpath.join(directory, node.module.replace(".", "/") + ".py"))
        if candidate in paths and candidate != logical_path and candidate not in results:
            results.append(candidate)
    return results


def select_sources(case: Any, *, max_files: int = 8, max_bytes: int = 200_000,
                   focal_file_bytes: int = PYTHON_FOCAL_FILE_BYTES) -> dict[str, Any]:
    """Select bounded source candidates using only evidence visible in admitted sources.

    ``max_files`` includes the masked test.  Files are atomic: a candidate that
    would exceed ``max_bytes`` is omitted rather than truncated, except for one
    Python exact focal path within the separately recorded ``focal_file_bytes``
    allowance. Further expansion stops after that allowance is used. Because the
    FrozenCase API intentionally exposes no pre-read size, that atomic candidate
    is authenticated and read before the output budget decision.
    """
    if type(max_files) is not int or max_files < 1:
        raise ValueError("max_files must be a positive integer")
    if type(max_bytes) is not int or max_bytes < 1:
        raise ValueError("max_bytes must be a positive integer")
    if type(focal_file_bytes) is not int or focal_file_bytes < 0:
        raise ValueError("focal_file_bytes must be a nonnegative integer")
    if case.language not in {"python", "java"}:
        raise ValueError("case language must be python or java")

    test = case.masked_test
    test_bytes = len(test.text.encode("utf-8"))
    sources = [{"source_id": "test", "logical_path": test.logical_path,
                "text": test.text, "start_line": 1}]
    evidence: list[dict[str, Any]] = []
    diagnostics: list[str] = []
    source_rejections: list[dict[str, str]] = []
    opaque_methods: list[dict[str, Any]] = []
    truncated = test_bytes > max_bytes
    used_bytes = test_bytes
    read_bytes = test_bytes
    attempted_files = 0
    focal_allowance: dict[str, Any] | None = None
    budget_stopped = truncated
    if truncated:
        diagnostics.append("masked_test_exceeds_byte_budget_but_is_required")

    support_paths: set[str] = set()
    if case.language == "python" and not budget_stopped:
        selector = getattr(case, "target_selector", None)
        for path in _python_loader_support_paths(
                test.text, test.logical_path, case.source_paths,
                selector.rsplit("::", 1)[-1] if selector else None):
            if attempted_files >= max_files - 1:
                diagnostics.append("loader_support_file_budget_exhausted")
                truncated = True
                break
            attempted_files += 1
            source = case.read_source(path)
            size = len(source.text.encode("utf-8"))
            read_bytes += size
            if size > PYTHON_LOADER_SUPPORT_FILE_BYTES or used_bytes + size > max_bytes:
                diagnostics.append(f"loader_support_byte_budget_omitted:{path}")
                truncated = True
                continue
            sources.append({"source_id": f"source:{len(sources)}", "logical_path": path,
                            "text": source.text, "start_line": source.start_line,
                            "selection_role": "loader_support"})
            used_bytes += size
            support_paths.add(path)
            evidence.append({"stage": "loader_support", "hint_kind": "explicit_relative_import",
                             "hint": path, "candidates": [path],
                             "precision": "support_source_only_not_product_binding"})

    available = tuple(
        path for path in case.source_paths
        if _is_product_source(path, test.logical_path)
        and (case.language != "java" or path.startswith("src/main/"))
        and path not in support_paths
    )
    def extract(text: str, logical_path: str):
        return (_python_hints(text, logical_path) if case.language == "python"
                else _java_hints(text))

    if case.language == "python":
        selector = getattr(case, "target_selector", None)
        initial_hints, initial_diagnostics = _python_hints(
            test.text, test.logical_path,
            target_symbol=selector.rsplit("::", 1)[-1] if selector else None)
    else:
        selector = getattr(case, "target_selector", None)
        initial_hints, initial_diagnostics = _java_hints(
            test.text, target_selector=selector)
    diagnostics.extend(initial_diagnostics)
    python_source_roots = (_python_source_roots(initial_hints, available)
                           if case.language == "python" else set())
    queue = deque((hint, "masked_test") for hint in initial_hints)
    considered: set[tuple[str, str]] = set()
    selected_paths: set[str] = set()
    attempted_paths: set[str] = set()
    primary_paths: list[str] = []

    while queue and not budget_stopped:
        hint, stage = queue.popleft()
        key = (hint["kind"], hint["value"])
        if key in considered:
            continue
        considered.add(key)
        candidates = sorted(set(_matches(
            hint, available, case.language, python_source_roots)) - selected_paths)
        if not candidates:
            continue
        selection_evidence = {"stage": stage, "hint_kind": hint["kind"],
                              "hint": hint["value"], "candidates": candidates,
                              "precision": _precision(hint, candidates)}
        if "selection_priority" in hint:
            selection_evidence["selection_priority"] = hint["selection_priority"]
        evidence.append(selection_evidence)
        for path in candidates:
            if path in attempted_paths:
                continue
            if attempted_files >= max_files - 1:
                truncated = True
                diagnostics.append("file_budget_exhausted")
                budget_stopped = True
                break
            attempted_paths.add(path)
            attempted_files += 1
            try:
                source = case.read_source(path)
            except InputValidationError as exc:
                if str(exc) != "frozen source is empty":
                    raise
                source_rejections.append({"logical_path": path, "reason": str(exc)})
                diagnostics.append(f"empty_source_rejected:{path}")
                continue
            size = len(source.text.encode("utf-8"))
            read_bytes += size
            if used_bytes + size > max_bytes:
                focal = (case.language == "python" and stage == "masked_test"
                         and hint.get("focal") == "True" and len(candidates) == 1
                         and _precision(hint, candidates) == "exact_visible_path")
                if not focal or size > focal_file_bytes:
                    truncated = True
                    diagnostics.append(f"byte_budget_omitted:{path}")
                    if focal:
                        diagnostics.append(f"focal_file_cap_exceeded:{path}")
                    budget_stopped = True
                    break
                focal_allowance = {"logical_path": path, "bytes": size,
                                   "limit_bytes": focal_file_bytes,
                                   "reason": "unique exact path in selected test or relevant helper"}
                diagnostics.append(f"focal_file_allowance_used:{path}")
                truncated = True
                budget_stopped = True
            source_id = f"source:{len(sources)}"
            sources.append({"source_id": source_id, "logical_path": path,
                            "text": source.text, "start_line": source.start_line})
            used_bytes += size
            selected_paths.add(path)
            if stage == "masked_test":
                primary_paths.append(path)
            opaque_methods.extend(
                _python_opaque(source_id, source.text) if case.language == "python"
                else _java_opaque(source_id, source.text)
            )

    # Only one import hop from sources selected directly by the masked test.
    for path in primary_paths:
        if budget_stopped:
            break
        source = next(item for item in sources if item["logical_path"] == path)
        hints, nested_diagnostics = extract(source["text"], source["logical_path"])
        diagnostics.extend(nested_diagnostics)
        for hint in hints:
            candidates = sorted(set(_matches(
                hint, available, case.language, python_source_roots)) - selected_paths)
            if not candidates:
                continue
            evidence.append({"stage": "one_hop_import", "from_path": path,
                             "hint_kind": hint["kind"], "hint": hint["value"],
                             "candidates": candidates,
                             "precision": _precision(hint, candidates)})
            for candidate in candidates:
                if candidate in attempted_paths:
                    continue
                if attempted_files >= max_files - 1:
                    truncated = True
                    diagnostics.append("file_budget_exhausted")
                    budget_stopped = True
                    break
                attempted_paths.add(candidate)
                attempted_files += 1
                try:
                    selected = case.read_source(candidate)
                except InputValidationError as exc:
                    if str(exc) != "frozen source is empty":
                        raise
                    source_rejections.append({"logical_path": candidate,
                                              "reason": str(exc)})
                    diagnostics.append(f"empty_source_rejected:{candidate}")
                    continue
                size = len(selected.text.encode("utf-8"))
                read_bytes += size
                if used_bytes + size > max_bytes:
                    truncated = True
                    diagnostics.append(f"byte_budget_omitted:{candidate}")
                    budget_stopped = True
                    break
                source_id = f"source:{len(sources)}"
                sources.append({"source_id": source_id, "logical_path": candidate,
                                "text": selected.text, "start_line": selected.start_line})
                used_bytes += size
                selected_paths.add(candidate)
                opaque_methods.extend(
                    _python_opaque(source_id, selected.text) if case.language == "python"
                    else _java_opaque(source_id, selected.text)
                )

    dynamic_loader_bindings = (python_dynamic_loader_evidence(sources)
                               if case.language == "python" else [])
    return {"sources": sources, "evidence": evidence, "diagnostics": diagnostics,
            "source_rejections": source_rejections,
            "dynamic_loader_bindings": dynamic_loader_bindings,
            "truncated": truncated, "opaque_methods": opaque_methods,
            "selected_bytes": used_bytes, "read_bytes": read_bytes,
            "attempted_files": attempted_files,
            "budgets": {"max_files": max_files, "max_bytes": max_bytes,
                        "focal_file_bytes": focal_file_bytes if case.language == "python" else 0,
                        "loader_support_file_bytes": PYTHON_LOADER_SUPPORT_FILE_BYTES
                        if case.language == "python" else 0},
            "focal_file_allowance": focal_allowance,
            "limitations": [
                "Selection uses visible imports, same-package Java type names, and statically resolved visible source paths.",
                "A matching path is a source candidate, not proof that it is the MUT or reachable.",
                "Only one import hop from masked-test-selected product sources is considered.",
                "A Python exact focal file may use a separate finite allowance; its size and cap are recorded and further expansion stops.",
                "Explicit local loader imports may supply bounded support source; selection alone does not authenticate the loader semantics.",
                "Opaque method detection recognizes only a narrow single-throw placeholder pattern.",
                "Source reads are atomic; the final attempted file may make read_bytes exceed max_bytes.",
            ]}
