"""Bounded goal-directed composition over GoalIR syntax trees.

The composer projects ordinary assertEquals observations to one object property,
then expands only expressions needed for that property.  It is intentionally a
partial interpreter: unknown dispatch and library behavior remain explicit.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from dataclasses import dataclass, field
import json
from pathlib import Path
from typing import Any

from local_check import Unknown as CheckUnknown
from local_check import evaluate as evaluate_supported


MAX_DEPTH = 24
MAX_PATHS = 32


def literal(value: Any) -> dict[str, Any]:
    return {"kind": "literal", "value": value}


def symbol(identifier: str) -> dict[str, Any]:
    return {"kind": "symbol", "id": identifier}


def loc(node: dict[str, Any] | None) -> dict[str, Any]:
    return dict((node or {}).get("location") or {})


def loc_key(node: dict[str, Any]) -> str:
    place = loc(node)
    return ":".join(str(place.get(key, "?")) for key in
                    ("source_id", "start_line", "start_column"))


def property_name(getter: str) -> str | None:
    if getter.startswith("get") and len(getter) > 3:
        return getter[3].lower() + getter[4:]
    if getter.startswith("is") and len(getter) > 2:
        return getter[2].lower() + getter[3:]
    return None


@dataclass
class Value:
    term: dict[str, Any]
    type_name: str = "unknown"
    object_id: str | None = None
    deferred: dict[str, Any] | None = None
    receiver_name: str | None = None
    collection: list["Value"] | None = None


@dataclass
class HeapObject:
    object_id: str
    type_name: str
    fields: dict[str, Value] = field(default_factory=dict)


@dataclass
class State:
    scopes: list[dict[str, Value]] = field(default_factory=lambda: [{}])
    heap: dict[str, HeapObject] = field(default_factory=dict)
    this_stack: list[str | None] = field(default_factory=list)
    guards: list[dict[str, Any]] = field(default_factory=list)
    events: list[dict[str, Any]] = field(default_factory=list)
    evidence: list[dict[str, Any]] = field(default_factory=list)
    unknowns: list[dict[str, Any]] = field(default_factory=list)
    assumptions: list[dict[str, Any]] = field(default_factory=list)
    invocation_counts: dict[str, int] = field(default_factory=dict)
    next_object: int = 1
    outcome: str = "active"
    returned: Value | None = None
    raised: Value | None = None
    truncated: bool = False

    def lookup(self, name: str) -> Value | None:
        for scope in reversed(self.scopes):
            if name in scope:
                return scope[name]
        return None


@dataclass
class Evaluated:
    state: State
    value: Value | None


class Composer:
    def __init__(self, ir: dict[str, Any]):
        self.ir = ir
        self.types = {item["name"]: item for item in ir.get("types", [])}

    def compose(self, goal_property: str | None = None) -> dict[str, Any]:
        goals: list[dict[str, Any]] = []
        for owner in self.types.values():
            for method in owner.get("methods", []):
                if not any(annotation.endswith(".Test") for annotation in method.get("annotations", [])):
                    continue
                goals.extend(self._test_goals(owner, method, goal_property))
        return {
            "backend": "goal-directed-source-composer/v1",
            "goals": goals,
            "bounds": {"max_call_depth": MAX_DEPTH, "max_paths_per_goal": MAX_PATHS},
            "limitations": [
                "Only literal-first assertEquals observations are selected.",
                "Setup interpretation is bounded to local construction and explicit/Lombok field setters.",
                "Lombok Data getter/setter and Builder projection are syntax-backed assumptions.",
                "Only the selected property is expanded; sibling builder arguments are not executed.",
                "Library calls remain call terms and may return normally or raise.",
                "No SAT, heap-alias proof, or complete Java exception semantics is provided.",
            ],
        }

    def _test_goals(self, owner: dict[str, Any], method: dict[str, Any],
                    requested: str | None) -> list[dict[str, Any]]:
        states = [State()]
        results: list[dict[str, Any]] = []
        statements = (method.get("body") or {}).get("statements", [])
        for statement in statements:
            assertion = self._assertion(statement)
            if assertion is not None:
                expected, actual = assertion
                prop = property_name(actual.get("name", ""))
                if prop is None or (requested is not None and prop != requested):
                    continue
                for index, state in enumerate(states):
                    goal = self._project_goal(state, expected, actual, prop, owner, method, statement)
                    goal["goal_id"] = f"{loc_key(statement)}:{prop}:{index + 1}"
                    results.append(goal)
                continue
            states = self._setup_statement(states, statement, owner["name"])
            states = states[:MAX_PATHS]
        return results

    @staticmethod
    def _assertion(statement: dict[str, Any]) -> tuple[Any, dict[str, Any]] | None:
        if statement.get("kind") != "expr":
            return None
        call = statement.get("value") or {}
        args = call.get("arguments", [])
        if call.get("kind") != "call" or call.get("name") != "assertEquals" or len(args) != 2:
            return None
        if args[0].get("kind") != "literal" or args[1].get("kind") != "call":
            return None
        return args[0].get("value"), args[1]

    def _setup_statement(self, states: list[State], statement: dict[str, Any],
                         owner_type: str) -> list[State]:
        output: list[State] = []
        for state in states:
            kind = statement.get("kind")
            if kind == "local":
                value_node = statement.get("value")
                if value_node is None:
                    value = Value(literal(None), statement.get("type", "unknown"))
                    state.scopes[-1][statement["name"]] = value
                    output.append(state)
                elif value_node.get("kind") == "new":
                    evaluated = self._new_object(state, value_node, 0)
                    for item in evaluated:
                        if item.state.outcome == "active" and item.value is not None:
                            item.state.scopes[-1][statement["name"]] = item.value
                            output.append(item.state)
                elif value_node.get("kind") == "call":
                    # Calls producing the object under test are expanded only after projection.
                    target_type = statement.get("type", "unknown")
                    state.scopes[-1][statement["name"]] = Value(
                        symbol(f"deferred:{statement['name']}"), target_type,
                        deferred=deepcopy(value_node))
                    state.evidence.append(loc(statement))
                    output.append(state)
                else:
                    state.scopes[-1][statement["name"]] = self._raw_value(
                        state, value_node, statement.get("type", "unknown"))
                    output.append(state)
            elif kind == "expr" and (statement.get("value") or {}).get("kind") == "call":
                call = statement["value"]
                target = self._raw_value(state, call.get("target"))
                prop = call.get("name", "")
                field_name = prop[3].lower() + prop[4:] if prop.startswith("set") and len(prop) > 3 else None
                if target.object_id and field_name and len(call.get("arguments", [])) == 1:
                    obj = state.heap[target.object_id]
                    if self._has_lombok_data(obj.type_name) and self._field(obj.type_name, field_name):
                        obj.fields[field_name] = self._raw_value(state, call["arguments"][0])
                        state.evidence.extend([loc(statement), loc(self._field(obj.type_name, field_name))])
                        output.append(state)
                        continue
                state.unknowns.append({"kind": "setup_call_not_interpreted", "location": loc(call),
                                       "text": call.get("text", "")})
                output.append(state)
            else:
                state.unknowns.append({"kind": "setup_statement_not_interpreted",
                                       "location": loc(statement), "text": statement.get("text", "")})
                output.append(state)
        return output

    def _project_goal(self, state: State, expected: Any, actual: dict[str, Any], prop: str,
                      test_type: dict[str, Any], test_method: dict[str, Any],
                      assertion: dict[str, Any]) -> dict[str, Any]:
        target = self._raw_value(state, actual.get("target"))
        projection: dict[str, Any] = {
            "property": prop,
            "getter": actual.get("name"),
            "getter_location": loc(actual),
            "test_method": test_type["name"] + "." + test_method["signature"],
        }
        paths: list[State] = []
        expression = None
        receiver: Value | None = None
        producer_method: dict[str, Any] | None = None
        producer_arguments: list[Value] = []
        if target.deferred is not None and self._has_lombok_data(target.type_name):
            producer = target.deferred
            producer_target = self._raw_value(state, producer.get("target"))
            resolution = self._resolve_method(producer_target, producer.get("name", ""),
                                              len(producer.get("arguments", [])))
            if len(resolution) == 1:
                owner_name, method = resolution[0]
                returned = self._single_return(method)
                expression, projection_evidence = self._builder_projection(
                    target.type_name, returned, prop)
                if expression is None:
                    state.unknowns.append({
                        "kind": "builder_projection_not_proven",
                        "property": prop,
                        "location": loc(returned),
                    })
                else:
                    state.evidence.extend(projection_evidence)
                    receiver = producer_target
                    producer_method = method
                    producer_arguments = [self._raw_value(state, argument)
                                          for argument in producer.get("arguments", [])]
                    projection.update({
                        "kind": "lombok_data_builder_property",
                        "producer": owner_name + "." + method["signature"],
                        "producer_location": loc(method),
                        "field_location": loc(self._field(target.type_name, prop)),
                        "selected_expression_location": loc(expression),
                        "sibling_builder_arguments_executed": False,
                    })
                    state.evidence.extend([loc(actual), loc(method), loc(expression),
                                           loc(self._field(target.type_name, prop))])
            else:
                state.unknowns.append({"kind": "producer_dispatch_ambiguous",
                                       "location": loc(producer), "candidates": [
                                           owner + "." + method["signature"]
                                           for owner, method in resolution]})
        elif target.object_id:
            obj = state.heap[target.object_id]
            field_value = obj.fields.get(prop)
            if field_value is not None:
                projection["kind"] = "object_field"
                expression = None
                paths = [deepcopy(state)]
                paths[0].returned = field_value
                paths[0].outcome = "return"
        else:
            state.unknowns.append({"kind": "assertion_target_not_projectable", "location": loc(actual)})

        if expression is not None and receiver is not None and producer_method is not None:
            seed = deepcopy(state)
            seed.scopes.append({parameter["name"]: self._typed(value, parameter.get("type", "unknown"))
                                for parameter, value in
                                zip(producer_method.get("parameters", []), producer_arguments, strict=True)})
            seed.this_stack.append(receiver.object_id)
            statements = (producer_method.get("body") or {}).get("statements", [])
            prefix = []
            for statement in statements:
                if statement.get("kind") == "return":
                    break
                prefix.append(statement)
            prepared = self._exec_block([seed], prefix, 0)
            for prepared_state in prepared:
                if prepared_state.outcome != "active":
                    prepared_state.this_stack.pop()
                    prepared_state.scopes.pop()
                    paths.append(prepared_state)
                    continue
                evaluated = self._eval_expr(prepared_state, expression, 0)
                for item in evaluated:
                    item.state.this_stack.pop()
                    item.state.scopes.pop()
                    if item.state.outcome == "active":
                        item.state.outcome = "return"
                        item.state.returned = item.value
                    paths.append(item.state)
        if not paths:
            state.outcome = "unknown"
            paths = [state]

        truncated = len(paths) > MAX_PATHS or any(path.truncated for path in paths)
        rendered = [self._render_path(path, index + 1) for index, path in enumerate(paths[:MAX_PATHS])]
        return {
            "expected": expected,
            "assertion_location": loc(assertion),
            "projection": projection,
            "paths": rendered,
            "truncated": truncated,
        }

    def _render_path(self, state: State, index: int) -> dict[str, Any]:
        value = state.returned.term if state.returned else None
        if state.outcome == "raise" and state.raised is not None:
            value = state.raised.term
        return {
            "path_id": f"p{index}",
            "guards": state.guards,
            "value": value,
            "events": state.events,
            "outcome": state.outcome,
            "evidence": self._unique_locations(state.evidence),
            "unknowns": state.unknowns,
            "assumptions": state.assumptions,
        }

    def _new_object(self, state: State, node: dict[str, Any], depth: int) -> list[Evaluated]:
        type_name = node.get("type", "unknown")
        if type_name == "java.util.ArrayList" and not node.get("arguments"):
            term = {"kind": "call", "owner": type_name, "name": "<init>",
                    "target": None, "arguments": []}
            state.assumptions.append({"kind": "supported_collection_summary",
                                      "operation": "empty_ArrayList", "location": loc(node)})
            return [Evaluated(state, Value(term, type_name, collection=[]))]
        object_id = f"o{state.next_object}"
        state.next_object += 1
        obj = HeapObject(object_id, type_name)
        state.heap[object_id] = obj
        for owner in reversed(self._lineage(type_name)):
            for field_node in owner.get("fields", []):
                initializer = field_node.get("initializer")
                obj.fields[field_node["name"]] = self._raw_value(
                    state, initializer, field_node.get("type", "unknown")) if initializer else Value(
                        self._java_default(field_node.get("type", "unknown")), field_node.get("type", "unknown"))
                state.evidence.append(loc(field_node))
        value = Value(symbol(f"object:{object_id}"), type_name, object_id)
        type_node = self.types.get(type_name)
        if type_node is None:
            return [Evaluated(state, value)]
        constructors = [ctor for ctor in type_node.get("constructors", [])
                        if len(ctor.get("parameters", [])) == len(node.get("arguments", []))]
        if len(constructors) != 1:
            state.unknowns.append({"kind": "constructor_resolution", "type": type_name,
                                   "arity": len(node.get("arguments", [])), "candidates": len(constructors)})
            return [Evaluated(state, value)]
        arguments = [self._raw_value(state, item) for item in node.get("arguments", [])]
        self._execute_constructor(state, value, constructors[0], arguments, depth + 1)
        return [Evaluated(state, value)]

    def _execute_constructor(self, state: State, receiver: Value, constructor: dict[str, Any],
                             arguments: list[Value], depth: int) -> None:
        if depth > MAX_DEPTH:
            state.unknowns.append({"kind": "depth_limit", "location": loc(constructor)})
            return
        env = {parameter["name"]: self._typed(value, parameter.get("type", "unknown"))
               for parameter, value in
               zip(constructor.get("parameters", []), arguments, strict=True)}
        state.scopes.append(env)
        state.this_stack.append(receiver.object_id)
        state.evidence.append(loc(constructor))
        for statement in (constructor.get("body") or {}).get("statements", []):
            if statement.get("kind") == "expr" and (statement.get("value") or {}).get("name") == "<init>":
                call = statement["value"]
                superclass = (call.get("target") or {}).get(
                    "type", call.get("declaring_type", "unknown"))
                if superclass in ("java.lang.Object", "unknown") and not call.get("arguments"):
                    state.assumptions.append({"kind": "java_object_constructor",
                                              "location": loc(call)})
                    continue
                owner = self.types.get(superclass)
                args = [self._raw_value(state, item) for item in call.get("arguments", [])]
                candidates = [] if owner is None else [ctor for ctor in owner.get("constructors", [])
                                                       if len(ctor.get("parameters", [])) == len(args)]
                if len(candidates) == 1:
                    self._execute_constructor(state, receiver, candidates[0], args, depth + 1)
                else:
                    state.unknowns.append({"kind": "super_constructor_resolution",
                                           "type": superclass, "location": loc(call)})
            elif statement.get("kind") == "assign":
                target = statement.get("target") or {}
                if target.get("kind") == "field" and receiver.object_id:
                    state.heap[receiver.object_id].fields[target["name"]] = self._raw_value(
                        state, statement.get("value"))
                    state.evidence.append(loc(statement))
                else:
                    state.unknowns.append({"kind": "constructor_assignment", "location": loc(statement)})
            else:
                state.unknowns.append({"kind": "constructor_statement", "location": loc(statement)})
        state.this_stack.pop()
        state.scopes.pop()

    def _eval_expr(self, state: State, node: dict[str, Any] | None, depth: int) -> list[Evaluated]:
        if node is None:
            return [Evaluated(state, Value(literal(None)))]
        if depth > MAX_DEPTH:
            state.unknowns.append({"kind": "depth_limit", "location": loc(node)})
            return [Evaluated(state, Value(symbol(f"bounded:{loc_key(node)}")))]
        kind = node.get("kind")
        if kind == "literal":
            return [Evaluated(state, Value(literal(node.get("value"))))]
        if kind == "var":
            value = state.lookup(node.get("name", ""))
            if value is not None:
                return [Evaluated(state, value)]
            name = node.get("name", "")
            return [Evaluated(state, Value(symbol(f"unbound:{name}"), receiver_name=name))]
        if kind in ("this", "super"):
            object_id = state.this_stack[-1] if state.this_stack else None
            type_name = state.heap[object_id].type_name if object_id in state.heap else node.get("type", "unknown")
            return [Evaluated(state, Value(symbol(f"object:{object_id}"), type_name, object_id))]
        if kind == "type":
            return [Evaluated(state, Value(symbol(f"type:{node.get('name')}"), node.get("name", "unknown")))]
        if kind == "field":
            output = []
            for target in self._eval_expr(state, node.get("target"), depth + 1):
                if target.value and target.value.object_id in target.state.heap:
                    obj = target.state.heap[target.value.object_id]
                    value = obj.fields.get(node.get("name"))
                    if value is not None:
                        output.append(Evaluated(target.state, value))
                        continue
                output.append(Evaluated(target.state, Value(symbol(
                    f"field:{node.get('name')}@{loc_key(node)}"),
                    node.get("type", "unknown"), receiver_name=node.get("name"))))
            return output
        if kind == "binary":
            return self._binary(state, node, depth)
        if kind == "unary":
            output = []
            for item in self._eval_expr(state, node.get("operand"), depth + 1):
                term = item.value.term if item.value else symbol("missing")
                if node.get("operator") == "NOT" and term.get("kind") == "literal":
                    value = Value(literal(not term.get("value")), "boolean")
                else:
                    value = Value({"kind": "unary", "operator": node.get("operator"), "operand": term})
                output.append(Evaluated(item.state, value))
            return output
        if kind == "conditional":
            return self._conditional(state, node, depth)
        if kind == "new":
            return self._new_object(state, node, depth)
        if kind == "call":
            return self._eval_call(state, node, depth)
        state.unknowns.append({"kind": "expression_not_interpreted", "location": loc(node),
                               "node_kind": kind, "text": node.get("text", "")})
        return [Evaluated(state, Value(symbol(f"unknown:{loc_key(node)}")))]

    def _binary(self, state: State, node: dict[str, Any], depth: int) -> list[Evaluated]:
        output = []
        for left in self._eval_expr(state, node.get("left"), depth + 1):
            if left.state.outcome != "active" or left.value is None:
                output.append(left)
                continue
            operator = node.get("operator")
            lterm = left.value.term
            if operator == "OR" and lterm.get("kind") == "literal" and lterm.get("value") is True:
                output.append(Evaluated(left.state, Value(literal(True), "boolean")))
                continue
            if operator == "AND" and lterm.get("kind") == "literal" and lterm.get("value") is False:
                output.append(Evaluated(left.state, Value(literal(False), "boolean")))
                continue
            if operator in ("OR", "AND") and lterm.get("kind") != "literal":
                short_polarity = operator == "OR"
                short = deepcopy(left.state)
                short.guards.append({"condition": lterm, "polarity": short_polarity})
                output.append(Evaluated(short, Value(literal(short_polarity), "boolean")))
                continuing = deepcopy(left.state)
                continuing.guards.append({"condition": lterm, "polarity": not short_polarity})
                right_inputs = self._eval_expr(continuing, node.get("right"), depth + 1)
            else:
                right_inputs = self._eval_expr(left.state, node.get("right"), depth + 1)
            for right in right_inputs:
                if right.value is None:
                    output.append(right)
                    continue
                rterm = right.value.term
                known = self._known_binary(operator, lterm, rterm)
                value = Value(known if known is not None else {
                    "kind": "binary", "operator": operator, "left": lterm, "right": rterm})
                output.append(Evaluated(right.state, value))
        return output

    @staticmethod
    def _known_binary(operator: str, left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any] | None:
        if left.get("kind") != "literal" or right.get("kind") != "literal":
            return None
        lvalue, rvalue = left.get("value"), right.get("value")
        if operator == "EQ":
            return literal(lvalue == rvalue)
        if operator == "NE":
            return literal(lvalue != rvalue)
        if operator == "PLUS" and isinstance(lvalue, (str, int, float)) and isinstance(rvalue, type(lvalue)):
            return literal(lvalue + rvalue)
        if operator == "OR" and isinstance(lvalue, bool) and isinstance(rvalue, bool):
            return literal(lvalue or rvalue)
        if operator == "AND" and isinstance(lvalue, bool) and isinstance(rvalue, bool):
            return literal(lvalue and rvalue)
        return None

    def _conditional(self, state: State, node: dict[str, Any], depth: int) -> list[Evaluated]:
        output = []
        for condition in self._eval_expr(state, node.get("condition"), depth + 1):
            if condition.value is None:
                output.append(condition)
                continue
            term = condition.value.term
            choices = [(bool(term.get("value")), node.get("then") if term.get("value") else node.get("else"))]
            if term.get("kind") != "literal":
                choices = [(True, node.get("then")), (False, node.get("else"))]
            for polarity, expression in choices:
                branch = deepcopy(condition.state)
                if term.get("kind") != "literal":
                    branch.guards.append({"condition": term, "polarity": polarity})
                output.extend(self._eval_expr(branch, expression, depth + 1))
        return output

    def _eval_call(self, state: State, node: dict[str, Any], depth: int) -> list[Evaluated]:
        targets = self._eval_expr(state, node.get("target"), depth + 1)
        output: list[Evaluated] = []
        for target in targets:
            if target.value is None or target.state.outcome != "active":
                output.append(target)
                continue
            combinations = [(target.state, [])]
            for argument in node.get("arguments", []):
                next_combinations = []
                for current, values in combinations:
                    for evaluated in self._eval_expr(current, argument, depth + 1):
                        if evaluated.state.outcome != "active" or evaluated.value is None:
                            output.append(evaluated)
                        else:
                            next_combinations.append((evaluated.state, [*values, evaluated.value]))
                combinations = next_combinations
            for current, arguments in combinations:
                visible = self._visible_call(current, target.value, node, arguments, depth)
                if visible is not None:
                    output.extend(visible)
                elif self._supported_library(node, target.value, arguments):
                    output.extend(self._library_call(current, target.value, node, arguments))
                elif target.value.receiver_name:
                    output.extend(self._boundary_call(current, target.value, node, arguments))
                else:
                    term = self._call_term(target.value, node, arguments)
                    current.unknowns.append({"kind": "unmodeled_library_or_external_call",
                                             "call": term, "location": loc(node)})
                    output.append(Evaluated(current, Value(term)))
        return output

    def _supported_library(self, node: dict[str, Any], target: Value,
                           arguments: list[Value]) -> bool:
        owner, name = self._effective_owner(node, target), node.get("name")
        if owner == "org.springframework.util.StringUtils" and name in ("hasText", "isEmpty"):
            return True
        if owner == "org.springframework.boot.actuate.endpoint.EndpointId" and name == "of":
            return True
        if owner == "org.springframework.web.util.UriComponentsBuilder":
            return name in ("fromHttpUrl", "fromUriString", "path", "toUriString")
        if owner == "java.util.Collections" and name == "singletonList":
            return True
        if target.collection is not None and name in ("isEmpty", "get"):
            return True
        return False

    def _library_call(self, state: State, target: Value, node: dict[str, Any],
                      arguments: list[Value]) -> list[Evaluated]:
        owner, name = self._effective_owner(node, target), node.get("name")
        term = self._call_term(target, node, arguments)
        assumption = {"kind": "supported_library_summary", "owner": owner,
                      "name": name, "location": loc(node),
                      "normal_completion": True}
        state.assumptions.append(assumption)
        if owner == "java.util.Collections" and name == "singletonList" and len(arguments) == 1:
            return [Evaluated(state, Value(term, "java.util.List", collection=[arguments[0]]))]
        if target.collection is not None and name == "isEmpty" and not arguments:
            return [Evaluated(state, Value(literal(not target.collection), "boolean"))]
        if target.collection is not None and name == "get" and len(arguments) == 1:
            index = arguments[0].term
            if index.get("kind") == "literal" and isinstance(index.get("value"), int):
                number = index["value"]
                if 0 <= number < len(target.collection):
                    return [Evaluated(state, target.collection[number])]
            state.unknowns.append({"kind": "collection_index_unknown", "call": term,
                                   "location": loc(node)})
            return [Evaluated(state, Value(term))]
        result_type = "unknown"
        if owner == "org.springframework.util.StringUtils":
            result_type = "boolean"
        elif owner == "org.springframework.boot.actuate.endpoint.EndpointId":
            result_type = owner
        elif owner == "org.springframework.web.util.UriComponentsBuilder":
            result_type = "java.lang.String" if name == "toUriString" else owner
        try:
            value = evaluate_supported(term)
        except CheckUnknown:
            return [Evaluated(state, Value(term, result_type))]
        if isinstance(value, (str, bool, int)) or value is None:
            return [Evaluated(state, Value(literal(value), result_type))]
        return [Evaluated(state, Value(term, result_type))]

    @staticmethod
    def _call_term(target: Value, node: dict[str, Any], arguments: list[Value]) -> dict[str, Any]:
        static = (node.get("target") or {}).get("kind") == "type"
        owner = Composer._effective_owner(node, target)
        return {"kind": "call", "owner": owner,
                "name": node.get("name"), "target": None if static else target.term,
                "arguments": [value.term for value in arguments]}

    @staticmethod
    def _effective_owner(node: dict[str, Any], target: Value) -> str:
        owner = node.get("declaring_type", "unknown")
        return target.type_name if owner == "unknown" and target.type_name != "unknown" else owner

    def _visible_call(self, state: State, target: Value, node: dict[str, Any],
                      arguments: list[Value], depth: int) -> list[Evaluated] | None:
        name = node.get("name", "")
        if target.object_id and self._has_lombok_data(target.type_name):
            prop = property_name(name)
            obj = state.heap[target.object_id]
            if prop and not arguments and prop in obj.fields:
                state.evidence.extend([loc(node), loc(self._field(target.type_name, prop))])
                return [Evaluated(state, obj.fields[prop])]
            if name.startswith("set") and len(arguments) == 1:
                prop = name[3].lower() + name[4:]
                if prop in obj.fields:
                    obj.fields[prop] = arguments[0]
                    state.evidence.extend([loc(node), loc(self._field(target.type_name, prop))])
                    return [Evaluated(state, Value(literal(None), "void"))]
        resolution = self._resolve_method(target, name, len(arguments))
        if len(resolution) != 1:
            if len(resolution) > 1:
                state.unknowns.append({"kind": "ambiguous_dispatch", "location": loc(node),
                                       "candidates": [owner + "." + method["signature"]
                                                      for owner, method in resolution]})
            return None
        owner_name, method = resolution[0]
        return self._execute_method(state, target, owner_name, method, arguments, depth + 1)

    def _execute_method(self, state: State, receiver: Value, owner_name: str,
                        method: dict[str, Any], arguments: list[Value], depth: int) -> list[Evaluated]:
        if depth > MAX_DEPTH:
            state.unknowns.append({"kind": "depth_limit", "location": loc(method)})
            return [Evaluated(state, Value(symbol(f"bounded:{owner_name}.{method['name']}")))]
        state.scopes.append({parameter["name"]: value for parameter, value in
                             zip(method.get("parameters", []), arguments, strict=True)})
        state.this_stack.append(receiver.object_id)
        state.evidence.append(loc(method))
        states = self._exec_block([state], (method.get("body") or {}).get("statements", []), depth)
        output = []
        for result in states:
            result.this_stack.pop()
            result.scopes.pop()
            if result.outcome == "return":
                value = result.returned or Value(literal(None))
                result.outcome, result.returned = "active", None
                output.append(Evaluated(result, value))
            elif result.outcome in ("raise", "unknown"):
                output.append(Evaluated(result, None))
            else:
                output.append(Evaluated(result, Value(literal(None), method.get("return_type", "unknown"))))
        return output

    def _exec_block(self, states: list[State], statements: list[dict[str, Any]], depth: int) -> list[State]:
        active = states
        terminal: list[State] = []
        for statement in statements:
            next_states = []
            for state in active:
                for result in self._exec_statement(state, statement, depth):
                    (next_states if result.outcome == "active" else terminal).append(result)
            if len(next_states) > MAX_PATHS:
                for retained in next_states[:MAX_PATHS]:
                    retained.truncated = True
            active = next_states[:MAX_PATHS]
            if not active:
                break
        combined = terminal + active
        if len(combined) > MAX_PATHS:
            for retained in combined[:MAX_PATHS]:
                retained.truncated = True
        return combined[:MAX_PATHS]

    def _exec_statement(self, state: State, statement: dict[str, Any], depth: int) -> list[State]:
        kind = statement.get("kind")
        if kind == "local":
            output = []
            for item in self._eval_expr(state, statement.get("value"), depth + 1):
                if item.state.outcome == "active" and item.value is not None:
                    item.state.scopes[-1][statement["name"]] = item.value
                output.append(item.state)
            return output
        if kind == "assign":
            output = []
            for item in self._eval_expr(state, statement.get("value"), depth + 1):
                target = statement.get("target") or {}
                if item.value is not None and target.get("kind") == "var":
                    item.state.scopes[-1][target["name"]] = item.value
                elif item.value is not None and target.get("kind") == "field":
                    targets = self._eval_expr(item.state, target.get("target"), depth + 1)
                    if len(targets) == 1 and targets[0].value and targets[0].value.object_id:
                        targets[0].state.heap[targets[0].value.object_id].fields[target["name"]] = item.value
                    else:
                        item.state.unknowns.append({"kind": "field_assignment", "location": loc(statement)})
                output.append(item.state)
            return output
        if kind == "if":
            output = []
            for condition in self._eval_expr(state, statement.get("condition"), depth + 1):
                if condition.state.outcome != "active" or condition.value is None:
                    output.append(condition.state)
                    continue
                term = condition.value.term
                if term.get("kind") == "literal":
                    branch = statement.get("then") if term.get("value") else statement.get("else")
                    if branch is None:
                        output.append(condition.state)
                    else:
                        output.extend(self._exec_statement(condition.state, branch, depth + 1))
                else:
                    for polarity, branch_node in ((True, statement.get("then")),
                                                  (False, statement.get("else"))):
                        branch = deepcopy(condition.state)
                        branch.guards.append({"condition": term, "polarity": polarity})
                        if branch_node is None:
                            output.append(branch)
                        else:
                            output.extend(self._exec_statement(branch, branch_node, depth + 1))
            if len(output) > MAX_PATHS:
                for retained in output[:MAX_PATHS]:
                    retained.truncated = True
            return output[:MAX_PATHS]
        if kind == "block":
            return self._exec_block([state], statement.get("statements", []), depth + 1)
        if kind == "return":
            output = []
            for item in self._eval_expr(state, statement.get("value"), depth + 1):
                if item.state.outcome == "active":
                    item.state.outcome, item.state.returned = "return", item.value
                output.append(item.state)
            return output
        if kind == "throw":
            output = []
            for item in self._eval_expr(state, statement.get("value"), depth + 1):
                if item.state.outcome == "active":
                    item.state.outcome, item.state.raised = "raise", item.value
                output.append(item.state)
            return output
        if kind == "expr":
            return [item.state for item in self._eval_expr(state, statement.get("value"), depth + 1)]
        state.unknowns.append({"kind": "statement_not_interpreted", "location": loc(statement),
                               "node_kind": kind})
        state.outcome = "unknown"
        return [state]

    def _boundary_call(self, state: State, target: Value, node: dict[str, Any],
                       arguments: list[Value]) -> list[Evaluated]:
        key = loc_key(node)
        ordinal = state.invocation_counts.get(key, 0) + 1
        state.invocation_counts[key] = ordinal
        call_id = f"{key}#{ordinal}"
        result_id = f"result:{call_id}"
        call_term = self._call_term(target, node, arguments)
        normal = deepcopy(state)
        receiver = target.receiver_name or target.term
        normal.events.append({"call_id": call_id, "receiver": receiver,
                              "name": node.get("name"),
                              "arguments": [value.term for value in arguments],
                              "outcome": "normal", "result_symbol": result_id,
                              "location": loc(node)})
        raised = deepcopy(state)
        exception_id = f"exception:{call_id}"
        raised.events.append({"call_id": call_id, "receiver": receiver,
                              "name": node.get("name"),
                              "arguments": [value.term for value in arguments],
                              "outcome": "raise", "result_symbol": exception_id,
                              "location": loc(node)})
        raised.outcome = "raise"
        raised.raised = Value(symbol(exception_id))
        normal.assumptions.append({"kind": "mock_boundary_response", "call_id": call_id,
                                   "receiver": receiver, "normal_completion": True,
                                   "heap_writes": "unknown_not_modeled", "location": loc(node)})
        return [Evaluated(normal, Value(symbol(result_id))), Evaluated(raised, None)]

    def _resolve_method(self, receiver: Value, name: str, arity: int) -> list[tuple[str, dict[str, Any]]]:
        type_name = receiver.type_name
        if receiver.object_id and type_name == "unknown":
            type_name = receiver.type_name
        for owner in self._lineage(type_name):
            matches = [method for method in owner.get("methods", [])
                       if method.get("name") == name and len(method.get("parameters", [])) == arity]
            if matches:
                return [(owner["name"], method) for method in matches]
        return []

    def _lineage(self, type_name: str) -> list[dict[str, Any]]:
        result = []
        seen = set()
        current = type_name
        while current in self.types and current not in seen:
            seen.add(current)
            owner = self.types[current]
            result.append(owner)
            current = owner.get("superclass", "unknown")
        return result

    def _has_lombok_data(self, type_name: str) -> bool:
        owner = self.types.get(type_name)
        return owner is not None and "lombok.Data" in owner.get("annotations", [])

    def _field(self, type_name: str, name: str) -> dict[str, Any] | None:
        for owner in self._lineage(type_name):
            for field_node in owner.get("fields", []):
                if field_node.get("name") == name:
                    return field_node
        return None

    @staticmethod
    def _single_return(method: dict[str, Any]) -> dict[str, Any] | None:
        body = method.get("body") or {}
        returns = [statement.get("value") for statement in body.get("statements", [])
                   if statement.get("kind") == "return"]
        return returns[0] if len(returns) == 1 else None

    def _builder_projection(self, type_name: str, node: dict[str, Any] | None,
                            prop: str) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
        owner = self.types.get(type_name)
        field_node = self._field(type_name, prop)
        if owner is None or field_node is None:
            return None, []
        getter_names = {"get" + prop[:1].upper() + prop[1:],
                        "is" + prop[:1].upper() + prop[1:]}
        if any(method.get("name") in getter_names for method in owner.get("methods", [])):
            return None, []
        constructors = [constructor for constructor in owner.get("constructors", [])
                        if "lombok.Builder" in constructor.get("annotations", [])]
        matching = []
        for constructor in constructors:
            if not any(parameter.get("name") == prop for parameter in constructor.get("parameters", [])):
                continue
            for statement in (constructor.get("body") or {}).get("statements", []):
                target = statement.get("target") or {}
                value = statement.get("value") or {}
                if (statement.get("kind") == "assign" and target.get("kind") == "field"
                        and target.get("name") == prop and value.get("kind") == "var"
                        and value.get("name") == prop):
                    matching.append((constructor, statement))
                    break
        if len(matching) != 1 or not isinstance(node, dict) or node.get("kind") != "call" \
                or node.get("name") != "build" or node.get("arguments"):
            return None, []
        cursor = node
        while isinstance(cursor, dict) and cursor.get("kind") == "call":
            if cursor.get("name") == prop and len(cursor.get("arguments", [])) == 1:
                constructor, assignment = matching[0]
                return cursor["arguments"][0], [loc(field_node), loc(constructor), loc(assignment)]
            cursor = cursor.get("target")
        return None, []

    def _raw_value(self, state: State, node: dict[str, Any] | None,
                   type_hint: str = "unknown") -> Value:
        if node is None:
            return Value(literal(None), type_hint)
        kind = node.get("kind")
        if kind == "literal":
            return Value(literal(node.get("value")), type_hint)
        if kind == "var":
            return state.lookup(node.get("name", "")) or Value(
                symbol(f"unbound:{node.get('name')}"), type_hint,
                receiver_name=node.get("name"))
        if kind in ("this", "super"):
            object_id = state.this_stack[-1] if state.this_stack else None
            return Value(symbol(f"object:{object_id}"), node.get("type", type_hint), object_id)
        if kind == "field":
            target = self._raw_value(state, node.get("target"))
            if target.object_id in state.heap:
                return state.heap[target.object_id].fields.get(node.get("name"), Value(
                    symbol(f"field:{node.get('name')}@{loc_key(node)}"), type_hint))
            return Value(symbol(f"field:{node.get('name')}@{loc_key(node)}"), type_hint,
                         receiver_name=node.get("name"))
        if kind == "type":
            return Value(symbol(f"type:{node.get('name')}"), node.get("name", type_hint))
        if kind == "call":
            target = self._raw_value(state, node.get("target"))
            values = [self._raw_value(state, item) for item in node.get("arguments", [])]
            args = [value.term for value in values]
            owner = self._effective_owner(node, target)
            static = (node.get("target") or {}).get("kind") == "type"
            term = {"kind": "call", "owner": owner,
                          "name": node.get("name"), "target": None if static else target.term,
                          "arguments": args}
            if owner == "java.util.Collections" and node.get("name") == "singletonList" \
                    and len(values) == 1:
                return Value(term, "java.util.List", collection=[values[0]])
            return Value(term, type_hint)
        if kind == "binary":
            return Value({"kind": "binary", "operator": node.get("operator"),
                          "left": self._raw_value(state, node.get("left")).term,
                          "right": self._raw_value(state, node.get("right")).term}, type_hint)
        if kind == "new":
            # Initializers are represented without executing constructors in raw setup values.
            term = {"kind": "call", "owner": node.get("type"), "name": "<init>",
                    "target": None,
                    "arguments": [self._raw_value(state, item).term
                                  for item in node.get("arguments", [])]}
            if node.get("type") == "java.util.ArrayList" and not node.get("arguments"):
                return Value(term, node.get("type"), collection=[])
            return Value(term, node.get("type", type_hint))
        return Value(symbol(f"raw:{loc_key(node)}"), type_hint)

    @staticmethod
    def _java_default(type_name: str) -> dict[str, Any]:
        if type_name == "boolean":
            return literal(False)
        if type_name in {"byte", "short", "int", "long", "float", "double", "char"}:
            return literal(0)
        return literal(None)

    @staticmethod
    def _typed(value: Value, type_name: str) -> Value:
        if value.type_name != "unknown" or type_name == "unknown":
            return value
        return Value(value.term, type_name, value.object_id, value.deferred,
                     value.receiver_name, value.collection)

    @staticmethod
    def _unique_locations(values: list[dict[str, Any]]) -> list[dict[str, Any]]:
        result = []
        seen = set()
        for value in values:
            key = json.dumps(value, sort_keys=True)
            if value and key not in seen:
                seen.add(key)
                result.append(value)
        return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ir", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--goal-property")
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError("output must not exist")
    result = Composer(json.loads(args.ir.read_text(encoding="utf-8"))).compose(args.goal_property)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"goals": len(result["goals"]),
                      "paths": sum(len(goal["paths"]) for goal in result["goals"])}, indent=2))


if __name__ == "__main__":
    main()
