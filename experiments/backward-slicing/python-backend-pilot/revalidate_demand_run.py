"""Append-only validation for an Issue 29 demand-analysis run."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path


HERE = Path(__file__).resolve().parent


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canonical_study(path: Path) -> dict:
    case = json.loads(path.read_text(encoding="utf-8"))["case_result"]
    selected = {key: case[key] for key in (
        "identity", "observations", "entry_point", "relations", "relation_coverage",
        "analysis_boundaries", "completion", "semantic_validation",
    )}
    selected["relations"] = sorted(selected["relations"], key=lambda row: json.dumps(row, sort_keys=True))
    return selected


def validate(run: Path, baseline: Path) -> dict:
    result = json.loads((run / "result.json").read_text(encoding="utf-8"))
    manifest = json.loads((run / "relations/manifest.json").read_text(encoding="utf-8"))
    receipts: dict[str, dict] = {}
    for receipt_path in sorted((run / "relations").glob("*.receipt.json")):
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        csv_path = receipt_path.with_name(receipt_path.name.removesuffix(".receipt.json") + ".csv")
        if receipt.get("result_sha256") != sha256(csv_path):
            raise ValueError(f"result receipt mismatch: {csv_path.name}")
        receipts[csv_path.name] = {
            "csv_sha256": sha256(csv_path), "receipt_sha256": sha256(receipt_path),
            "query_sha256": receipt.get("query_sha256"),
            "root_selector_sha256": receipt.get("root_selector_sha256"),
            "status": receipt.get("status"),
        }
    if len(receipts) != 9 or not manifest.get("complete"):
        raise ValueError("complete nine-query manifest is required")
    if any(row.get("status") not in {"executed", "cache_hit"} for row in manifest["attempts"]):
        raise ValueError("failed query in complete manifest")
    equivalence = {
        "flask": canonical_study(run / "observation-study.json")
                 == canonical_study(baseline / "observation-study.json"),
        "state": canonical_study(run / "state-observation-study.json")
                 == canonical_study(baseline / "state-observation-study.json"),
    }
    with (run / "relations/probe-recursive-boundary.csv").open(newline="", encoding="utf-8") as handle:
        recursive_rows = list(csv.DictReader(handle))
    members = {row["functionName"] for row in recursive_rows
               if row.get("relation") == "recursive_expansion_boundary"
               and row.get("observationRoot") == "recursive_left"
               and row.get("observationLine") == "5"}
    recursive_ok = members == {"recursive_left", "recursive_right"}
    state_expectations = json.loads((HERE / "state_observation_expectations.json").read_text())
    allowed_state_functions = {
        item["function"] for item in state_expectations["observations"]
    } | {item["function"] for item in state_expectations.get("unknown_cases", [])}
    emitted_state_functions: set[str] = set()
    for name in (
        "probe-visible-state-flow.csv", "probe-unknown-effect-flow.csv",
        "probe-state-boundary-flow.csv", "probe-unknown-exception-state.csv",
    ):
        with (run / "relations" / name).open(newline="", encoding="utf-8") as handle:
            emitted_state_functions.update(row.get("functionName", "") for row in csv.DictReader(handle))
    unrelated_excluded = emitted_state_functions <= allowed_state_functions
    sources = [
        "run_demand_analysis.py", "revalidate_demand_run.py", "demand_analysis.py", "demand_roots.py",
        "observation_mapping.py", "state_observation_mapping.py",
        "recursive_observation_expectations.json", "qlpack.yml",
    ] + sorted(path.name for path in HERE.glob("*.ql"))
    accepted = (result.get("status") == "passed" and all(equivalence.values())
                and recursive_ok and unrelated_excluded)
    return {
        "schema": "mock-spec.python-demand-revalidation/v1",
        "status": "revalidated_acceptance" if accepted else "blocked",
        "original_result": {"path": str(run / "result.json"), "sha256": sha256(run / "result.json"),
                            "status": result.get("status")},
        "pre_registration_sha256": sha256(run / "pre-registration.json"),
        "manifest_sha256": sha256(run / "relations/manifest.json"),
        "studies": {name: sha256(run / name) for name in (
            "observation-study.json", "state-observation-study.json",
            "demand-mechanism-summary.json",
        )},
        "semantic_equivalence": equivalence,
        "recursive_observation": {"function": "recursive_left", "line": 5,
                                  "members": sorted(members), "status": recursive_ok},
        "unrelated_methods": {
            "status": "excluded" if unrelated_excluded else "leaked",
            "allowed_state_functions": sorted(allowed_state_functions),
            "emitted_state_functions": sorted(emitted_state_functions),
        },
        "relations": receipts,
        "execution_sources": {name: sha256(HERE / name) for name in sources},
        "baseline": {"path": str(baseline), "studies": {
            name: sha256(baseline / name) for name in (
                "observation-study.json", "state-observation-study.json")}},
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    value = validate(args.run, args.baseline)
    args.output.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    return 0 if value["status"] == "revalidated_acceptance" else 1


if __name__ == "__main__":
    raise SystemExit(main())
