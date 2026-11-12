"""Run the integrated observation-effect query against a static synthetic source fixture."""

from __future__ import annotations

import ast
import csv
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time

from opaque_facts import build_parameter_boundary_facts


HERE = Path(__file__).resolve().parent
QUERY = HERE / "observation-effects.ql"
FACT_BUILDER = HERE / "opaque_facts.py"
PROJECTION = HERE / "effect_projection.py"
RUNNER = Path(__file__).resolve()

SOURCE = """import pytest
from loader_support import support_fill
from product import Factory, Writer, always_throw, async_fill, unreachable_fill, visible_fill

class Box:
    def __init__(self):
        self.ready = False

async def test_effect(dep, fill, flag):
    box = Box()
    dep.fill(box)
    assert box.ready
    dep.fill(box)
    box.ready = False
    assert box.ready
    alias = box
    dep.fill(alias)
    assert box.ready
    other = Box()
    dep.fill(other)
    assert box.ready
    dep.fill(box)
    if flag:
        box.ready = False
    assert box.ready
    try:
        dep.fill(box)
    except ValueError:
        assert box.ready
    with pytest.raises(ValueError):
        dep.fill(box)
    free_box = Box()
    fill(free_box)
    assert free_box.ready
    visible_box = Box()
    visible_fill(visible_box)
    assert visible_box.ready
    rebound_box = Box()
    dep.fill(rebound_box)
    rebound_box = Box()
    assert rebound_box.ready
    raw_box = Box()
    async_fill(raw_box)
    assert raw_box.ready
    completed_box = Box()
    await async_fill(completed_box)
    assert completed_box.ready
    self_box = Box()
    dep.fill(self_box)
    self_box.ready = self_box.ready
    assert self_box.ready
    invert_box = Box()
    dep.fill(invert_box)
    invert_box.ready = not invert_box.ready
    assert invert_box.ready
    double_box = Box()
    dep.fill(double_box); dep.fill(double_box)
    assert double_box.ready
    restore_box = Box()
    saved = restore_box.ready
    dep.fill(restore_box)
    restore_box.ready = False
    restore_box.ready = saved
    assert restore_box.ready
    alias_restore_box = Box()
    alias_saved = alias_restore_box.ready
    alias_restore = alias_restore_box
    dep.fill(alias_restore_box)
    alias_restore_box.ready = False
    alias_restore.ready = alias_saved
    assert alias_restore_box.ready
    throw_box = Box()
    try:
        always_throw(throw_box)
    except ValueError:
        pass
    assert throw_box.ready
    writer = Writer()
    method_box = Box()
    writer.fill(method_box)
    assert method_box.ready
    static_box = Box()
    static_other = Box()
    Factory.static_fill(static_box, static_other)
    assert static_box.ready
    frontend_box = Box()
    loaded_set(frontend_box)
    assert frontend_box.ready
    support_box = Box()
    support_fill(support_box)
    assert support_box.ready
    local_box = Box()
    local_loaded(local_box)
    assert local_box.ready
    raises_normal_box = Box()
    with pytest.raises(ValueError):
        visible_fill(raises_normal_box)
    assert raises_normal_box.ready
    nested_box = Box()
    if flag:
        dep = LocalDependency()
        dep.fill(nested_box)
        assert nested_box.ready
    dead_write_box = Box()
    dep.fill(dead_write_box)
    if False:
        dead_write_box.ready = False
    assert dead_write_box.ready
    unreachable_product_box = Box()
    unreachable_fill(unreachable_product_box)
    assert unreachable_product_box.ready

class LocalDependency:
    def fill(self, item):
        item.ready = True

def local_loaded(item):
    item.ready = True
"""

PRODUCT_SOURCE = """def visible_fill(item):
    item.ready = True

async def async_fill(item):
    item.ready = True

def always_throw(item):
    item.ready = True
    raise ValueError("always")

class Writer:
    def fill(self, item):
        item.ready = True

class Factory:
    @staticmethod
    def static_fill(first, second):
        second.ready = True

def detached_set(owner):
    owner.ready = True

def unreachable_fill(item):
    if False:
        item.ready = True
"""

SUPPORT_SOURCE = """def support_fill(item):
    item.ready = True
"""


def write_rows(path: Path, rows: list[list[object]]) -> None:
    with path.open("w", newline="") as stream:
        csv.writer(stream).writerows(rows)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source_line(fragment: str, occurrence: int = 1) -> int:
    matches = [line for line, text in enumerate(SOURCE.splitlines(), 1) if fragment in text]
    if len(matches) < occurrence:
        raise ValueError(f"fixture fragment missing: {fragment!r} occurrence {occurrence}")
    return matches[occurrence - 1]


def function_write_line(name: str) -> int:
    tree = ast.parse(PRODUCT_SOURCE)
    function = next(node for node in ast.walk(tree)
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                    and node.name == name)
    return next(node.lineno for node in ast.walk(function) if isinstance(node, ast.Assign)
                and isinstance(node.targets[0], ast.Attribute))


def fixture_facts() -> dict[str, list[list[object]]]:
    tree = ast.parse(SOURCE)
    target = next(node for node in tree.body
                  if isinstance(node, ast.AsyncFunctionDef) and node.name == "test_effect")
    observations: list[list[object]] = []
    for node in ast.walk(target):
        if isinstance(node, ast.Assert):
            observations.append([node.lineno, node.test.lineno, node.test.col_offset + 1, "assert"])
        elif (isinstance(node, ast.With) and isinstance(node.items[0].context_expr, ast.Call)
              and isinstance(node.items[0].context_expr.func, ast.Attribute)
              and node.items[0].context_expr.func.attr == "raises"):
            call = node.items[0].context_expr
            observations.append([call.lineno, call.lineno, call.col_offset + 1, "call"])
    loaded_call = next(node for node in ast.walk(target) if isinstance(node, ast.Call)
                       and isinstance(node.func, ast.Name) and node.func.id == "loaded_set")
    product_tree = ast.parse(PRODUCT_SOURCE)
    detached = next(node for node in product_tree.body
                    if isinstance(node, ast.FunctionDef) and node.name == "detached_set")
    local_loaded = next(node for node in tree.body
                        if isinstance(node, ast.FunctionDef) and node.name == "local_loaded")
    local_call = next(node for node in ast.walk(target) if isinstance(node, ast.Call)
                      and isinstance(node.func, ast.Name) and node.func.id == "local_loaded")
    support_call = next(node for node in ast.walk(target) if isinstance(node, ast.Call)
                        and isinstance(node.func, ast.Name) and node.func.id == "support_fill")
    return {
        "selectedTestPathFact": [["test_effect.py"]],
        "selectedTestFunctionFact": [["test_effect"]],
        "selectedTestStartLineFact": [[target.lineno]],
        "selectedObservationLineFact": [[row[0]] for row in sorted(observations)],
        "selectedObservationExprFact": sorted(observations),
        "selectedSupportPathFact": [["loader_support.py"]],
        "selectedOpaqueCallFact": [
            [fact.line, fact.column, fact.origin]
            for fact in build_parameter_boundary_facts(
                SOURCE, function_name="test_effect", function_start_line=target.lineno,
            )
        ] + [[source_line("dep.fill(nested_box)"), 9, "unresolved_parameter_boundary"]],
        "visibleLoaderBindingFact": [[
            loaded_call.lineno, "loaded_set", "product.py", detached.lineno,
            detached.name, detached.end_lineno, 0, "",
        ], [
            local_call.lineno, "local_loaded", "test_effect.py", local_loaded.lineno,
            local_loaded.name, local_loaded.end_lineno, 0, "",
        ]],
        "visibleImportBindingFact": [[
            support_call.lineno, "support_fill", "loader_support.py", 1,
            "support_fill", 2, 0, "",
        ]],
    }


def run(codeql: Path, output: Path) -> dict[str, object]:
    output.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    inputs = {path.name: sha256(path) for path in [QUERY, FACT_BUILDER, PROJECTION, RUNNER]}
    for path in [QUERY, FACT_BUILDER, PROJECTION, RUNNER]:
        shutil.copy2(path, output / path.name)
    phase_seconds: list[float] = []
    with tempfile.TemporaryDirectory(prefix="mockspec-effect-fixture-") as temporary:
        root = Path(temporary)
        source = root / "source"
        source.mkdir()
        (source / "test_effect.py").write_text(SOURCE)
        (source / "product.py").write_text(PRODUCT_SOURCE)
        (source / "loader_support.py").write_text(SUPPORT_SOURCE)
        facts = fixture_facts()
        fact_args: list[str] = []
        for predicate, fact_rows in facts.items():
            path = output / f"{predicate}.csv"
            write_rows(path, fact_rows)
            fact_args.append(f"--external={predicate}={path}")
        database = root / "database"
        bqrs = root / "result.bqrs"
        commands = [
            [str(codeql), "database", "create", str(database), "--language=python",
             f"--source-root={source}", "--threads=1", "--ram=3072", "--overwrite"],
            [str(codeql), "query", "run", f"--database={database}", "--threads=1",
             "--ram=3072", f"--output={bqrs}", *fact_args, str(QUERY)],
            [str(codeql), "bqrs", "decode", "--format=csv",
             f"--output={output / 'relations.csv'}", str(bqrs)],
        ]
        for index, command in enumerate(commands):
            phase_started = time.monotonic()
            completed = subprocess.run(command, text=True, capture_output=True, timeout=360)
            phase_seconds.append(time.monotonic() - phase_started)
            (output / f"phase-{index}.stdout").write_text(completed.stdout)
            (output / f"phase-{index}.stderr").write_text(completed.stderr)
            if completed.returncode:
                raise RuntimeError(f"phase {index} failed: {completed.returncode}")
    with (output / "relations.csv").open(newline="") as stream:
        rows: list[dict[str, str]] = list(csv.DictReader(stream))
    def has(relation: str, source: int, observation: int) -> bool:
        return any(row["sourceLine"] == str(source)
                   and row["observationLine"] == str(observation)
                   and row["relation"] == relation for row in rows)

    checks = {
        "fill_reaches_original_assert": has(
            "unknown_region_effect_to_observation", source_line("dep.fill(box)", 1),
            source_line("assert box.ready", 1)),
        "definite_overwrite_excludes": has(
            "region_effect_excluded_by_overwrite", source_line("dep.fill(box)", 2),
            source_line("assert box.ready", 2)),
        "conditional_overwrite_not_a_kill": has(
            "unknown_region_effect_to_observation", source_line("dep.fill(box)", 3),
            source_line("assert box.ready", 5)),
        "distinct_fresh_argument_excluded": not any(
            row["sourceLine"] == str(source_line("dep.fill(other)"))
            and row["observationLine"] == str(source_line("assert box.ready", 4)) for row in rows
        ),
        "handler_exception_path": has(
            "unknown_exception_path_to_observation", source_line("dep.fill(box)", 4),
            source_line("assert box.ready", 6)),
        "pytest_shape_separate": has(
            "unknown_exception_to_pytest_observation", source_line("dep.fill(box)", 5),
            source_line("with pytest.raises")),
        "free_function_boundary_reaches_assert": has(
            "unknown_region_effect_to_observation", source_line("fill(free_box)"),
            source_line("assert free_box.ready")),
        "resolved_actual_formal_visible_write": has(
            "visible_product_write_to_observation", function_write_line("visible_fill"),
            source_line("assert visible_box.ready")),
        "normal_only_product_write_does_not_cross_pytest_raises": not has(
            "visible_product_write_to_observation", function_write_line("visible_fill"),
            source_line("assert raises_normal_box.ready")),
        "same_variable_rebind_breaks_region": not any(
            row["sourceLine"] == str(source_line("dep.fill(rebound_box)"))
            and row["observationLine"] == str(source_line("assert rebound_box.ready"))
            for row in rows
        ),
        "raw_async_body_write_excluded": not has(
            "visible_product_write_to_observation", function_write_line("async_fill"),
            source_line("assert raw_box.ready")),
        "awaited_async_body_write_present": has(
            "visible_product_write_to_observation", function_write_line("async_fill"),
            source_line("assert completed_box.ready")),
        "self_copy_does_not_kill_dependency_effect": has(
            "unknown_region_effect_to_observation", source_line("dep.fill(self_box)"),
            source_line("assert self_box.ready")) and not has(
                "region_effect_excluded_by_overwrite", source_line("dep.fill(self_box)"),
                source_line("assert self_box.ready")),
        "inversion_does_not_kill_dependency_effect": has(
            "unknown_region_effect_to_observation", source_line("dep.fill(invert_box)"),
            source_line("assert invert_box.ready")) and not has(
                "region_effect_excluded_by_overwrite", source_line("dep.fill(invert_box)"),
                source_line("assert invert_box.ready")),
        "same_line_calls_keep_two_column_identities": len({
            row["sourceColumn"] for row in rows
            if row["sourceLine"] == str(source_line("dep.fill(double_box)"))
            and row["observationLine"] == str(source_line("assert double_box.ready"))
            and row["relation"] == "unknown_region_effect_to_observation"
        }) == 2,
        "restoration_after_literal_retains_effect": has(
            "unknown_region_effect_to_observation", source_line("dep.fill(restore_box)"),
            source_line("assert restore_box.ready")) and not has(
                "region_effect_excluded_by_overwrite", source_line("dep.fill(restore_box)"),
                source_line("assert restore_box.ready")),
        "alias_restoration_after_literal_retains_effect": has(
            "unknown_region_effect_to_observation", source_line("dep.fill(alias_restore_box)"),
            source_line("assert alias_restore_box.ready")) and not has(
                "region_effect_excluded_by_overwrite",
                source_line("dep.fill(alias_restore_box)"),
                source_line("assert alias_restore_box.ready")),
        "always_throwing_product_write_excluded": not has(
            "visible_product_write_to_observation", function_write_line("always_throw"),
            source_line("assert throw_box.ready")),
        "bound_method_actual_formal_present": has(
            "visible_product_write_to_observation", function_write_line("fill"),
            source_line("assert method_box.ready")),
        "static_method_wrong_target_excluded": not has(
            "visible_product_write_to_observation", function_write_line("static_fill"),
            source_line("assert static_box.ready")),
        "frontend_detached_method_actual_formal_present": has(
            "visible_product_write_to_observation", function_write_line("detached_set"),
            source_line("assert frontend_box.ready")),
        "product_write_keeps_call_and_target_identity": any(
            row["relation"] == "visible_product_write_to_observation"
            and row["observationLine"] == str(source_line("assert visible_box.ready"))
            and row["callLine"] == str(source_line("visible_fill(visible_box)"))
            and row["productPath"] == "product.py"
            and row["productName"] == "visible_fill" for row in rows),
        "pytest_unknown_keeps_concrete_call_identity": any(
            row["relation"] == "unknown_exception_to_pytest_observation"
            and row["callLine"] == str(source_line("dep.fill(box)", 5))
            and row["productPath"] == "" for row in rows),
        "nested_rebound_parameter_fact_rejected_by_dataflow": not any(
            row["sourceLine"] == str(source_line("dep.fill(nested_box)"))
            and row["observationLine"] == str(source_line("assert nested_box.ready"))
            for row in rows),
        "unreachable_literal_overwrite_does_not_kill_dependency_effect": has(
            "unknown_region_effect_to_observation", source_line("dep.fill(dead_write_box)"),
            source_line("assert dead_write_box.ready")) and not has(
                "region_effect_excluded_by_overwrite",
                source_line("dep.fill(dead_write_box)"),
                source_line("assert dead_write_box.ready")),
        "unreachable_product_write_excluded": not has(
            "visible_product_write_to_observation", function_write_line("unreachable_fill"),
            source_line("assert unreachable_product_box.ready")),
        "loader_support_target_not_promoted_to_product_effect": not any(
            row["sourcePath"] == "loader_support.py"
            and row["observationLine"] == str(source_line("assert support_box.ready"))
            for row in rows),
        "masked_test_frontend_target_not_promoted_to_product_effect": not any(
            row["relation"] == "visible_product_write_to_observation"
            and row["observationLine"] == str(source_line("assert local_box.ready"))
            for row in rows),
    }
    stable_inputs = {path.name: sha256(path) for path in [QUERY, FACT_BUILDER, PROJECTION, RUNNER]}
    checks["inputs_stable_during_run"] = inputs == stable_inputs
    summary: dict[str, object] = {
        "status": "mechanism_verified" if all(checks.values()) else "failed",
        "rows": len(rows), "checks": checks,
        "cost_seconds": {
            "database": phase_seconds[0], "query": phase_seconds[1],
            "decode": phase_seconds[2], "total": time.monotonic() - started,
        },
        "provenance": {
            "input_sha256": inputs,
            "fixture_source_sha256": hashlib.sha256(SOURCE.encode()).hexdigest(),
            "product_source_sha256": hashlib.sha256(PRODUCT_SOURCE.encode()).hexdigest(),
            "support_source_sha256": hashlib.sha256(SUPPORT_SOURCE.encode()).hexdigest(),
            "relations_sha256": sha256(output / "relations.csv"),
            "codeql_version": json.loads(subprocess.check_output(
                [str(codeql), "version", "--format=json"], text=True, timeout=30
            )),
        },
    }
    (output / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    if not all(checks.values()):
        raise AssertionError(checks)
    return summary


if __name__ == "__main__":
    print(json.dumps(run(Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve()), sort_keys=True))
