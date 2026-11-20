import json
from pathlib import Path

import python_batch


HERE = Path(__file__).resolve().parent
RESULTS = HERE / "python_results/paired.json"


def test_generation_freeze_covers_complete_matrix():
    freeze = python_batch.verify_freeze()
    assert freeze["attempts"] == 54
    assert sum(freeze["statuses"].values()) == 54


def test_paired_results_cover_every_python_reference_and_attempt():
    paired = json.loads(RESULTS.read_text())
    assert len(paired["results"]) == 40
    assert {row["variant"] for row in paired["results"] if row["variant"] == "reference"} == {
        "reference"
    }
    for result in paired["results"]:
        assert result["legal_denominator"] == 3
        assert len(result["mutants"]) == 3
        assert all(mutant["rows"] for mutant in result["mutants"])
        assert all("applicable" in row for mutant in result["mutants"] for row in mutant["rows"])


def test_stop_iteration_is_raw_kill_but_not_assertion_kill():
    paired = json.loads(RESULTS.read_text())
    ansible_candidates = {
        row["variant"]: row
        for row in paired["results"]
        if row["variant"] in {"ansible_119/3/A", "ansible_119/3/B"}
    }
    assert set(ansible_candidates) == {"ansible_119/3/A", "ansible_119/3/B"}
    for result in ansible_candidates.values():
        duplicate = next(
            mutant for mutant in result["mutants"]
            if mutant["mutant"] == "duplicate_importlib_probe"
        )
        assert duplicate["detected"] is True
        assert duplicate["detected_by_assertion"] is False
        assert duplicate["detected_by_stub_exhaustion"] is True
        assert any(row["classification"] == "killed_stub_exhaustion" for row in duplicate["rows"])
        assert result["raw_detected"] == 3
        assert result["assertion_detected"] == 2


def test_generation_failures_have_explicit_missing_rows():
    paired = json.loads(RESULTS.read_text())
    failures = [row for row in paired["results"] if row["status"] == "generation_failure"]
    assert len(failures) == 2
    for result in failures:
        assert result["qualified"] is False
        assert result["mutation_score"] is None
        assert all(
            row["classification"] == "generation_failure"
            for mutant in result["mutants"] for row in mutant["rows"]
        )
