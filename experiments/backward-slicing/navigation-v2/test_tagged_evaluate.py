import hashlib
import json
from pathlib import Path

import pytest

import tagged_evaluate


def _write(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value) + "\n")


def _freeze(run_root: Path):
    hashes = {}
    statuses = {}
    for case in tagged_evaluate.CASES:
        for repeat in tagged_evaluate.REPEATS:
            for arm in tagged_evaluate.ARMS:
                directory = run_root / case / str(repeat) / arm
                trace = directory / "trace.json"
                candidate = directory / "candidate.json"
                _write(trace, {"status": "generated"})
                _write(candidate, {"slots": {"slot": " pass"}, "rationale": []})
                for path in (trace, candidate):
                    hashes[str(path.relative_to(run_root))] = hashlib.sha256(
                        path.read_bytes()
                    ).hexdigest()
                statuses["generated"] = statuses.get("generated", 0) + 1
    _write(
        run_root / "freeze.json",
        {
            "attempts": tagged_evaluate.EXPECTED_ATTEMPTS,
            "statuses": statuses,
            "sha256": hashes,
        },
    )


def test_validate_complete_tagged_matrix(tmp_path):
    _freeze(tmp_path)

    freeze, rows = tagged_evaluate.validate_freeze(tmp_path)

    assert freeze["attempts"] == 42
    assert len(rows) == 42
    assert {row["case"] for row in rows} == set(tagged_evaluate.CASES)
    assert all(row["candidate_error"] is None for row in rows)


def test_validate_rejects_unfrozen_candidate_change(tmp_path):
    _freeze(tmp_path)
    candidate = tmp_path / "jib/1/v2/candidate.json"
    _write(candidate, {"slots": {}, "rationale": []})

    with pytest.raises(ValueError, match="differs from generation freeze"):
        tagged_evaluate.validate_freeze(tmp_path)


def test_tagged_candidate_requires_empty_host_rationale(tmp_path):
    candidate = tmp_path / "candidate.json"
    _write(candidate, {"slots": {}, "rationale": ["model text"]})

    error = tagged_evaluate._candidate_error(candidate)

    assert error == {
        "type": "ValueError",
        "message": "tagged transport must supply an empty host-side rationale",
    }
