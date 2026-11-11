"""Generate CodeQL root facts from authenticated observation records and visible source."""
from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path


def _quoted(value: str) -> str:
    return json.dumps(value)


def _location_terms(path: str, rows: list[tuple[str, int]], node: str) -> str:
    return "\n    or ".join(
        f"{node}.getLocation().getFile().getRelativePath() = {_quoted(path)} and "
        f"{node}.getLocation().getStartLine() = {line}"
        for _, line in rows
    ) or "none()"


def discover_test_call(source: Path, context_line: int) -> int:
    tree = ast.parse(source.read_text(encoding="utf-8"))
    contexts = [node for node in ast.walk(tree) if isinstance(node, ast.With)
                and node.lineno == context_line]
    if len(contexts) != 1:
        raise ValueError(f"registered context at {context_line} is not unique")
    calls = [node for statement in contexts[0].body for node in ast.walk(statement)
             if isinstance(node, ast.Call)]
    lines = sorted({node.lineno for node in calls})
    if len(lines) != 1:
        raise ValueError(f"registered context has ambiguous calls: {lines}")
    return lines[0]


def discover_state_roots(source: Path, expectations: dict) -> list[tuple[str, int]]:
    roots = [(item["function"], int(item["line"])) for item in expectations["observations"]]
    tree = ast.parse(source.read_text(encoding="utf-8"))
    functions = {node.name: node for node in tree.body if isinstance(node, ast.FunctionDef)}
    for item in expectations.get("unknown_cases", []):
        function = functions[item["function"]]
        reads = [node for node in ast.walk(function)
                 if isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Load)]
        return_reads = [node for statement in ast.walk(function)
                        if isinstance(statement, ast.Return) and statement.value is not None
                        for node in ast.walk(statement.value) if isinstance(node, ast.Attribute)]
        candidates = return_reads or reads
        lines = sorted({node.lineno for node in candidates})
        if len(lines) != 1:
            raise ValueError(f"unknown observation {item['function']} has ambiguous reads: {lines}")
        roots.append((item["function"], lines[0]))
    return sorted(set(roots))


def generate(*, flask_source_root: Path, state_source: Path, recursive_source: Path,
             observation_expectations: dict, state_expectations: dict,
             recursive_expectations: dict,
             reached_product: tuple[str, int] | None = None) -> tuple[str, dict]:
    test_path = observation_expectations["observations"][0]["logical_path"]
    observation_rows = [(item["observation_id"], int(item["line"]))
                        for item in observation_expectations["observations"]]
    context_line = next(item[1] for item in observation_rows if item[0].startswith("raises-context:"))
    test_call_line = discover_test_call(flask_source_root / test_path, context_line)
    guard_lines = sorted({int(item["guard_line"]) for item in observation_expectations["observations"]
                          if "guard_line" in item})
    state_roots = discover_state_roots(state_source, state_expectations)
    recursive_tree = ast.parse(recursive_source.read_text(encoding="utf-8"))
    recursive_functions = {node.name: node for node in recursive_tree.body
                           if isinstance(node, ast.FunctionDef)}
    recursive_observation = recursive_expectations["observation"]
    root_name = recursive_observation["function"]
    if root_name not in recursive_functions:
        raise ValueError(f"recursive observation function is absent: {root_name}")
    graph = {name: {call.func.id for call in ast.walk(function)
                    if isinstance(call, ast.Call) and isinstance(call.func, ast.Name)
                    and call.func.id in recursive_functions}
             for name, function in recursive_functions.items()}
    reached = {root_name}
    frontier = [root_name]
    while frontier:
        current = frontier.pop()
        for target in graph[current] - reached:
            reached.add(target)
            frontier.append(target)
    recursive_names = sorted(reached)
    product_term = "none()" if reached_product is None else (
        f"function.getLocation().getFile().getRelativePath() = {_quoted(reached_product[0])} and "
        f"function.getLocation().getStartLine() = {reached_product[1]}"
    )
    state_term = "\n    or ".join(
        f"function.getName() = {_quoted(name)} and node.getLocation().getStartLine() = {line}"
        for name, line in state_roots
    ) or "none()"
    recursive_term = " or ".join(
        f"function.getName() = {_quoted(name)}" for name in recursive_names
    ) or "none()"
    text = f'''import python

module DemandRoots {{
  predicate selectedTestPath(string path) {{ path = {_quoted(test_path)} }}
  predicate selectedObservation(AstNode node) {{
    {_location_terms(test_path, observation_rows, "node")}
  }}
  predicate selectedGuard(If guard) {{
    guard.getLocation().getFile().getRelativePath() = {_quoted(test_path)} and
    guard.getLocation().getStartLine() in [{", ".join(map(str, guard_lines))}]
  }}
  predicate selectedTestCall(Call call) {{
    call.getLocation().getFile().getRelativePath() = {_quoted(test_path)} and
    call.getLocation().getStartLine() = {test_call_line}
  }}
  predicate selectedProductFunction(Function function) {{ {product_term} }}
  predicate selectedStateObservation(Function function, AstNode node) {{
    function.getLocation().getFile().getRelativePath() = {_quoted(state_source.name)} and
    ({state_term})
  }}
  predicate selectedRecursiveFunction(Function function) {{
    function.getLocation().getFile().getRelativePath() = {_quoted(recursive_source.name)} and
    ({recursive_term})
  }}
  predicate selectedRecursiveObservation(Function function, int line) {{
    function.getLocation().getFile().getRelativePath() = {_quoted(recursive_source.name)} and
    function.getName() = {_quoted(root_name)} and line = {int(recursive_observation['line'])}
  }}
}}
'''
    record = {
        "test_path": test_path, "observation_locations": observation_rows,
        "test_call_line": test_call_line, "guard_lines": guard_lines,
        "state_observations": state_roots, "recursive_functions": recursive_names,
        "recursive_observation": recursive_observation,
        "reached_product": reached_product,
        "module_sha256": hashlib.sha256(text.encode()).hexdigest(),
    }
    return text, record
