"""Combine the FULL execution/control and NONE value/state Jib channels."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def combine(full_dir: Path, state_dir: Path, output: Path) -> dict:
    full_path, state_path = full_dir / "summary.json", state_dir / "summary.json"
    full, state = json.loads(full_path.read_text()), json.loads(state_path.read_text())
    execution = {item["observation_id"]: item for item in full["observations"]}
    value_state = {item["observation_id"]: item for item in state["observations"]}
    if execution.keys() != value_state.keys():
        raise ValueError("channel observation identities differ")
    observations = []
    for observation_id in execution:
        full_item, state_item = execution[observation_id], value_state[observation_id]
        if full_item["expression"] != state_item["expression"]:
            raise ValueError(f"channel expression differs: {observation_id}")
        observations.append({
            "observation_id": observation_id,
            "expression": state_item["expression"],
            "range": [state_item["line"], state_item["end_line"]],
            "observed_line": state_item["observed_line"],
            "value_state": {
                "status": state_item["status"],
                "ssa_value_witness": state_item["ssa_value_witness"],
                "observed_expression_witness": state_item["observed_expression_witness"],
                "state_evidence": state_item["state_evidence"],
            },
            "execution_control": {
                "source_boundaries": full_item["source_boundaries"],
                "source_alternatives": full_item["source_alternatives"],
                "execution_effects": full_item["execution_effects"],
                "warning": "FULL membership is possible execution influence, not field/value support",
            },
        })
    result = {
        "schema": "mockspec.jib-dual-channel-summary/v1",
        "status": "partial", "target_executed": False,
        "full_summary": {"path": str(full_path), "sha256": sha256(full_path)},
        "value_state_summary": {"path": str(state_path), "sha256": sha256(state_path)},
        "observations": observations,
        "counts": {
            "observations": len(observations),
            "verified_incomplete_state_flow": sum(
                item["value_state"]["status"] == "verified_incomplete" for item in observations),
            "state_candidates_only": sum(
                item["value_state"]["status"] == "state_candidates_only" for item in observations),
            "unknown": sum(item["value_state"]["status"] == "unknown" for item in observations),
        },
        "closure_gate": "blocked",
        "reason": (
            "The two-channel design removes the FULL-control false closure for exact state witnesses, "
            "but native returns, collection-copy calls, nested layer calls, overwrite, and exception/path "
            "feasibility remain unknown."
        ),
        "result_contract": state["result_contract"],
    }
    output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--full", required=True, type=Path)
    parser.add_argument("--state", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(combine(args.full, args.state, args.output), indent=2))


if __name__ == "__main__":
    main()
