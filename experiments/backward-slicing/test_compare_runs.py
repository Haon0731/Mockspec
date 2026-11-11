import copy
import json

import pytest

from compare_runs import compare, write_comparison
from result_contract import attach_result_contract


def row(case_id="a", status="partial"):
    return {"identity": {"case_id": case_id, "dataset": "frozen", "language": "python",
                         "snapshot_id": "snapshot"},
            "status": status, "failure_categories": [], "elapsed_seconds": 2,
            "evidence": {"reaches_product_candidate": True}}


def test_original_denominator_and_failures_are_preserved_without_semantic_success():
    baseline = {"cases": [row("a"), row("b", "failed")]}
    current = copy.deepcopy(baseline)
    current["cases"][1]["status"] = "timeout"
    current["cases"][1]["failure_categories"] = ["timeout"]
    result = compare(baseline, current, expected_count=2)
    assert result["counts"]["current_status"] == {"partial": 1, "timeout": 1}
    assert result["counts"]["current_validation"] == {"not_evaluated": 2}
    assert result["cases"][1]["changes"]["failure_categories_added"] == ["timeout"]
    assert result["cases"][0]["current"]["relations"] is None
    assert result["cases"][0]["changes"]["cost_comparison"]["delta_seconds"] is None


@pytest.mark.parametrize("change", ["missing", "duplicate", "substituted", "snapshot", "unselected", "running"])
def test_cannot_silently_drop_or_substitute_original_cases(change):
    baseline = {"cases": [row("a"), row("b")]}
    current = copy.deepcopy(baseline)
    if change == "missing":
        current["cases"].pop()
    elif change == "duplicate":
        current["cases"][1] = row("a")
    elif change == "substituted":
        current["cases"][1] = row("c")
    elif change == "snapshot":
        current["cases"][1]["identity"]["snapshot_id"] = "changed"
    elif change == "unselected":
        current["cases"][1]["status"] = "not_selected"
    else:
        current["cases"][1]["status"] = "running"
    with pytest.raises(ValueError):
        compare(baseline, current, expected_count=2)


def test_versioned_unknown_and_relation_evidence_survive_comparison():
    prior = row()
    current = row()
    attach_result_contract(current, current["identity"])
    result = compare({"cases": [prior]}, {"cases": [current]}, expected_count=1)
    case = result["cases"][0]
    assert case["current"]["entry_point"] == current["result_contract"]["entry_point"]
    assert case["current"]["unknown_effects"] == current["result_contract"]["analysis_boundaries"]["unknown_effects"]
    assert all(value["baseline"] == "not_recorded" for value in case["changes"]["relation_coverage"].values())
    assert set(case["changes"]["input_identity_checks"].values()) == {"not_recorded_on_both_sides"}
    current["result_contract"]["identity"]["snapshot_id"] = "other"
    with pytest.raises(ValueError, match="identity mismatch"):
        compare({"cases": [prior]}, {"cases": [current]}, expected_count=1)


@pytest.mark.parametrize("field", ["masking_contract", "masked_test_sha256", "target_selector"])
def test_recorded_mask_and_selector_cannot_change_under_same_snapshot(field):
    prior = row()
    attach_result_contract(prior, prior["identity"])
    prior["result_contract"]["identity"][field] = "a" * 64 if field == "masked_test_sha256" else "original"
    current = copy.deepcopy(prior)
    current["result_contract"]["identity"][field] = "b" * 64 if field == "masked_test_sha256" else "changed"
    with pytest.raises(ValueError, match=f"input identity differs: .*{field}"):
        compare({"cases": [prior]}, {"cases": [current]}, expected_count=1)


def test_artifact_cannot_overwrite_prior_evidence(tmp_path):
    source = tmp_path / "results.json"
    source.write_text(json.dumps({"cases": [row()]}))
    output = tmp_path / "comparison"
    first = write_comparison(source, source, output, expected_count=1)
    assert first["inputs"]["baseline"]["sha256"] == first["inputs"]["current"]["sha256"]
    saved = (output / "comparison.json").read_bytes()
    with pytest.raises(FileExistsError):
        write_comparison(source, source, output, expected_count=1)
    assert (output / "comparison.json").read_bytes() == saved


def test_r3_recorded_source_hash_and_selector_are_compared_without_fabricating_masking():
    prior = row()
    prior["sources"] = [{"source_id": "test", "sha256": "a" * 64}]
    prior["target_extraction"] = {"target_scope": {"selector": "test.py::test_a"}}
    current = row()
    current["input_context"] = {"masked_test_sha256": "a" * 64,
                                "target_selector": "test.py::test_a", "masking_contract": "slots"}
    attach_result_contract(current, current["identity"])
    result = compare({"cases": [prior]}, {"cases": [current]}, expected_count=1)
    assert result["cases"][0]["changes"]["input_identity_checks"] == {
        "masked_test_sha256": "equal", "target_selector": "equal", "masking_contract": "baseline_not_recorded"}
    current["result_contract"]["identity"]["masked_test_sha256"] = "b" * 64
    with pytest.raises(ValueError, match="masked_test_sha256"):
        compare({"cases": [prior]}, {"cases": [current]}, expected_count=1)
