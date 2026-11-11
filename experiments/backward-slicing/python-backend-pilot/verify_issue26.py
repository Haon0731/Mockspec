"""Verify Issue #26 from the unified Flask result and registered mechanism evidence."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


HERE = Path(__file__).resolve().parent


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify(results: Path, expectations_path: Path) -> dict[str, Any]:
    expectations = json.loads(expectations_path.read_text(encoding="utf-8"))
    prereg = json.loads((results / "issue26-pre-registration.json").read_text(encoding="utf-8"))
    expected_prereg = {
        "schema": "mock-spec.issue26-pre-registration/v1",
        "expectations_sha256": sha256(expectations_path),
        "observation_mapper_sha256": sha256(HERE / "observation_mapping.py"),
        "pytest_query_sha256": sha256(HERE / "pytest-exception-flow.ql"),
        "flask_query_sha256": sha256(HERE / "flask-observation-flow.ql"),
        "mechanism_source_sha256": sha256(HERE / "mechanism_probe.py"),
        "verifier_sha256": sha256(Path(__file__)),
    }
    if prereg != expected_prereg:
        raise ValueError("Issue #26 pre-registration does not match current analysis inputs")

    study = json.loads((results / "observation-study.json").read_text(encoding="utf-8"))
    verdict = json.loads((results / "verdict.json").read_text(encoding="utf-8"))
    case = study["case_result"]
    observations = {item["location"]["start_line"]: item for item in case["observations"]}
    relations = case["relations"]

    def linked(kind: str, source: int, target: int) -> bool:
        return any(
            row["kind"] == kind
            and row["from"].get("start_line") == source
            and row["to"].get("start_line") == target
            and row.get("observation_ids")
            for row in relations
        )

    checks = {
        "identity_and_three_observations": (
            case["identity"]["case_id"] == expectations["case_id"]
            and case["identity"]["snapshot_id"] == expectations["snapshot_id"]
            and set(observations) == {102, 120, 126}
        ),
        "exact_call_and_actual_formal": (
            any(row["kind"] == "call_binding" and row["from"].get("start_line") == 103
                and row["to"].get("start_line") == 265 for row in relations)
            and any(row["kind"] == "value_flow" and row["from"].get("start_line") == 103
                    and row["to"].get("parameter_index") == 0 for row in relations)
        ),
        "guarded_assertions": linked("control_dependence", 111, 120)
        and linked("control_dependence", 111, 126),
        "normal_product_continuation": any(
            row["kind"] == "normal_return_dependence"
            and row["from"].get("start_line") == 283 and row["to"].get("start_line") == 285
            for row in relations
        ),
        "exception_to_each_observation": all(any(
            row["kind"] == "exception_dependence" and observation["observation_id"]
            in row.get("observation_ids", []) for row in relations
        ) for observation in observations.values()),
        "no_unmapped_observation": study["unknown_observation_mappings"] == [],
        "exact_values_remain_unknown": {
            row["observation_id"] for row in study["unknown_value_constraints"]
        } == {item["observation_id"] for item in observations.values()},
        "overwrite_and_cross_process_values": all(verdict["checks"].get(key) is True for key in (
            "overwrite_removes_old_value_flow", "matched_actual_formal_return_flow",
        )),
        "early_return_catch_finally_and_exception": all(
            verdict["checks"].get(key) is True for key in (
                "unknown_call_normal_and_exception_edges",
                "early_return_and_raise_do_not_reach_later_observe",
                "catch_and_finally_structure",
            )
        ),
        "visible_pure_not_opaque": verdict["state_boundary_evidence"]["checks"].get(
            "visible_pure_call_has_no_unknown_effect"
        ) is True,
        "conditional_support_domain_preserved": (
            case["completion"]["status"] == "partial"
            and case["semantic_validation"]["status"] == "verified_incomplete"
            and any(item["scope"] == "path satisfiability and hidden Mock behavior"
                    for item in case["analysis_boundaries"]["unanalysed_regions"])
        ),
        "timing_and_memory_recorded": (
            case["cost"]["total_seconds"] > 0
            and case["cost"]["peak_memory"]["status"] == "measured_per_process_max"
        ),
    }
    return {
        "schema": "mock-spec.issue26-verdict/v1",
        "status": "passed" if all(checks.values()) else "blocked",
        "checks": checks,
        "expectations": expectations,
        "case_result_sha256": sha256(results / "observation-study.json"),
        "backend_verdict_sha256": sha256(results / "verdict.json"),
        "semantic_scope": (
            "bounded may-influence for the registered Flask observations; exact values and path "
            "satisfiability remain unknown"
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--expectations", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = verify(args.results, args.expectations)
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
