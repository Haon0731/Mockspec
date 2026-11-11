"""Observation-driven CodeQL query planning with identity-bound relation caching."""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable


SCHEMA = "mock-spec.python-demand-analysis/v1"
SUMMARY_VERSION = "python-bounded-effects-20261111-r12"


@dataclass(frozen=True)
class Unit:
    name: str
    database: str | None
    query: str | None
    dependencies: tuple[str, ...] = ()


UNITS = {
    "flask-observations": Unit(
        "flask-observations", None, None,
        ("flask-observation-flow", "flask-abort", "exceptions",
         "pytest-exception-flow"),
    ),
    "state-observations": Unit(
        "state-observations", None, None,
        ("visible-state-flow", "unknown-effect-flow", "state-boundary-flow",
         "unknown-exception-state"),
    ),
    "recursive-observation": Unit(
        "recursive-observation", None, None, ("recursive-boundary",)
    ),
    "flask-observation-flow": Unit(
        "flask-observation-flow", "flask", "flask-observation-flow.ql"
    ),
    "flask-abort": Unit("flask-abort", "flask", "flask-abort.ql"),
    "exceptions": Unit("exceptions", "flask", "exceptions.ql"),
    "pytest-exception-flow": Unit(
        "pytest-exception-flow", "flask", "pytest-exception-flow.ql"
    ),
    "visible-state-flow": Unit("visible-state-flow", "probe", "visible-state-flow.ql"),
    "unknown-effect-flow": Unit("unknown-effect-flow", "probe", "unknown-effect-flow.ql"),
    "state-boundary-flow": Unit("state-boundary-flow", "probe", "state-boundary-flow.ql"),
    "unknown-exception-state": Unit(
        "unknown-exception-state", "probe", "unknown-exception-state.ql"
    ),
    "recursive-boundary": Unit("recursive-boundary", "probe", "recursive-boundary.ql"),
}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source_identity(root: Path) -> str:
    digest = hashlib.sha256()
    files = sorted(path for path in root.rglob("*") if path.is_file())
    for path in files:
        digest.update(path.relative_to(root).as_posix().encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def cache_key(*, unit: Unit, source_sha256: str, codeql_version: str,
              context: str, plan_sha256: str = "unspecified",
              mapper_sha256: str = "unspecified", config_sha256: str = "unspecified",
              budget_sha256: str = "unspecified",
              root_selector_sha256: str = "unspecified",
              summary_version: str = SUMMARY_VERSION) -> str:
    if unit.query is None:
        raise ValueError(f"non-query unit has no cache key: {unit.name}")
    query_path = Path(__file__).with_name(unit.query)
    payload = {
        "schema": SCHEMA,
        "unit": unit.name,
        "database": unit.database,
        "query_sha256": sha256(query_path),
        "source_sha256": source_sha256,
        "codeql_version": codeql_version,
        "context": context,
        "plan_sha256": plan_sha256,
        "mapper_sha256": mapper_sha256,
        "config_sha256": config_sha256,
        "budget_sha256": budget_sha256,
        "root_selector_sha256": root_selector_sha256,
        "summary_version": summary_version,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def plan(roots: Iterable[str], *, max_units: int, max_depth: int,
         units: dict[str, Unit] | None = None) -> dict:
    roots = tuple(roots)
    catalog = units or UNITS
    selected: list[str] = []
    boundaries: list[dict[str, str | int]] = []
    visiting: set[str] = set()
    visited: set[str] = set()

    def expand(name: str, depth: int) -> None:
        if name in visited:
            return
        if name in visiting:
            boundaries.append({"unit": name, "reason": "recursive_dependency", "depth": depth})
            return
        if depth > max_depth:
            boundaries.append({"unit": name, "reason": "max_depth", "depth": depth})
            return
        if name not in catalog:
            boundaries.append({"unit": name, "reason": "unknown_unit", "depth": depth})
            return
        visiting.add(name)
        for dependency in catalog[name].dependencies:
            expand(dependency, depth + 1)
        visiting.remove(name)
        visited.add(name)
        if catalog[name].query is not None:
            if len(selected) >= max_units:
                boundaries.append({"unit": name, "reason": "unit_budget", "depth": depth})
            else:
                selected.append(name)

    for root in roots:
        expand(root, 0)
    return {
        "schema": SCHEMA,
        "roots": list(roots),
        "selected_units": selected,
        "unexpanded_boundaries": boundaries,
        "complete": not boundaries,
    }


def execute_plan(plan_result: dict, *, source_ids: dict[str, str], codeql_version: str,
                 context: str, cache: Path, output: Path, identity: dict[str, str] | None = None,
                 summary_version: str = SUMMARY_VERSION,
                 on_result: Callable[[Unit, Path], None] | None = None,
                 identity_for_unit: Callable[[Unit], dict[str, str]] | None = None,
                 runner: Callable[[Unit, Path], dict]) -> dict:
    output.mkdir(parents=True, exist_ok=False)
    cache.mkdir(parents=True, exist_ok=True)
    attempts: list[dict] = []
    identity = identity or {}
    for name in plan_result["selected_units"]:
        unit = UNITS[name]
        assert unit.database is not None
        assert unit.query is not None
        unit_identity = {**identity, **(identity_for_unit(unit) if identity_for_unit else {})}
        key = cache_key(
            unit=unit, source_sha256=source_ids[unit.database],
            codeql_version=codeql_version, context=context, **unit_identity,
            summary_version=summary_version,
        )
        cached = cache / f"{key}.csv"
        receipt = cache / f"{key}.json"
        target = output / f"{unit.database}-{unit.name}.csv"
        validation_started = time.monotonic()
        cache_valid = False
        if cached.is_file() and receipt.is_file():
            record = json.loads(receipt.read_text(encoding="utf-8"))
            cache_valid = (
                record.get("cache_key") == key
                and record.get("result_sha256") == sha256(cached)
            )
        if cache_valid:
            target.write_bytes(cached.read_bytes())
            validation_seconds = time.monotonic() - validation_started
            attempt = {"unit": name, "status": "cache_hit", "cache_key": key,
                       "result_sha256": sha256(target),
                       "cache_validation_seconds": validation_seconds}
            attempts.append(attempt)
            (output / f"{unit.database}-{unit.name}.receipt.json").write_text(
                json.dumps({"schema": SCHEMA, **attempt,
                            "query_sha256": sha256(Path(__file__).with_name(unit.query)),
                            "source_sha256": source_ids[unit.database],
                            "summary_version": summary_version, **unit_identity}, indent=2) + "\n",
                encoding="utf-8",
            )
            if on_result:
                on_result(unit, target)
            continue
        result = runner(unit, target)
        if result.get("status") != "complete":
            attempts.append({"unit": name, "status": "failed", "cache_key": key, **result})
            continue
        if not target.is_file():
            raise RuntimeError(f"runner did not create result for {name}")
        cached.write_bytes(target.read_bytes())
        receipt.write_text(json.dumps({
            "schema": SCHEMA, "cache_key": key, "unit": name,
            "source_sha256": source_ids[unit.database], "codeql_version": codeql_version,
            "context": context, "summary_version": summary_version,
            **unit_identity,
            "query_sha256": sha256(Path(__file__).with_name(unit.query)),
            "result_sha256": sha256(cached),
        }, indent=2) + "\n", encoding="utf-8")
        attempt = {"unit": name, "cache_key": key, "result_sha256": sha256(target),
                   **result, "status": "executed"}
        attempts.append(attempt)
        (output / f"{unit.database}-{unit.name}.receipt.json").write_text(
            json.dumps({"schema": SCHEMA, **attempt,
                        "query_sha256": sha256(Path(__file__).with_name(unit.query)),
                        "source_sha256": source_ids[unit.database],
                        "summary_version": summary_version, **unit_identity}, indent=2) + "\n",
            encoding="utf-8",
        )
        if on_result:
            on_result(unit, target)
    failed_boundaries = [{"unit": row["unit"], "reason": "query_execution_failed",
                          "status": row["status"]}
                         for row in attempts if row["status"] not in {"executed", "cache_hit"}]
    manifest = {**plan_result, "attempts": attempts, "source_identities": source_ids,
                "codeql_version": codeql_version, "context": context,
                "summary_version": summary_version}
    manifest["unexpanded_boundaries"] = [*plan_result["unexpanded_boundaries"], *failed_boundaries]
    manifest["complete"] = plan_result["complete"] and not failed_boundaries
    (output / "manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    return manifest
