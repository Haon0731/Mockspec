"""Identity-bound planning and caching for observation-demanded Jib slices."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping, Sequence


SCHEMA = "mock-spec.java-demand-analysis/v1"
SUMMARY_VERSION = "jib-observation-relations-20261111-r6"


@dataclass(frozen=True)
class Unit:
    name: str
    criterion: str | None
    dependencies: tuple[str, ...] = ()


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def digest_json(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def tree_identity(path: Path) -> str:
    files = sorted(item for item in path.rglob("*") if item.is_file())
    return digest_json([
        {"path": str(item.relative_to(path)), "sha256": sha256(item)} for item in files
    ])


def plan(roots: Iterable[str], *, units: Mapping[str, Unit], max_units: int,
         max_depth: int) -> dict:
    """Expand demand units fail-closed while retaining recursion/budget frontiers."""
    if max_units < 0 or max_depth < 0:
        raise ValueError("demand budgets must be non-negative")
    roots = tuple(roots)
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
        unit = units.get(name)
        if unit is None:
            boundaries.append({"unit": name, "reason": "unknown_unit", "depth": depth})
            return
        visiting.add(name)
        for dependency in unit.dependencies:
            expand(dependency, depth + 1)
        visiting.remove(name)
        visited.add(name)
        if unit.criterion is None:
            return
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


def cache_key(*, unit: Unit, source_identity: str, configuration_identity: str,
              context: str, summary_version: str = SUMMARY_VERSION) -> str:
    if unit.criterion is None:
        raise ValueError("aggregate units cannot be cached as analysis results")
    return digest_json({
        "schema": SCHEMA,
        "unit": unit.name,
        "criterion": unit.criterion,
        "source_identity": source_identity,
        "configuration_identity": configuration_identity,
        "context": context,
        "summary_version": summary_version,
    })


def valid_cache_record(path: Path, *, key: str) -> dict | None:
    if not path.is_file():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if value.get("schema") != SCHEMA or value.get("cache_key") != key:
        return None
    observation = value.get("observation")
    if not isinstance(observation, dict):
        return None
    if value.get("observation_sha256") != digest_json(observation):
        return None
    return value


def canonical_observation(value: Mapping) -> dict:
    """Normalize traversal order/cost while preserving every dependency witness."""
    normalized = {
        key: item for key, item in value.items()
        if key not in {
            "slice_millis", "demand_pdg", "slice_statement_count",
            "retained_statement_count", "analysis_status",
        }
    }
    if isinstance(normalized.get("statements"), list):
        normalized["statements"] = sorted(
            normalized["statements"], key=lambda row: json.dumps(row, sort_keys=True)
        )
    return normalized


def canonical_summary(value: Mapping) -> dict:
    contract = value["result_contract"]
    observations = json.loads(json.dumps(value["observations"]))
    for observation in observations:
        excluded = observation.get("excluded_from_state_support")
        if isinstance(excluded, dict):
            excluded.pop("examples", None)
    setter_evidence = dict(value["optional_setter_bytecode_evidence"])
    setter_evidence["inputs"] = [
        {"sha256": item["sha256"]} for item in setter_evidence["inputs"]
    ]
    return {
        "observations": observations,
        "outcome": value["outcome"],
        "control_evidence": value["control_evidence"],
        "optional_evidence": value["optional_evidence"],
        "optional_setter_bytecode_evidence": setter_evidence,
        "result_contract": {
            "identity": contract["identity"],
            "source_scope": contract["source_scope"],
            "observations": contract["observations"],
            "entry_point": contract["entry_point"],
            "relations": sorted(
                contract["relations"], key=lambda row: json.dumps(row, sort_keys=True)
            ),
            "relation_coverage": contract["relation_coverage"],
            "analysis_boundaries": contract["analysis_boundaries"],
            "completion": contract["completion"],
            "semantic_validation": contract["semantic_validation"],
        },
    }


def method_footprint(observations: Sequence[Mapping]) -> dict:
    methods = {
        statement.get("method")
        for observation in observations
        for statement in observation.get("statements", [])
        if isinstance(statement.get("method"), str)
    }
    statement_count = sum(
        len(observation.get("statements", [])) for observation in observations
    )
    return {
        "demanded_observations": len(observations),
        "retained_dependency_statements": statement_count,
        "retained_dependency_methods": len(methods),
        "method_signatures": sorted(methods),
    }
