"""Project a backward-analysis result as objective navigation facts.

The projection does not recommend test actions.  In particular, a call binding
is structural evidence only: it never establishes that a return value or an
exception reaches an assertion.  Reachability is reported only from an
explicit flow/dependence relation, with its original modality intact.

The small source parsers below are deliberately bounded.  Python calls and
guards come from the standard-library AST.  Java call locations are matched
against method names already reported by the analysis; this is lexical source
mapping, not Java name resolution.  Every derived item states that precision.
"""

from __future__ import annotations

import ast
import hashlib
import json
import re
from typing import Any

from .backward_result import validate_result_contract


SCHEMA_VERSION = "mock-spec.navigation-facts/v2"
FLOW_EFFECT = {"value_flow": "return", "exception_dependence": "exception"}
OTHER_FLOW_KINDS = frozenset(
    {
        "state_flow",
        "control_dependence",
        "normal_return_dependence",
    }
)

__all__ = ["SCHEMA_VERSION", "project_navigation_facts"]


def _encoded(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode()


def _digest(value: Any) -> str:
    return hashlib.sha256(_encoded(value)).hexdigest()


def _contract(case_result: dict[str, Any]) -> dict[str, Any]:
    contract = case_result.get("result_contract", case_result)
    validate_result_contract(contract)
    return contract


def _sources(visible_sources: Any) -> list[dict[str, Any]]:
    if isinstance(visible_sources, dict) and "sources" in visible_sources:
        visible_sources = visible_sources["sources"]
    if isinstance(visible_sources, dict):
        visible_sources = [
            (
                {"source_id": key, "logical_path": key, "start_line": 1, "text": value}
                if isinstance(value, str)
                else {"source_id": key, **value}
            )
            for key, value in visible_sources.items()
        ]
    if not isinstance(visible_sources, list):
        raise ValueError(
            "visible_sources must be an array, source map, or sources object"
        )
    records = []
    for source in visible_sources:
        if not isinstance(source, dict):
            raise ValueError("each visible source must be an object")
        path = source.get("logical_path")
        text = source.get("text")
        start = source.get("start_line", 1)
        if not isinstance(path, str) or not path:
            raise ValueError("each visible source requires logical_path")
        if text is not None and not isinstance(text, str):
            raise ValueError("visible source text must be a string")
        if type(start) is not int or start < 1:
            raise ValueError("visible source start_line must be a positive integer")
        records.append({**source, "logical_path": path, "start_line": start})
    return sorted(records, key=lambda row: (row["logical_path"], row["start_line"]))


def _relation_fact(relation: dict[str, Any]) -> dict[str, Any]:
    fact_id = "r:" + _digest(relation)[:16]
    return {
        "fact_id": fact_id,
        **{
            key: relation[key]
            for key in (
                "kind",
                "modality",
                "from",
                "to",
                "precision",
                "provenance",
                "assumptions",
            )
        },
        "observation_ids": relation.get("observation_ids", []),
    }


def _fact_tables(facts: list[dict[str, Any]]) -> tuple[list, list, list]:
    """Deduplicate verbose relation endpoints and source descriptions."""
    endpoints, sources, rows = {}, {}, []
    for fact in facts:
        endpoint_refs = []
        for endpoint in (fact["from"], fact["to"]):
            reference = "e:" + _digest(endpoint)[:16]
            endpoints[reference] = endpoint
            endpoint_refs.append(reference)
        source = {
            "precision": fact["precision"],
            "provenance": fact["provenance"],
            "assumptions": fact["assumptions"],
        }
        source_ref = "s:" + _digest(source)[:16]
        sources[source_ref] = source
        rows.append(
            {
                "fact_id": fact["fact_id"],
                "kind": fact["kind"],
                "modality": fact["modality"],
                "from_ref": endpoint_refs[0],
                "to_ref": endpoint_refs[1],
                "observation_ids": fact["observation_ids"],
                "source_ref": source_ref,
            }
        )
    return (
        rows,
        [{"endpoint_id": key, "value": endpoints[key]} for key in sorted(endpoints)],
        [{"source_id": key, **sources[key]} for key in sorted(sources)],
    )


def _location(endpoint: Any) -> dict[str, Any] | None:
    if not isinstance(endpoint, dict):
        return None
    path, start = endpoint.get("logical_path"), endpoint.get("start_line")
    if not isinstance(path, str) or type(start) is not int:
        return None
    return {
        "logical_path": path,
        "start_line": start,
        "end_line": endpoint.get("end_line", start),
    }


def _observation_cards(contract: dict[str, Any]) -> list[dict[str, Any]]:
    paths = {
        source["source_id"]: source["logical_path"]
        for source in contract["source_scope"]
    }
    cards = []
    for observation in contract["observations"]:
        location = observation["location"]
        cards.append(
            {
                "observation_id": observation["observation_id"],
                "kind": observation["kind"],
                "origin": observation["origin"],
                "reason": observation.get("reason"),
                "location": {
                    "logical_path": paths[location["source_id"]],
                    "start_line": location["start_line"],
                    "end_line": location["end_line"],
                },
                "provenance": observation.get("provenance"),
            }
        )
    return cards


def _method_name(signature: str) -> str | None:
    before = signature.split("(", 1)[0]
    match = re.search(r"([A-Za-z_$][\w$]*)$", before)
    return match.group(1) if match else None


def _java_declared_class(signature: str) -> str | None:
    before = signature.split("(", 1)[0]
    return before.rsplit(".", 1)[0] if "." in before else None


def _java_visible_classes(sources: list[dict[str, Any]]) -> set[str]:
    classes = set()
    for source in sources:
        text = source.get("text")
        if not isinstance(text, str) or not source["logical_path"].endswith(".java"):
            continue
        package = re.search(r"(?m)^\s*package\s+([\w.]+)\s*;", text)
        for declaration in re.finditer(
            r"(?m)^\s*(?:public\s+)?(?:final\s+|abstract\s+)?(?:class|interface|enum)\s+([\w$]+)",
            text,
        ):
            classes.add(
                (package.group(1) + "." if package else "") + declaration.group(1)
            )
    return classes


def _java_source_locations(
    target: str, mut_sources: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    name = _method_name(target)
    if not name:
        return []
    pattern = re.compile(r"\b" + re.escape(name) + r"\s*\(")
    locations = []
    for source in mut_sources:
        text = source.get("text")
        if not isinstance(text, str):
            continue
        for offset, line in enumerate(text.splitlines()):
            if pattern.search(line):
                number = source["start_line"] + offset
                locations.append(
                    {
                        "logical_path": source["logical_path"],
                        "start_line": number,
                        "end_line": number,
                        "source_text": line.strip(),
                    }
                )
    return locations


def _mut_candidates(contract: dict[str, Any]) -> list[str]:
    return [
        item
        for item in contract["entry_point"]["mut_binding"].get("candidates", [])
        if isinstance(item, str)
    ]


def _java_parameter_types(candidates: list[str]) -> set[str]:
    types: set[str] = set()
    for candidate in candidates:
        match = re.search(r"\((.*)\)$", candidate)
        if match:
            types.update(
                item.strip() for item in match.group(1).split(",") if item.strip()
            )
    return types


def _same_java_method(method: str, candidate: str) -> bool:
    return method.split("(", 1)[0] == candidate.split("(", 1)[0]


def _effect_rows(
    fact_refs: list[dict[str, Any]], *, discarded_return: bool = False
) -> list[dict[str, Any]]:
    rows = []
    for effect in ("return", "exception"):
        related = [
            fact for fact in fact_refs if FLOW_EFFECT.get(fact["kind"]) == effect
        ]
        supported = [fact for fact in related if fact["observation_ids"]]
        rows.append(
            {
                "effect": effect,
                # Absence of an explicit path is unknown, not a negative fact.
                "reaches_assertion": (
                    True
                    if supported
                    else False
                    if effect == "return" and discarded_return
                    else None
                ),
                "observation_ids": sorted(
                    {
                        observation
                        for fact in supported
                        for observation in fact["observation_ids"]
                    }
                ),
                "evidence": [
                    {
                        "fact_ref": fact["fact_id"],
                        "kind": fact["kind"],
                        "modality": fact["modality"],
                        "path_quantifier": (
                            "may"
                            if fact["modality"] == "analysis_may"
                            else "not_encoded"
                        ),
                    }
                    for fact in related
                ],
            }
        )
    return rows


def _other_flows(fact_refs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep non-value relations visible without recasting them as return flow."""
    return [
        {
            "fact_ref": fact["fact_id"],
            "kind": fact["kind"],
            "modality": fact["modality"],
            "path_quantifier": (
                "may" if fact["modality"] == "analysis_may" else "not_encoded"
            ),
            "observation_ids": fact["observation_ids"],
        }
        for fact in fact_refs
        if fact["kind"] in OTHER_FLOW_KINDS
    ]


def _source_symbol(location: dict[str, Any], sources: list[dict[str, Any]]) -> str:
    start_line = location.get("start_line")
    if type(start_line) is not int:
        symbol = location.get("symbol")
        return symbol if isinstance(symbol, str) else location["logical_path"]
    for source in sources:
        if source["logical_path"] != location["logical_path"] or not isinstance(
            source.get("text"), str
        ):
            continue
        line = start_line - source["start_line"] + 1
        if source["logical_path"].endswith(".py"):
            try:
                tree = ast.parse(source["text"])
            except SyntaxError:
                break
            functions = [
                node
                for node in ast.walk(tree)
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and _contains_line(node, line)
            ]
            if functions:
                return min(functions, key=_node_line_span).name
    return f"{location['logical_path']}:{start_line}"


def _entry_calls(
    contract: dict[str, Any], sources: list[dict[str, Any]], facts: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    calls = []
    binding_status = contract["entry_point"]["mut_binding"]["status"]
    source_paths = {
        row["source_id"]: row["logical_path"] for row in contract["source_scope"]
    }
    test_path = next(
        (
            row["logical_path"]
            for row in contract["source_scope"]
            if row["selection_role"] == "masked_test"
        ),
        None,
    )
    for fact in facts:
        if fact["kind"] != "call_binding":
            continue
        location = _location(fact["from"])
        if location is None and isinstance(fact["from"], str):
            match = re.fullmatch(r"([^:]+):(\d+):\d+:.+", fact["from"])
            if match and match.group(1) in source_paths:
                location = {
                    "logical_path": source_paths[match.group(1)],
                    "start_line": int(match.group(2)),
                    "end_line": int(match.group(2)),
                }
        if (
            location is None
            or test_path is None
            or location["logical_path"] != test_path
        ):
            continue
        calls.append(
            {
                "call_id": "c:" + _digest(["entry", fact["fact_id"]])[:16],
                "structural_role": "entry_call",
                "display": (
                    fact["to"]
                    if isinstance(fact["to"], str)
                    else _source_symbol(fact["to"], sources)
                ),
                "location": location,
                "boundary_resolution": f"{binding_status}_mut_binding",
                "source_precision": fact["precision"],
                "guards": [],
                "effects": _effect_rows([fact]),
                "other_flows": _other_flows([fact]),
                "fact_refs": [fact["fact_id"]],
            }
        )
    return calls


def _java_calls(
    contract: dict[str, Any], sources: list[dict[str, Any]], facts: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    candidates = _mut_candidates(contract)
    if not candidates:
        return []
    product_paths = {
        row["logical_path"]
        for row in contract["source_scope"]
        if row["selection_role"] != "masked_test"
    }
    mut_sources = [
        source for source in sources if source["logical_path"] in product_paths
    ]
    visible_classes = _java_visible_classes(mut_sources)
    parameter_types = _java_parameter_types(candidates)
    found: dict[tuple[str, str, int | None], list[tuple[dict[str, Any], str]]] = {}
    for fact in facts:
        for endpoint_name in ("from", "to"):
            endpoint = fact[endpoint_name]
            if not isinstance(endpoint, dict):
                continue
            enclosing = endpoint.get("method")
            target = endpoint.get("declared_target")
            if not isinstance(enclosing, str) or not isinstance(target, str):
                continue
            if not any(
                _same_java_method(enclosing, candidate) for candidate in candidates
            ):
                continue
            key = (
                target,
                endpoint.get("call_site", target),
                endpoint.get("instruction_index"),
            )
            found.setdefault(key, []).append((fact, endpoint_name))

    calls = []
    for (target, call_site, instruction), bindings in found.items():
        target_class = _java_declared_class(target)
        related = [fact for fact, _ in bindings]
        outgoing = [fact for fact, side in bindings if side == "from"]
        internal = target_class in visible_classes
        parameter_boundary = target_class in parameter_types
        locations = _java_source_locations(target, mut_sources)
        calls.append(
            {
                "call_id": "c:" + _digest([target, call_site, instruction])[:16],
                "structural_role": (
                    "internal_call"
                    if internal
                    else "outbound_call"
                    if parameter_boundary
                    else "unresolved_call"
                ),
                "display": target.split("(", 1)[0],
                "location": locations[0] if len(locations) == 1 else None,
                "candidate_locations": locations if len(locations) != 1 else [],
                "boundary_resolution": (
                    "visible_product_declaration"
                    if internal
                    else "mut_parameter_declared_target"
                    if parameter_boundary
                    else "declared_target_not_in_visible_product_declarations"
                ),
                "source_precision": (
                    "analysis callsite plus unique lexical source match"
                    if len(locations) == 1
                    else "analysis callsite; lexical source match is non-unique or unavailable"
                ),
                "guards": [],
                "effects": _effect_rows(outgoing),
                "other_flows": _other_flows(outgoing),
                "fact_refs": sorted({fact["fact_id"] for fact in related}),
            }
        )
    return calls


def _call_name(node: ast.Call) -> str:
    try:
        return ast.unparse(node.func)
    except ValueError:
        return "<unparseable call>"


def _python_scope_nodes(
    statements: list[ast.stmt],
) -> tuple[list[ast.AST], dict[ast.AST, ast.AST]]:
    """Walk executable scope nodes without entering nested lexical bodies.

    Decorators, defaults, and class bases remain included because Python
    evaluates them when it executes the nested declaration statement. Type
    annotations are omitted because postponed evaluation is source-dependent.
    """
    nodes: list[ast.AST] = []
    parents: dict[ast.AST, ast.AST] = {}

    class ScopeVisitor(ast.NodeVisitor):
        def __init__(self) -> None:
            self.stack: list[ast.AST] = []

        def visit(self, node: ast.AST) -> Any:
            nodes.append(node)
            if self.stack:
                parents[node] = self.stack[-1]
            self.stack.append(node)
            try:
                return super().visit(node)
            finally:
                self.stack.pop()

        def _visit_function_header(
            self, node: ast.FunctionDef | ast.AsyncFunctionDef
        ) -> None:
            for decorator in node.decorator_list:
                self.visit(decorator)
            for default in [
                *node.args.defaults,
                *(item for item in node.args.kw_defaults if item is not None),
            ]:
                self.visit(default)

        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            self._visit_function_header(node)

        def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
            self._visit_function_header(node)

        def visit_ClassDef(self, node: ast.ClassDef) -> None:
            for expression in [*node.decorator_list, *node.bases]:
                self.visit(expression)
            for keyword in node.keywords:
                self.visit(keyword)

        def visit_Lambda(self, node: ast.Lambda) -> None:
            return

    visitor = ScopeVisitor()
    for statement in statements:
        visitor.visit(statement)
    return nodes, parents


def _python_scope_imports(statements: list[ast.stmt]) -> dict[str, tuple[str, str]]:
    imports: dict[str, tuple[str, str]] = {}
    nodes, _ = _python_scope_nodes(statements)
    for node in nodes:
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports[alias.asname or alias.name.split(".")[0]] = (
                    "module",
                    alias.name,
                )
        elif isinstance(node, ast.ImportFrom):
            prefix = "." * node.level + (node.module or "")
            for alias in node.names:
                imports[alias.asname or alias.name] = (
                    "from",
                    prefix + "." + alias.name,
                )
    return imports


def _bound_names(target: ast.AST) -> set[str]:
    if isinstance(target, ast.Name):
        return {target.id}
    if isinstance(target, (ast.Tuple, ast.List)):
        return {name for item in target.elts for name in _bound_names(item)}
    if isinstance(target, ast.Starred):
        return _bound_names(target.value)
    return set()


def _python_local_bindings(
    function: ast.FunctionDef | ast.AsyncFunctionDef,
) -> set[str]:
    arguments = [
        *function.args.posonlyargs,
        *function.args.args,
        *function.args.kwonlyargs,
    ]
    names = {argument.arg for argument in arguments}
    if function.args.vararg is not None:
        names.add(function.args.vararg.arg)
    if function.args.kwarg is not None:
        names.add(function.args.kwarg.arg)
    nodes, _ = _python_scope_nodes(function.body)
    global_names: set[str] = set()
    for node in nodes:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Global):
            global_names.update(node.names)
        elif isinstance(node, ast.Nonlocal):
            names.update(node.names)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                names.update(_bound_names(target))
        elif isinstance(node, (ast.AnnAssign, ast.AugAssign, ast.NamedExpr)):
            names.update(_bound_names(node.target))
        elif isinstance(node, (ast.For, ast.AsyncFor)):
            names.update(_bound_names(node.target))
        elif isinstance(node, (ast.With, ast.AsyncWith)):
            for item in node.items:
                if item.optional_vars is not None:
                    names.update(_bound_names(item.optional_vars))
        elif isinstance(node, ast.ExceptHandler) and node.name is not None:
            names.add(node.name)
        elif isinstance(node, ast.Delete):
            for target in node.targets:
                names.update(_bound_names(target))
        elif isinstance(node, (ast.MatchAs, ast.MatchStar)) and node.name is not None:
            names.add(node.name)
        elif isinstance(node, ast.MatchMapping) and node.rest is not None:
            names.add(node.rest)
    return names - global_names


def _python_imports(
    tree: ast.Module, function: ast.FunctionDef | ast.AsyncFunctionDef
) -> dict[str, tuple[str, str]]:
    """Resolve imports visible in the target without crossing lexical scopes."""
    imports = _python_scope_imports(tree.body)
    local_imports = _python_scope_imports(function.body)
    local_bindings = _python_local_bindings(function)
    for shadowed in local_bindings:
        imports.pop(shadowed, None)
    imports.update(
        (name, imported)
        for name, imported in local_imports.items()
        if name not in local_bindings
    )
    return imports


def _python_project_modules(paths: set[str]) -> tuple[set[str], set[str]]:
    """Return visible module names and package roots derivable from source paths."""
    modules: set[str] = set()
    package_roots: set[str] = set()
    for path in paths:
        if not path.endswith(".py"):
            continue
        parts = path[:-3].split("/")
        if parts[-1] == "__init__":
            parts = parts[:-1]
        if not parts:
            continue
        start = len(parts) - 1 - parts[::-1].index("src") + 1 if "src" in parts else 0
        project_parts = parts[start:]
        if not project_parts:
            continue
        modules.update(
            ".".join(project_parts[index:]) for index in range(len(project_parts))
        )
        package_roots.update(project_parts[:-1])
    return modules, package_roots


def _contains_line(node: ast.AST, line: int) -> bool:
    return getattr(node, "lineno", 10**9) <= line <= getattr(node, "end_lineno", -1)


def _node_line_span(node: ast.AST) -> int:
    """Return a sortable source-line span for nodes with optional end positions."""
    start = getattr(node, "lineno", 0)
    end = getattr(node, "end_lineno", None)
    return (end if isinstance(end, int) else start) - start


def _python_visible_definitions(
    tree: ast.Module, function: ast.FunctionDef | ast.AsyncFunctionDef
) -> set[str]:
    """Return declarations lexically visible by name in the selected function."""
    module_names = {
        node.name
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    }
    local_names: set[str] = set()

    class LocalDefinitionVisitor(ast.NodeVisitor):
        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            local_names.add(node.name)

        def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
            local_names.add(node.name)

        def visit_ClassDef(self, node: ast.ClassDef) -> None:
            local_names.add(node.name)

        def visit_Lambda(self, node: ast.Lambda) -> None:
            return

    visitor = LocalDefinitionVisitor()
    for statement in function.body:
        visitor.visit(statement)
    shadowed = _python_local_bindings(function) | _python_scope_imports(
        function.body
    ).keys()
    return (module_names - shadowed) | local_names


def _python_guard(
    node: ast.Call, scope_nodes: list[ast.AST], path: str, offset: int
) -> list[dict[str, Any]]:
    guards = []
    for parent in scope_nodes:
        if not isinstance(parent, ast.If) or not _contains_line(parent, node.lineno):
            continue
        in_body = any(_contains_line(child, node.lineno) for child in parent.body)
        in_else = any(_contains_line(child, node.lineno) for child in parent.orelse)
        if not (in_body or in_else):
            continue
        expression = ast.unparse(parent.test)
        guards.append(
            {
                "expression": expression if in_body else f"not ({expression})",
                "location": {
                    "logical_path": path,
                    "start_line": offset + parent.test.lineno - 1,
                    "end_line": offset
                    + (parent.test.end_lineno or parent.test.lineno)
                    - 1,
                },
                "modality": "certain",
                "provenance": "Python AST lexical nesting",
                "precision": "syntactic path guard; runtime feasibility not established",
            }
        )
    return guards


def _python_calls(
    contract: dict[str, Any], sources: list[dict[str, Any]], facts: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    product_paths = {
        row["logical_path"]
        for row in contract["source_scope"]
        if row["selection_role"] != "masked_test"
    }
    visible_modules, package_roots = _python_project_modules(product_paths)
    entry_targets = [
        location
        for fact in facts
        if fact["kind"] == "call_binding"
        and (location := _location(fact["to"])) is not None
        and location["logical_path"] in product_paths
    ]
    calls = []
    for source in sources:
        text = source.get("text")
        path, offset = source["logical_path"], source["start_line"]
        if (
            path not in product_paths
            or not isinstance(text, str)
            or not path.endswith(".py")
        ):
            continue
        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue
        target_lines = [
            location["start_line"] - offset + 1
            for location in entry_targets
            if location["logical_path"] == path
        ]
        functions = [
            node
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and any(_contains_line(node, line) for line in target_lines)
        ]
        if not functions:
            continue
        function = min(functions, key=_node_line_span)
        local_defs = _python_visible_definitions(tree, function)
        imports = _python_imports(tree, function)
        scope_nodes, parents = _python_scope_nodes(function.body)
        call_nodes = [node for node in scope_nodes if isinstance(node, ast.Call)]
        calls_per_line: dict[int, int] = {}
        for item in call_nodes:
            calls_per_line[item.lineno] = calls_per_line.get(item.lineno, 0) + 1
        for node in call_nodes:
            name = _call_name(node)
            root_name = name.split(".", 1)[0]
            if isinstance(node.func, ast.Name) and name in local_defs:
                role, resolution = "internal_call", "visible_product_declaration"
            elif isinstance(node.func, ast.Name) and root_name in imports:
                import_form, imported = imports[root_name]
                if imported.startswith("."):
                    role, resolution = (
                        "unresolved_call",
                        "relative_import_target_not_resolved",
                    )
                elif (
                    import_form == "module" and imported in visible_modules
                ) or (
                    import_form == "from"
                    and imported.rsplit(".", 1)[0] in visible_modules
                ):
                    role, resolution = (
                        "internal_call",
                        f"visible_product_import:{imported}",
                    )
                elif imported.split(".", 1)[0] in package_roots:
                    role, resolution = (
                        "unresolved_call",
                        f"same_project_absolute_import_not_visible:{imported}",
                    )
                else:
                    role, resolution = "outbound_call", f"external_import:{imported}"
            else:
                role, resolution = (
                    "unresolved_call",
                    "dynamic_receiver_or_unresolved_name",
                )
            absolute_start = offset + node.lineno - 1
            absolute_end = offset + node.end_lineno - 1
            related = [
                fact
                for fact in facts
                if any(
                    (location := _location(fact[side])) is not None
                    and location["logical_path"] == path
                    and absolute_start <= location["start_line"] <= absolute_end
                    for side in ("from", "to")
                )
            ]
            outgoing = [
                fact
                for fact in related
                if calls_per_line[node.lineno] == 1
                and (location := _location(fact["from"])) is not None
                and location["logical_path"] == path
                and absolute_start <= location["start_line"] <= absolute_end
            ]
            calls.append(
                {
                    "call_id": "c:"
                    + _digest(
                        [
                            path,
                            absolute_start,
                            node.col_offset,
                            absolute_end,
                            node.end_col_offset,
                            name,
                        ]
                    )[:16],
                    "structural_role": role,
                    "display": name,
                    "location": {
                        "logical_path": path,
                        "start_line": absolute_start,
                        "end_line": absolute_end,
                        "start_column": node.col_offset,
                        "end_column": node.end_col_offset,
                        "source_text": ast.get_source_segment(text, node),
                    },
                    "boundary_resolution": resolution,
                    "source_precision": "exact Python AST call expression",
                    "guards": _python_guard(node, scope_nodes, path, offset),
                    "effects": _effect_rows(
                        outgoing,
                        discarded_return=isinstance(parents.get(node), ast.Expr),
                    ),
                    "other_flows": _other_flows(outgoing),
                    "fact_refs": sorted({fact["fact_id"] for fact in related}),
                }
            )
    return calls


def _guard_facts(facts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "guard_id": "g:" + fact["fact_id"][2:],
            "from": fact["from"],
            "to": fact["to"],
            "modality": fact["modality"],
            "precision": fact["precision"],
            "provenance": fact["provenance"],
            "assumptions": fact["assumptions"],
            "observation_ids": fact["observation_ids"],
            "fact_ref": fact["fact_id"],
        }
        for fact in facts
        if fact["kind"] == "control_dependence"
    ]


def _static_boundaries(boundaries: dict[str, Any]) -> dict[str, Any]:
    """Exclude host/evaluator compatibility notes from the program fact package."""
    regions = []
    for region in boundaries["unanalysed_regions"]:
        if (
            region.get("scope")
            == "frozen-case pytest version compatibility with the host-verified reference"
            and region.get("reason") == "outside bounded model"
        ):
            continue
        regions.append(region)
    return {
        "assumptions": boundaries["assumptions"],
        "unknown_effects": boundaries["unknown_effects"],
        "unanalysed_regions": regions,
    }


def project_navigation_facts(
    case_result: dict[str, Any], visible_sources: Any
) -> dict[str, Any]:
    """Return a deterministic v2 fact map without test-construction advice."""
    contract = _contract(case_result)
    sources = _sources(visible_sources)
    facts = sorted(
        (_relation_fact(relation) for relation in contract["relations"]),
        key=lambda fact: fact["fact_id"],
    )
    calls = _entry_calls(contract, sources, facts)
    if contract["identity"]["language"] == "java":
        calls.extend(_java_calls(contract, sources, facts))
    elif contract["identity"]["language"] == "python":
        calls.extend(_python_calls(contract, sources, facts))
    calls.sort(key=lambda call: call["call_id"])
    relation_rows, endpoints, evidence_sources = _fact_tables(facts)
    return {
        "schema_version": SCHEMA_VERSION,
        "identity": contract["identity"],
        "entry_binding": contract["entry_point"]["mut_binding"],
        "observations": _observation_cards(contract),
        "calls": calls,
        "guards": _guard_facts(facts),
        "relations": relation_rows,
        "endpoints": endpoints,
        "evidence_sources": evidence_sources,
        "analysis_boundaries": _static_boundaries(contract["analysis_boundaries"]),
        "projection": {
            "input_contract_sha256": _digest(contract),
            "visible_sources_sha256": _digest(sources),
            "input_relation_count": len(contract["relations"]),
            "call_count": len(calls),
            "reachability_rule": (
                "true only for an explicit observation-linked value/exception relation; "
                "false only for a syntactically discarded return; otherwise null"
            ),
            "source_mapping_precision": {
                "python": "standard-library AST",
                "java": "analysis-declared target plus lexical method-name mapping",
            },
        },
    }
