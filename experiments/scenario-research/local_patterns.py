"""Conservative source-bound extraction for the research solver path.

Only two local shapes are formalized:

* a zero-argument Java call compared with an integer literal; and
* a Python call result compared with ``None`` by identity.

The extractors consume only a reviewed ``UnifiedTask`` and its in-memory
``SourceStore``.  Parser success is a prerequisite.  The returned records keep
target resolution and mock controllability separate from the supported local
predicate, because a syntactically visible call need not be a resolved or
configurable dependency.
"""

from __future__ import annotations

import ast
import hashlib
import json
import re
import textwrap
from typing import Any

from inputs import ReviewedInput
from mock_spec.java_analysis import analyze_source as analyze_java_source
from mock_spec.python_analysis import analyze_source_isolated as analyze_python_source


_JAVA_COMPARISON = re.compile(
    r"^\s*\(?\s*"
    r"(?P<receiver>[A-Za-z_$][\w$]*)\.(?P<method>[A-Za-z_$][\w$]*)\(\)"
    r"\s*(?P<operator><=|>=|<|>|==|!=)\s*(?P<literal>-?\d+)"
    r"\s*\)?\s*$"
)
_JAVA_REVERSED_COMPARISON = re.compile(
    r"^\s*\(?\s*(?P<literal>-?\d+)\s*"
    r"(?P<operator><=|>=|<|>|==|!=)\s*"
    r"(?P<receiver>[A-Za-z_$][\w$]*)\.(?P<method>[A-Za-z_$][\w$]*)\(\)"
    r"\s*\)?\s*$"
)
_JAVA_OPERATOR = {
    "<=": "le", "<": "lt", ">": "gt", ">=": "ge", "==": "eq", "!=": "ne"
}
_JAVA_REVERSED_OPERATOR = {
    "<=": "ge", "<": "gt", ">": "lt", ">=": "le", "==": "eq", "!=": "ne"
}
_JAVA_PARAMETER = re.compile(
    r"(?P<type>[A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)*"
    r"(?:\s*<[^,()]+>)?(?:\[\])?)\s+"
    r"(?P<name>[A-Za-z_$][\w$]*)\b"
)
_JAVA_INTERFACE_RETURN = re.compile(
    r"\b(?P<return>Integer|int)\s+(?P<method>[A-Za-z_$][\w$]*)\s*\(\s*\)"
)


def _location(source_id: str, start_line: int, end_line: int, quote: str) -> dict[str, Any]:
    return {
        "source_id": source_id,
        "start_line": start_line,
        "end_line": end_line,
        "quote": quote,
    }


def _pattern_id(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return "local-pattern:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:20]


def _finalize(record: dict[str, Any]) -> dict[str, Any]:
    identity = {
        "source_id": record["source_id"],
        "call": record["dependency_call"]["location"],
        "callee": record["dependency_call"]["callee"],
        "condition": record["condition"],
        "binding": record["binding"],
        "term": record["term"],
    }
    return {"pattern_id": _pattern_id(identity), **record}


def _source_lines(source: Any, start_line: int, end_line: int) -> str:
    first = start_line - source.start_line
    last = end_line - source.start_line + 1
    return "\n".join(source.text.splitlines()[first:last])


def _java_receiver_type(source: Any, receiver: str, declaration_line: int) -> str | None:
    # Restrict the declaration search to the parser-reported declaration line.
    declaration = _source_lines(source, declaration_line, declaration_line)
    for match in _JAVA_PARAMETER.finditer(declaration):
        if match.group("name") == receiver:
            return re.sub(r"\s+", "", match.group("type"))
    return None


def _qualify_java_type(source_text: str, receiver_type: str) -> str | None:
    raw_type = receiver_type.split("<", 1)[0].removesuffix("[]")
    if "." in raw_type:
        return raw_type
    imports = re.findall(
        r"(?m)^\s*import\s+(?!static\s+)([A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)*|"
        r"[A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)*\.\*)\s*;",
        source_text,
    )
    explicit = [item for item in imports if item.rsplit(".", 1)[-1] == raw_type]
    if len(explicit) == 1:
        return explicit[0]
    if explicit or any(item.endswith(".*") for item in imports):
        return None
    package = re.search(
        r"(?m)^\s*package\s+([A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)*)\s*;",
        source_text,
    )
    return f"{package.group(1)}.{raw_type}" if package else raw_type


def _java_interface_evidence(
    reviewed: ReviewedInput, *, qualified_receiver_type: str, method: str
) -> tuple[Any, str] | None:
    matches = []
    for interface in reviewed.task.permitted_interfaces:
        signature = _JAVA_INTERFACE_RETURN.search(interface.signature)
        if signature is None or signature.group("method") != method:
            continue
        owner = interface.symbol.rsplit(".", 1)[0]
        # An implementation of the receiver type does not establish that this
        # call dispatches to that implementation.  Require the receiver's
        # declared type to own the reviewed interface exactly.
        if qualified_receiver_type == owner:
            matches.append((interface, signature.group("return")))
    if len(matches) != 1:
        return None
    return matches[0]


def _extract_java(
    reviewed: ReviewedInput, parser: dict[str, Any]
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    source = reviewed.task.mut_source
    calls = [fact for fact in parser["facts"] if fact.get("kind") == "call"]
    conditions = [
        fact for fact in parser["facts"] if fact.get("kind") in {"if", "conditional"}
    ]
    patterns: list[dict[str, Any]] = []
    diagnostics: list[dict[str, str]] = []
    for condition in conditions:
        expression = condition.get("condition")
        if not isinstance(expression, str):
            continue
        match = _JAVA_COMPARISON.fullmatch(expression)
        reversed_match = False
        if match is None:
            match = _JAVA_REVERSED_COMPARISON.fullmatch(expression)
            reversed_match = match is not None
        if match is None:
            condition_calls = [
                fact for fact in calls
                if any(
                    guard.get("branch") == "condition"
                    and guard.get("condition") == expression
                    for guard in fact.get("lexical_guards", [])
                )
            ]
            if condition_calls:
                diagnostics.append({
                    "category": "unsupported",
                    "code": "unsupported_java_condition",
                    "message": (
                        f"Call-bearing {condition['kind']} condition at line "
                        f"{condition['start_line']} is outside the simple integer-comparison grammar."
                    ),
                })
            continue

        receiver = match.group("receiver")
        method = match.group("method")
        matching_calls = [
            fact for fact in calls
            if fact.get("name") == method
            and fact.get("target") == receiver
            and condition["start_line"] <= fact["start_line"] <= condition["end_line"]
            and any(
                guard.get("branch") == "condition"
                and guard.get("condition") == expression
                for guard in fact.get("lexical_guards", [])
            )
        ]
        if len(matching_calls) != 1:
            diagnostics.append({
                "category": "unresolved",
                "code": "unresolved_java_call_binding",
                "message": (
                    f"Condition at line {condition['start_line']} did not bind to exactly one "
                    "Spoon call fact."
                ),
            })
            continue
        call = matching_calls[0]
        receiver_reads = [
            fact for fact in parser["facts"]
            if fact.get("kind") == "variable_read"
            and fact.get("name") == receiver
            and fact.get("start_line") == call.get("start_line")
            and fact.get("declaration_status") == "resolved_local"
            and isinstance(fact.get("declared_at"), dict)
        ]
        if len(receiver_reads) != 1:
            diagnostics.append({
                "category": "unresolved",
                "code": "unresolved_java_receiver_binding",
                "message": (
                    f"Call at line {call['start_line']} did not bind to exactly one resolved "
                    "receiver declaration."
                ),
            })
            continue
        declared_at = receiver_reads[0]["declared_at"]
        receiver_type = _java_receiver_type(source, receiver, declared_at["start_line"])
        qualified_receiver_type = (
            None if receiver_type is None else _qualify_java_type(source.text, receiver_type)
        )
        operator_map = _JAVA_REVERSED_OPERATOR if reversed_match else _JAVA_OPERATOR
        parsed_operator = operator_map[match.group("operator")]
        evidence = None if qualified_receiver_type is None else _java_interface_evidence(
            reviewed, qualified_receiver_type=qualified_receiver_type, method=method
        )
        if evidence is None:
            support_status = "unresolved"
            term = None
            type_evidence = None
            controllability = "unresolved"
            caveat = "No unique reviewed numeric interface compatible with the receiver type."
        else:
            interface, return_type = evidence
            support_status = "supported"
            term = {
                "sort": "Int",
                "operator": parsed_operator,
                "literal": int(match.group("literal")),
                "bounds": [-2147483648, 2147483647],
            }
            type_evidence = {
                "declared_return_type": return_type,
                "receiver_static_type": receiver_type,
                "receiver_qualified_type": qualified_receiver_type,
                "interface_symbol": interface.symbol,
                "interface_source_id": interface.source_id,
                "interface_location": {
                    "source_id": interface.source_id,
                    "start_line": interface.start_line,
                    "end_line": interface.end_line,
                },
                "resolution_status": interface.resolution_status,
            }
            controllability = "reviewed_interface_partial"
            caveat = (
                "The reviewed interface is a compatible implementation declaration; "
                "runtime dispatch remains partial."
            )
        record = {
            "support_status": support_status,
            "source_id": source.source_id,
            "source_sha256": reviewed.source_sha256[source.source_id],
            "dependency_call": {
                "callee": f"{receiver}.{method}",
                "receiver": receiver,
                "method": method,
                "location": _location(
                    source.source_id, call["start_line"], call["end_line"], call["quote"]
                ),
                "target_resolution": call.get("declaration_status", "unresolved"),
                "type_evidence": type_evidence,
            },
            "binding": {
                "kind": "direct_call_result",
                "name": None,
                "resolution_status": "resolved_local" if evidence else "unresolved",
                "receiver_declaration": declared_at,
            },
            "condition": {
                "construct": condition["kind"],
                "location": _location(
                    source.source_id,
                    condition["start_line"],
                    condition["end_line"],
                    condition.get("quote") or _source_lines(
                        source, condition["start_line"], condition["end_line"]
                    ),
                ),
                "expression": expression,
                "operator": parsed_operator,
                "literal": int(match.group("literal")),
            },
            "term": term,
            "normal_return_assumption": (
                "The call returns normally and comparison evaluation completes; nullable "
                "Integer null and exceptions are outside this fragment."
            ),
            "controllability": controllability,
            "control_caveats": [
                caveat,
                "Only the local comparison predicate is modeled; surrounding guards and "
                "branch effects are not solver claims.",
            ],
        }
        patterns.append(_finalize(record))
    return patterns, diagnostics


def _py_location(source: Any, node: ast.AST) -> dict[str, Any]:
    start = source.start_line + node.lineno - 1
    end = source.start_line + getattr(node, "end_lineno", node.lineno) - 1
    return _location(source.source_id, start, end, _source_lines(source, start, end))


def _python_callee(call: ast.Call) -> str:
    return ast.unparse(call.func)


def _simple_python_call(call: ast.Call) -> bool:
    cursor: ast.expr = call.func
    while isinstance(cursor, ast.Attribute):
        cursor = cursor.value
    if not isinstance(cursor, ast.Name):
        return False
    return not any(
        isinstance(descendant, (ast.Call, ast.Lambda, ast.NamedExpr))
        for argument in (*call.args, *(item.value for item in call.keywords))
        for descendant in ast.walk(argument)
    )


def _simple_python_binding(
    body: list[ast.stmt], condition_index: int, name: str
) -> tuple[ast.Call, ast.AST] | None:
    if condition_index == 0:
        return None
    if any(
        isinstance(node, (ast.Global, ast.Nonlocal)) and name in node.names
        for statement in body
        for node in ast.walk(statement)
    ):
        return None
    statement = body[condition_index - 1]
    if not isinstance(statement, ast.Assign) or len(statement.targets) != 1:
        return None
    target = statement.targets[0]
    if (
        not isinstance(target, ast.Name)
        or target.id != name
        or not isinstance(statement.value, ast.Call)
        or not _simple_python_call(statement.value)
    ):
        return None
    return statement.value, statement


def _python_operand_binding(
    operand: ast.expr, body: list[ast.stmt], condition_index: int
) -> tuple[ast.Call, str, str | None, ast.AST] | None:
    if isinstance(operand, ast.Call) and _simple_python_call(operand):
        return operand, "direct_call_result", None, operand
    if (
        isinstance(operand, ast.NamedExpr)
        and isinstance(operand.target, ast.Name)
        and isinstance(operand.value, ast.Call)
        and _simple_python_call(operand.value)
    ):
        return operand.value, "same_expression_named_binding", operand.target.id, operand
    if isinstance(operand, ast.Name):
        found = _simple_python_binding(body, condition_index, operand.id)
        if found is not None:
            call, assignment = found
            return call, "single_local_assignment", operand.id, assignment
    return None


def _extract_python(
    reviewed: ReviewedInput, parser: dict[str, Any]
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    source = reviewed.task.mut_source
    tree = ast.parse(textwrap.dedent(source.text))
    patterns: list[dict[str, Any]] = []
    diagnostics: list[dict[str, str]] = []

    def visit_body(body: list[ast.stmt]) -> None:
        for index, statement in enumerate(body):
            if isinstance(statement, ast.If):
                test = statement.test
                simple_identity = (
                    isinstance(test, ast.Compare) and len(test.ops) == 1
                    and len(test.comparators) == 1
                    and isinstance(test.ops[0], (ast.Is, ast.IsNot))
                    and any(isinstance(node, ast.Constant) and node.value is None
                            for node in (test.left, test.comparators[0]))
                )
                if not simple_identity and any(isinstance(node, ast.Call) for node in ast.walk(test)):
                    diagnostics.append({
                        "category": "unsupported", "code": "unsupported_python_condition",
                        "message": f"Call-bearing condition at line {source.start_line + test.lineno - 1} "
                                   "is outside the single None identity predicate grammar.",
                    })
                if (
                    isinstance(test, ast.Compare)
                    and len(test.ops) == 1
                    and len(test.comparators) == 1
                    and isinstance(test.ops[0], (ast.Is, ast.IsNot))
                ):
                    left, right = test.left, test.comparators[0]
                    if isinstance(right, ast.Constant) and right.value is None:
                        operand = left
                    elif isinstance(left, ast.Constant) and left.value is None:
                        operand = right
                    else:
                        operand = None
                    if operand is not None:
                        binding = _python_operand_binding(operand, body, index)
                        if binding is None:
                            record = {
                                "support_status": "unsupported",
                                "source_id": source.source_id,
                                "source_sha256": reviewed.source_sha256[source.source_id],
                                "dependency_call": {
                                    "callee": None,
                                    "receiver": None,
                                    "method": None,
                                    "location": _py_location(source, operand),
                                    "target_resolution": "unresolved",
                                    "type_evidence": None,
                                },
                                "binding": {
                                    "kind": "unsupported_expression",
                                    "name": ast.unparse(operand),
                                    "resolution_status": "unsupported",
                                    "receiver_declaration": None,
                                },
                                "condition": {
                                    "construct": "if",
                                    "location": _py_location(source, test),
                                    "expression": ast.unparse(test),
                                    "operator": None,
                                    "literal": None,
                                },
                                "term": None,
                                "normal_return_assumption": None,
                                "controllability": "unresolved",
                                "control_caveats": [
                                    "The None identity operand is not a direct call, same-expression "
                                    "binding, or unique simple local call assignment."
                                ],
                            }
                            patterns.append(_finalize(record))
                        else:
                            call, binding_kind, bound_name, declaration = binding
                            operator = "is_none" if isinstance(test.ops[0], ast.Is) else "is_not_none"
                            callee = _python_callee(call)
                            call_location = _py_location(source, call)
                            condition_location = _py_location(source, test)
                            parser_calls = [
                                fact for fact in parser["facts"]
                                if fact.get("kind") == "call"
                                and fact.get("callee") == callee
                                and fact.get("start_line") == call_location["start_line"]
                                and fact.get("end_line") == call_location["end_line"]
                            ]
                            parser_conditions = [
                                fact for fact in parser["facts"]
                                if fact.get("kind") == "condition"
                                and fact.get("construct") == "if"
                                and fact.get("expression") == ast.unparse(test)
                                and fact.get("start_line") == condition_location["start_line"]
                                and fact.get("end_line") == condition_location["end_line"]
                            ]
                            parser_bound = len(parser_calls) == 1 and len(parser_conditions) == 1
                            receiver = callee.rsplit(".", 1)[0] if "." in callee else None
                            method = callee.rsplit(".", 1)[-1]
                            record = {
                                "support_status": "supported" if parser_bound else "unresolved",
                                "source_id": source.source_id,
                                "source_sha256": reviewed.source_sha256[source.source_id],
                                "dependency_call": {
                                    "callee": callee,
                                    "receiver": receiver,
                                    "method": method,
                                    "location": call_location,
                                    "target_resolution": "unresolved",
                                    "type_evidence": {
                                        "declared_return_type": "unknown_nullable_object",
                                        "resolution_status": "unresolved",
                                    },
                                },
                                "binding": {
                                    "kind": binding_kind,
                                    "name": bound_name,
                                    "resolution_status": (
                                        "resolved_local" if parser_bound else "unresolved"
                                    ),
                                    "receiver_declaration": _py_location(source, declaration),
                                },
                                "condition": {
                                    "construct": "if",
                                    "location": condition_location,
                                    "expression": ast.unparse(test),
                                    "operator": operator,
                                    "literal": None,
                                },
                                "term": ({
                                    "sort": "Bool",
                                    "operator": operator,
                                    "literal": None,
                                    "bounds": None,
                                } if parser_bound else None),
                                "normal_return_assumption": (
                                    "The call returns normally; raised exceptions and external call "
                                    "semantics are outside this fragment."
                                ),
                                "controllability": "unresolved",
                                "control_caveats": [
                                    "The local returned-value predicate is formalized, but the dynamic "
                                    "call target is unresolved and is not approved for mock configuration.",
                                    "Only the selected if condition is modeled; later calls and exception "
                                    "propagation are not solver claims.",
                                    *([] if parser_bound else [
                                        "The independently reconstructed AST nodes did not bind uniquely "
                                        "to the isolated parser facts."
                                    ]),
                                ],
                            }
                            patterns.append(_finalize(record))
                visit_body(statement.body)
                visit_body(statement.orelse)
            elif isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef)):
                visit_body(statement.body)
            elif isinstance(statement, (ast.For, ast.AsyncFor, ast.While, ast.Try, ast.With, ast.AsyncWith)):
                # Conditions nested in these constructs are deliberately not formalized because this
                # extractor does not construct their control-flow semantics.
                diagnostics.append({
                    "category": "unsupported",
                    "code": "unsupported_python_control",
                    "message": (
                        f"{type(statement).__name__} at visible line "
                        f"{source.start_line + statement.lineno - 1} is outside the local control grammar."
                    ),
                })
                continue

    visit_body(tree.body)
    return patterns, diagnostics


async def extract_patterns(
    reviewed: ReviewedInput, *, timeout_seconds: float = 15
) -> dict[str, Any]:
    """Extract supported local predicates from one reviewed MUT source."""
    if not isinstance(reviewed, ReviewedInput):
        raise TypeError("reviewed must be a ReviewedInput")
    source = reviewed.task.mut_source
    if reviewed.task.language == "java":
        parser = await analyze_java_source(source, timeout_seconds=timeout_seconds)
    elif reviewed.task.language == "python":
        parser = await analyze_python_source(source, timeout_seconds=timeout_seconds)
    else:  # UnifiedTask rejects this, retained as a closed host boundary.
        raise ValueError("unsupported reviewed language")

    if parser.get("status") == "error":
        return {
            "status": "error",
            "source_id": source.source_id,
            "parser_backend": parser.get("backend"),
            "parser_status": "error",
            "error_code": parser.get("error_code", "analysis_error"),
            "patterns": [],
            "diagnostics": list(parser.get("diagnostics", [])),
        }

    patterns, extraction_diagnostics = (
        _extract_java(reviewed, parser)
        if reviewed.task.language == "java"
        else _extract_python(reviewed, parser)
    )
    status = "ok"
    if parser.get("status") != "ok" or extraction_diagnostics or any(
        pattern["support_status"] != "supported" for pattern in patterns
    ):
        status = "incomplete"
    return {
        "status": status,
        "source_id": source.source_id,
        "parser_backend": parser.get("backend"),
        "parser_status": parser.get("status"),
        "error_code": None,
        "patterns": patterns,
        "diagnostics": [*parser.get("diagnostics", []), *extraction_diagnostics],
    }


__all__ = ["extract_patterns"]
