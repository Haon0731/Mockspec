import hashlib
import json
from pathlib import Path

import evaluate_runs


def _write(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value) + "\n")


def _fake_run(root: Path, arms=evaluate_runs.ARMS) -> None:
    digests = {}
    for repeat in evaluate_runs.REPEATS:
        for arm in arms:
            trace = root / str(repeat) / arm / "trace.json"
            candidate = trace.with_name("candidate.json")
            _write(trace, {"status": "generated"})
            _write(candidate, {"slots": {"MOCK-STUB": "", "VERIFY": ""}, "rationale": []})
            for path in (trace, candidate):
                digests[str(path.relative_to(root))] = hashlib.sha256(path.read_bytes()).hexdigest()
    count = len(arms) * len(evaluate_runs.REPEATS)
    _write(root / "freeze.json", {"attempts": count, "statuses": {"generated": count},
                                   "sha256": digests})


def test_prepare_binds_complete_matrix(tmp_path):
    run = tmp_path / "run"
    output = tmp_path / "evaluation"
    _fake_run(run)

    frozen = evaluate_runs.prepare(run, output)

    assert len(frozen["rows"]) == 12
    assert frozen["available_candidates"] == 12
    assert frozen["arms"] == ["OC", "PC", "OR", "PR"]


def test_prepare_rejects_unfrozen_candidate(tmp_path):
    run = tmp_path / "run"
    _fake_run(run)
    (run / "1/OC/candidate.json").write_text("{}\n")

    try:
        evaluate_runs.prepare(run, tmp_path / "evaluation")
    except ValueError as error:
        assert "not bound" in str(error)
    else:
        raise AssertionError("changed candidate was accepted")


def test_prepare_supports_independent_direct_matrix(tmp_path):
    run = tmp_path / "direct-run"
    _fake_run(run, evaluate_runs.DIRECT_ARMS)

    frozen = evaluate_runs.prepare(run, tmp_path / "direct-evaluation", evaluate_runs.DIRECT_ARMS)

    assert frozen["attempts"] == 6
    assert frozen["arms"] == ["DC", "DR"]


def test_paired_keeps_fixed_denominator():
    rows = [{"repeat": 1, "arm": "OC", "subject_id": "repeat-1-oc",
             "candidate": "/candidate.json"}]
    records = [
        {"subject_id": "repeat-1-oc", "mutant": "original", "status": "passed",
         "receipt": "original.json"},
        {"subject_id": "repeat-1-oc", "mutant": "omit_create_layers",
         "status": "test_assertion_or_verification_failed", "receipt": "a.json"},
        {"subject_id": "repeat-1-oc", "mutant": "omit_compute_entrypoint",
         "status": "passed", "receipt": "b.json"},
        {"subject_id": "repeat-1-oc", "mutant": "duplicate_create_layers",
         "status": "driver_error", "receipt": "c.json"},
    ]

    result = evaluate_runs._paired({"rows": rows}, records)["subjects"][0]

    assert result["status"] == "mutation_incomplete"
    assert result["original_passed"] is True
    assert result["mutation_detected"] == 1
    assert result["mutation_valid"] == 2
    assert result["mutation_denominator"] == 3
    assert result["mutation_score"] is None
