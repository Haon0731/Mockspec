"""Inventory membership must never be confused with source admission."""
import importlib.util
import hashlib
import json
from pathlib import Path

import pytest


PATH = Path(__file__).resolve().parents[1] / "experiments/mockbench-study/inventory.py"
SPEC = importlib.util.spec_from_file_location("study_inventory", PATH)
inventory = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(inventory)


def reports(tmp_path):
    catalog = json.loads((PATH.parents[1].parent / "src/mock_spec/reviewed_inputs.json").read_text())
    selected = [next(r for r in catalog if r["language"] == lang) for lang in ("java", "python")]
    folder = tmp_path / "reports/readiness"
    folder.mkdir(parents=True)
    readiness = {"schema_version": "developer-realistic-readiness/v1",
                 "protocol_version": "developer_realistic_workspace_v1",
                 "status": "pass", "errors": [], "languages": {}}
    for review in selected:
        report = {
            "dataset": review["dataset"], "status": "pass",
            "inventory_revision": review["inventory_revision"],
            "indexed_cases": 2, "validated_cases": 2,
            "cases": [
                {k: review[k] for k in ("case_id", "language", "snapshot_id")},
                {"case_id": f"unreviewed-{review['language']}", "language": review["language"],
                 "snapshot_id": "b" * 64},
            ],
        }
        content = json.dumps(report)
        (folder / f"{review['language']}-snapshot-validation.json").write_text(content)
        readiness["languages"][review["language"]] = {
            "dataset": review["dataset"], "inventory_revision": review["inventory_revision"],
            "snapshot_validation_sha256": hashlib.sha256(content.encode()).hexdigest(),
            "indexed_cases": 2, "validated_cases": 2,
            "coverage": {"inventory_cases": 2, "frozen_cases": 2},
        }
        (tmp_path / "formal-snapshots" / review["dataset"] / review["snapshot_id"]).mkdir(parents=True)
        index = {
            "schema_version": "developer-realistic-snapshot-index/v1",
            "protocol_version": "developer_realistic_workspace_v1",
            "dataset": review["dataset"], "inventory_revision": review["inventory_revision"],
            "cases": {entry["case_id"]: entry["snapshot_id"] for entry in report["cases"]},
        }
        index["index_digest"] = hashlib.sha256(json.dumps(
            index, sort_keys=True, ensure_ascii=False, separators=(",", ":")
        ).encode()).hexdigest()
        index_dir = tmp_path / "formal-snapshots/indexes" / review["dataset"]
        index_dir.mkdir(parents=True)
        (index_dir / f"{review['inventory_revision']}.json").write_text(json.dumps(index))
        (tmp_path / "formal-snapshots" / review["dataset"] / ("b" * 64)).mkdir()
    (folder / "developer-realistic-readiness.json").write_text(json.dumps(readiness))
    return folder, selected


def test_complete_membership_does_not_grant_admission(tmp_path):
    reports(tmp_path)
    result = inventory.build_inventory(tmp_path, input_policy="reviewed")
    assert result["summary"] == {
        "total": 4, "by_language": {"java": 2, "python": 2},
        "admission_status": {"reviewed_candidate": 2, "needs_review": 2},
    }
    assert all(row["admission_verified"] is False for row in result["cases"])
    assert len(result["sources"]) == 2
    assert all(len(source["report_sha256"]) == 64 for source in result["sources"])


def test_frozen_policy_does_not_require_legacy_case_whitelist(tmp_path, monkeypatch):
    reports(tmp_path)
    def forbid_catalog(*args):
        raise AssertionError("frozen policy must not open the legacy catalog")
    monkeypatch.setattr(inventory, "files", forbid_catalog)
    result = inventory.build_inventory(tmp_path)
    assert result["input_policy"] == "frozen-agent"
    assert result["summary"]["admission_status"] == {"frozen_candidate": 4}
    assert all(not row["admission_verified"] for row in result["cases"])
    assert result["review_catalog_sha256"] is None


def test_frozen_policy_rejects_index_with_different_snapshot(tmp_path):
    _, selected = reports(tmp_path)
    review = selected[0]
    path = tmp_path / "formal-snapshots/indexes" / review["dataset"] / f"{review['inventory_revision']}.json"
    data = json.loads(path.read_text())
    data["cases"][review["case_id"]] = "c" * 64
    del data["index_digest"]
    data["index_digest"] = hashlib.sha256(json.dumps(
        data, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    ).encode()).hexdigest()
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="snapshot differs"):
        inventory.build_inventory(tmp_path)


@pytest.mark.parametrize("mutation", ["duplicate", "count", "language", "snapshot", "dataset", "failed"])
def test_malformed_membership_fails_closed(tmp_path, mutation):
    folder, _ = reports(tmp_path)
    path = folder / "java-snapshot-validation.json"
    data = json.loads(path.read_text())
    if mutation == "duplicate":
        data["cases"][1] = data["cases"][0]
    elif mutation == "count":
        data["indexed_cases"] = 99
    elif mutation == "language":
        data["cases"][0]["language"] = "python"
    elif mutation == "snapshot":
        data["cases"][0]["snapshot_id"] = "../../outside"
    elif mutation == "dataset":
        data["dataset"] = "other"
    else:
        data["status"] = "fail"
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError):
        inventory.build_inventory(tmp_path, input_policy="reviewed")


def test_different_snapshot_is_not_substituted_by_review(tmp_path):
    folder, selected = reports(tmp_path)
    path = folder / "python-snapshot-validation.json"
    data = json.loads(path.read_text())
    data["cases"][0]["snapshot_id"] = "c" * 64
    path.write_text(json.dumps(data))
    readiness_path = folder / "developer-realistic-readiness.json"
    readiness = json.loads(readiness_path.read_text())
    readiness["languages"]["python"]["snapshot_validation_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    readiness_path.write_text(json.dumps(readiness))
    result = inventory.build_inventory(tmp_path, input_policy="reviewed")
    row = next(r for r in result["cases"] if r["case_id"] == selected[1]["case_id"])
    assert row["admission_status"] == "review_identity_mismatch"
    assert row["snapshot_root"].endswith("c" * 64)
    assert row["reviewed_snapshot_id"] == selected[1]["snapshot_id"]


def test_missing_reviewed_snapshot_remains_blocked(tmp_path):
    _, selected = reports(tmp_path)
    review = selected[0]
    (tmp_path / "formal-snapshots" / review["dataset"] / review["snapshot_id"]).rmdir()
    result = inventory.build_inventory(tmp_path, input_policy="reviewed")
    row = next(r for r in result["cases"] if r["case_id"] == review["case_id"])
    assert row["admission_status"] == "snapshot_missing"


def test_duplicate_json_fields_and_overwrites_are_rejected(tmp_path):
    folder, _ = reports(tmp_path)
    path = folder / "java-snapshot-validation.json"
    path.write_text('{"cases": [], "cases": []}')
    with pytest.raises(ValueError, match="duplicate"):
        inventory.build_inventory(tmp_path, input_policy="reviewed")
    with pytest.raises(FileExistsError):
        inventory.write_new_json(path, {})


@pytest.mark.parametrize("mutation", ["subset", "digest", "revision", "failed"])
def test_readiness_binding_rejects_incomplete_or_unbound_cohorts(tmp_path, mutation):
    folder, _ = reports(tmp_path)
    path = folder / "developer-realistic-readiness.json"
    data = json.loads(path.read_text())
    language = data["languages"]["java"]
    if mutation == "subset":
        language["coverage"]["inventory_cases"] = 3
    elif mutation == "digest":
        language["snapshot_validation_sha256"] = "f" * 64
    elif mutation == "revision":
        language["inventory_revision"] = "f" * 64
    else:
        data["status"] = "fail"
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError):
        inventory.build_inventory(tmp_path, input_policy="reviewed")
