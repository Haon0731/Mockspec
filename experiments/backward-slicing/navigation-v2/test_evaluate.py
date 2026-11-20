import hashlib
import json
from pathlib import Path
import sys

import pytest


sys.path.insert(0, str(Path(__file__).resolve().parent))
import evaluate


def _write(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value) + "\n")


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _fake_run(root: Path) -> None:
    hashes = {}
    for case in evaluate.CASES:
        for repeat in evaluate.REPEATS:
            for arm in evaluate.ARMS:
                directory = root / case / str(repeat) / arm
                trace = directory / "trace.json"
                _write(trace, {"status": "failed", "error_type": "Synthetic"})
                hashes[trace.relative_to(root).as_posix()] = _digest(trace)
    _write(root / "freeze.json", {
        "attempts": evaluate.EXPECTED_ATTEMPTS,
        "statuses": {"failed": evaluate.EXPECTED_ATTEMPTS},
        "sha256": hashes,
    })


def test_validate_freeze_accepts_complete_failed_matrix(tmp_path):
    _fake_run(tmp_path)

    freeze, rows = evaluate.validate_freeze(tmp_path)

    assert freeze["attempts"] == 18
    assert len(rows) == 18
    assert all(row["candidate"] is None for row in rows)


def test_validate_freeze_rejects_changed_trace(tmp_path):
    _fake_run(tmp_path)
    trace = tmp_path / "jib/1/v1/trace.json"
    _write(trace, {"status": "generated"})

    with pytest.raises(ValueError, match="differs from generation freeze"):
        evaluate.validate_freeze(tmp_path)


def test_flask_wrapper_adapts_top_level_candidate_and_fills_missing(tmp_path):
    candidate = tmp_path / "candidate.json"
    value = {"slots": {"SLOT": "body"}, "rationale": ["reason"]}
    _write(candidate, value)
    rows = [
        {"arm": "v1", "subject_id": "v1", "candidate": str(candidate),
         "candidate_error": None},
        {"arm": "ablation", "subject_id": "ablation", "candidate": None,
         "candidate_error": None},
        {"arm": "v2", "subject_id": "v2", "candidate": None,
         "candidate_error": None},
    ]

    mapping = evaluate._flask_wrappers(rows, tmp_path / "wrapped")

    assert mapping == {"a": "v1", "b": "v1", "c": "v1"}
    assert json.loads((tmp_path / "wrapped/generation-a.json").read_text()) == {"flask": value}


def test_flask_summary_uses_full_original_and_mutant_assertion_failures():
    row = {"case": "flask_308", "repeat": 1, "arm": "v2", "subject_id": "subject",
           "candidate": "/candidate", "candidate_error": None}
    variants = [{
        "mutant": "original", "status": "passed", "valid_execution": True,
        "passed_rows": 5, "parameter_rows": [],
    }]
    variants.extend({
        "mutant": mutant, "status": "assertion_failure", "valid_execution": True,
        "passed_rows": 4, "parameter_rows": [
            {"outcome": "failed", "exception_type": "AssertionError"},
        ],
    } for mutant in evaluate.FLASK_MUTANTS[1:])

    summary = evaluate._summarize_flask(row, variants)

    assert summary["main_score_eligible"] is True
    assert summary["mutation_detected"] == 3
    assert summary["mutation_score"] == 1.0


def test_evaluate_records_all_generation_failures_without_docker(tmp_path):
    run = tmp_path / "run"
    _fake_run(run)
    output = run / "evaluation.json"

    result = evaluate.evaluate(run, output, tmp_path / "old")

    assert output.is_file()
    assert len(result["subjects"]) == 18
    assert all(subject["candidate_available"] is False for subject in result["subjects"])
    assert all(subject["main_score_eligible"] is False for subject in result["subjects"])
