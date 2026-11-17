#!/usr/bin/env python3
"""Bounded regression gate for reviewed Java slice evidence.

This gate reuses immutable, already-reviewed receipts. It does not execute a
target test or rebuild a full WALA graph. Its purpose is deliberately narrow:
retain one useful Jib observation relation while proving that known bad
cross-context routes cannot pass the current authentication rule.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
from typing import Any


HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
ENTRY_REPAIR = REPO / "out/mockbench-study/entry-repair-20261111"
JIB_SUMMARY = (
    REPO / "out/mockbench-study/issue25-jib-observations-all-none-20261111-r6/summary.json"
)
ARCHIVED = ENTRY_REPAIR / "full165-r5/semantic"


def _load_backend():
    spec = importlib.util.spec_from_file_location("java_slice_gate_backend", HERE / "generic_backend.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _compact_route(projection: dict[str, Any], relation: dict[str, Any]) -> dict[str, Any]:
    by_original = {
        int(row["original_index"]): row["statement"] for row in projection["statements"]
    }
    original_edges = relation["provenance"]["dependency_path"]
    original_indexes = [int(original_edges[0]["from"]), *(
        int(edge["to"]) for edge in original_edges
    )]
    if any(index not in by_original for index in original_indexes):
        raise AssertionError("reviewed projection omitted a route statement")
    compact_index = {original: index for index, original in enumerate(original_indexes)}
    return {
        "seed_index": len(original_indexes) - 1,
        "slice_statements": [by_original[index] for index in original_indexes],
        "slice_edges": [{
            "from": compact_index[int(edge["from"])],
            "to": compact_index[int(edge["to"])],
            "labels": edge["labels"],
        } for edge in original_edges],
    }


def _reject_reviewed_route(
        backend, projection_path: Path, relation_index: int,
        ) -> dict[str, Any]:
    projection = json.loads(projection_path.read_text())
    try:
        relation = projection["relations"][relation_index]
    except IndexError as error:
        raise AssertionError(
            f"reviewed relation index missing at {projection_path}:{relation_index}"
        ) from error
    raw = _compact_route(projection, relation)
    path, evidence = backend._authenticated_dependency_path(
        raw,
        0,
        raw["seed_index"],
        {"DATA_DEP", "CONTROL_DEP"},
        require_exception_route=relation["kind"] == "exception_dependence",
    )
    if path is not None or evidence["reason"] != "no_authenticated_matched_call_return_path":
        raise AssertionError(f"reviewed bad route was not rejected: {evidence}")
    return {
        "observation_id": projection["observation_id"],
        "source_path": relation["from"]["logical_path"],
        "source_line": relation["from"]["start_line"],
        "archived_relation_index": relation_index,
        "archived_relation_kind": relation["kind"],
        "reason": evidence["reason"],
        "classification": "excluded_unknown_not_relation",
        "artifact_sha256": _sha256(projection_path),
    }


def run_gate() -> dict[str, Any]:
    backend = _load_backend()
    jib = json.loads(JIB_SUMMARY.read_text())
    observation = next(row for row in jib["observations"] if row["observation_id"] == "criterion:0")
    if observation["ssa_value_witness"]["status"] != "complete":
        raise AssertionError("Jib result-to-observation SSA witness is not complete")
    if observation["observed_expression_witness"]["status"] != "complete":
        raise AssertionError("Jib observed expression witness is not complete")
    if observation["base_image_source_witness"]["status"] != "complete":
        raise AssertionError("Jib base-image source witness is not complete")
    unknown_dependencies = [
        boundary["call"] for boundary in observation["source_boundaries"]
        if boundary["dependency_status"] == "unknown"
    ]
    if not unknown_dependencies:
        raise AssertionError("Jib conditional relation lost its dependency UNKNOWN boundaries")

    excluded: list[dict[str, Any]] = []
    rejected = {
        # Independent review rejected every local relation 0..8 for these two
        # Jib List.size observations (14 controls plus four exceptions).
        "2a37d7a1edfad4e10a7d": {10: tuple(range(9)), 15: tuple(range(9))},
        # Hadoop review rejected local exception rows 2,3,14,16 and control
        # rows 4..9 for both earlier Random observations.
        "3d83a681774c1109f1c4": {
            6: (2, 3, 4, 5, 6, 7, 8, 9, 14, 16),
            7: (2, 3, 4, 5, 6, 7, 8, 9, 14, 16),
        },
    }
    for case, criteria in rejected.items():
        for criterion, relation_indexes in criteria.items():
            projection = ARCHIVED / case / f"criterion:{criterion}.json"
            excluded.extend(
                _reject_reviewed_route(backend, projection, index)
                for index in relation_indexes
            )

    return {
        "schema": "mockspec.java-slice-reliability-gate/v1",
        "status": "passed_bounded_reviewed_artifacts",
        "target_executed": False,
        "full_graph_rebuilt": False,
        "positive_relations": [{
            "case": "Jib",
            "observation_id": "criterion:0",
            "kind": "state",
            "certainty": "conditional",
            "claim": "the returned builder/plan baseImage state may influence the original base-image observation",
            "evidence": [
                "exact result-to-observation SSA witness",
                "exact field Mod/Ref chain through Builder.build and plan construction",
            ],
            "unknown_dependencies": sorted(unknown_dependencies),
            "artifact_sha256": _sha256(JIB_SUMMARY),
        }],
        "exclusions": excluded,
        "semantic_claim": "clues_only_not_semantic_proof",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = run_gate()
    payload = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload)
    else:
        print(payload, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
