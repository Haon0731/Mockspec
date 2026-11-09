"""Conservative slicing-criterion extraction from a selected masked test.

This module locates the selected test before looking for observations.  It does
not bind calls, infer an oracle from variable names, or perform slicing.
"""
from __future__ import annotations

import ast
import re
from typing import Any

from survey_targets import java_observations


_PYTHON_VALUE_ASSERTIONS = {
    "assertEqual", "assertNotEqual", "assertTrue", "assertFalse", "assertIs",
    "assertIsNot", "assertIsNone", "assertIsNotNone", "assertIn", "assertNotIn",
    "assertAlmostEqual", "assertCountEqual", "assertDictEqual", "assertListEqual",
}
_PYTHON_EXCEPTION_OBSERVATIONS = {"raises", "assertRaises", "assertRaisesRegex"}
_PYTHON_INTERACTION_OBSERVATIONS = {
    "assert_called", "assert_called_once", "assert_called_with",
    "assert_called_once_with", "assert_not_called", "assert_any_call",
    "assert_has_calls", "assert_awaited", "assert_awaited_once",
    "assert_awaited_with", "assert_awaited_once_with", "assert_not_awaited",
}
_DYNAMIC_LOADING = {
    "load_function_from_source_ast", "exec", "eval", "compile", "import_module",
    "spec_from_file_location", "module_from_spec", "forName", "getDeclaredMethod", "invoke",
}
_JAVA_OBSERVATION_KINDS = (
    "value_assertion", "exception_observation", "interaction_observation",
)


def _call_name(call: ast.Call) -> str:
    if isinstance(call.func, ast.Name):
        return call.func.id
    if isinstance(call.func, ast.Attribute):
        return call.func.attr
    return ""


def _criterion(line: int, kind: str, confidence: str, reason: str, **extra: Any) -> dict:
    value = {"line": line, "kind": kind, "confidence": confidence, "reason": reason}
    value.update(extra)
    return value


class _FunctionBodyVisitor(ast.NodeVisitor):
    """Visit one function body without absorbing observations from nested functions."""

    def __init__(self) -> None:
        self.nodes: list[ast.AST] = []

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # pragma: no cover - dispatch guard
        return

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:  # pragma: no cover
        return

    def visit_Lambda(self, node: ast.Lambda) -> None:
        return

    def generic_visit(self, node: ast.AST) -> None:
        self.nodes.append(node)
        super().generic_visit(node)


def _python_criteria(text: str, target_selector: str) -> dict:
    diagnostics: list[dict[str, str]] = []
    try:
        tree = ast.parse(text)
    except SyntaxError as exc:
        return _result(target_selector, "python_ast", [], [], [], [
            {"code": "parse_error", "message": f"{exc.msg} at line {exc.lineno}"}
        ])

    symbol = target_selector.rsplit("::", 1)[-1]
    matches = [node for node in ast.walk(tree)
               if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == symbol]
    if len(matches) != 1:
        code = "target_not_found" if not matches else "target_ambiguous"
        diagnostics.append({"code": code, "message": f"selector resolved to {len(matches)} functions"})
        return _result(target_selector, "python_ast", [], [], [], diagnostics)

    function = matches[0]
    scope = [{"symbol": symbol, "start_line": function.lineno,
              "end_line": function.end_lineno or function.lineno}]
    visitor = _FunctionBodyVisitor()
    for statement in function.body:
        visitor.visit(statement)

    criteria: list[dict] = []
    calls: list[ast.Call] = []
    dynamic: list[dict] = []
    for node in visitor.nodes:
        if isinstance(node, ast.Assert):
            criteria.append(_criterion(node.lineno, "value_assertion", "explicit",
                                        "Python assert in the selected test function"))
        if not isinstance(node, ast.Call):
            continue
        calls.append(node)
        name = _call_name(node)
        if name in _DYNAMIC_LOADING:
            dynamic.append({"line": node.lineno, "name": name, "precision": "python_ast_call_name"})
        if name in _PYTHON_EXCEPTION_OBSERVATIONS:
            criteria.append(_criterion(node.lineno, "exception_observation", "explicit",
                                        f"{name} in the selected test function", call=name))
        elif name in _PYTHON_INTERACTION_OBSERVATIONS:
            criteria.append(_criterion(node.lineno, "interaction_observation", "explicit",
                                        f"{name} in the selected test function", call=name))
        elif name in _PYTHON_VALUE_ASSERTIONS:
            criteria.append(_criterion(node.lineno, "value_assertion", "explicit",
                                        f"{name} in the selected test function", call=name))

    if not criteria:
        ignored = (_PYTHON_VALUE_ASSERTIONS | _PYTHON_EXCEPTION_OBSERVATIONS |
                   _PYTHON_INTERACTION_OBSERVATIONS | _DYNAMIC_LOADING)
        seen: set[tuple[int, str]] = set()
        for call in calls:
            name = _call_name(call)
            key = (call.lineno, name)
            if not name or name in ignored or key in seen:
                continue
            seen.add(key)
            criteria.append(_criterion(
                call.lineno, "candidate_event", "candidate",
                "visible call in a selected test with no explicit observation; it is not an oracle",
                call=name,
            ))
        diagnostics.append({
            "code": "no_explicit_observation",
            "message": "candidate events do not establish expected values, arguments, counts, or reachability",
        })
    return _result(target_selector, "python_ast", scope, criteria, dynamic, diagnostics)


def _strip_java_comments_and_literals(text: str) -> str:
    return re.sub(
        r'//[^\n]*|/\*.*?\*/|""".*?"""|"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'',
        lambda match: "".join("\n" if char == "\n" else " " for char in match.group()),
        text,
        flags=re.DOTALL,
    )


def _java_method_ranges(code: str, symbol: str) -> list[tuple[int, int, int, int]]:
    declaration = re.compile(
        rf"(?m)^[^\n;{{}}]*\b{re.escape(symbol)}\s*\([^;{{}}]*\)"
        rf"\s*(?:throws\s+[^{{]+)?\{{"
    )
    ranges = []
    for match in declaration.finditer(code):
        open_brace = code.find("{", match.start(), match.end())
        depth = 0
        close_brace = None
        for offset in range(open_brace, len(code)):
            if code[offset] == "{":
                depth += 1
            elif code[offset] == "}":
                depth -= 1
                if depth == 0:
                    close_brace = offset
                    break
        if close_brace is not None:
            ranges.append((match.start(), close_brace + 1,
                           code.count("\n", 0, match.start()) + 1,
                           code.count("\n", 0, close_brace) + 1))
    return ranges


def _java_criteria(text: str, target_selector: str) -> dict:
    code = _strip_java_comments_and_literals(text)
    symbol = target_selector.rsplit("#", 1)[-1]
    ranges = _java_method_ranges(code, symbol)
    if len(ranges) != 1:
        code_name = "target_not_found" if not ranges else "target_ambiguous"
        return _result(target_selector, "java_lexical_method_scope", [], [], [], [{
            "code": code_name, "message": f"selector resolved to {len(ranges)} lexical method ranges",
        }])

    start, end, start_line, end_line = ranges[0]
    method = code[start:end]
    observations = java_observations(method)
    criteria = []
    for kind in _JAVA_OBSERVATION_KINDS:
        for line in observations[kind]:
            criteria.append(_criterion(
                start_line + line - 1, kind, "explicit",
                "lexical observation syntax inside the selected Java method",
                precision="java_lexical_method_scope",
            ))
    dynamic = [
        {"line": start_line + line - 1, "name": "unresolved_dynamic_call",
         "precision": "java_lexical_method_scope"}
        for line in observations["dynamic_loading"]
    ]
    diagnostics: list[dict[str, str]] = []
    if not criteria:
        ignored = {
            "if", "for", "while", "switch", "catch", "synchronized", "return", "new",
            "assertEquals", "assertNotEquals", "assertThat", "assertTrue", "assertFalse",
            "assertNull", "assertNotNull", "assertThrows", "verify", "inOrder",
        }
        seen: set[tuple[int, str]] = set()
        for call in re.finditer(r"\b([A-Za-z_$][\w$]*)\s*\(", method):
            name = call.group(1)
            line = start_line + method.count("\n", 0, call.start())
            key = (line, name)
            if name == symbol or name in ignored or key in seen:
                continue
            seen.add(key)
            criteria.append(_criterion(
                line, "candidate_event", "candidate",
                "lexically visible call in a selected method with no explicit observation; it is not an oracle",
                call=name, precision="java_lexical_method_scope",
            ))
        diagnostics.append({
            "code": "no_explicit_observation",
            "message": "candidate events do not establish expected values, arguments, counts, or reachability",
        })
    criteria.sort(key=lambda item: (item["line"], item["kind"], item.get("call", "")))
    return _result(
        target_selector, "java_lexical_method_scope",
        [{"symbol": symbol, "start_line": start_line, "end_line": end_line}],
        criteria, dynamic, diagnostics,
    )


def _result(selector: str, precision: str, locations: list[dict], criteria: list[dict],
            dynamic_loading: list[dict], diagnostics: list[dict]) -> dict:
    return {
        "target_scope": {
            "selector": selector,
            "status": "located" if locations else "not_located",
            "precision": precision,
            "locations": locations,
        },
        "criteria": sorted(criteria, key=lambda item: (item["line"], item["kind"])),
        "dynamic_loading": sorted(dynamic_loading, key=lambda item: item["line"]),
        "diagnostics": diagnostics,
    }


def extract_criteria(text: str, language: str, target_selector: str) -> dict:
    """Extract conservative observation roots within the selected test only."""
    if language == "python":
        return _python_criteria(text, target_selector)
    if language == "java":
        return _java_criteria(text, target_selector)
    raise ValueError("language must be python or java")
