"""Bounded, evidence-preserving source selection for slicing research.

This module never scans source contents to discover a target.  It derives path
candidates from the already admitted masked test, matches them against
``FrozenCase.source_paths``, and reads only the selected candidates through the
case's authenticated ``read_source`` API.
"""
from __future__ import annotations

import ast
from collections import deque
import re
from typing import Any

from mock_spec.validation import InputValidationError


_PYTHON_LOADER_NAMES = {
    "load_function_from_source_ast",
    "load_function_and_class_from_source_ast",
    "spec_from_file_location",
}
_JAVA_IMPORT = re.compile(r"(?m)^\s*import\s+(?:static\s+)?([\w.]+)(?:\.\*)?\s*;")
_JAVA_PACKAGE = re.compile(r"(?m)^\s*package\s+([\w.]+)\s*;")
_JAVA_TYPE_NAME = re.compile(r"\b[A-Z][A-Za-z0-9_]*\b")


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


def _python_hints(text: str) -> tuple[list[dict[str, str]], list[str]]:
    diagnostics: list[str] = []
    try:
        tree = ast.parse(text)
    except SyntaxError as exc:
        return [], [f"python_parse_error:{exc.lineno}"]
    bindings = _literal_bindings(tree)
    hints: list[dict[str, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                hints.append({"kind": "python_import", "value": alias.name})
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            hints.append({"kind": "python_import", "value": node.module})
        elif isinstance(node, ast.Call):
            name = node.func.attr if isinstance(node.func, ast.Attribute) else (
                node.func.id if isinstance(node.func, ast.Name) else ""
            )
            if name in _PYTHON_LOADER_NAMES and node.args:
                path = _string_value(node.args[0], bindings)
                if path is not None:
                    hints.append({"kind": "python_dynamic_path", "value": path})
                else:
                    diagnostics.append(f"unresolved_dynamic_path:{getattr(node, 'lineno', 0)}")
    return hints, diagnostics


def _java_hints(text: str) -> tuple[list[dict[str, str]], list[str]]:
    code = _strip_java_comments_and_literals(text)
    hints = [{"kind": "java_import", "value": name}
             for name in _JAVA_IMPORT.findall(code) if not name.endswith(".*")]
    package = _JAVA_PACKAGE.search(code)
    if package:
        imported = {hint["value"].rsplit(".", 1)[-1] for hint in hints}
        for name in sorted(set(_JAVA_TYPE_NAME.findall(code)) - imported):
            hints.append({"kind": "java_same_package_type",
                          "value": package.group(1) + "." + name})
    return hints, []


def _matches(hint: dict[str, str], paths: tuple[str, ...], language: str) -> list[str]:
    value = hint["value"].replace("\\", "/").lstrip("./")
    if hint["kind"] == "python_dynamic_path":
        return [path for path in paths if path == value or path.endswith("/" + value)]
    if language == "python":
        module = value.replace(".", "/")
        suffixes = (module + ".py", module + "/__init__.py")
        return [path for path in paths if any(path == suffix or path.endswith("/" + suffix)
                                               for suffix in suffixes)]
    suffix = value.replace(".", "/") + ".java"
    return [path for path in paths if path == suffix or path.endswith("/" + suffix)]


def _precision(hint: dict[str, str], candidates: list[str]) -> str:
    if len(candidates) > 1:
        return "candidate"
    if hint["kind"] == "python_dynamic_path":
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
    pattern = re.compile(
        r"(?ms)(?:public|protected|private|static|final|synchronized|native|abstract|\s)+"
        r"[\w<>?,.\[\]\s]+\s+(\w+)\s*\([^{};]*\)\s*(?:throws\s+[^{}]+)?\{\s*"
        r"throw\s+new\s+(?:UnsupportedOperationException|NotImplementedException)\s*\([^;]*\);\s*\}"
    )
    return [{"source_id": source_id, "name": match.group(1),
             "start_line": text.count("\n", 0, match.start()) + 1,
             "end_line": text.count("\n", 0, match.end()) + 1}
            for match in pattern.finditer(text)]


def select_sources(case: Any, *, max_files: int = 8, max_bytes: int = 200_000) -> dict[str, Any]:
    """Select bounded source candidates using only evidence visible in admitted sources.

    ``max_files`` includes the masked test.  Files are atomic: a candidate that
    would exceed ``max_bytes`` is omitted rather than truncated.  Because the
    FrozenCase API intentionally exposes no pre-read size, that atomic candidate
    is authenticated and read before the output budget decision.
    """
    if type(max_files) is not int or max_files < 1:
        raise ValueError("max_files must be a positive integer")
    if type(max_bytes) is not int or max_bytes < 1:
        raise ValueError("max_bytes must be a positive integer")
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
    budget_stopped = truncated
    if truncated:
        diagnostics.append("masked_test_exceeds_byte_budget_but_is_required")

    available = tuple(path for path in case.source_paths
                      if _is_product_source(path, test.logical_path))
    extractor = _python_hints if case.language == "python" else _java_hints
    initial_hints, initial_diagnostics = extractor(test.text)
    diagnostics.extend(initial_diagnostics)
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
        candidates = sorted(set(_matches(hint, available, case.language)) - selected_paths)
        if not candidates:
            continue
        evidence.append({"stage": stage, "hint_kind": hint["kind"],
                         "hint": hint["value"], "candidates": candidates,
                         "precision": _precision(hint, candidates)})
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
                truncated = True
                diagnostics.append(f"byte_budget_omitted:{path}")
                budget_stopped = True
                break
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
        hints, nested_diagnostics = extractor(source["text"])
        diagnostics.extend(nested_diagnostics)
        for hint in hints:
            candidates = sorted(set(_matches(hint, available, case.language)) - selected_paths)
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

    return {"sources": sources, "evidence": evidence, "diagnostics": diagnostics,
            "source_rejections": source_rejections,
            "truncated": truncated, "opaque_methods": opaque_methods,
            "selected_bytes": used_bytes, "read_bytes": read_bytes,
            "attempted_files": attempted_files,
            "limitations": [
                "Selection uses visible imports, same-package Java type names, and literal dynamic loader paths.",
                "A matching path is a source candidate, not proof that it is the MUT or reachable.",
                "Only one import hop from masked-test-selected product sources is considered.",
                "Opaque method detection recognizes only a narrow single-throw placeholder pattern.",
                "Source reads are atomic; the final attempted file may make read_bytes exceed max_bytes.",
            ]}
