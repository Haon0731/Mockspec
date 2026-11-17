"""Run the preordered single-attempt pair, retaining all failures without retries."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import time
from typing import Any

from evaluate_candidate import evaluate
from run_generation import DEFAULT_STUB_TESTER_ROOT, run_generation


def run_pair(gate_path: Path, pair: Path, registry: Path) -> dict:
    pair = pair.resolve()
    report_path = pair / "results.json"
    if report_path.exists():
        raise FileExistsError("paired attempt already recorded")
    manifest = json.loads((pair / "manifest.json").read_text())
    gate = json.loads(gate_path.read_text())
    row = next(row for row in gate["cases"] if row["case_id"] == manifest["case_id"])
    report = {"case_id": manifest["case_id"], "model": manifest["model"],
              "order": manifest["order"], "arms": {}, "usage": None, "cost_usd": None,
              "scope": "single paired pilot; no statistical efficacy claim"}
    report_path.write_text(json.dumps(report, indent=2) + "\n")
    for arm in manifest["order"]:
        started = time.monotonic()
        record: dict[str, Any] = {"status": "started", "usage": None, "cost_usd": None}
        report["arms"][arm] = record
        report_path.write_text(json.dumps(report, indent=2) + "\n")
        try:
            generated = run_generation(gate_path, pair, arm, pair / arm / "generation")
            record.update(status="candidate_produced", generation=generated)
        except Exception as exc:
            # Provider/runtime diagnostics remain in controlled trajectory files;
            # never serialize arbitrary exception strings containing credentials.
            record.update(status="generation_failed", exception_type=type(exc).__name__)
        record["generation_wall_seconds"] = time.monotonic() - started
        if (pair / arm / "workspace" / manifest["candidate_path"]).is_file():
            snapshot = registry / "formal-snapshots" / row["dataset"] / row["snapshot_id"]
            try:
                record["evaluation"] = evaluate(snapshot, registry, pair / arm, manifest, row["language"])
            except Exception as exc:
                record["evaluation"] = {"status": "infrastructure_error", "exception_type": type(exc).__name__}
        report_path.write_text(json.dumps(report, indent=2) + "\n")
        print(json.dumps({"case_id": manifest["case_id"], "arm": arm,
                          "status": record["status"], "evaluation": record.get("evaluation", {}).get("status")}), flush=True)
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gate", type=Path, required=True)
    parser.add_argument("--pair", type=Path, required=True)
    parser.add_argument("--registry", type=Path,
                        default=DEFAULT_STUB_TESTER_ROOT / ".omx/developer-realistic-registry-max")
    args = parser.parse_args()
    run_pair(args.gate.resolve(), args.pair, args.registry.resolve())
