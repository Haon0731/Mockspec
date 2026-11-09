"""Metadata-only cohort inventory; never grants source admission."""
from __future__ import annotations

from collections import Counter
import hashlib
from importlib.resources import files
import json
from pathlib import Path
import re

from mock_spec.validation import decode_json

SCHEMA = "mockbench-study.inventory/v1"
DATASETS = {"java": "java-bechmark-strong", "python": "python-benchmark-new"}


def _digest(value: object) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _read_report(path: Path) -> tuple[dict, str]:
    if path.stat().st_size > 16_000_000:
        raise ValueError("Inventory metadata exceeds 16 MB")
    content = path.read_bytes()
    report = decode_json(content.decode("utf-8"))
    if not isinstance(report, dict):
        raise ValueError("Expected snapshot-validation report object")
    return report, hashlib.sha256(content).hexdigest()


def build_inventory(
    registry_root: Path, *, reports: dict[str, Path] | None = None,
    input_policy: str = "frozen-agent",
) -> dict:
    """Read two validation reports, compare installed reviews, inspect no source trees.

    A historical report establishes cohort membership only. Runtime adapters must
    still validate the exact selected snapshot and masking before any analysis.
    """
    registry_root = registry_root.absolute()
    if input_policy not in {"frozen-agent", "reviewed"}:
        raise ValueError("Unknown input policy")
    reports = reports or {
        language: registry_root / "reports/readiness" / f"{language}-snapshot-validation.json"
        for language in DATASETS
    }
    if set(reports) != set(DATASETS):
        raise ValueError("Both Java and Python reports are required")
    readiness_path = registry_root / "reports/readiness/developer-realistic-readiness.json"
    readiness, readiness_digest = _read_report(readiness_path)
    if (readiness.get("schema_version") != "developer-realistic-readiness/v1"
            or readiness.get("protocol_version") != "developer_realistic_workspace_v1"
            or readiness.get("status") != "pass" or readiness.get("errors") != []
            or not isinstance(readiness.get("languages"), dict)):
        raise ValueError("Invalid frozen agent readiness report")
    catalog_text = None
    reviews = {}
    if input_policy == "reviewed":
        catalog_text = files("mock_spec").joinpath("reviewed_inputs.json").read_text()
        catalog = decode_json(catalog_text)
        for entry in catalog:
            key = (entry["language"], entry["case_id"])
            if key in reviews:
                raise ValueError("Duplicate installed input review")
            reviews[key] = entry
    cases, provenance = [], []
    for language, dataset in DATASETS.items():
        report, digest = _read_report(reports[language])
        entries = report.get("cases")
        if (report.get("dataset") != dataset or report.get("status") != "pass"
                or not _digest(report.get("inventory_revision"))
                or not isinstance(entries, list) or not entries):
            raise ValueError(f"Invalid {language} snapshot-validation report")
        for count in ("indexed_cases", "validated_cases"):
            if type(report.get(count)) is not int or report[count] != len(entries):
                raise ValueError(f"{language}: {count} does not match case membership")
        coverage = readiness["languages"].get(language)
        if (not isinstance(coverage, dict)
                or coverage.get("snapshot_validation_sha256") != digest
                or coverage.get("dataset") != dataset
                or coverage.get("inventory_revision") != report["inventory_revision"]):
            raise ValueError(f"{language}: validation report is not bound to readiness")
        totals = coverage.get("coverage")
        if (not isinstance(totals, dict)
                or any(type(coverage.get(key)) is not int or coverage[key] != len(entries)
                       for key in ("indexed_cases", "validated_cases"))
                or any(type(totals.get(key)) is not int or totals[key] != len(entries)
                       for key in ("inventory_cases", "frozen_cases"))):
            raise ValueError(f"{language}: membership does not cover the readiness inventory")
        if input_policy == "frozen-agent":
            from mock_spec.adapters.frozen_dataset import FrozenDataset

            dataset_input = FrozenDataset(
                registry_root, dataset=dataset, inventory_revision=report["inventory_revision"],
            )
            if set(dataset_input.case_ids) != {entry.get("case_id") for entry in entries if isinstance(entry, dict)}:
                raise ValueError(f"{language}: frozen index differs from readiness membership")
        seen = set()
        for entry in entries:
            if not isinstance(entry, dict):
                raise ValueError("Case entry must be an object")
            case_id = entry.get("case_id")
            if (not isinstance(case_id, str) or not case_id.strip() or case_id in seen
                    or entry.get("language") != language or not _digest(entry.get("snapshot_id"))):
                raise ValueError(f"Invalid or duplicate {language} case identity")
            seen.add(case_id)
            snapshot = entry["snapshot_id"]
            if input_policy == "frozen-agent" and dataset_input.snapshot_ids[case_id] != snapshot:
                raise ValueError(f"{language}: snapshot differs from frozen index")
            root = registry_root / "formal-snapshots" / dataset / snapshot
            review = reviews.get((language, case_id))
            if input_policy == "frozen-agent":
                status = "frozen_candidate" if root.is_dir() else "snapshot_missing"
            elif review is None:
                status = "needs_review"
            elif (review["dataset"] != dataset or review["snapshot_id"] != snapshot
                  or review["inventory_revision"] != report["inventory_revision"]):
                status = "review_identity_mismatch"
            elif not root.is_dir():
                status = "snapshot_missing"
            else:
                status = "reviewed_candidate"
            cases.append({
                "language": language, "dataset": dataset, "case_id": case_id,
                "inventory_revision": report["inventory_revision"], "snapshot_id": snapshot,
                "snapshot_root": str(root), "admission_status": status,
                "admission_verified": False,
                "reviewed_snapshot_id": review["snapshot_id"] if review else None,
            })
        provenance.append({
            "language": language, "dataset": dataset, "report": str(reports[language].absolute()),
            "report_sha256": digest, "inventory_revision": report["inventory_revision"],
            "case_count": len(entries),
        })
    cases.sort(key=lambda row: (row["language"], row["case_id"]))
    return {
        "schema_version": SCHEMA, "cohort_basis": "frozen_agent_snapshot_validation_reports",
        "input_policy": input_policy,
        "registry_root": str(registry_root), "sources": provenance,
        "readiness": {"path": str(readiness_path), "sha256": readiness_digest},
        "review_catalog_sha256": (
            hashlib.sha256(catalog_text.encode()).hexdigest() if catalog_text is not None else None
        ),
        "cases": cases,
        "summary": {"total": len(cases), "by_language": dict(Counter(r["language"] for r in cases)),
                    "admission_status": dict(Counter(r["admission_status"] for r in cases))},
        "limitations": [
            "Historical metadata membership is not a fresh source or masking audit.",
            "No snapshot substitution, raw-test fallback, source scan or dependency hashing.",
            "Candidates must pass their selected input policy at execution time.",
        ],
    }


def write_new_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")
