#!/usr/bin/env python3
"""Generate a read-only taxonomy for generic Python cohort intake evidence."""

from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import sys
from typing import Any


TYPED_KINDS = {
    "value_flow",
    "control_dependence",
    "state_flow",
    "normal_return_dependence",
    "exception_dependence",
}
NATIVE_BINDING = "resolved_test_product_call"
NATIVE_INFLUENCE = "resolved_product_result_to_observation"
LOADER_BINDING = "visible_loader_product_call"
LOADER_INFLUENCE = "loader_product_result_to_observation"


def find_repo_root(script: Path) -> Path:
    for candidate in script.resolve().parents:
        if (candidate / "experiments/backward-slicing/result_contract.py").is_file():
            return candidate
    raise RuntimeError(f"cannot locate Mockspec repository root from {script}")


ROOT = find_repo_root(Path(__file__))
ORIGINAL = ROOT / "out/mockbench-study/backward-slice-full-20261109-r3/results.json"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise ValueError(f"{path}: expected a JSON object")
    return value


def key(row: dict[str, Any]) -> tuple[str, str]:
    identity = row["identity"]
    return identity["dataset"], identity["case_id"]


def load_rows(run: Path, *, partial: bool) -> list[dict[str, Any]]:
    results = run / "results.json"
    if results.is_file():
        rows = read_json(results).get("cases", [])
    elif partial:
        rows = []
        for path in sorted(run.glob("[0-9a-f]*.json")):
            candidate = read_json(path)
            if "identity" in candidate and "result_contract" in candidate:
                rows.append(candidate)
    else:
        raise FileNotFoundError(f"final results are absent: {results}; use --partial only for a provisional report")
    if not isinstance(rows, list):
        raise ValueError("cases must be an array")
    return [row for row in rows if row.get("identity", {}).get("language") == "python"]


def relation_query(relation: dict[str, Any]) -> str:
    return str(relation.get("provenance", {}).get("query_relation", "unlabelled"))


def is_observation_linked(relation: dict[str, Any]) -> bool:
    return bool(relation.get("observation_ids")) and relation.get("kind") in TYPED_KINDS


def is_cross_source(relation: dict[str, Any]) -> bool:
    source = relation.get("from", {}).get("logical_path")
    target = relation.get("to", {}).get("logical_path")
    return bool(source and target and source != target)


def taxonomy(rows: list[dict[str, Any]], *, final: bool) -> dict[str, Any]:
    original_rows = read_json(ORIGINAL).get("cases", [])
    expected = {
        key(row) for row in original_rows if row.get("identity", {}).get("language") == "python"
    }
    actual = {key(row) for row in rows}
    integrity_errors: list[dict[str, Any]] = []
    if len(actual) != len(rows):
        integrity_errors.append({"check": "unique_identity", "rows": len(rows), "unique": len(actual)})
    if final and actual != expected:
        integrity_errors.append({
            "check": "exact_original_python106",
            "missing": sorted(expected - actual),
            "extra": sorted(actual - expected),
        })
    if final and len(rows) != 106:
        integrity_errors.append({"check": "python_count", "expected": 106, "actual": len(rows)})

    stages: Counter[str] = Counter()
    backend_status: Counter[str] = Counter()
    entry_status: Counter[str] = Counter()
    relation_kinds: Counter[str] = Counter()
    cross_source_kinds: Counter[str] = Counter()
    linked_cross_source_kinds: Counter[str] = Counter()
    query_relations: Counter[str] = Counter()
    failure_categories: Counter[str] = Counter()
    unanalysed_reasons: Counter[str] = Counter()
    loader_fact_statuses: Counter[str] = Counter()
    loader_rejection_reasons: Counter[str] = Counter()
    backend_failure_reasons: Counter[str] = Counter()
    cases: list[dict[str, Any]] = []

    for row in rows:
        backend = row.get("semantic_backend", {})
        evidence = backend.get("entry_evidence", {})
        contract = row.get("result_contract", {})
        relations = contract.get("relations", [])
        loaders = backend.get("source_selection", {}).get("dynamic_loader_bindings", [])
        boundaries = contract.get("analysis_boundaries", {})
        observations_total = evidence.get("observations_total", 0)
        observations_mapped = evidence.get("observations_mapped", 0)
        ambiguous = evidence.get("ambiguous_observation_lines", [])
        missing = evidence.get("missing_observations", evidence.get("missing_observation_ids", []))
        status = backend.get("status", "missing")

        stages["attempted"] += 1
        stages["input_prepared"] += evidence.get("input_prepared") is True
        stages["database_created"] += evidence.get("database_created") is True
        stages["backend_completed"] += status == "complete"
        stages["any_observation_mapped"] += observations_mapped > 0
        stages["all_observations_mapped"] += (
            observations_total > 0 and observations_mapped == observations_total and not ambiguous
        )
        stages["ambiguous_observation_cases"] += bool(ambiguous)
        stages["missing_observation_cases"] += bool(missing)
        stages["product_binding_cases"] += evidence.get("product_call_bindings", 0) > 0
        backend_status[status] += 1
        entry_status[evidence.get("analysis_entry_status", "unreported")] += 1

        native_binding_relations = [relation for relation in relations
                                    if relation_query(relation) == NATIVE_BINDING]
        loader_binding_relations = [relation for relation in relations
                                    if relation_query(relation) == LOADER_BINDING]
        native_influence_relations = [
            relation for relation in relations
            if relation_query(relation) == NATIVE_INFLUENCE
            and is_observation_linked(relation) and is_cross_source(relation)
        ]
        loader_influence_relations = [
            relation for relation in relations
            if relation_query(relation) == LOADER_INFLUENCE
            and is_observation_linked(relation) and is_cross_source(relation)
        ]
        other_linked_cross_source = [
            relation for relation in relations
            if is_observation_linked(relation) and is_cross_source(relation)
            and relation_query(relation) not in {NATIVE_INFLUENCE, LOADER_INFLUENCE}
        ]
        resolved_loader_facts = [loader for loader in loaders if loader.get("status") == "resolved"]
        stages["native_binding_cases"] += bool(native_binding_relations)
        stages["authenticated_loader_fact_cases"] += bool(resolved_loader_facts)
        stages["authenticated_loader_binding_cases"] += bool(loader_binding_relations)
        stages["native_influence_cases"] += bool(native_influence_relations)
        stages["authenticated_loader_influence_cases"] += bool(loader_influence_relations)
        stages["other_typed_cross_source_influence_cases"] += bool(other_linked_cross_source)

        for relation in relations:
            kind = relation.get("kind", "missing")
            relation_kinds[kind] += 1
            query_relations[relation_query(relation)] += 1
            if is_cross_source(relation):
                cross_source_kinds[kind] += 1
                if is_observation_linked(relation):
                    linked_cross_source_kinds[kind] += 1
        failure_categories.update(set(contract.get("completion", {}).get("failure_categories", [])))
        for region in boundaries.get("unanalysed_regions", []):
            unanalysed_reasons[str(region.get("reason", "missing"))] += 1
        for loader in loaders:
            loader_fact_statuses[str(loader.get("status", "missing"))] += 1
            if loader.get("status") != "resolved":
                loader_rejection_reasons[str(loader.get("reason", "missing"))] += 1
        if status != "complete":
            reason = backend.get("reason") or backend.get("error", {}).get("message")
            error_type = backend.get("error_type") or backend.get("error", {}).get("type")
            backend_failure_reasons[str(error_type or reason or "unclassified")] += 1

        semantic_status = contract.get("semantic_validation", {}).get("status")
        if semantic_status != "not_evaluated":
            integrity_errors.append({
                "check": "semantic_validation_not_evaluated",
                "case": key(row),
                "actual": semantic_status,
            })
        evaluated_coverage = [
            coverage for coverage in contract.get("relation_coverage", [])
            if coverage.get("status") != "not_evaluated"
        ]
        if evaluated_coverage:
            integrity_errors.append({
                "check": "relation_coverage_not_evaluated",
                "case": key(row),
                "actual": evaluated_coverage,
            })
        cases.append({
            "identity": row["identity"],
            "backend_status": status,
            "analysis_entry_status": evidence.get("analysis_entry_status"),
            "stages": {
                "input_prepared": evidence.get("input_prepared") is True,
                "database_created": evidence.get("database_created") is True,
                "backend_completed": status == "complete",
                "all_observations_mapped": (
                    observations_total > 0
                    and observations_mapped == observations_total
                    and not ambiguous
                ),
                "product_binding_present": evidence.get("product_call_bindings", 0) > 0,
                "typed_observation_linked_cross_source_influence": bool(
                    native_influence_relations
                    or loader_influence_relations
                    or other_linked_cross_source
                ),
            },
            "observations": {
                "total": observations_total,
                "mapped": observations_mapped,
                "ambiguous": ambiguous,
                "missing": missing,
            },
            "mechanisms": {
                "native_bindings": len(native_binding_relations),
                "authenticated_loader_facts": len(resolved_loader_facts),
                "authenticated_loader_bindings": len(loader_binding_relations),
                "native_observation_influence": len(native_influence_relations),
                "authenticated_loader_observation_influence": len(loader_influence_relations),
                "other_typed_cross_source_observation_influence": len(other_linked_cross_source),
            },
            "relation_kinds": dict(Counter(relation.get("kind", "missing") for relation in relations)),
            "loader_fact_statuses": dict(Counter(str(loader.get("status", "missing"))
                                                  for loader in loaders)),
            "loader_rejection_reasons": sorted({str(loader.get("reason")) for loader in loaders
                                                 if loader.get("status") != "resolved"}),
            "failure_categories": contract.get("completion", {}).get("failure_categories", []),
            "unanalysed_reasons": sorted({str(region.get("reason"))
                                           for region in boundaries.get("unanalysed_regions", [])}),
        })

    return {
        "taxonomy_schema": "mockspec.issue32-python-intake-taxonomy/v1",
        "taxonomy_implementation": {
            "path": str(Path(__file__).resolve().relative_to(ROOT)),
            "sha256": sha256_file(Path(__file__).resolve()),
        },
        "cohort_state": "final" if final else "partial",
        "cohort": {"python_cases": len(rows), "expected_final": 106},
        "integrity_errors": integrity_errors,
        "stage_counts": dict(stages),
        "backend_status": dict(backend_status),
        "analysis_entry_status": dict(entry_status),
        "mechanism_definitions": {
            "native_binding": NATIVE_BINDING,
            "authenticated_loader_binding": LOADER_BINDING,
            "native_observation_influence": NATIVE_INFLUENCE,
            "authenticated_loader_observation_influence": LOADER_INFLUENCE,
            "counting_rule": "Influence requires a typed observation-linked cross-source relation; loader facts alone are not bindings or influence.",
        },
        "relations": {
            "all_kinds": dict(relation_kinds),
            "cross_source_kinds": dict(cross_source_kinds),
            "observation_linked_cross_source_kinds": dict(linked_cross_source_kinds),
            "query_relations": dict(query_relations),
        },
        "limitations": {
            "failure_categories": dict(failure_categories),
            "unanalysed_reasons": dict(unanalysed_reasons),
            "loader_fact_statuses": dict(loader_fact_statuses),
            "loader_rejection_reasons": dict(loader_rejection_reasons),
            "backend_failure_reasons": dict(backend_failure_reasons),
        },
        "semantic_correctness": {
            "status": "not_evaluated",
            "verified_cases": 0,
            "statement": "This taxonomy classifies static intake evidence only; it does not validate relation correctness, Mock recovery, generation, execution, or scoring.",
        },
        "cases": sorted(cases, key=lambda item: (item["identity"]["dataset"],
                                                  item["identity"]["case_id"])),
    }


def markdown(report: dict[str, Any]) -> str:
    return "\n".join([
        "# Python generic-intake taxonomy",
        "",
        f"- Cohort state: **{report['cohort_state']}**",
        f"- Python cases: {report['cohort']['python_cases']} / {report['cohort']['expected_final']}",
        f"- Integrity errors: {len(report['integrity_errors'])}",
        f"- Stages: `{json.dumps(report['stage_counts'], sort_keys=True)}`",
        f"- Backend status: `{json.dumps(report['backend_status'], sort_keys=True)}`",
        f"- Mechanisms: `{json.dumps(report['mechanism_definitions'], sort_keys=True)}`",
        f"- Relations: `{json.dumps(report['relations'], sort_keys=True)}`",
        f"- Limitations: `{json.dumps(report['limitations'], sort_keys=True)}`",
        "- Semantic correctness: **not_evaluated** (zero verified cases)",
        "",
        report["semantic_correctness"]["statement"],
        "",
    ])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--partial", action="store_true")
    args = parser.parse_args()
    try:
        rows = load_rows(args.run.resolve(), partial=args.partial)
    except FileNotFoundError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    report = taxonomy(rows, final=not args.partial)
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "python-taxonomy.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n"
    )
    (args.output / "python-taxonomy.md").write_text(markdown(report))
    print(json.dumps({
        "state": report["cohort_state"],
        "cases": report["cohort"]["python_cases"],
        "errors": len(report["integrity_errors"]),
    }, sort_keys=True))
    return 0 if not report["integrity_errors"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
