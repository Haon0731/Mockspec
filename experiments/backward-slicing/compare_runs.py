"""Align frozen-case results without turning candidate graphs into semantic success."""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path

from result_contract import validate_result_contract


TERMINAL_STATUSES = frozenset({
    "complete", "partial", "unknown", "failed", "timeout", "unsupported", "budget_exceeded", "blocked",
})
INPUT_IDENTITY_FIELDS = ("masking_contract", "masked_test_sha256", "target_selector")


def _index(rows: list[dict], expected_count: int) -> dict[tuple[str, str], dict]:
    indexed = {}
    for row in rows:
        identity = row["identity"]
        key = (identity["dataset"], identity["case_id"])
        if key in indexed:
            raise ValueError(f"duplicate case identity: {key}")
        if row.get("status") not in TERMINAL_STATUSES:
            raise ValueError(f"case has no attempted terminal result: {key}")
        contract = row.get("result_contract")
        if contract is not None:
            validate_result_contract(contract)
            for name in ("case_id", "dataset", "language", "snapshot_id"):
                if contract["identity"][name] != identity[name]:
                    raise ValueError(f"contract identity mismatch: {key}: {name}")
        indexed[key] = row
    if len(indexed) != expected_count:
        raise ValueError(f"expected {expected_count} cases, got {len(indexed)}")
    return indexed


def _facts(row: dict) -> dict:
    contract = row.get("result_contract", {})
    boundaries = contract.get("analysis_boundaries", {})
    # r3 predates the versioned contract but already records the selected test
    # hash and selector. Compare those facts; do not synthesize missing masking.
    test_source: dict = next((source for source in row.get("sources", [])
                        if source.get("source_id") == "test"), {})
    recorded = {
        "masked_test_sha256": test_source.get("sha256"),
        "target_selector": row.get("target_extraction", {}).get("target_scope", {}).get("selector"),
        **row.get("input_context", {}), **contract.get("identity", {}),
    }
    return {
        "status": row["status"],
        "candidate_diagnostics": {name: row.get("candidate_diagnostics", row).get(name)
                                  for name in ("status", "failure_categories", "stage", "error", "elapsed_seconds")},
        "semantic_backend": {name: row.get("semantic_backend", {}).get(name)
                             for name in ("status", "backend", "reason", "limitations", "error")},
        "input_identity": {name: recorded.get(name) for name in INPUT_IDENTITY_FIELDS},
        "failure_categories": sorted(set(row.get("failure_categories", []))
                                     | set(contract.get("completion", {}).get("failure_categories", []))),
        "entry_point": contract.get("entry_point"),
        "observations": contract.get("observations"),
        "relations": contract.get("relations"),
        "relation_coverage": contract.get("relation_coverage"),
        "semantic_validation": contract.get("semantic_validation", {
            "status": "not_evaluated", "reason": "baseline has no versioned semantic evidence"}),
        "unknown_effects": boundaries.get("unknown_effects"),
        "unanalysed_regions": boundaries.get("unanalysed_regions"),
        "candidate_product_reached": row.get("evidence", {}).get("reaches_product_candidate"),
        "reported_elapsed_seconds": row.get("elapsed_seconds"),
        "cost": contract.get("cost"),
        "reported_costs": {
            "batch_case": row.get("batch_case_cost"),
            "semantic_backend": row.get("semantic_backend", {}).get("cost"),
            "semantic_dispatch_seconds": row.get("semantic_backend", {}).get("batch_dispatch_seconds"),
            "candidate": row.get("candidate_diagnostics", {}).get("result_contract", {}).get("cost"),
        },
    }


def compare(baseline: dict, current: dict, *, expected_count: int = 165) -> dict:
    old, new = _index(baseline["cases"], expected_count), _index(current["cases"], expected_count)
    if old.keys() != new.keys():
        raise ValueError("case sets differ; missing or substituted cases cannot be dropped")
    rows = []
    for key, prior in old.items():
        latest = new[key]
        for name in ("language", "snapshot_id"):
            if prior["identity"][name] != latest["identity"][name]:
                raise ValueError(f"frozen identity differs: {key}: {name}")
        before, after = _facts(prior), _facts(latest)
        identity_checks = {}
        for name in INPUT_IDENTITY_FIELDS:
            old_value, new_value = before["input_identity"][name], after["input_identity"][name]
            if old_value is not None and new_value is not None:
                if old_value != new_value:
                    raise ValueError(f"frozen input identity differs: {key}: {name}")
                identity_checks[name] = "equal"
            else:
                identity_checks[name] = ("not_recorded_on_both_sides" if old_value is None and new_value is None
                                         else "baseline_not_recorded" if old_value is None else "current_not_recorded")
        before_coverage = {item["kind"]: item["status"]
                           for item in before["relation_coverage"] or []}
        after_coverage = {item["kind"]: item["status"]
                          for item in after["relation_coverage"] or []}
        rows.append({
            "identity": prior["identity"], "baseline": before, "current": after,
            "changes": {
                "input_identity_checks": identity_checks,
                "status": [before["status"], after["status"]],
                "failure_categories_added": sorted(set(after["failure_categories"]) - set(before["failure_categories"])),
                "failure_categories_removed": sorted(set(before["failure_categories"]) - set(after["failure_categories"])),
                "relation_coverage": {kind: {"baseline": before_coverage.get(kind, "not_recorded"),
                                             "current": after_coverage.get(kind, "not_recorded")}
                                      for kind in sorted(before_coverage.keys() | after_coverage.keys())},
                "semantic_improvement": "not_inferred_from_counts_or_coverage_labels",
                "cost_comparison": {
                    "status": "not_comparable", "delta_seconds": None,
                    "reason": "Timing boundaries, backend configuration and shared costs require a separate matched comparison; recorded values are retained.",
                },
            },
        })
    return {
        "schema": "mock-spec.backward-analysis-comparison/v1", "report_only": True,
        "counts": {"total": len(rows),
                   "by_language": dict(Counter(row["identity"]["language"] for row in rows)),
                   "baseline_status": dict(Counter(row["baseline"]["status"] for row in rows)),
                   "current_status": dict(Counter(row["current"]["status"] for row in rows)),
                   "current_validation": dict(Counter(row["current"]["semantic_validation"]["status"] for row in rows))},
        "cases": rows,
        "interpretation": "All original identities remain. Relation evidence, unknowns and failure states are separate; graph construction and product reach are not semantic correctness.",
    }


def write_comparison(baseline: Path, current: Path, output: Path, *, expected_count: int = 165) -> dict:
    if output.exists():
        raise FileExistsError(f"output must not exist: {output}")
    before, after = baseline.read_bytes(), current.read_bytes()
    result = compare(json.loads(before), json.loads(after), expected_count=expected_count)
    result["inputs"] = {
        "baseline": {"path": str(baseline), "sha256": hashlib.sha256(before).hexdigest()},
        "current": {"path": str(current), "sha256": hashlib.sha256(after).hexdigest()},
    }
    result["implementation_sha256"] = {
        name: hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest()
        for name in ("compare_runs.py", "result_contract.py")
    }
    output.mkdir(parents=True, exist_ok=False)
    (output / "comparison.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", required=True, type=Path)
    parser.add_argument("--current", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(write_comparison(args.baseline, args.current, args.output)["counts"], indent=2))
