"""Bounded Python AST path interpreter for mock-behavior research.

The interpreter never imports or executes analyzed code.  Unknown calls fork into
normal-return and exceptional outcomes, giving each invocation its own symbol.
"""
from __future__ import annotations

import ast
import argparse
from copy import deepcopy
from dataclasses import dataclass, field
import json
import sys
from typing import Any


MAX_PATHS = 64
MAX_CALLS_PER_PATH = 16


@dataclass
class State:
    env: dict[str, dict[str, Any]] = field(default_factory=dict)
    guards: list[dict[str, Any]] = field(default_factory=list)
    events: list[dict[str, Any]] = field(default_factory=list)
    value_relations: list[dict[str, Any]] = field(default_factory=list)
    unknowns: list[dict[str, Any]] = field(default_factory=list)
    facts: dict[str, dict[str, Any]] = field(default_factory=dict)
    ordinals: dict[str, int] = field(default_factory=dict)


@dataclass
class Eval:
    state: State
    value: dict[str, Any] | None = None
    terminal: dict[str, Any] | None = None


def value(text: str, *, atom: str | None = None, const: Any = ...) -> dict[str, Any]:
    result: dict[str, Any] = {"text": text}
    if atom is not None:
        result["atom"] = atom
    if const is not ...:
        result["const"] = const
    return result


def source_line(source: dict[str, Any], node: ast.AST) -> int:
    return int(source.get("start_line", 1)) + int(node.lineno) - 1


def location(source: dict[str, Any], node: ast.AST) -> str:
    return f"{source['source_id']}:{source_line(source, node)}:{getattr(node, 'col_offset', 0)}"


class Interpreter:
    def __init__(self, request: dict[str, Any]):
        self.request = request
        self.source: dict[str, Any] | None = None
        self.function: ast.FunctionDef | ast.AsyncFunctionDef | None = None
        self.completed: list[dict[str, Any]] = []
        self.pruned: list[dict[str, Any]] = []
        self.truncated = False

    def run(self) -> dict[str, Any]:
        sources = self.request.get("sources")
        entry = self.request.get("entry")
        if not isinstance(sources, list) or not sources or not isinstance(entry, dict):
            raise ValueError("sources and entry are required")
        source_id = entry.get("source_id")
        function_name = entry.get("function")
        matches: list[tuple[dict[str, Any], ast.FunctionDef | ast.AsyncFunctionDef]] = []
        for source in sources:
            if source.get("source_id") != source_id:
                continue
            tree = ast.parse(source["text"], filename=source["logical_path"])
            for node in tree.body:
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == function_name:
                    matches.append((source, node))
        if len(matches) != 1:
            raise ValueError("entry must identify exactly one top-level function")
        self.source, self.function = matches[0]
        initial = State()
        for argument in (
            *self.function.args.posonlyargs,
            *self.function.args.args,
            *self.function.args.kwonlyargs,
        ):
            symbol = f"arg:{argument.arg}"
            initial.env[argument.arg] = value(argument.arg, atom=symbol)
        if self.function.args.vararg:
            initial.env[self.function.args.vararg.arg] = value(
                self.function.args.vararg.arg, atom=f"arg:{self.function.args.vararg.arg}"
            )
        if self.function.args.kwarg:
            initial.env[self.function.args.kwarg.arg] = value(
                self.function.args.kwarg.arg, atom=f"arg:{self.function.args.kwarg.arg}"
            )
        survivors = self.exec_block([initial], self.function.body)
        for state in survivors:
            self.finish(state, {"kind": "fallthrough"})
        paths = []
        for index, item in enumerate(self.completed, 1):
            state = item.pop("_state")
            precision = "exact_supported"
            if state.unknowns:
                precision = "bounded_with_unknowns"
            paths.append({
                "path_id": f"p{index}",
                "guards": state.guards,
                "events": state.events,
                "outcome": item,
                "value_relations": state.value_relations,
                "unknowns": state.unknowns,
                "verdict": "candidate_consistent",
                "precision": precision,
            })
        return {
            "status": "ok",
            "backend": "python-ast-bounded-paths/v1",
            "entry": {
                "source_id": source_id,
                "function": function_name,
                "line": source_line(self.source, self.function),
            },
            "paths": paths,
            "pruned_paths": self.pruned,
            "complete": not self.truncated and all(not p["unknowns"] for p in paths),
            "exploration_complete": not self.truncated,
            "limits": {"max_paths": MAX_PATHS, "max_calls_per_path": MAX_CALLS_PER_PATH},
            "limitations": [
                "Only the selected function is interpreted; calls are unknown boundaries.",
                "Each unknown invocation has independent normal and exceptional outcomes.",
                "Null, constant, and simple integer contradictions are checked without SMT.",
                "Attribute/subscript evaluation, loops, try, comprehensions, dynamic dispatch, and heap effects are not modeled precisely.",
                "candidate_consistent means no contradiction was found in the supported abstraction, not executable feasibility.",
                "The analysis contains no mock-framework replaceability or read-only binding model.",
            ],
        }

    def exec_block(self, states: list[State], statements: list[ast.stmt]) -> list[State]:
        active = states
        for statement in statements:
            next_states: list[State] = []
            for state in active:
                next_states.extend(self.exec_statement(state, statement))
                if len(next_states) + len(self.completed) >= MAX_PATHS:
                    self.truncated = True
                    next_states = next_states[: max(0, MAX_PATHS - len(self.completed))]
                    break
            active = next_states
            if not active:
                break
        return active

    def exec_statement(self, state: State, node: ast.stmt) -> list[State]:
        assert self.source is not None
        if isinstance(node, (ast.Pass, ast.Global, ast.Nonlocal)):
            return [state]
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            return [state]
        if isinstance(node, ast.Assign):
            evaluations = self.eval_expr(state, node.value)
            result: list[State] = []
            for evaluated in evaluations:
                if evaluated.terminal:
                    self.finish(evaluated.state, evaluated.terminal)
                    continue
                if len(node.targets) != 1 or not isinstance(node.targets[0], ast.Name):
                    self.add_unknown(evaluated.state, node, "unsupported_assignment_target")
                    result.append(evaluated.state)
                    continue
                self.bind(evaluated.state, node.targets[0].id, evaluated.value, node)
                result.append(evaluated.state)
            return result
        if isinstance(node, ast.AnnAssign):
            if node.value is None or not isinstance(node.target, ast.Name):
                self.add_unknown(state, node, "unsupported_annotated_assignment")
                return [state]
            result: list[State] = []
            for evaluated in self.eval_expr(state, node.value):
                if evaluated.terminal:
                    self.finish(evaluated.state, evaluated.terminal)
                else:
                    self.bind(evaluated.state, node.target.id, evaluated.value, node)
                    result.append(evaluated.state)
            return result
        if isinstance(node, ast.If):
            evaluations = self.eval_expr(state, node.test)
            result: list[State] = []
            for evaluated in evaluations:
                if evaluated.terminal:
                    self.finish(evaluated.state, evaluated.terminal)
                    continue
                for truth, body in ((True, node.body), (False, node.orelse)):
                    branch = deepcopy(evaluated.state)
                    if self.add_guard(branch, evaluated.value, truth, node):
                        result.extend(self.exec_block([branch], body))
            return result
        if isinstance(node, ast.Return):
            evaluations = [Eval(state, value("None", const=None))] if node.value is None else self.eval_expr(state, node.value)
            for evaluated in evaluations:
                if evaluated.terminal:
                    self.finish(evaluated.state, evaluated.terminal)
                else:
                    self.finish(evaluated.state, {"kind": "return", "value": evaluated.value})
            return []
        if isinstance(node, ast.Raise):
            expression = "reraised" if node.exc is None else ast.unparse(node.exc)
            self.finish(state, {"kind": "raise", "exception": expression, "origin": "visible_raise"})
            return []
        if isinstance(node, ast.Expr):
            result: list[State] = []
            for evaluated in self.eval_expr(state, node.value):
                if evaluated.terminal:
                    self.finish(evaluated.state, evaluated.terminal)
                else:
                    result.append(evaluated.state)
            return result
        if isinstance(node, (ast.For, ast.AsyncFor, ast.While, ast.Try, ast.TryStar, ast.With, ast.AsyncWith)):
            self.add_unknown(state, node, "unsupported_control_structure")
            return [state]
        self.add_unknown(state, node, "unsupported_statement")
        return [state]

    def eval_expr(self, state: State, node: ast.AST) -> list[Eval]:
        if isinstance(node, ast.Constant):
            return [Eval(state, value(repr(node.value), const=node.value))]
        if isinstance(node, ast.Name):
            return [Eval(state, deepcopy(state.env.get(node.id, value(node.id, atom=f"global:{node.id}"))))]
        if isinstance(node, ast.Starred):
            output = self.eval_expr(state, node.value)
            for item in output:
                if item.terminal is None:
                    item.value = value("*" + item.value["text"], atom=item.value.get("atom"))
            return output
        if isinstance(node, ast.NamedExpr) and isinstance(node.target, ast.Name):
            results = self.eval_expr(state, node.value)
            for result in results:
                if result.terminal is None:
                    self.bind(result.state, node.target.id, result.value, node)
            return results
        if isinstance(node, ast.Attribute):
            results = self.eval_expr(state, node.value)
            for result in results:
                if result.terminal is None:
                    self.add_unknown(result.state, node, "attribute_access_may_raise")
                    result.value = value(f"{result.value['text']}.{node.attr}", atom=f"attr:{location(self.source, node)}")
            return results
        if isinstance(node, ast.Subscript):
            bases = self.eval_expr(state, node.value)
            output: list[Eval] = []
            for base in bases:
                if base.terminal:
                    output.append(base)
                    continue
                indexes = self.eval_expr(base.state, node.slice)
                for index in indexes:
                    if index.terminal:
                        output.append(index)
                    else:
                        self.add_unknown(index.state, node, "subscript_may_raise")
                        output.append(Eval(index.state, value(
                            f"{base.value['text']}[{index.value['text']}]",
                            atom=f"subscript:{location(self.source, node)}",
                        )))
            return output
        if isinstance(node, ast.Call):
            return self.eval_call(state, node)
        if isinstance(node, ast.Compare) and len(node.ops) == 1 and len(node.comparators) == 1:
            lefts = self.eval_expr(state, node.left)
            output: list[Eval] = []
            for left in lefts:
                if left.terminal:
                    output.append(left)
                    continue
                rights = self.eval_expr(left.state, node.comparators[0])
                for right in rights:
                    if right.terminal:
                        output.append(right)
                        continue
                    op = type(node.ops[0]).__name__
                    operator_text = {
                        "Eq": "==", "NotEq": "!=", "Is": "is", "IsNot": "is not",
                        "Lt": "<", "LtE": "<=", "Gt": ">", "GtE": ">=",
                        "In": "in", "NotIn": "not in",
                    }.get(op, op)
                    comparison = value(f"({left.value['text']} {operator_text} {right.value['text']})")
                    comparison["constraint"] = {
                        "left": left.value,
                        "operator": op,
                        "right": right.value,
                    }
                    output.append(Eval(right.state, comparison))
            return output
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
            output = self.eval_expr(state, node.operand)
            for item in output:
                if item.terminal is None:
                    item.value = {"text": f"not ({item.value['text']})", "negated": item.value}
            return output
        if isinstance(node, ast.BinOp):
            return self.eval_binary(state, node)
        if isinstance(node, ast.BoolOp):
            self.add_unknown(state, node, "boolean_short_circuit_not_expanded")
            return [Eval(state, value(ast.unparse(node), atom=f"bool:{location(self.source, node)}"))]
        self.add_unknown(state, node, "unsupported_expression")
        return [Eval(state, value(ast.unparse(node), atom=f"opaque:{location(self.source, node)}"))]

    def eval_binary(self, state: State, node: ast.BinOp) -> list[Eval]:
        lefts = self.eval_expr(state, node.left)
        output: list[Eval] = []
        for left in lefts:
            if left.terminal:
                output.append(left)
                continue
            for right in self.eval_expr(left.state, node.right):
                if right.terminal:
                    output.append(right)
                    continue
                text = f"({left.value['text']} {ast.unparse(node.op)} {right.value['text']})"
                if isinstance(node.op, (ast.Add, ast.Sub)) and isinstance(left.value.get("const"), int) and isinstance(right.value.get("const"), int):
                    concrete = left.value["const"] + right.value["const"] if isinstance(node.op, ast.Add) else left.value["const"] - right.value["const"]
                    output.append(Eval(right.state, value(repr(concrete), const=concrete)))
                else:
                    output.append(Eval(right.state, value(text, atom=f"binary:{location(self.source, node)}")))
        return output

    def eval_call(self, state: State, node: ast.Call) -> list[Eval]:
        assert self.source is not None
        if len(state.events) >= MAX_CALLS_PER_PATH:
            self.truncated = True
            self.add_unknown(state, node, "call_limit")
            return [Eval(state, value(ast.unparse(node), atom=f"opaque-call:{location(self.source, node)}"))]
        target = ast.unparse(node.func)
        receiver = ast.unparse(node.func.value) if isinstance(node.func, ast.Attribute) else None
        evaluations = [Eval(state, value(""))]
        arguments: list[list[str]] = [[]]
        for argument in node.args:
            next_evaluations: list[Eval] = []
            next_arguments: list[list[str]] = []
            for current, prior in zip(evaluations, arguments):
                if current.terminal:
                    next_evaluations.append(current)
                    next_arguments.append(prior)
                    continue
                for item in self.eval_expr(current.state, argument):
                    next_evaluations.append(item)
                    next_arguments.append([*prior, item.value["text"]] if item.terminal is None else prior)
            evaluations, arguments = next_evaluations, next_arguments
        keyword_arguments: list[list[dict[str, str]]] = [[] for _ in evaluations]
        for keyword in node.keywords:
            next_evaluations = []
            next_arguments = []
            next_keywords: list[list[dict[str, str]]] = []
            for current, prior_args, prior_keywords in zip(evaluations, arguments, keyword_arguments):
                if current.terminal:
                    next_evaluations.append(current)
                    next_arguments.append(prior_args)
                    next_keywords.append(prior_keywords)
                    continue
                for item in self.eval_expr(current.state, keyword.value):
                    next_evaluations.append(item)
                    next_arguments.append(prior_args)
                    appended = prior_keywords if item.terminal else [
                        *prior_keywords,
                        {"name": keyword.arg if keyword.arg is not None else "**", "value": item.value["text"]},
                    ]
                    next_keywords.append(appended)
            evaluations, arguments, keyword_arguments = next_evaluations, next_arguments, next_keywords
        output: list[Eval] = []
        for current, args, kwargs in zip(evaluations, arguments, keyword_arguments):
            if current.terminal:
                output.append(current)
                continue
            callsite = location(self.source, node)
            ordinal = current.state.ordinals.get(callsite, 0) + 1
            for outcome in ("normal", "raise"):
                branch = deepcopy(current.state)
                branch.ordinals[callsite] = ordinal
                event_id = f"{callsite}#{ordinal}"
                event = {
                    "event_id": event_id,
                    "callsite": callsite,
                    "target": target,
                    "receiver": self.render_reference(node.func.value, branch) if receiver else None,
                    "arguments": args,
                    "keyword_arguments": kwargs,
                    "outcome": outcome,
                    "effect": "unknown",
                    "resolution": "external_or_unresolved",
                }
                branch.events.append(event)
                self.add_unknown(branch, node, "external_call_effect_unknown")
                if isinstance(node.func, ast.Attribute):
                    self.add_unknown(branch, node.func, "call_receiver_evaluation_not_modeled")
                if outcome == "raise":
                    output.append(Eval(branch, terminal={
                        "kind": "raise",
                        "exception": f"exception[{event_id}]",
                        "origin": "unknown_call",
                        "event_id": event_id,
                    }))
                else:
                    symbol = f"result[{event_id}]"
                    output.append(Eval(branch, value(symbol, atom=symbol)))
        return output

    def render_reference(self, node: ast.AST, state: State) -> str:
        if isinstance(node, ast.Name):
            return state.env.get(node.id, value(node.id))["text"]
        if isinstance(node, ast.Attribute):
            return f"{self.render_reference(node.value, state)}.{node.attr}"
        return ast.unparse(node)

    def bind(self, state: State, name: str, assigned: dict[str, Any] | None, node: ast.AST) -> None:
        assert self.source is not None and assigned is not None
        state.env[name] = deepcopy(assigned)
        state.value_relations.append({
            "target": name,
            "value": assigned["text"],
            "source": location(self.source, node),
        })

    def add_guard(self, state: State, condition: dict[str, Any] | None, truth: bool, node: ast.AST) -> bool:
        assert self.source is not None and condition is not None
        rendered = condition["text"] if truth else f"not ({condition['text']})"
        contradiction = self.update_facts(state, condition, truth)
        guard = {"expression": rendered, "polarity": truth, "source": location(self.source, node)}
        if contradiction:
            self.pruned.append({"guards": [*state.guards, guard], "reason": contradiction})
            return False
        state.guards.append(guard)
        return True

    def update_facts(self, state: State, condition: dict[str, Any], truth: bool) -> str | None:
        if "const" in condition:
            if bool(condition["const"]) != truth:
                return "constant_condition_contradiction"
            return None
        if "negated" in condition:
            return self.update_facts(state, condition["negated"], not truth)
        constraint = condition.get("constraint")
        if not constraint:
            return None
        left, right = constraint["left"], constraint["right"]
        operator = constraint["operator"]
        atom, constant = None, ...
        if left.get("atom") is not None and "const" in right:
            atom, constant = left["atom"], right["const"]
        elif right.get("atom") is not None and "const" in left:
            atom, constant = right["atom"], left["const"]
            operator = {"Lt": "Gt", "LtE": "GtE", "Gt": "Lt", "GtE": "LtE"}.get(operator, operator)
        if atom is None or constant is ...:
            return None
        if not truth:
            operator = {
                "Is": "IsNot", "IsNot": "Is", "Eq": "NotEq", "NotEq": "Eq",
                "Lt": "GtE", "LtE": "Gt", "Gt": "LtE", "GtE": "Lt",
            }.get(operator, operator)
        fact = state.facts.setdefault(atom, {})
        if constant is None and operator in ("Is", "Eq", "IsNot", "NotEq"):
            desired = "null" if operator in ("Is", "Eq") else "non_null"
            prior = fact.get("nullness")
            if prior and prior != desired:
                return f"{atom}_both_null_and_non_null"
            fact["nullness"] = desired
            return None
        if isinstance(constant, (bool, int)) and operator in ("Eq", "NotEq"):
            if operator == "Eq":
                if "eq" in fact and fact["eq"] != constant:
                    return f"{atom}_conflicting_equalities"
                if constant in fact.get("not_eq", set()):
                    return f"{atom}_equality_conflicts_with_inequality"
                fact["eq"] = constant
            else:
                if fact.get("eq") == constant:
                    return f"{atom}_inequality_conflicts_with_equality"
                fact.setdefault("not_eq", set()).add(constant)
            return None
        if isinstance(constant, int) and not isinstance(constant, bool):
            lower = constant + (1 if operator == "Gt" else 0) if operator in ("Gt", "GtE") else None
            upper = constant - (1 if operator == "Lt" else 0) if operator in ("Lt", "LtE") else None
            if lower is not None:
                fact["lower"] = max(fact.get("lower", lower), lower)
            if upper is not None:
                fact["upper"] = min(fact.get("upper", upper), upper)
            if fact.get("lower", -10**100) > fact.get("upper", 10**100):
                return f"{atom}_empty_integer_interval"
        return None

    def add_unknown(self, state: State, node: ast.AST, reason: str) -> None:
        assert self.source is not None
        item = {"source": location(self.source, node), "reason": reason, "code": ast.unparse(node)[:200]}
        if item not in state.unknowns:
            state.unknowns.append(item)

    def finish(self, state: State, outcome: dict[str, Any]) -> None:
        if len(self.completed) >= MAX_PATHS:
            self.truncated = True
            return
        self.completed.append({**outcome, "_state": state})


def analyze(request: dict[str, Any]) -> dict[str, Any]:
    return Interpreter(request).run()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input")
    parser.add_argument("--sources-input")
    parser.add_argument("--source-id")
    parser.add_argument("--function")
    parser.add_argument("--output")
    args = parser.parse_args()
    if args.sources_input:
        if not args.source_id or not args.function:
            parser.error("--sources-input requires --source-id and --function")
        with open(args.sources_input, encoding="utf-8") as handle:
            source_bundle = json.load(handle)
        request = {
            "sources": source_bundle["sources"],
            "entry": {"source_id": args.source_id, "function": args.function},
        }
    elif args.input:
        with open(args.input, encoding="utf-8") as handle:
            request = json.load(handle)
    else:
        request = json.load(sys.stdin)
    result = analyze(request)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as handle:
            json.dump(result, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
    else:
        json.dump(result, sys.stdout, ensure_ascii=False, sort_keys=True)


if __name__ == "__main__":
    main()
