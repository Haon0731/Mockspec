import copy
import hashlib
import json

import pytest

import report


def _write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def _run_fixture(tmp_path):
    run_root = tmp_path / "run"
    digests = {}
    statuses = {}
    for case in report.EXPECTED_CASES:
        for repeat in report.EXPECTED_REPEATS:
            for arm_index, arm in enumerate("ABC"):
                directory = run_root / case / repeat / arm
                _write(directory / "candidate.json", {
                    "slots": {"mock": f"candidate {repeat}"}, "rationale": ["hidden"],
                })
                _write(directory / "trace.json", {
                    "status": "generated",
                    "input_bytes": 1000 + arm_index * 100,
                    "source_calls": 3 - arm_index,
                    "elapsed_seconds": 10 + arm_index,
                    "turns": [{"usage": {"input_tokens": 100 + arm_index, "output_tokens": 20}}],
                })
                for name in ("candidate.json", "trace.json"):
                    path = directory / name
                    digests[str(path.relative_to(run_root))] = hashlib.sha256(path.read_bytes()).hexdigest()
                statuses["generated"] = statuses.get("generated", 0) + 1
    _write(run_root / "freeze.json", {"attempts": 54, "statuses": statuses, "sha256": digests})
    return run_root


def _review_all(packet):
    reviewed = copy.deepcopy(packet)
    for index, candidate in enumerate(reviewed["candidates"]):
        candidate["review"] = {
            "status": "reviewed",
            "execution": {"status": "all_passed", "passed_rows": 2, "total_rows": 2},
            "f_stub": {"matched": 2 + index % 2, "decidable": 3},
            "mutations": {"detected": 2, "valid": 3},
            "qualified": True,
            "unsupported_assertions": 0,
            "extra_constraints": 0,
            "notes": "manual anonymous review",
            "missing_reason": None,
        }
    return reviewed


def test_blind_packet_hides_arm_paths_and_keeps_mapping_separate(tmp_path):
    run_root = _run_fixture(tmp_path)

    packet, mapping = report.build_blind_review(run_root, salt="secret")

    assert len(packet["candidates"]) == 54
    assert all(set(row) == {"blind_id", "case", "slots", "review"} for row in packet["candidates"])
    public_text = json.dumps(packet)
    assert '"arm"' not in public_text
    assert "trace.json" not in public_text
    assert "rationale" not in public_text
    assert {row["arm"] for row in mapping["attempts"]} == {"a", "b", "c"}
    assert [row["blind_id"] for row in packet["candidates"]] == sorted(
        row["blind_id"] for row in packet["candidates"]
    )
    second, _ = report.build_blind_review(run_root, salt="secret")
    assert packet == second


def test_summary_reports_raw_paired_values_ranges_directions_and_costs(tmp_path):
    run_root = _run_fixture(tmp_path)
    packet, mapping = report.build_blind_review(run_root, salt="secret")
    reviewed = _review_all(packet)

    summary = report.build_summary(run_root, mapping, reviewed)

    case = summary["cases"][0]
    assert len(case["attempts"]) == 9
    arms = {row["arm"]: row for row in case["arms"]}
    assert arms["a"]["candidate_delivery_count"] == 3
    assert arms["c"]["metrics"]["source_calls"]["range"] == [1, 1]
    comparisons = {row["comparison"]: row for row in case["comparisons"]}
    assert set(comparisons) == {"A/C", "B/C"}
    source_calls = comparisons["A/C"]["metrics"]["source_calls"]
    assert source_calls["baseline_range"] == [3, 3]
    assert source_calls["comparison_range"] == [1, 1]
    assert [row["delta"] for row in source_calls["raw"]] == [-2, -2, -2]
    assert source_calls["direction_consistency"] == "consistent"
    assert all(row["outcome"] == "improved" for row in source_calls["raw"])
    assert comparisons["B/C"]["metrics"]["input_bytes"]["comparison_range"] == [1200, 1200]
    assert summary["interpretation"]["candidate_text_classification"] == "not_performed"


def test_missing_review_requires_explicit_reason_and_propagates_missing_values(tmp_path):
    run_root = _run_fixture(tmp_path)
    packet, mapping = report.build_blind_review(run_root, salt="secret")
    reviewed = _review_all(packet)
    reviewed["candidates"][0]["review"] = {"status": "missing", "missing_reason": "fixture unavailable"}

    summary = report.build_summary(run_root, mapping, reviewed)

    missing_case = next(
        case for case in summary["cases"]
        if any(row["review"]["status"] == "missing" for row in case["attempts"])
    )
    missing = next(row for row in missing_case["attempts"] if row["review"]["status"] == "missing")
    assert missing["review"]["reason"] == "fixture unavailable"
    assert missing["metrics"]["f_stub_rate"] is None
    comparison_rows = [
        row
        for comparison in missing_case["comparisons"]
        for row in comparison["metrics"]["f_stub_rate"]["raw"]
    ]
    assert any(row["direction"] == "missing" for row in comparison_rows)


def test_pending_or_unexplained_missing_review_is_rejected(tmp_path):
    run_root = _run_fixture(tmp_path)
    packet, mapping = report.build_blind_review(run_root, salt="secret")

    with pytest.raises(report.ReportError, match="explicitly reviewed or missing"):
        report.build_summary(run_root, mapping, packet)

    reviewed = _review_all(packet)
    reviewed["candidates"][0]["review"] = {"status": "missing", "missing_reason": ""}
    with pytest.raises(report.ReportError, match="missing_reason"):
        report.build_summary(run_root, mapping, reviewed)


def test_review_ids_must_exactly_cover_private_map(tmp_path):
    run_root = _run_fixture(tmp_path)
    packet, mapping = report.build_blind_review(run_root, salt="secret")
    reviewed = _review_all(packet)
    reviewed["candidates"].pop()

    with pytest.raises(report.ReportError, match="exactly cover"):
        report.build_summary(run_root, mapping, reviewed)


def test_unqualified_candidate_has_no_primary_mutation_score(tmp_path):
    run_root = _run_fixture(tmp_path)
    packet, mapping = report.build_blind_review(run_root, salt="secret")
    reviewed = _review_all(packet)
    blind_id = reviewed["candidates"][0]["blind_id"]
    reviewed["candidates"][0]["review"]["qualified"] = False

    summary = report.build_summary(run_root, mapping, reviewed)

    attempt = next(
        attempt for case in summary["cases"] for attempt in case["attempts"]
        if attempt["blind_id"] == blind_id
    )
    assert attempt["metrics"]["mutation_detected"] == 2
    assert attempt["metrics"]["mutation_valid"] == 3
    assert attempt["metrics"]["mutation_score"] is None
    assert attempt["metrics"]["qualified_candidate"] == 0


def test_qualification_requires_full_original_pass_and_mutation_denominator(tmp_path):
    run_root = _run_fixture(tmp_path)
    packet, mapping = report.build_blind_review(run_root, salt="secret")
    reviewed = _review_all(packet)
    review = reviewed["candidates"][0]["review"]
    review["execution"] = {"status": "partial", "passed_rows": 1, "total_rows": 2}
    with pytest.raises(report.ReportError, match="did not pass all original rows"):
        report.build_summary(run_root, mapping, reviewed)

    reviewed = _review_all(packet)
    reviewed["candidates"][0]["review"]["mutations"] = {"detected": 0, "valid": 0}
    with pytest.raises(report.ReportError, match="lacks a mutation denominator"):
        report.build_summary(run_root, mapping, reviewed)


def test_freeze_digest_and_complete_matrix_are_enforced(tmp_path):
    run_root = _run_fixture(tmp_path)
    packet, mapping = report.build_blind_review(run_root, salt="secret")
    reviewed = _review_all(packet)
    candidate_path = run_root / mapping["attempts"][0]["candidate"]
    _write(candidate_path, {"slots": {"mock": "changed"}})

    with pytest.raises(report.ReportError, match="differs from freeze.json"):
        report.build_summary(run_root, mapping, reviewed)

    incomplete_root = _run_fixture(tmp_path / "incomplete")
    arm_dir = incomplete_root / report.EXPECTED_CASES[0] / "1" / "C"
    for path in arm_dir.iterdir():
        path.unlink()
    arm_dir.rmdir()
    with pytest.raises(report.ReportError, match="contain A, B and C"):
        report.build_blind_review(incomplete_root, salt="secret")
