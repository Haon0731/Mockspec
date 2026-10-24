"""Acceptance-matrix tests for the staged-inference research entry."""

from __future__ import annotations

import json
from pathlib import Path
import sys


STAGED_ROOT = Path(__file__).parents[1] / "experiments" / "staged-inference"
sys.path.insert(0, str(STAGED_ROOT))

from acceptance import REPORT_VERSION, build_acceptance, main  # noqa: E402


def test_acceptance_builds_and_verifies_full_fixed_matrix(tmp_path) -> None:
    output_dir = tmp_path / "acceptance"
    report = build_acceptance(output_dir)

    assert report["schema_version"] == REPORT_VERSION
    assert report["engineering_acceptance"] == "passed"
    assert report["run_count"] == 15
    assert report["coverage"] == {
        "variants": ["B", "C"],
        "result_states": ["SELECTED", "AMBIGUOUS", "INCONCLUSIVE", "ERROR"],
        "query_attempt_counts": [0, 6, 7],
        "boundary_cases": [
            "original_assertion_tamper", "parser_backend_unavailable",
            "cross_stage_total_deadline",
        ],
    }
    assert {run["result_status"] for run in report["runs"]} == {
        "SELECTED", "AMBIGUOUS", "INCONCLUSIVE", "ERROR",
    }
    assert {(run["variant"], run["query_count"]) for run in report["runs"]} >= {
        (variant, count) for variant in ("B", "C") for count in (0, 6, 7)
    }
    assert all(run["run_manifest_sha256"] for run in report["runs"])
    assert all(run["artifact_sha256"]["input_snapshot.json"] == run["input_hash"]
               for run in report["runs"])
    saved = json.loads((output_dir / "acceptance_report.json").read_text())
    assert saved == report


def test_acceptance_refuses_to_overwrite_evidence(tmp_path, capsys) -> None:
    output_dir = tmp_path / "existing"
    output_dir.mkdir()
    marker = output_dir / "preserve.txt"
    marker.write_text("preserve")

    assert main(["--output-dir", str(output_dir)]) == 1
    assert "acceptance failed" in capsys.readouterr().err
    assert marker.read_text() == "preserve"
