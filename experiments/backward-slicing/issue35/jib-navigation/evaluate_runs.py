"""Freeze and evaluate the Jib navigation-factorial generation matrix."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
from typing import Any


HERE = Path(__file__).resolve().parent
ISSUE35 = HERE.parent
sys.path.insert(0, str(ISSUE35))

import java_evaluate  # noqa: E402


ARMS = ("OC", "PC", "OR", "PR")
DIRECT_ARMS = ("DC", "DR")
REPEATS = (1, 2, 3)
MUTANTS = ("original", *java_evaluate.MUTATIONS["jib"])
EXPECTED_ATTEMPTS = len(ARMS) * len(REPEATS)


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _generation_rows(run_root: Path, arms: tuple[str, ...] = ARMS) -> list[dict[str, Any]]:
    rows = []
    for repeat in REPEATS:
        for arm in arms:
            directory = run_root / str(repeat) / arm
            trace_path = directory / "trace.json"
            if not trace_path.is_file():
                raise FileNotFoundError(f"generation trace is missing: {trace_path}")
            trace = _read(trace_path)
            candidate_path = directory / "candidate.json"
            candidate = None
            candidate_digest = None
            candidate_error = None
            if candidate_path.is_file():
                try:
                    java_evaluate.load_candidate(candidate_path, "jib")
                except (ValueError, json.JSONDecodeError) as error:
                    candidate_error = {"type": type(error).__name__, "message": str(error)}
                else:
                    candidate = str(candidate_path.resolve())
                    candidate_digest = _digest(candidate_path)
            if trace.get("status") == "generated" and candidate is None and candidate_error is None:
                candidate_error = {"type": "MissingCandidate", "message": "generated trace has no candidate"}
            rows.append({
                "repeat": repeat,
                "arm": arm,
                "subject_id": f"repeat-{repeat}-{arm.lower()}",
                "trace": str(trace_path.resolve()),
                "trace_sha256": _digest(trace_path),
                "trace_status": trace.get("status"),
                "candidate": candidate,
                "candidate_sha256": candidate_digest,
                "candidate_error": candidate_error,
            })
    return rows


def prepare(
    run_root: Path, output: Path, arms: tuple[str, ...] = ARMS,
) -> dict[str, Any]:
    """Freeze the exact 12 attempts before any candidate is executed."""
    if output.exists():
        raise FileExistsError("evaluation output already exists")
    generation_freeze_path = run_root / "freeze.json"
    if not generation_freeze_path.is_file():
        raise FileNotFoundError("generation freeze.json is absent")
    generation_freeze = _read(generation_freeze_path)
    expected_attempts = len(arms) * len(REPEATS)
    if generation_freeze.get("attempts") != expected_attempts:
        raise ValueError(f"generation freeze does not declare the complete {expected_attempts}-attempt matrix")
    frozen_paths = generation_freeze.get("sha256")
    if not isinstance(frozen_paths, dict):
        raise ValueError("generation freeze lacks artifact digests")
    for relative, expected in frozen_paths.items():
        artifact = run_root / relative
        if not artifact.is_file() or _digest(artifact) != expected:
            raise ValueError(f"artifact is not bound by generation freeze: {relative}")
    rows = _generation_rows(run_root, arms)
    for row in rows:
        trace = Path(row["trace"])
        trace_key = str(trace.relative_to(run_root.resolve()))
        if frozen_paths.get(trace_key) != row["trace_sha256"]:
            raise ValueError(f"trace is not bound by generation freeze: {trace_key}")
        if row["candidate"] is not None:
            candidate = Path(row["candidate"])
            candidate_key = str(candidate.relative_to(run_root.resolve()))
            if frozen_paths.get(candidate_key) != row["candidate_sha256"]:
                raise ValueError(f"candidate is not bound by generation freeze: {candidate_key}")
    manifest = {
        "schema_version": "mockspec.issue35.jib-navigation-evaluation-freeze/v1",
        "generation_run_root": str(run_root.resolve()),
        "generation_freeze_sha256": _digest(generation_freeze_path),
        "attempts": expected_attempts,
        "available_candidates": sum(row["candidate"] is not None for row in rows),
        "arms": list(arms),
        "mutants": list(MUTANTS),
        "rows": rows,
    }
    output.mkdir(parents=True)
    _write(output / "freeze.json", manifest)
    return manifest


def _validate(run_root: Path, output: Path) -> dict[str, Any]:
    manifest = _read(output / "freeze.json")
    if manifest.get("generation_run_root") != str(run_root.resolve()):
        raise ValueError("generation root differs from evaluation freeze")
    generation_freeze = run_root / "freeze.json"
    if _digest(generation_freeze) != manifest.get("generation_freeze_sha256"):
        raise ValueError("generation freeze identity changed")
    if _generation_rows(run_root, tuple(manifest["arms"])) != manifest.get("rows"):
        raise ValueError("generation artifacts changed after evaluation freeze")
    return manifest


def _status(receipt: dict[str, Any]) -> str:
    return str(receipt.get("status", "driver_error"))


def _run_subject(
    subject: dict[str, Any], output: Path, old_root: Path,
) -> list[dict[str, Any]]:
    records = []
    if subject.get("candidate") is None:
        for mutant in MUTANTS:
            receipt = output / "runs" / subject["subject_id"] / mutant / "result.json"
            value = {
                "schema_version": "mockspec.issue35.jib-navigation-missing/v1",
                "subject_id": subject["subject_id"], "mutant": mutant,
                "status": "missing_candidate_generation",
                "trace_status": subject.get("trace_status"),
                "candidate_error": subject.get("candidate_error"),
            }
            _write(receipt, value)
            records.append({"mutant": mutant, "status": value["status"], "receipt": str(receipt)})
        return records
    candidate = Path(subject["candidate"])
    for mutant in MUTANTS:
        receipt_dir = output / "runs" / subject["subject_id"] / mutant
        receipt_path = receipt_dir / "result.json"
        if receipt_path.is_file():
            value = _read(receipt_path)
        else:
            try:
                value = java_evaluate.evaluate("jib", mutant, receipt_dir, old_root, candidate)
            except Exception as error:
                value = {
                    "schema_version": "mockspec.issue35.jib-navigation-driver-error/v1",
                    "subject_id": subject["subject_id"], "mutant": mutant,
                    "status": "driver_error", "error_type": type(error).__name__,
                    "message": str(error),
                }
                _write(receipt_path, value)
        records.append({"mutant": mutant, "status": _status(value), "receipt": str(receipt_path)})
        if mutant == "original" and _status(value) in {"compile_failed", "compile_timeout"}:
            for skipped in MUTANTS[1:]:
                skipped_path = output / "runs" / subject["subject_id"] / skipped / "result.json"
                skipped_value = {
                    "schema_version": "mockspec.issue35.jib-navigation-skipped/v1",
                    "subject_id": subject["subject_id"], "mutant": skipped,
                    "status": "not_run_original_compile_failed",
                    "original_receipt": str(receipt_path),
                }
                _write(skipped_path, skipped_value)
                records.append({"mutant": skipped, "status": skipped_value["status"],
                                "receipt": str(skipped_path)})
            break
    return records


def _paired(manifest: dict[str, Any], records: list[dict[str, Any]]) -> dict[str, Any]:
    by_subject: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        by_subject.setdefault(record["subject_id"], []).append(record)
    subjects = []
    for row in manifest["rows"]:
        variants = by_subject[row["subject_id"]]
        original = next(item for item in variants if item["mutant"] == "original")
        eligible = original["status"] == "passed"
        detected = sum(
            item["mutant"] != "original"
            and item["status"] == "test_assertion_or_verification_failed"
            for item in variants
        ) if eligible else 0
        observed = sum(
            item["mutant"] != "original"
            and item["status"] in {"passed", "test_assertion_or_verification_failed"}
            for item in variants
        ) if eligible else 0
        subjects.append({
            "subject_id": row["subject_id"], "repeat": row["repeat"], "arm": row["arm"],
            "candidate_available": row["candidate"] is not None,
            "status": (
                "missing" if row["candidate"] is None else
                "original_failed" if not eligible else
                "mutation_incomplete" if observed != 3 else "complete"
            ),
            "original_status": original["status"], "original_passed": eligible,
            "main_score_eligible": eligible,
            "mutation_detected": detected if eligible else None,
            "mutation_valid": observed if eligible else 0,
            "mutation_denominator": 3,
            "missing_mutants": 3 - observed if eligible else 3,
            "mutation_score": detected / 3 if eligible and observed == 3 else None,
            "variants": variants,
        })
    historical_path = ISSUE35 / "java_results/paired.json"
    historical = _read(historical_path)
    reference = next(
        row for row in historical["subjects"]
        if row["case"] == "jib" and row["subject_id"] == "developer-reference"
    )
    if (
        not reference["main_score_eligible"]
        or reference["valid_mutation_denominator"] != 3
        or reference["mutation_detected"] != 3
    ):
        raise ValueError("the frozen #35 Jib reference is not a valid 3/3 baseline")
    return {
        "schema_version": "mockspec.issue35.jib-navigation-execution/v1",
        "design": {
            "layout": {"O": "original", "P": "path-oriented", "D": "direct-edge"},
            "details": {"C": "compact", "R": "restored"},
        },
        "fixed_mutation_denominator": 3,
        "reference": {
            "source": str(historical_path),
            "source_sha256": _digest(historical_path),
            "original_passed": True,
            "mutation_detected": 3,
            "mutation_denominator": 3,
            "mutation_score": 1.0,
        },
        "subjects": subjects,
    }


def execute(run_root: Path, output: Path, old_root: Path) -> dict[str, Any]:
    manifest = _validate(run_root, output)
    records = []
    for subject in manifest["rows"]:
        for result in _run_subject(subject, output, old_root):
            records.append({"subject_id": subject["subject_id"], **result})
        _write(output / "progress.json", {"completed": records})
    result = _paired(manifest, records)
    _write(output / "execution.json", result)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "execute"))
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--old-root", type=Path, default=Path("/home/haon/projects/Stub-Tester"))
    parser.add_argument("--design", choices=("factorial", "direct"), default="factorial")
    args = parser.parse_args()
    arms = ARMS if args.design == "factorial" else DIRECT_ARMS
    result = prepare(args.run_root, args.output, arms) if args.action == "prepare" else execute(
        args.run_root, args.output, args.old_root,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
