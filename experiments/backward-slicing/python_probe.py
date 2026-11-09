"""Bounded Python source-level backward evidence graph research prototype."""
from __future__ import annotations

import ast
import json
import sys


def compact(node: ast.AST) -> str:
    try:
        return ast.unparse(node)[:300]
    except Exception:
        return type(node).__name__


class Probe:
    def __init__(self, request: dict):
        self.request = request
        self.nodes: dict[str, dict] = {}
        self.edges: list[dict] = []
        self.edge_keys: set[tuple] = set()
        self.trees: list[tuple[dict, ast.Module]] = []
        self.functions: dict[str, list[tuple[dict, ast.FunctionDef | ast.AsyncFunctionDef]]] = {}
        self.parents: dict[int, ast.AST] = {}

    def node(self, source: dict, item: ast.AST, kind: str | None = None) -> str | None:
        if not hasattr(item, "lineno"):
            return None
        start = source.get("start_line", 1)
        line = start + item.lineno - 1
        end = start + getattr(item, "end_lineno", item.lineno) - 1
        ident = f"{source['source_id']}:{line}:{getattr(item, 'col_offset', 0)}:{type(item).__name__}"
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
                    self.node(source, item)
        for source, tree in self.trees:
            self.walk_block(source, tree.body)
            for function in [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]:
                self.walk_block(source, function.body)
                self.add_local_data(source, function)
            self.add_calls(source, tree)
        seeds = self.resolve_seeds()
        closure = self.backward_closure(seeds)
        return {
            "status": "ok", "backend": "python-ast-source-evidence-prototype",
            "seeds": seeds,
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
            ],
        }

    def walk_block(self, source: dict, statements: list[ast.stmt]):
        for previous, current in zip(statements, statements[1:]):
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
        ordered = sorted((n for n in ast.walk(function) if hasattr(n, "lineno")), key=lambda n: n.lineno)
        for item in ordered:
            if isinstance(item, ast.Name) and isinstance(item.ctx, ast.Store):
                writes.append((item.lineno, item.id, item))
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
            name = call.func.id if isinstance(call.func, ast.Name) else (
                call.func.attr if isinstance(call.func, ast.Attribute) else None)
            candidates = self.functions.get(name or "", [])
            if not candidates:
                marker = self.nodes[statement_id]
                marker["contains_unresolved_call"] = True
                marker.setdefault("unresolved_calls", []).append(compact(call))
            for target_source, function in candidates:
                returns = [n for n in ast.walk(function) if isinstance(n, ast.Return)]
                for returned in returns:
                    self.edge(self.node(target_source, returned), statement_id,
                              "call_target_candidate", callee=function.name,
                              precision="visible_name_candidate")
                events = [n for n in ast.walk(function)
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
                          and n["start_line"] <= seed["line"] <= n["end_line"]]
            if not candidates:
                raise ValueError("seed line has no statement")
            seeds.append(min(candidates, key=lambda n: n["end_line"] - n["start_line"])["id"])
        if seeds:
            return seeds
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
