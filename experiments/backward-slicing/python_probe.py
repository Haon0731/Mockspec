"""Bounded Python source-level backward evidence graph research prototype."""
from __future__ import annotations

import ast
import json
import sys

from source_selection import python_dynamic_loader_evidence


def compact(node: ast.AST) -> str:
    try:
        return ast.unparse(node)[:300]
    except Exception:
        return type(node).__name__


def scoped_nodes(root: ast.AST):
    """Walk one lexical function scope, excluding nested executable scopes."""
    yield root
    for child in ast.iter_child_nodes(root):
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)):
            continue
        yield from scoped_nodes(child)


class Probe:
    def __init__(self, request: dict):
        self.request = request
        self.nodes: dict[str, dict] = {}
        self.edges: list[dict] = []
        self.edge_keys: set[tuple] = set()
        self.trees: list[tuple[dict, ast.Module]] = []
        self.functions: dict[str, list[tuple[dict, ast.FunctionDef | ast.AsyncFunctionDef]]] = {}
        self.parents: dict[int, ast.AST] = {}
        self.seed_bindings: list[dict] = []
        self.statement_ids: set[str] = set()

    def opaque(self, source: dict, item: ast.AST) -> bool:
        line = source.get("start_line", 1) + getattr(item, "lineno", 0) - 1
        return any(boundary["source_id"] == source["source_id"]
                   and boundary["start_line"] <= line <= boundary["end_line"]
                   for boundary in self.request.get("opaque_methods", []))

    def node(self, source: dict, item: ast.AST, kind: str | None = None) -> str | None:
        if not hasattr(item, "lineno") or self.opaque(source, item):
            return None
        start = source.get("start_line", 1)
        line = start + item.lineno - 1
        end = start + getattr(item, "end_lineno", item.lineno) - 1
        ident = f"{source['source_id']}:{line}:{getattr(item, 'col_offset', 0)}:{type(item).__name__}"
        if ident not in self.nodes and len(self.nodes) >= 10000:
            raise ValueError("node limit exceeded")
        self.nodes.setdefault(ident, {
            "id": ident, "source_id": source["source_id"], "start_line": line,
            "end_line": end, "kind": kind or type(item).__name__, "code": compact(item),
        })
        return ident

    def edge(self, source_id: str | None, target_id: str | None, kind: str, **details):
        if not source_id or not target_id or source_id == target_id:
            return
        key = (source_id, target_id, kind, json.dumps(details, sort_keys=True))
        if key in self.edge_keys:
            return
        if len(self.edges) >= 50000:
            raise ValueError("edge limit exceeded")
        self.edge_keys.add(key)
        self.edges.append({"from": source_id, "to": target_id, "kind": kind, **details})

    def run(self) -> dict:
        sources = self.request.get("sources")
        if not isinstance(sources, list) or not sources:
            raise ValueError("sources must be a non-empty array")
        for source in sources:
            tree = ast.parse(source["text"], filename=source["logical_path"])
            self.trees.append((source, tree))
            for parent in ast.walk(tree):
                for child in ast.iter_child_nodes(parent):
                    self.parents[id(child)] = parent
            for item in ast.walk(tree):
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    self.functions.setdefault(item.name, []).append((source, item))
                if isinstance(item, ast.stmt):
                    ident = self.node(source, item)
                    if ident is not None and not isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                        self.statement_ids.add(ident)
        for source, tree in self.trees:
            self.walk_block(source, tree.body)
            for function in [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]:
                if self.opaque(source, function):
                    continue
                self.walk_block(source, function.body)
                self.add_local_data(source, function)
            self.add_calls(source, tree)
        dynamic_loader_bindings = python_dynamic_loader_evidence(sources)
        self.add_dynamic_loader_edges(dynamic_loader_bindings)
        seeds = self.resolve_seeds()
        closure = self.backward_closure(seeds)
        return {
            "status": "ok", "backend": "python-ast-source-evidence-prototype",
            "seeds": seeds,
            "seed_bindings": self.seed_bindings,
            "dynamic_loader_bindings": dynamic_loader_bindings,
            "nodes": sorted((n for key, n in self.nodes.items() if key in closure), key=lambda n: n["id"]),
            "edges": [e for e in self.edges if e["from"] in closure and e["to"] in closure],
            "limitations": [
                "data_candidate edges use lexical name and ordering candidates, not sound reaching definitions",
                "lexical_control edges are AST ancestry, not post-dominator control dependence",
                "normal_predecessor records possible continuation after normal completion; it does not prove branch feasibility",
                "an exception-capable call may prevent its normal successor from running",
                "call_target_candidate uses visible function names and does not resolve dynamic dispatch, aliases, decorators, or reflection",
                "call_event_candidate exposes a visible callee event that may execute; it does not guarantee the event is reached or returns normally",
                "unresolved calls are retained as dependency boundaries; no external implementation is executed or inspected",
                "dynamic loader origins require a visible static path, namespace key, selected definition, and unrebound test callable",
            ],
        }

    def item_at(self, source_id: str, line: int, kinds: tuple[type, ...]) -> tuple[dict, ast.AST] | None:
        for source, tree in self.trees:
            if source["source_id"] != source_id:
                continue
            for item in ast.walk(tree):
                actual = source.get("start_line", 1) + getattr(item, "lineno", 0) - 1
                if actual == line and isinstance(item, kinds):
                    return source, item
        return None

    def add_dynamic_loader_edges(self, bindings: list[dict]):
        resolved_calls: set[tuple[int, str]] = set()
        for binding in bindings:
            if binding["status"] != "resolved":
                continue
            definition = binding["function_definition"]
            function_item = self.item_at(
                definition["source_id"], definition["start_line"],
                (ast.FunctionDef, ast.AsyncFunctionDef),
            )
            loader_return = self.item_at("test", binding["return_line"], (ast.Return,))
            read_statement = self.item_at("test", binding["read_line"], (ast.stmt,))
            exec_statement = self.item_at("test", binding["exec_line"], (ast.stmt,))
            if function_item and loader_return:
                function_id = self.node(
                    function_item[0], function_item[1], "dynamic_function_definition"
                )
                if function_id:
                    self.nodes[function_id]["kind"] = "dynamic_function_definition"
                self.edge(
                    function_id,
                    self.node(loader_return[0], loader_return[1]),
                    "dynamic_function_origin",
                    precision="visible_path_namespace_key_and_definition",
                    logical_path=definition["logical_path"],
                    function=definition["name"],
                )
            if read_statement and exec_statement:
                self.edge(
                    self.node(read_statement[0], read_statement[1]),
                    self.node(exec_statement[0], exec_statement[1]),
                    "dynamic_source_text",
                    precision="visible_read_to_exec_compile",
                    logical_path=definition["logical_path"],
                )
            if exec_statement and loader_return:
                self.edge(
                    self.node(exec_statement[0], exec_statement[1]),
                    self.node(loader_return[0], loader_return[1]),
                    "dynamic_namespace_definition",
                    precision="visible_exec_namespace_to_literal_key",
                    namespace=binding["namespace"],
                    function=binding["function_key"],
                )
            for call_site in binding["call_sites"]:
                resolved_calls.add((call_site["line"], call_site["name"]))
                call_statement = self.item_at("test", call_site["line"], (ast.stmt,))
                if not call_statement or not function_item:
                    continue
                for returned in (node for node in scoped_nodes(function_item[1])
                                 if isinstance(node, ast.Return)):
                    self.edge(
                        self.node(function_item[0], returned),
                        self.node(call_statement[0], call_statement[1]),
                        "dynamic_call_target",
                        precision="visible_loader_binding",
                        callee=definition["name"],
                    )
        if not resolved_calls:
            return
        for node in self.nodes.values():
            unresolved = node.get("unresolved_calls")
            if not unresolved:
                continue
            if node["source_id"] != "test":
                continue
            names = {name for line, name in resolved_calls if line == node["start_line"]}
            node["unresolved_calls"] = [call for call in unresolved
                                        if not any(call.startswith(name + "(") for name in names)]
            if not node["unresolved_calls"]:
                node.pop("unresolved_calls")
                node.pop("contains_unresolved_call", None)

    def walk_block(self, source: dict, statements: list[ast.stmt]):
        for previous, current in zip(statements, statements[1:]):
            if not self.request.get("dependency_only", False):
                self.edge(self.node(source, previous), self.node(source, current), "normal_predecessor",
                          precision="syntactic_sequence_after_normal_completion")
        for statement in statements:
            if isinstance(statement, ast.If):
                condition = self.node(source, statement.test, "if_condition")
                for branch_name, body in (("true", statement.body), ("false", statement.orelse)):
                    for nested in body:
                        self.edge(condition, self.node(source, nested), "lexical_control",
                                  branch=branch_name, precision="ast_ancestry")
                    if body:
                        self.edge(self.node(source, body[-1]), self.node(source, statement),
                                  "normal_branch_exit", branch=branch_name,
                                  precision="possible_only_if_branch_completes_normally")
                    self.walk_block(source, body)
            elif isinstance(statement, (ast.With, ast.AsyncWith, ast.For, ast.AsyncFor,
                                        ast.While, ast.Try)):
                for field in ("body", "orelse", "finalbody"):
                    nested = getattr(statement, field, [])
                    if nested:
                        self.edge(self.node(source, nested[-1]), self.node(source, statement),
                                  "normal_body_exit", field=field,
                                  precision="possible_only_if_body_completes_normally")
                    self.walk_block(source, nested)

    def add_local_data(self, source: dict, function: ast.FunctionDef | ast.AsyncFunctionDef):
        writes: list[tuple[int, str, ast.AST]] = []
        for arg in (*function.args.posonlyargs, *function.args.args, *function.args.kwonlyargs):
            writes.append((arg.lineno, arg.arg, arg))
        ordered = sorted((n for n in scoped_nodes(function) if hasattr(n, "lineno")), key=lambda n: n.lineno)
        for item in ordered:
            if isinstance(item, ast.Name) and isinstance(item.ctx, ast.Store):
                writes.append((item.lineno, item.id, item))
                if self.request.get("dependency_only", False):
                    statement = self.enclosing_statement(item)
                    if statement is not None:
                        self.edge(self.node(source, statement), self.node(source, item),
                                  "definition_value_candidate", precision="assignment_syntax")
            elif isinstance(item, ast.NamedExpr) and isinstance(item.target, ast.Name):
                writes.append((item.lineno, item.target.id, item))
            if isinstance(item, ast.Name) and isinstance(item.ctx, ast.Load):
                use_stmt = self.enclosing_statement(item)
                if use_stmt is None:
                    continue
                for line, name, definition in writes:
                    if name == item.id and line <= item.lineno:
                        self.edge(self.node(source, definition, "name_definition"),
                                  self.node(source, use_stmt), "data_candidate",
                                  variable=name, precision="prior_lexical_write")

    def add_calls(self, source: dict, tree: ast.Module):
        for call in (n for n in ast.walk(tree) if isinstance(n, ast.Call)):
            statement = self.enclosing_statement(call)
            if statement is None:
                continue
            statement_id = self.node(source, statement)
            if statement_id is None:
                continue
            name = call.func.id if isinstance(call.func, ast.Name) else (
                call.func.attr if isinstance(call.func, ast.Attribute) else None)
            candidates = self.functions.get(name or "", [])
            if not candidates:
                marker = self.nodes[statement_id]
                marker["contains_unresolved_call"] = True
                marker.setdefault("unresolved_calls", []).append(compact(call))
            for target_source, function in candidates:
                if self.opaque(target_source, function):
                    marker = self.nodes[statement_id]
                    marker["contains_boundary_call"] = True
                    marker.setdefault("boundary_calls", []).append(compact(call))
                    continue
                returns = [n for n in scoped_nodes(function) if isinstance(n, ast.Return)]
                for returned in returns:
                    self.edge(self.node(target_source, returned), statement_id,
                              "call_target_candidate", callee=function.name,
                              precision="visible_name_candidate")
                events = [n for n in scoped_nodes(function)
                          if isinstance(n, ast.Raise)
                          or (isinstance(n, ast.Expr) and isinstance(n.value, ast.Call))]
                for event in events:
                    self.edge(self.node(target_source, event, "callee_event"), statement_id,
                              "call_event_candidate", callee=function.name,
                              precision="visible_callee_event_may_execute")
            if self.call_may_raise(call):
                self.nodes[statement_id]["exception_possible"] = True

    def resolve_seeds(self) -> list[str]:
        explicit = self.request.get("seeds") or []
        seeds: list[str] = []
        for seed in explicit:
            candidates = [n for n in self.nodes.values()
                          if n["source_id"] == seed["source_id"]
                          and n["start_line"] <= seed["line"] <= n["end_line"]
                          and (not seed.get("criterion_id") or n["id"] in self.statement_ids)]
            if not candidates:
                raise ValueError("seed line has no statement")
            if seed.get("kind") == "value_assertion":
                exact = [n for n in candidates if n["kind"] == "Assert" and n["start_line"] == seed["line"]]
                candidates = exact or candidates
            elif seed.get("kind") == "exception_observation":
                exact = [n for n in candidates if n["kind"] in {"With", "AsyncWith"}
                         and n["start_line"] == seed["line"]]
                candidates = exact or candidates
            span = min(n["end_line"] - n["start_line"] for n in candidates)
            closest = [n for n in candidates if n["end_line"] - n["start_line"] == span]
            if seed.get("criterion_id") and len(closest) > 1:
                # Do not silently choose between multiple same-line statements.
                statements = [n for n in closest if n["kind"] in {"Assert", "Expr", "Assign", "With", "AsyncWith"}]
                if len(statements) == 1:
                    closest = statements
                elif len(closest) > 1:
                    raise ValueError("ambiguous criterion line: multiple candidate statements")
            ident = closest[0]["id"]
            seeds.append(ident)
            self.seed_bindings.append({"criterion_id": seed.get("criterion_id"), "seed": ident})
        if seeds:
            return list(dict.fromkeys(seeds))
        for source, tree in self.trees:
            for item in ast.walk(tree):
                if isinstance(item, ast.Assert):
                    seeds.append(self.node(source, item))
                elif isinstance(item, (ast.With, ast.AsyncWith)) and any(
                    isinstance(c.context_expr, ast.Call)
                    and self.call_name(c.context_expr) == "raises" for c in item.items
                ):
                    seeds.append(self.node(source, item))
        if not seeds:
            raise ValueError("no explicit or automatic assertion/pytest.raises seed")
        return list(dict.fromkeys(seeds))

    def backward_closure(self, seeds: list[str]) -> set[str]:
        incoming: dict[str, list[str]] = {}
        for edge in self.edges:
            incoming.setdefault(edge["to"], []).append(edge["from"])
        found = set(seeds)
        queue = list(seeds)
        while queue:
            for prior in incoming.get(queue.pop(0), []):
                if prior not in found:
                    found.add(prior)
                    queue.append(prior)
        return found

    def enclosing_statement(self, node: ast.AST) -> ast.stmt | None:
        cursor = node
        while id(cursor) in self.parents:
            cursor = self.parents[id(cursor)]
            if isinstance(cursor, ast.stmt):
                return cursor
        return None

    @staticmethod
    def call_name(call: ast.Call) -> str | None:
        if isinstance(call.func, ast.Name):
            return call.func.id
        if isinstance(call.func, ast.Attribute):
            return call.func.attr
        return None

    @staticmethod
    def call_may_raise(call: ast.Call) -> bool:
        # Calls are exception-capable unless they are syntax-only constructs; this is
        # a conservative marker, not an assertion that an exception occurs.
        return True


def main() -> None:
    request = json.load(sys.stdin)
    json.dump(Probe(request).run(), sys.stdout, ensure_ascii=False, sort_keys=True)


if __name__ == "__main__":
    main()
