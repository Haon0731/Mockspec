"""Fail-closed local AST projection from test observations to return boundaries.

This module deliberately implements a small, auditable language subset.  It
does not execute source, resolve imports, authorize a Mock target, or claim a
complete program path.  Call binding is supplied by the caller and recorded as
``task_provided`` evidence.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
import math
from typing import Any


SCHEMA_VERSION = "mock-spec.local-conditional-constraints/v1"


class LocalConstraintError(ValueError):
    """The selected source cannot be projected without leaving the safe subset."""


@dataclass(frozen=True)
class _OpaqueCall:
    expression: ast.Call


_UNKNOWN = object()


def _location(node: ast.AST) -> dict[str, int]:
    return {
        "start_line": node.lineno,
        "start_column": node.col_offset,
        "end_line": getattr(node, "end_lineno", node.lineno),
        "end_column": getattr(node, "end_col_offset", node.col_offset),
    }


def _selected_function(tree: ast.Module, name: str, line: int, label: str) -> ast.FunctionDef:
    matches = [
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == name and node.lineno == line
    ]
    if len(matches) != 1:
        raise LocalConstraintError(f"{label}_function_not_uniquely_selected")
    return matches[0]


def _call_name(call: ast.Call) -> str | None:
    return call.func.id if isinstance(call.func, ast.Name) else None


def _reject_shadowed_product_name(function: ast.FunctionDef, product_name: str) -> None:
    parameters = [
        argument.arg for argument in (
            *function.args.posonlyargs, *function.args.args, *function.args.kwonlyargs
        )
    ]
    if function.args.vararg is not None:
        parameters.append(function.args.vararg.arg)
    if function.args.kwarg is not None:
        parameters.append(function.args.kwarg.arg)
    if product_name in parameters:
        raise LocalConstraintError("task_provided_product_name_shadowed_by_test_parameter")

    class Bindings(ast.NodeVisitor):
        def __init__(self) -> None:
            self.bound = False
            self.declared = False

        def visit_Name(self, node: ast.Name) -> None:  # noqa: N802
            if node.id == product_name and isinstance(node.ctx, (ast.Store, ast.Del)):
                self.bound = True

        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # noqa: N802
            if node.name == product_name:
                self.bound = True

        visit_AsyncFunctionDef = visit_FunctionDef
        visit_ClassDef = visit_FunctionDef

        def visit_Lambda(self, node: ast.Lambda) -> None:  # noqa: N802
            return

        def visit_Import(self, node: ast.Import) -> None:  # noqa: N802
            if any((alias.asname or alias.name.split(".")[0]) == product_name
                   for alias in node.names):
                self.bound = True

        visit_ImportFrom = visit_Import

        def visit_Global(self, node: ast.Global) -> None:  # noqa: N802
            if product_name in node.names:
                self.declared = True

        visit_Nonlocal = visit_Global

    bindings = Bindings()
    for statement in function.body:
        bindings.visit(statement)
    if bindings.declared:
        raise LocalConstraintError("explicit_product_name_scope_declaration_unsupported")
    if bindings.bound:
        raise LocalConstraintError("task_provided_product_name_shadowed_in_test_scope")


def _parametrize_rows(function: ast.FunctionDef) -> tuple[list[str], list[tuple[Any, ...]]]:
    decorators = [
        decorator for decorator in function.decorator_list
        if isinstance(decorator, ast.Call)
        and isinstance(decorator.func, ast.Attribute)
        and decorator.func.attr == "parametrize"
        and isinstance(decorator.func.value, ast.Attribute)
        and decorator.func.value.attr == "mark"
        and isinstance(decorator.func.value.value, ast.Name)
        and decorator.func.value.value.id == "pytest"
    ]
    if len(decorators) != 1 or len(decorators[0].args) < 2:
        raise LocalConstraintError("literal_pytest_parametrize_required")
    if len(function.decorator_list) != 1:
        raise LocalConstraintError("additional_test_decorator_unsupported")
    if any(keyword.arg == "indirect" for keyword in decorators[0].keywords):
        raise LocalConstraintError("indirect_parametrize_unsupported")
    try:
        names_value = ast.literal_eval(decorators[0].args[0])
        rows_value = ast.literal_eval(decorators[0].args[1])
    except (ValueError, TypeError, SyntaxError) as exc:
        raise LocalConstraintError("parametrize_values_not_literal") from exc
    if not isinstance(names_value, str):
        raise LocalConstraintError("parametrize_names_not_string")
    names = [item.strip() for item in names_value.split(",")]
    if (
        not names or len(names) != len(set(names))
        or any(not item.isidentifier() for item in names)
    ):
        raise LocalConstraintError("parametrize_names_invalid")
    parameters = [
        argument.arg for argument in (
            *function.args.posonlyargs, *function.args.args, *function.args.kwonlyargs
        )
    ]
    if len(parameters) != len(set(parameters)) or any(name not in parameters for name in names):
        raise LocalConstraintError("parametrize_name_not_explicit_test_parameter")
    positional = [*function.args.posonlyargs, *function.args.args]
    defaulted = {
        argument.arg for argument in positional[-len(function.args.defaults):]
    } if function.args.defaults else set()
    defaulted.update(
        argument.arg for argument, default in zip(
            function.args.kwonlyargs, function.args.kw_defaults, strict=True,
        ) if default is not None
    )
    if set(names) & defaulted:
        raise LocalConstraintError("parametrize_parameter_has_default")
    if not isinstance(rows_value, (list, tuple)):
        raise LocalConstraintError("parametrize_rows_not_sequence")
    rows: list[tuple[Any, ...]] = []
    for row in rows_value:
        values = row if isinstance(row, tuple) else (row,)
        if len(values) != len(names):
            raise LocalConstraintError("parametrize_row_arity_mismatch")
        for value in values:
            _validate_literal(value)
        rows.append(values)
    if not rows:
        raise LocalConstraintError("parametrize_rows_empty")
    return names, rows


def _validate_literal(value: Any) -> None:
    if value is None or isinstance(value, (bool, int, str, bytes)):
        return
    if isinstance(value, float):
        if math.isfinite(value):
            return
        raise LocalConstraintError("parametrize_non_finite_literal")
    if isinstance(value, (tuple, list)):
        for item in value:
            _validate_literal(item)
        return
    if isinstance(value, dict) and all(isinstance(key, str) for key in value):
        for item in value.values():
            _validate_literal(item)
        return
    raise LocalConstraintError("parametrize_literal_type_unsupported")


def _contains_binding(node: ast.AST, protected: set[str]) -> bool:
    for child in ast.walk(node):
        if isinstance(child, ast.Name) and isinstance(child.ctx, (ast.Store, ast.Del)):
            if child.id in protected:
                return True
    return False


def _references_name(node: ast.AST, names: set[str]) -> bool:
    return any(isinstance(child, ast.Name) and child.id in names for child in ast.walk(node))


def _safe_setup(statements: list[ast.stmt], protected: set[str]) -> list[dict[str, Any]]:
    unknowns: list[dict[str, Any]] = []
    for statement in statements:
        if _contains_binding(statement, protected):
            raise LocalConstraintError("setup_rebinds_protected_name")
        if _references_name(statement, protected):
            raise LocalConstraintError("setup_may_use_or_mutate_protected_value")
        if isinstance(statement, ast.Pass):
            continue
        if isinstance(statement, ast.Expr):
            if isinstance(statement.value, ast.Constant) and isinstance(statement.value.value, str):
                continue
            unknowns.append({
                "kind": "unanalysed_test_setup_expression",
                "location": _location(statement),
                "impact": "not_used_as_constraint_evidence",
            })
            continue
        if isinstance(statement, (ast.Assign, ast.AnnAssign)):
            targets = statement.targets if isinstance(statement, ast.Assign) else [statement.target]
            unknowns.append({
                "kind": "unanalysed_test_setup_assignment",
                "location": _location(statement),
                "impact": "not_used_as_constraint_evidence",
            })
            continue
        if isinstance(statement, ast.If):
            if any(
                not isinstance(child, (ast.Pass, ast.Expr, ast.Assign, ast.AnnAssign))
                for child in (*statement.body, *statement.orelse)
            ):
                raise LocalConstraintError("unsupported_test_setup_control_flow")
            nested_unknowns = _safe_setup([*statement.body, *statement.orelse], protected)
            unknowns.append({
                "kind": "unanalysed_test_setup_branch",
                "location": _location(statement),
                "impact": "assumed_not_to_rebind_selected_call_inputs_or_result",
            })
            unknowns.extend(nested_unknowns)
            continue
        raise LocalConstraintError("unsupported_test_setup_statement")
    return unknowns


def _selected_call(
    function: ast.FunctionDef, product_name: str, call_line: int,
) -> tuple[ast.Assign, ast.Call, str, int]:
    class Calls(ast.NodeVisitor):
        def __init__(self) -> None:
            self.items: list[ast.Call] = []

        def visit_Call(self, node: ast.Call) -> None:  # noqa: N802
            if _call_name(node) == product_name:
                self.items.append(node)
            self.generic_visit(node)

        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # noqa: N802
            return

        visit_AsyncFunctionDef = visit_FunctionDef
        visit_Lambda = visit_FunctionDef
        visit_ClassDef = visit_FunctionDef

    visitor = Calls()
    for statement in function.body:
        visitor.visit(statement)
    if len(visitor.items) != 1:
        raise LocalConstraintError("selected_product_call_not_unique_in_test")
    calls: list[tuple[ast.Assign, ast.Call, str, int]] = []
    for index, statement in enumerate(function.body):
        if not isinstance(statement, ast.Assign) or len(statement.targets) != 1:
            continue
        target = statement.targets[0]
        call = statement.value
        if (
            isinstance(target, ast.Name) and isinstance(call, ast.Call)
            and _call_name(call) == product_name
        ):
            calls.append((statement, call, target.id, index))
    if len(calls) != 1 or calls[0][1] is not visitor.items[0]:
        raise LocalConstraintError("selected_product_call_shape_unsupported")
    selected = calls[0]
    if selected[1].lineno != call_line:
        raise LocalConstraintError("selected_product_call_line_mismatch")
    return selected


def _starred_call_arguments(call: ast.Call, test_env: dict[str, Any]) -> tuple[Any, ...]:
    if call.keywords or len(call.args) != 1 or not isinstance(call.args[0], ast.Starred):
        raise LocalConstraintError("selected_call_requires_one_literal_star_argument")
    value = _evaluate(call.args[0].value, test_env, len_available=True)
    if value is _UNKNOWN or not isinstance(value, tuple):
        raise LocalConstraintError("selected_call_star_argument_not_known_tuple")
    return value


def _evaluate(node: ast.AST, env: dict[str, Any], *, len_available: bool) -> Any:
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Name):
        return env.get(node.id, _UNKNOWN)
    if isinstance(node, ast.Tuple):
        values = [_evaluate(item, env, len_available=len_available) for item in node.elts]
        return _UNKNOWN if _UNKNOWN in values else tuple(values)
    if isinstance(node, ast.List):
        values = [_evaluate(item, env, len_available=len_available) for item in node.elts]
        return _UNKNOWN if _UNKNOWN in values else values
    if isinstance(node, ast.Dict):
        keys = [_evaluate(item, env, len_available=len_available) for item in node.keys]
        values = [_evaluate(item, env, len_available=len_available) for item in node.values]
        if _UNKNOWN in keys or _UNKNOWN in values:
            return _UNKNOWN
        try:
            return dict(zip(keys, values, strict=True))
        except TypeError:
            return _UNKNOWN
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
        value = _evaluate(node.operand, env, len_available=len_available)
        return _UNKNOWN if value is _UNKNOWN or isinstance(value, _OpaqueCall) else not bool(value)
    if isinstance(node, ast.Compare) and len(node.ops) == len(node.comparators) == 1:
        left = _evaluate(node.left, env, len_available=len_available)
        right = _evaluate(node.comparators[0], env, len_available=len_available)
        if (
            left is _UNKNOWN or right is _UNKNOWN
            or isinstance(left, _OpaqueCall) or isinstance(right, _OpaqueCall)
            or not isinstance(node.ops[0], ast.Eq)
        ):
            return _UNKNOWN
        return left == right
    if (
        isinstance(node, ast.Call) and len_available and isinstance(node.func, ast.Name)
        and node.func.id == "len" and len(node.args) == 1 and not node.keywords
    ):
        value = _evaluate(node.args[0], env, len_available=len_available)
        if (
            value is _UNKNOWN or isinstance(value, _OpaqueCall)
            or not isinstance(value, (tuple, list, str, bytes, dict))
        ):
            return _UNKNOWN
        return len(value)
    if isinstance(node, ast.Subscript):
        value = _evaluate(node.value, env, len_available=len_available)
        index = _evaluate(node.slice, env, len_available=len_available)
        if (
            value is _UNKNOWN or index is _UNKNOWN
            or isinstance(value, _OpaqueCall) or isinstance(index, _OpaqueCall)
        ):
            return _UNKNOWN
        try:
            return value[index]
        except (IndexError, KeyError, TypeError):
            return _UNKNOWN
    return _UNKNOWN


def _builtin_available(tree: ast.Module, function: ast.FunctionDef, name: str) -> bool:
    if name in {argument.arg for argument in (*function.args.posonlyargs, *function.args.args,
                                               *function.args.kwonlyargs)}:
        return False
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node is not function:
            if node.name == name:
                return False
        if isinstance(node, ast.Name) and node.id == name and isinstance(node.ctx, ast.Store):
            return False
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                if (alias.asname or alias.name.split(".")[0]) == name:
                    return False
    return True


def _call_target(expression: ast.expr) -> str | None:
    if isinstance(expression, ast.Name):
        return expression.id
    if isinstance(expression, ast.Attribute):
        prefix = _call_target(expression.value)
        return f"{prefix}.{expression.attr}" if prefix else None
    return None


def _json_value(value: Any) -> Any:
    if isinstance(value, float) and not math.isfinite(value):
        raise LocalConstraintError("non_finite_literal_value")
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, bytes):
        return {"kind": "bytes", "hex": value.hex()}
    if isinstance(value, tuple):
        return [_json_value(item) for item in value]
    if isinstance(value, list):
        return [_json_value(item) for item in value]
    if isinstance(value, dict) and all(isinstance(key, str) for key in value):
        return {key: _json_value(item) for key, item in value.items()}
    raise LocalConstraintError("non_json_literal_value")


def _typed_literal(value: Any) -> dict[str, Any]:
    if value is None:
        return {"kind": "none", "value": None}
    if isinstance(value, bool):
        return {"kind": "bool", "value": value}
    if isinstance(value, int):
        return {"kind": "int", "value": value}
    if isinstance(value, float):
        if not math.isfinite(value):
            raise LocalConstraintError("non_finite_literal_value")
        return {"kind": "float", "value": value}
    if isinstance(value, str):
        return {"kind": "str", "value": value}
    if isinstance(value, bytes):
        return {"kind": "bytes", "hex": value.hex()}
    if isinstance(value, tuple):
        return {"kind": "tuple", "items": [_typed_literal(item) for item in value]}
    if isinstance(value, list):
        return {"kind": "list", "items": [_typed_literal(item) for item in value]}
    if isinstance(value, dict):
        return {
            "kind": "dict",
            "entries": [
                {"key": _typed_literal(key), "value": _typed_literal(item)}
                for key, item in value.items()
            ],
        }
    raise LocalConstraintError("unsupported_literal_type")


def _return_requirement(value: Any, requirements: list[dict[str, Any]]) -> dict[str, Any]:
    if not isinstance(value, _OpaqueCall):
        return {
            "kind": "visible_value_return",
            "value": _json_value(value),
            "requirements": [],
            "test_obligations": requirements,
            "constraint_scope": "visible_return_not_dependency_behavior",
        }
    call = value.expression
    target = _call_target(call.func)
    if target is None or any(isinstance(argument, ast.Starred) for argument in call.args):
        raise LocalConstraintError("opaque_return_call_target_or_arguments_unsupported")
    arguments = []
    for argument in call.args:
        resolved = _evaluate(argument, {}, len_available=False)
        if resolved is _UNKNOWN:
            raise LocalConstraintError("opaque_return_call_argument_unresolved")
        arguments.append(_json_value(resolved))
    keywords: dict[str, Any] = {}
    for keyword in call.keywords:
        if keyword.arg is None:
            raise LocalConstraintError("opaque_return_call_kwargs_unsupported")
        resolved = _evaluate(keyword.value, {}, len_available=False)
        if resolved is _UNKNOWN:
            raise LocalConstraintError("opaque_return_call_argument_unresolved")
        keywords[keyword.arg] = _json_value(resolved)
    return {
        "kind": "opaque_external_call_return",
        "target_expression": target,
        "arguments": arguments,
        "argument_literals": [
            _typed_literal(_evaluate(argument, {}, len_available=False))
            for argument in call.args
        ],
        "keyword_arguments": keywords,
        "argument_status": "implementation_derived_path_fact_not_test_requirement",
        "call_location": _location(call),
        "requirements": requirements,
        "authorization": "not_established",
        "unknown_effects": ["exception", "state_change", "runtime_dispatch", "concrete_return"],
    }


def _branch_status(boundary: dict[str, Any]) -> dict[str, str]:
    if boundary["kind"] == "opaque_external_call_return":
        return {
            "explanation": "partial",
            "support": "conditional",
            "realization": "conditional_only",
        }
    obligations = boundary["test_obligations"]
    if any(item["kind"] == "return_non_null" for item in obligations):
        if boundary["value"] is None:
            return {"explanation": "partial", "support": "refuted", "realization": "blocked"}
    if any(item["kind"] == "return_has_attribute" for item in obligations):
        return {"explanation": "partial", "support": "unresolved", "realization": "blocked"}
    return {"explanation": "partial", "support": "conditional", "realization": "grounded"}


def _resolve_return_expression(node: ast.expr | None, env: dict[str, Any]) -> Any:
    if node is None:
        return None
    if isinstance(node, ast.Call):
        copied = ast.fix_missing_locations(ast.Call(
            func=node.func,
            args=[_literal_node(_evaluate(item, env, len_available=False)) for item in node.args],
            keywords=[ast.keyword(
                arg=item.arg,
                value=_literal_node(_evaluate(item.value, env, len_available=False)),
            ) for item in node.keywords],
        ))
        if any(isinstance(item, ast.Starred) for item in node.args):
            raise LocalConstraintError("opaque_return_call_arguments_unsupported")
        copied.lineno = node.lineno
        copied.col_offset = node.col_offset
        copied.end_lineno = getattr(node, "end_lineno", node.lineno)
        copied.end_col_offset = getattr(node, "end_col_offset", node.col_offset)
        return _OpaqueCall(copied)
    value = _evaluate(node, env, len_available=False)
    if value is _UNKNOWN and isinstance(node, ast.Name):
        value = env.get(node.id, _UNKNOWN)
    if value is _UNKNOWN:
        raise LocalConstraintError("return_expression_unresolved")
    return value


def _literal_node(value: Any) -> ast.expr:
    if value is _UNKNOWN:
        raise LocalConstraintError("opaque_return_call_argument_unresolved")
    return ast.parse(repr(value), mode="eval").body


def _execute_product(
    function: ast.FunctionDef, bound_args: tuple[Any, ...], *, len_available: bool,
) -> tuple[ast.Return, Any, list[dict[str, Any]]]:
    positional = [*function.args.posonlyargs, *function.args.args]
    if positional or function.args.kwonlyargs or function.args.kwarg is not None:
        raise LocalConstraintError("only_variadic_product_signature_supported")
    if function.args.vararg is None:
        raise LocalConstraintError("variadic_product_signature_required")
    env: dict[str, Any] = {function.args.vararg.arg: bound_args}
    conditions: list[dict[str, Any]] = []

    def execute(statements: list[ast.stmt]) -> tuple[ast.Return, Any] | None:
        for statement in statements:
            if isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Constant):
                continue
            if isinstance(statement, ast.Pass):
                continue
            if isinstance(statement, ast.Assign):
                if len(statement.targets) != 1 or not isinstance(statement.targets[0], ast.Name):
                    raise LocalConstraintError("product_assignment_target_unsupported")
                if isinstance(statement.value, ast.Call):
                    env[statement.targets[0].id] = _OpaqueCall(statement.value)
                else:
                    value = _evaluate(statement.value, env, len_available=len_available)
                    if value is _UNKNOWN:
                        raise LocalConstraintError("product_assignment_expression_unresolved")
                    env[statement.targets[0].id] = value
                continue
            if isinstance(statement, ast.If):
                result = _evaluate(statement.test, env, len_available=len_available)
                if type(result) is not bool:
                    raise LocalConstraintError("product_condition_unresolved")
                conditions.append({
                    "expression": ast.unparse(statement.test),
                    "polarity": result,
                    "location": _location(statement.test),
                    "evaluation": "safe_static_subset",
                })
                selected = statement.body if result else statement.orelse
                returned = execute(selected)
                if returned is not None:
                    return returned
                continue
            if isinstance(statement, ast.Return):
                return statement, _resolve_return_expression(statement.value, env)
            raise LocalConstraintError("unsupported_product_statement")
        return None

    returned = execute(function.body)
    if returned is None:
        raise LocalConstraintError("product_path_has_no_return")
    return returned[0], returned[1], conditions


def _observations(
    statements: list[ast.stmt], result_name: str, *, hasattr_available: bool,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    active = True
    observations: list[dict[str, Any]] = []
    requirements: list[dict[str, Any]] = []
    unknowns: list[dict[str, Any]] = []
    assert_indexes = [index for index, item in enumerate(statements) if isinstance(item, ast.Assert)]
    if not assert_indexes:
        raise LocalConstraintError("no_post_call_assertions")
    last_assert = assert_indexes[-1]
    for index, statement in enumerate(statements):
        if not isinstance(statement, ast.Assert):
            if isinstance(statement, ast.Pass) or (
                isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Constant)
                and isinstance(statement.value.value, str)
            ):
                continue
            unknowns.append({
                "kind": (
                    "post_observation_test_behavior_unanalysed"
                    if index > last_assert else "intervening_post_call_effect"
                ),
                "location": _location(statement),
                "impact": (
                    "does_not_change_earlier_obligations"
                    if index > last_assert else "invalidates_later_observation_projection"
                ),
            })
            if index <= last_assert:
                active = False
            continue
        raw = ast.unparse(statement.test)
        observation = {
            "observation_id": f"assert:{statement.lineno}:{statement.col_offset}",
            "expression": raw,
            "location": _location(statement),
            "result_version": "selected_call" if active else "overwritten",
            "projection": "unsupported",
        }
        requirement: dict[str, Any] | None = None
        test = statement.test
        if (
            active and isinstance(test, ast.Compare) and len(test.ops) == 1
            and isinstance(test.ops[0], ast.IsNot) and len(test.comparators) == 1
            and isinstance(test.left, ast.Name) and test.left.id == result_name
            and isinstance(test.comparators[0], ast.Constant)
            and test.comparators[0].value is None
        ):
            requirement = {"kind": "return_non_null", "observation_id": observation["observation_id"]}
        elif (
            active and hasattr_available and isinstance(test, ast.Call) and isinstance(test.func, ast.Name)
            and test.func.id == "hasattr" and len(test.args) == 2 and not test.keywords
            and isinstance(test.args[0], ast.Name) and test.args[0].id == result_name
            and isinstance(test.args[1], ast.Constant) and isinstance(test.args[1].value, str)
        ):
            requirement = {
                "kind": "return_has_attribute",
                "attribute": test.args[1].value,
                "observation_id": observation["observation_id"],
            }
        if requirement is not None:
            observation["projection"] = "return_shape_requirement"
            requirements.append(requirement)
        else:
            active = False
        observations.append(observation)
    return observations, requirements, unknowns


def analyze_local_constraints(
    *,
    test_source: str,
    product_source: str,
    test_function: str,
    test_function_line: int,
    product_function: str,
    product_function_line: int,
    call_line: int,
) -> dict[str, Any]:
    """Project a deliberately small local constraint slice without executing code."""
    try:
        test_tree = ast.parse(test_source)
        product_tree = ast.parse(product_source)
    except SyntaxError as exc:
        raise LocalConstraintError("source_syntax_error") from exc
    test = _selected_function(test_tree, test_function, test_function_line, "test")
    product = _selected_function(product_tree, product_function, product_function_line, "product")
    if any(isinstance(node, (ast.Yield, ast.YieldFrom)) for node in ast.walk(test)):
        raise LocalConstraintError("generator_test_function_unsupported")
    if any(isinstance(node, (ast.Yield, ast.YieldFrom)) for node in ast.walk(product)):
        raise LocalConstraintError("generator_product_function_unsupported")
    if product.decorator_list:
        raise LocalConstraintError("decorated_product_function_unsupported")
    _reject_shadowed_product_name(test, product_function)
    names, rows = _parametrize_rows(test)
    call_statement, call, result_name, call_index = _selected_call(test, product_function, call_line)
    protected = {result_name, product_function}
    protected.update(
        argument.id for argument in call.args if isinstance(argument, ast.Starred)
        for argument in ([argument.value] if isinstance(argument.value, ast.Name) else [])
    )
    setup_unknowns = _safe_setup(test.body[:call_index], protected)
    observations, requirements, observation_unknowns = _observations(
        test.body[call_index + 1:], result_name,
        hasattr_available=_builtin_available(test_tree, test, "hasattr"),
    )
    len_available = _builtin_available(product_tree, product, "len")
    branches: list[dict[str, Any]] = []
    for row_index, values in enumerate(rows):
        test_env = dict(zip(names, values, strict=True))
        row_id_value = test_env.get("scenario", row_index)
        row_id = str(row_id_value) if isinstance(row_id_value, (str, int)) else str(row_index)
        try:
            bound_args = _starred_call_arguments(call, test_env)
            returned, value, conditions = _execute_product(
                product, bound_args, len_available=len_available,
            )
            boundary = _return_requirement(value, requirements)
            branches.append({
                "branch_id": f"row:{row_index}:{row_id}",
                "parameter_row": {name: _json_value(test_env[name]) for name in names},
                "parameter_row_literals": {
                    name: _typed_literal(test_env[name]) for name in names
                },
                "normalized_call": {
                    "target": product_function,
                    "actual_variadic_arguments": _json_value(bound_args),
                    "actual_variadic_arguments_literal": _typed_literal(bound_args),
                    "location": _location(call),
                    "binding_origin": "task_provided",
                    "argument_status": "implementation_derived_path_fact_not_test_requirement",
                },
                "conditions": conditions,
                "return_site": _location(returned),
                "boundary_return": boundary,
                "status": _branch_status(boundary),
                "blockers": [],
            })
        except LocalConstraintError as exc:
            branches.append({
                "branch_id": f"row:{row_index}:{row_id}",
                "parameter_row": {name: _json_value(test_env[name]) for name in names},
                "parameter_row_literals": {
                    name: _typed_literal(test_env[name]) for name in names
                },
                "status": {
                    "explanation": "partial",
                    "support": "unresolved",
                    "realization": "blocked",
                },
                "blockers": [str(exc)],
            })
    return {
        "schema_version": SCHEMA_VERSION,
        "analysis_kind": "restricted_local_ast_constraint_projection",
        "binding": {
            "origin": "task_provided",
            "test_function": test_function,
            "test_function_location": _location(test),
            "product_function": product_function,
            "product_function_location": _location(product),
            "call_location": _location(call_statement),
        },
        "observations": observations,
        "branches": branches,
        "common_constraints": [],
        "unknowns": [
            *setup_unknowns,
            *observation_unknowns,
            {"kind": "external_call_runtime_behavior_not_modelled"},
            {"kind": "call_binding_not_automatically_recovered"},
        ],
        "coverage": {
            "status": "partial",
            "scope": "literal_parametrize_rows_and_selected_local_functions",
            "unanalysed": [
                "runtime_dispatch", "exceptions", "state_effects", "aliases",
                "loops", "async", "nonliteral_inputs", "path_completeness",
            ],
        },
    }


__all__ = ["LocalConstraintError", "SCHEMA_VERSION", "analyze_local_constraints"]
