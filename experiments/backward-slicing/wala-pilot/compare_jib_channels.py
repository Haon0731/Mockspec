"""Compare FULL-control execution and NONE-control value/state diagnostics."""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def compare(full_dir: Path, state_dir: Path, output: Path) -> dict:
    full_raw = json.loads((full_dir / "analysis.json").read_text(encoding="utf-8"))
    state_raw = json.loads((state_dir / "analysis.json").read_text(encoding="utf-8"))
    full_registration = json.loads((full_dir / "pre-registration.json").read_text(encoding="utf-8"))
    state_registration = json.loads((state_dir / "pre-registration.json").read_text(encoding="utf-8"))
    for key in ("test_sha256", "visible_mut_sha256", "container_builders", "libraries"):
        if full_registration[key] != state_registration[key]:
            raise ValueError(f"channel inputs differ: {key}")
    if full_raw["configuration"]["control_dependence"] != "FULL" \
            or state_raw["configuration"]["control_dependence"] != "NONE":
        raise ValueError("expected FULL and NONE control channels")
    if len(full_raw["observations"]) != 1 or len(state_raw["observations"]) != 1:
        raise ValueError("channel diagnostic expects one matching observation")
    full = full_raw["observations"][0]
    state = state_raw["observations"][0]
    result = {
        "schema": "mockspec.jib-channel-comparison/v1",
        "target_executed": False,
        "criterion": full["criterion"],
        "input_identity_equal": True,
        "execution_channel": {
            "control_dependence": "FULL",
            "slice_statement_count": full["slice_statement_count"],
            "retained_statement_count": full["retained_statement_count"],
            "total_millis": full_raw["timings_millis"]["total"],
            "meaning": "possible execution/control/exception influence; includes unrelated state writes",
        },
        "value_state_channel": {
            "control_dependence": "NONE",
            "slice_statement_count": state["slice_statement_count"],
            "retained_statement_count": state["retained_statement_count"],
            "total_millis": state_raw["timings_millis"]["total"],
            "meaning": "value/state diagnostic only; absence cannot exclude control or exception influence",
        },
        "reduction": {
            "slice_statement_ratio": state["slice_statement_count"] / full["slice_statement_count"],
            "retained_statement_ratio": state["retained_statement_count"] / full["retained_statement_count"],
        },
        "interpretation": (
            "FULL control dependence is the dominant cause of the indistinguishable 325k-statement closure. "
            "NONE preserves the exact builder/plan/getter state witness but intentionally drops source branch "
            "and exception evidence; the two channels must be reported together."
        ),
    }
    output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--full", required=True, type=Path)
    parser.add_argument("--state", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(compare(args.full, args.state, args.output), indent=2))


if __name__ == "__main__":
    main()
