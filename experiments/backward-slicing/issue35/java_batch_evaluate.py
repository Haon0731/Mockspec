"""Freeze and execute the Issue #35 Java paired evaluation matrix."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
from typing import Any

import java_evaluate


HERE = Path(__file__).resolve().parent
CASES = ("zipkin", "jib")
ARMS = ("A", "B", "C")
REPEATS = (1, 2, 3)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def verify_generation_freeze(run_root: Path) -> dict:
    freeze_path = run_root / "freeze.json"
    if not freeze_path.is_file():
        raise FileNotFoundError("54-attempt generation freeze is absent")
    freeze = json.loads(freeze_path.read_text())
    if freeze.get("attempts") != 54 or len(freeze.get("sha256", {})) < 55:
        raise ValueError("generation freeze does not cover all 54 attempts")
    for relative, expected in freeze["sha256"].items():
        path = run_root / relative
        if not path.is_file() or digest(path) != expected:
            raise ValueError(f"generation artifact differs from freeze: {relative}")
    return freeze


def candidate_rows(run_root: Path, freeze: dict) -> list[dict[str, Any]]:
    rows = []
    for case in CASES:
        for repeat in REPEATS:
            for arm in ARMS:
                path = run_root / case / str(repeat) / arm / "candidate.json"
                trace_path = run_root / case / str(repeat) / arm / "trace.json"
                relative_trace = str(trace_path.relative_to(run_root))
                if relative_trace not in freeze["sha256"]:
                    raise ValueError(f"Java trace is absent from generation freeze: {relative_trace}")
                trace = json.loads(trace_path.read_text())
                row = {
                    "case": case, "repeat": repeat, "arm": arm,
                    "subject_id": f"repeat-{repeat}-{arm.lower()}",
                    "trace": str(trace_path), "trace_status": trace["status"],
                    "generation_error_type": trace.get("error_type"),
                    "generation_reason": trace.get("reason"),
                }
                relative_candidate = str(path.relative_to(run_root))
                if path.is_file() and relative_candidate in freeze["sha256"]:
                    try:
                        java_evaluate.load_candidate(path, case)
                    except (ValueError, json.JSONDecodeError) as error:
                        row.update(available=False, candidate=None, candidate_sha256=None,
                                   generation_error_type=type(error).__name__,
                                   generation_reason=str(error))
                    else:
                        row.update(available=True, candidate=str(path),
                                   candidate_sha256=digest(path))
                else:
                    row.update(available=False, candidate=None, candidate_sha256=None)
                rows.append(row)
    return rows


def prepare(run_root: Path, output: Path) -> dict:
    if output.exists():
        raise FileExistsError("batch output already exists")
    generation_freeze = verify_generation_freeze(run_root)
    rows = candidate_rows(run_root, generation_freeze)
    manifest = {
        "schema_version": "mockspec.issue35-java-batch/v1",
        "status": "frozen",
        "run_root": str(run_root.resolve()),
        "candidate_count": len(rows),
        "available_candidate_count": sum(row["available"] for row in rows),
        "missing_candidate_count": sum(not row["available"] for row in rows),
        "expected_candidate_count": 18,
        "variants": {
            case: ["original", *java_evaluate.MUTATIONS[case]] for case in CASES
        },
        "subjects_per_case": 10,
        "runs_per_case": 40,
        "total_runs": 80,
        "candidates": rows,
        "generation_freeze_sha256": digest(run_root / "freeze.json"),
    }
    output.mkdir(parents=True)
    write_json(output / "freeze.json", manifest)
    return manifest


def validate_freeze(run_root: Path, output: Path) -> dict:
    manifest = json.loads((output / "freeze.json").read_text())
    if manifest["run_root"] != str(run_root.resolve()):
        raise ValueError("run root differs from frozen manifest")
    generation_freeze = verify_generation_freeze(run_root)
    if digest(run_root / "freeze.json") != manifest["generation_freeze_sha256"]:
        raise ValueError("generation freeze identity changed")
    current = candidate_rows(run_root, generation_freeze)
    expected = manifest["candidates"]
    if current != expected:
        raise ValueError("candidate set or content changed after freeze")
    return manifest


def skipped_result(case: str, subject: dict, mutant: str, original: dict) -> dict:
    return {
        "schema_version": "mockspec.issue35-java-skipped/v1",
        "case": case,
        "subject_id": subject["subject_id"],
        "mutant": mutant,
        "status": "not_run_original_compile_failed",
        "primary_mutation_score_eligible": False,
        "reason": (
            "The same frozen candidate failed to compile on the unmutated product. "
            "The product mutant was not applied, so the failure is independent of that mutant."
        ),
        "original_receipt": original["receipt"],
        "original_status": original["status"],
    }


def execute_subject(
    case: str, subject: dict, output: Path, old_root: Path,
) -> list[dict]:
    results = []
    variants = ["original", *java_evaluate.MUTATIONS[case]]
    if subject.get("available") is False:
        for mutant in variants:
            receipt = output / "runs" / case / subject["subject_id"] / mutant / "result.json"
            result = {
                "schema_version": "mockspec.issue35-java-missing-candidate/v1",
                "case": case, "subject_id": subject["subject_id"], "mutant": mutant,
                "status": "missing_candidate_generation",
                "generation_trace": subject["trace"],
                "generation_status": subject["trace_status"],
                "generation_error_type": subject.get("generation_error_type"),
                "generation_reason": subject.get("generation_reason"),
                "primary_mutation_score_eligible": False,
            }
            write_json(receipt, result)
            results.append({
                "case": case, "subject_id": subject["subject_id"], "mutant": mutant,
                "status": result["status"], "receipt": str(receipt),
                "primary_mutation_score_eligible": False,
            })
        return results
    original_record = None
    for mutant in variants:
        receipt_dir = output / "runs" / case / subject["subject_id"] / mutant
        receipt_path = receipt_dir / "result.json"
        if receipt_path.is_file():
            result = json.loads(receipt_path.read_text())
        elif original_record and original_record["status"] in {
            "compile_failed", "compile_timeout"
        }:
            result = skipped_result(case, subject, mutant, original_record)
            write_json(receipt_path, result)
        else:
            candidate = None if subject["subject_id"] == "developer-reference" else Path(
                subject["candidate"]
            )
            try:
                result = java_evaluate.evaluate(
                    case, mutant, receipt_dir, old_root, candidate
                )
            except Exception as error:
                result = {
                    "schema_version": "mockspec.issue35-java-driver-error/v1",
                    "case": case, "subject_id": subject["subject_id"],
                    "mutant": mutant, "status": "fixture_or_infrastructure_failed",
                    "error_type": type(error).__name__, "error": str(error),
                }
                write_json(receipt_dir / "driver-error.json", result)
        if "compile" in result:
            normalized = java_evaluate.result_status(result["compile"], result.get("test"))
            if normalized != result.get("status"):
                result["status"] = normalized
                result["status_reclassified_from_raw_logs"] = True
                write_json(receipt_path, result)
        record = {
            "case": case, "subject_id": subject["subject_id"], "mutant": mutant,
            "status": result["status"],
            "receipt": str(receipt_path if receipt_path.is_file()
                           else receipt_dir / "driver-error.json"),
            "primary_mutation_score_eligible": None,
        }
        if mutant == "original":
            record["primary_mutation_score_eligible"] = result["status"] == "passed"
            original_record = record
        else:
            record["primary_mutation_score_eligible"] = bool(
                original_record and original_record["status"] == "passed"
            )
        results.append(record)
    return results


def exception_type(result: dict) -> str | None:
    if result.get("error_type"):
        return result["error_type"]
    status = result.get("status")
    if status == "passed" or status == "missing_candidate_generation":
        return None
    stage = "compile" if status in {"compile_failed", "compile_timeout"} else "test"
    streams = "\n".join(
        str((result.get(stage) or {}).get(field, "")) for field in ("stdout", "stderr")
    )
    matches = re.findall(
        r"(?:org\.mockito\.exceptions\.verification\.[A-Z][\w$]*|"
        r"(?:[a-zA-Z_$][\w$]*\.)+"
        r"(?:AssertionError|[A-Z][\w$]*(?:Exception|Error)))",
        streams,
    )
    if matches:
        return matches[0]
    if status == "compile_failed":
        return "javac_compile_error"
    return None


def paired_results(output: Path, results: list[dict]) -> dict:
    by_subject: dict[tuple[str, str], list[dict]] = {}
    for row in results:
        by_subject.setdefault((row["case"], row["subject_id"]), []).append(row)
    reference_valid_mutants: dict[str, set[str]] = {}
    for (case, subject_id), rows in by_subject.items():
        if subject_id != "developer-reference":
            continue
        original = next(row for row in rows if row["mutant"] == "original")
        reference_valid_mutants[case] = {
            row["mutant"] for row in rows
            if row["mutant"] != "original"
            and original["status"] == "passed"
            and row["status"] in {"passed", "test_assertion_or_verification_failed"}
        }
    subjects = []
    for (case, subject_id), rows in by_subject.items():
        variants = []
        original = next(row for row in rows if row["mutant"] == "original")
        original_receipt = json.loads(Path(original["receipt"]).read_text())
        eligible = original["status"] == "passed"
        fixed_mutants = reference_valid_mutants.get(case, set())
        fixed_denominator = len(fixed_mutants)
        detected = 0
        missing_fixed_mutants = []
        for row in rows:
            receipt = json.loads(Path(row["receipt"]).read_text())
            is_mutant = row["mutant"] != "original"
            fixed = bool(is_mutant and row["mutant"] in fixed_mutants)
            observed = row["status"] in {"passed", "test_assertion_or_verification_failed"}
            valid = bool(fixed and eligible and observed)
            killed = bool(valid and row["status"] == "test_assertion_or_verification_failed")
            if fixed and eligible and not observed:
                missing_fixed_mutants.append(row["mutant"])
            detected += int(killed)
            variants.append({
                "mutant": row["mutant"], "status": row["status"],
                "row_passed": row["status"] == "passed",
                "fixed_valid_mutant": fixed, "observed_pair": valid,
                "mutation_detected": killed,
                "exception_type": exception_type(receipt), "receipt": row["receipt"],
            })
        subjects.append({
            "case": case, "subject_id": subject_id,
            "main_score_eligible": eligible,
            "original_rows_passed": 1 if eligible else 0,
            "original_rows_total": 1,
            "mutation_detected": detected,
            "valid_mutation_denominator": fixed_denominator,
            "missing_fixed_mutants": missing_fixed_mutants,
            "mutation_score": (
                detected / fixed_denominator
                if eligible and fixed_denominator and not missing_fixed_mutants else None
            ),
            "original_exception_type": exception_type(original_receipt),
            "variants": variants,
        })
    return {
        "schema_version": "mockspec.issue35-java-paired/v1",
        "subjects": subjects,
        "subject_count": len(subjects),
        "main_eligible_count": sum(item["main_score_eligible"] for item in subjects),
        "total_detected": sum(item["mutation_detected"] for item in subjects),
        "fixed_valid_mutants": {
            case: sorted(mutants) for case, mutants in reference_valid_mutants.items()
        },
        "total_valid_denominator_for_main_eligible": sum(
            item["valid_mutation_denominator"]
            for item in subjects if item["main_score_eligible"]
        ),
    }


def execute(run_root: Path, output: Path, old_root: Path) -> dict:
    manifest = validate_freeze(run_root, output)
    candidate_by_case = {
        case: [row for row in manifest["candidates"] if row["case"] == case]
        for case in CASES
    }
    results: list[dict] = []
    for case in CASES:
        subjects = [{"case": case, "subject_id": "developer-reference"}]
        subjects.extend(candidate_by_case[case])
        for subject in subjects:
            results.extend(execute_subject(case, subject, output, old_root))
            write_json(output / "progress.json", {"completed": results})
    summary = {
        "schema_version": "mockspec.issue35-java-batch-summary/v1",
        "status": "complete",
        "expected_runs": 80,
        "recorded_runs": len(results),
        "status_counts": {
            status: sum(row["status"] == status for row in results)
            for status in sorted({row["status"] for row in results})
        },
        "results": results,
    }
    write_json(output / "summary.json", summary)
    write_json(output / "paired.json", paired_results(output, results))
    return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("prepare", "execute"))
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--old-root", type=Path, default=Path("/home/haon/projects/Stub-Tester"))
    args = parser.parse_args()
    result = (prepare(args.run_root, args.output) if args.mode == "prepare" else
              execute(args.run_root, args.output, args.old_root))
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
