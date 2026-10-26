"""Read-only Python syntax facts from source text already admitted by an Adapter.

The analyzer deliberately reports AST and lexical-containment facts only.  It does
not import the source or infer control flow, reaching definitions, call targets,
or path feasibility.
"""

from __future__ import annotations

import asyncio
import ast
import json
import math
from pathlib import Path
import sys
import tempfile
import textwrap
from typing import Any

from .analysis_process import communicate as _communicate
from .schemas import Source
from .validation import decode_json


BACKEND = "cpython_ast"
_MAX_SOURCE_CHARS = 1_000_000
_MAX_AST_NODES = 100_000
_MAX_PROCESS_OUTPUT = 1_000_000
_LIMITATIONS = [
    "Syntax and lexical-containment facts only; no CFG or path-feasibility proof.",
    "Calls, aliases, and cross-statement def-use relationships are unresolved.",
    "Exception syntax does not prove exception propagation or handler reachability.",
]
_DIRECT_DEADLINE_LIMITATION = (
    "The direct parser has no independent wall-clock deadline; callers that need one "
    "must run it in an isolated process."
)
_ISOLATED_DEADLINE_LIMITATION = (
    "The host enforces the configured wall-clock deadline by terminating the isolated "
    "analysis process."
)


def _render(node: ast.AST) -> str:
    try:
        return ast.unparse(node)
    except (AttributeError, ValueError):
        return type(node).__name__


class _FactCollector(ast.NodeVisitor):
    def __init__(self, source: Source, parsed_text: str) -> None:
        self.source = source
        self.parsed_text = parsed_text
        self.original_lines = source.text.splitlines()
        self.facts: list[dict[str, Any]] = []
        self.guards: list[dict[str, Any]] = []
        self.parents: list[ast.AST] = []

    def _line(self, local_line: int) -> int:
        return self.source.start_line + local_line - 1

    def _location(self, node: ast.AST) -> dict[str, Any]:
        start_local = getattr(node, "lineno", 1)
        end_local = getattr(node, "end_lineno", start_local)
        start_index = max(0, start_local - 1)
        end_index = max(start_index + 1, end_local)
        return {
            "source_id": self.source.source_id,
            "start_line": self._line(start_local),
            "end_line": self._line(end_local),
            "quote": "\n".join(self.original_lines[start_index:end_index]),
        }

    def _add(self, kind: str, node: ast.AST, **details: Any) -> None:
        self.facts.append({
            "kind": kind,
            **self._location(node),
            "lexical_guards": [dict(item) for item in self.guards],
            "match_precision": "syntax_match",
            **details,
        })

    def _visit_child(self, child: ast.AST) -> None:
        self.parents.append(child)
        try:
            self.visit(child)
        finally:
            self.parents.pop()

    def _visit_children(self, node: ast.AST) -> None:
        for child in ast.iter_child_nodes(node):
            self._visit_child(child)

    def _qualified_name(self, name: str) -> str:
        owners = [
            owner.name for owner in self.parents[:-1]
            if isinstance(owner, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
        ]
        return ".".join([*owners, name])

    def _add_definition(self, kind: str, node: ast.AST, **details: Any) -> None:
        """Locate a declaration header and retain the complete syntax extent separately."""
        local_line = getattr(node, "lineno", 1)
        end_local = getattr(node, "end_lineno", local_line)
        self.facts.append({
            "kind": kind,
            "source_id": self.source.source_id,
            "start_line": self._line(local_line),
            "end_line": self._line(local_line),
            "body_end_line": self._line(end_local),
            "quote": self.original_lines[max(0, local_line - 1)],
            "lexical_guards": [dict(item) for item in self.guards],
            **details,
        })

    def visit_FunctionDef(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        """Report a located declaration without claiming runtime binding resolution."""
        self._add_definition(
            "function_definition",
            node,
            name=node.name,
            qualified_name=self._qualified_name(node.name),
            asynchronous=isinstance(node, ast.AsyncFunctionDef),
            match_precision="syntax_match",
            resolution_status="resolved_local",
        )
        self._visit_children(node)

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        """Report a located class declaration as a syntax fact."""
        self._add_definition(
            "class_definition",
            node,
            name=node.name,
            qualified_name=self._qualified_name(node.name),
            match_precision="syntax_match",
            resolution_status="resolved_local",
        )
        self._visit_children(node)

    def _visit_branch(
        self,
        owner: ast.AST,
        label: str,
        polarity: bool | None,
        nodes: list[ast.stmt] | ast.AST,
    ) -> None:
        owner_line = self._line(getattr(owner, "lineno", 1))
        guard = {
            "kind": type(owner).__name__.lower(),
            "line": owner_line,
            "branch": label,
            "polarity": polarity,
        }
        self.guards.append(guard)
        try:
            if isinstance(nodes, list):
                for child in nodes:
                    self._visit_child(child)
            else:
                self._visit_child(nodes)
        finally:
            self.guards.pop()

    def visit_If(self, node: ast.If) -> None:
        self._add("condition", node.test, construct="if", expression=_render(node.test))
        self._visit_child(node.test)
        self._add("branch", node, construct="if", branch="true", polarity=True,
                  body_statement_count=len(node.body))
        self._visit_branch(node, "true", True, node.body)
        if node.orelse:
            self._add("branch", node, construct="if", branch="false", polarity=False,
                      body_statement_count=len(node.orelse))
            self._visit_branch(node, "false", False, node.orelse)

    def visit_IfExp(self, node: ast.IfExp) -> None:
        self._add("condition", node.test, construct="if_expression",
                  expression=_render(node.test))
        self._visit_child(node.test)
        self._add("branch", node.body, construct="if_expression", branch="true",
                  polarity=True, body_statement_count=1)
        self._visit_branch(node, "true", True, node.body)
        self._add("branch", node.orelse, construct="if_expression", branch="false",
                  polarity=False, body_statement_count=1)
        self._visit_branch(node, "false", False, node.orelse)

    def visit_Match(self, node: ast.Match) -> None:
        self._add("condition", node.subject, construct="match",
                  expression=_render(node.subject))
        self._visit_child(node.subject)
        owner_line = self._line(node.lineno)
        for index, case in enumerate(node.cases):
            pattern = _render(case.pattern)
            self._add(
                "branch",
                case.pattern,
                construct="match",
                branch=f"case_{index}",
                polarity=None,
                pattern=pattern,
                guard=_render(case.guard) if case.guard is not None else None,
                body_statement_count=len(case.body),
            )
            self._visit_child(case.pattern)
            case_guard = {
                "kind": "match",
                "line": owner_line,
                "branch": f"case_{index}",
                "polarity": None,
            }
            self.guards.append(case_guard)
            try:
                if case.guard is not None:
                    self._add("condition", case.guard, construct="match_guard",
                              expression=_render(case.guard))
                    self._visit_child(case.guard)
                for child in case.body:
                    self._visit_child(child)
            finally:
                self.guards.pop()

    def visit_While(self, node: ast.While) -> None:
        self._add("condition", node.test, construct="while", expression=_render(node.test))
        self._visit_child(node.test)
        self._add("branch", node, construct="while", branch="body", polarity=True,
                  body_statement_count=len(node.body))
        self._visit_branch(node, "body", True, node.body)
        if node.orelse:
            self._add("branch", node, construct="while", branch="else", polarity=False,
                      body_statement_count=len(node.orelse))
            self._visit_branch(node, "else", False, node.orelse)

    def visit_Assert(self, node: ast.Assert) -> None:
        self._add("condition", node.test, construct="assert", expression=_render(node.test))
        self._visit_children(node)

    def visit_For(self, node: ast.For | ast.AsyncFor) -> None:
        self._add("branch", node, construct="async_for" if isinstance(node, ast.AsyncFor)
                  else "for", branch="body", polarity=None,
                  body_statement_count=len(node.body))
        self._visit_child(node.target)
        self._visit_child(node.iter)
        self._visit_branch(node, "body", None, node.body)
        if node.orelse:
            self._add("branch", node, construct="for", branch="else", polarity=None,
                      body_statement_count=len(node.orelse))
            self._visit_branch(node, "else", None, node.orelse)

    visit_AsyncFor = visit_For

    def visit_Try(self, node: ast.Try) -> None:
        self._add("try", node, handler_count=len(node.handlers),
                  has_else=bool(node.orelse), has_finally=bool(node.finalbody))
        self._visit_branch(node, "try_body", None, node.body)
        for handler in node.handlers:
            exception = _render(handler.type) if handler.type is not None else None
            self._add("except_handler", handler, exception=exception, bound_name=handler.name)
            if handler.type is not None:
                self._visit_child(handler.type)
            self._visit_branch(node, "except", None, handler.body)
        if node.orelse:
            self._visit_branch(node, "try_else", None, node.orelse)
        if node.finalbody:
            self._visit_branch(node, "finally", None, node.finalbody)

    # Python 3.11 exception groups have the same lexical shape.
    visit_TryStar = visit_Try

    def visit_With(self, node: ast.With | ast.AsyncWith) -> None:
        items = []
        for item in node.items:
            items.append({
                "context_expression": _render(item.context_expr),
                "bound_name": _render(item.optional_vars) if item.optional_vars else None,
            })
        self._add("with", node, asynchronous=isinstance(node, ast.AsyncWith), items=items)
        for item in node.items:
            self._visit_child(item.context_expr)
            if item.optional_vars is not None:
                self._visit_child(item.optional_vars)
        self._visit_branch(node, "with_body", None, node.body)

    visit_AsyncWith = visit_With

    def visit_Call(self, node: ast.Call) -> None:
        self._add(
            "call",
            node,
            callee=_render(node.func),
            positional_argument_count=len(node.args),
            keyword_arguments=[item.arg for item in node.keywords],
            resolution_status="unresolved",
            match_precision="unresolved",
            syntax_context=self._expression_context(node),
        )
        self._visit_children(node)

    def visit_NamedExpr(self, node: ast.NamedExpr) -> None:
        self._add("named_expression", node, target=_render(node.target),
                  value=_render(node.value), relation="same_expression_assignment")
        self._visit_children(node)

    def visit_Name(self, node: ast.Name) -> None:
        if isinstance(node.ctx, ast.Load):
            self._add(
                "local_reference",
                node,
                name=node.id,
                resolution_status="unresolved",
                match_precision="unresolved",
                relation="lexical_name_read",
            )

    def visit_Return(self, node: ast.Return) -> None:
        self._add("return", node, value=_render(node.value) if node.value else None)
        self._visit_children(node)

    def visit_Raise(self, node: ast.Raise) -> None:
        self._add("raise", node, exception=_render(node.exc) if node.exc else None,
                  cause=_render(node.cause) if node.cause else None)
        self._visit_children(node)

    def _expression_context(self, node: ast.AST) -> dict[str, Any]:
        ancestors = list(reversed(self.parents[:-1]))
        for parent in ancestors:
            if isinstance(parent, (ast.Assign, ast.AnnAssign, ast.NamedExpr)):
                value = parent.value
                if node is value or node in ast.walk(value):
                    if isinstance(parent, ast.Assign):
                        targets = [_render(target) for target in parent.targets]
                    else:
                        targets = [_render(parent.target)]
                    return {
                        "kind": "assignment_rhs",
                        "targets": targets,
                        "relation": "syntactic_containment",
                    }
            if isinstance(parent, ast.Return):
                return {"kind": "return_value", "relation": "syntactic_containment"}
            if isinstance(parent, ast.Raise):
                return {"kind": "raise_expression", "relation": "syntactic_containment"}
            if isinstance(parent, (ast.If, ast.While, ast.IfExp)):
                if node is parent.test or node in ast.walk(parent.test):
                    return {"kind": "condition_test", "relation": "syntactic_containment"}
            if isinstance(parent, (ast.With, ast.AsyncWith)):
                if any(
                    node is item.context_expr or node in ast.walk(item.context_expr)
                    for item in parent.items
                ):
                    return {"kind": "with_context", "relation": "syntactic_containment"}
            if isinstance(parent, ast.Call):
                if node is parent.func or node in ast.walk(parent.func):
                    return {"kind": "call_callee", "callee": _render(parent.func),
                            "relation": "syntactic_containment"}
                return {"kind": "call_argument", "callee": _render(parent.func),
                        "relation": "syntactic_containment"}
        return {"kind": "expression", "relation": "syntactic_containment"}

    def _visit_comprehension_owner(self, node: ast.AST, construct: str) -> None:
        self._add(
            "unsupported_construct",
            node,
            construct=construct,
            resolution_status="unsupported",
            diagnostic=(
                "Comprehension iteration and filters are traversed as syntax but are not "
                "reported as feasible branches."
            ),
        )
        self._visit_children(node)

    def visit_ListComp(self, node: ast.ListComp) -> None:
        self._visit_comprehension_owner(node, "list_comprehension")

    def visit_SetComp(self, node: ast.SetComp) -> None:
        self._visit_comprehension_owner(node, "set_comprehension")

    def visit_DictComp(self, node: ast.DictComp) -> None:
        self._visit_comprehension_owner(node, "dict_comprehension")

    def visit_GeneratorExp(self, node: ast.GeneratorExp) -> None:
        self._visit_comprehension_owner(node, "generator_expression")

    def generic_visit(self, node: ast.AST) -> None:
        self._visit_children(node)


def analyze_source(source: Source) -> dict[str, Any]:
    """Return bounded-host-ready syntax facts for one visible Python source."""

    if not isinstance(source, Source):
        return {
            "status": "error",
            "backend": BACKEND,
            "source_id": None,
            "error_code": "analysis_error",
            "failure_kind": "invalid_request",
            "facts": [],
            "diagnostics": ["Python analysis requires a visible Source."],
            "limitations": list(_LIMITATIONS),
        }
    parsed_text = textwrap.dedent(source.text)
    if len(parsed_text) > _MAX_SOURCE_CHARS:
        return {
            "status": "error",
            "backend": BACKEND,
            "source_id": source.source_id,
            "error_code": "analysis_error",
            "failure_kind": "input_limit",
            "facts": [],
            "diagnostics": ["Python source exceeds the bounded analyzer input size."],
            "limitations": [*_LIMITATIONS, _DIRECT_DEADLINE_LIMITATION],
        }
    try:
        tree = ast.parse(parsed_text)
    except (SyntaxError, ValueError, TypeError) as exc:
        local_line = exc.lineno if isinstance(exc, SyntaxError) and exc.lineno else 1
        visible_line = source.start_line + local_line - 1
        message = exc.msg if isinstance(exc, SyntaxError) else type(exc).__name__
        return {
            "status": "error",
            "backend": BACKEND,
            "source_id": source.source_id,
            "error_code": "analysis_error",
            "failure_kind": "parse_error",
            "facts": [],
            "diagnostics": [f"Python syntax analysis failed at visible line {visible_line}: {message}"],
            "limitations": list(_LIMITATIONS),
        }

    if sum(1 for _ in ast.walk(tree)) > _MAX_AST_NODES:
        return {
            "status": "error",
            "backend": BACKEND,
            "source_id": source.source_id,
            "error_code": "analysis_error",
            "failure_kind": "input_limit",
            "facts": [],
            "diagnostics": ["Python AST exceeds the bounded analyzer node count."],
            "limitations": [*_LIMITATIONS, _DIRECT_DEADLINE_LIMITATION],
        }

    collector = _FactCollector(source, parsed_text)
    collector.visit(tree)
    if not collector.facts:
        return {
            "status": "unresolved",
            "backend": BACKEND,
            "source_id": source.source_id,
            "facts": [],
            "diagnostics": ["No supported syntax facts were present in the visible source."],
            "limitations": list(_LIMITATIONS),
        }
    unsupported = sorted({fact["construct"] for fact in collector.facts
                          if fact["kind"] == "unsupported_construct"})
    return {
        "status": "incomplete" if unsupported else "ok",
        "backend": BACKEND,
        "source_id": source.source_id,
        "facts": collector.facts,
        "diagnostics": [{"category": "unsupported", "construct": kind,
                         "message": "Calls are visible but this construct's branch semantics are unsupported."}
                        for kind in unsupported],
        "limitations": list(_LIMITATIONS),
    }


def _failure(
    source_id: str | None, code: str, message: str, *, failure_kind: str
) -> dict[str, Any]:
    return {
        "status": "error",
        "backend": BACKEND,
        "source_id": source_id,
        "error_code": code,
        "failure_kind": failure_kind,
        "facts": [],
        "diagnostics": [message],
        "limitations": [
            *_LIMITATIONS,
            "Analysis failure does not establish absence of behavior.",
        ],
    }


async def analyze_source_isolated(
    source: Source, *, timeout_seconds: float = 15
) -> dict[str, Any]:
    """Analyze one visible source in a cancellable, credential-free subprocess."""

    if not isinstance(source, Source):
        raise TypeError("source must be a Source")
    if (type(timeout_seconds) not in {int, float}
            or not math.isfinite(timeout_seconds) or timeout_seconds <= 0):
        raise ValueError("timeout_seconds must be positive and finite")
    request = json.dumps(
        source.to_dict(), ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    runner = Path(__file__).with_name("python_analysis_runner.py").resolve()
    try:
        with tempfile.TemporaryDirectory(prefix="mockspec-python-ast-") as work:
            code, stdout, stderr = await _communicate(
                [sys.executable, "-I", str(runner)],
                input_bytes=request,
                timeout_seconds=float(timeout_seconds),
                cwd=work,
                max_output_bytes=_MAX_PROCESS_OUTPUT,
            )
    except asyncio.TimeoutError:
        return _failure(
            source.source_id, "timeout", "Python AST analysis timed out.",
            failure_kind="timeout",
        )
    except asyncio.CancelledError:
        raise
    except OverflowError:
        return _failure(
            source.source_id,
            "analysis_error",
            "Python AST process output exceeded the host limit.",
            failure_kind="output_limit",
        )
    except OSError:
        return _failure(
            source.source_id, "analysis_error", "Python AST process launch failed.",
            failure_kind="backend_failure",
        )
    if code != 0 or stderr:
        return _failure(
            source.source_id, "analysis_error", "Python AST analysis failed.",
            failure_kind="backend_failure",
        )
    try:
        result = decode_json(stdout.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return _failure(
            source.source_id, "analysis_error", "Python AST analysis returned invalid JSON.",
            failure_kind="malformed_result",
        )
    if not isinstance(result, dict) or result.get("source_id") != source.source_id:
        return _failure(
            source.source_id, "analysis_error", "Python AST analysis returned invalid identity.",
            failure_kind="malformed_result",
        )
    limitations = result.get("limitations")
    if isinstance(limitations, list) and _DIRECT_DEADLINE_LIMITATION in limitations:
        result["limitations"] = [
            _ISOLATED_DEADLINE_LIMITATION
            if item == _DIRECT_DEADLINE_LIMITATION else item
            for item in limitations
        ]
    return result


def _subprocess_main() -> int:
    """Fixed module entry point; stdin is data and never a path or executable."""

    try:
        payload = decode_json(sys.stdin.read())
        source = Source.from_dict(payload)
        result = analyze_source(source)
    except Exception:
        result = _failure(
            None, "analysis_error", "Python AST request was invalid.",
            failure_kind="invalid_request",
        )
    sys.stdout.write(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
    return 0


__all__ = ["BACKEND", "analyze_source", "analyze_source_isolated"]


if __name__ == "__main__":
    raise SystemExit(_subprocess_main())
