"""Evaluate the frozen navigation-v2 Jib and Flask generation matrix.

The driver deliberately reuses the frozen #35 Java evaluator and the #34
round-2 Flask evaluator.  It adds only matrix-freeze validation, the new
top-level candidate envelope, and a common result summary.
"""

from __future__ import annotations

import argparse
from contextlib import redirect_stdout
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import sys
from typing import Any


HERE = Path(__file__).resolve().parent
BACKWARD_SLICING = HERE.parent
ISSUE35 = BACKWARD_SLICING / "issue35"
sys.path.insert(0, str(ISSUE35))
sys.path.insert(0, str(BACKWARD_SLICING))

import java_evaluate  # noqa: E402


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load evaluator: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


flask_evaluate = _load_module("navigation_v2_flask_evaluate", BACKWARD_SLICING / "evaluate.py")

CASES = ("jib", "flask_308")
REPEATS = (1, 2, 3)
ARMS = ("v1", "ablation", "v2")
EXPECTED_ATTEMPTS = len(CASES) * len(REPEATS) * len(ARMS)
JIB_MUTANTS = ("original", *java_evaluate.MUTATIONS["jib"])
FLASK_MUTANTS = ("original", *flask_evaluate.MUTANTS)
FLASK_VARIANTS = dict(zip(ARMS, ("a", "b", "c"), strict=True))


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _candidate_error(path: Path, case: str) -> dict[str, str] | None:
    try:
        if case == "jib":
            java_evaluate.load_candidate(path, "jib")
        else:
            value = _read(path)
            if not isinstance(value, dict) or set(value) != {"slots", "rationale"}:
                raise ValueError("candidate requires exactly slots and rationale")
            if not isinstance(value["slots"], dict):
                raise ValueError("candidate slots must be an object")
            if any(not isinstance(item, str) for item in value["slots"].values()):
                raise ValueError("candidate slots must be strings")
            if not isinstance(value["rationale"], list) or any(
                not isinstance(item, str) for item in value["rationale"]
            ):
                raise ValueError("candidate rationale must be a list of strings")
    except (OSError, ValueError, json.JSONDecodeError) as error:
        return {"type": type(error).__name__, "message": str(error)}
    return None


def _matrix_rows(run_root: Path) -> list[dict[str, Any]]:
    rows = []
    for case in CASES:
        for repeat in REPEATS:
            for arm in ARMS:
                directory = run_root / case / str(repeat) / arm
                trace_path = directory / "trace.json"
                if not trace_path.is_file():
                    raise FileNotFoundError(f"generation trace is missing: {trace_path}")
                trace = _read(trace_path)
                candidate_path = directory / "candidate.json"
                candidate = str(candidate_path.resolve()) if candidate_path.is_file() else None
                error = _candidate_error(candidate_path, case) if candidate else None
                if trace.get("status") == "generated" and candidate is None:
                    error = {"type": "MissingCandidate", "message": "generated trace has no candidate"}
                rows.append({
                    "case": case,
                    "repeat": repeat,
                    "arm": arm,
                    "subject_id": f"{case}-repeat-{repeat}-{arm}",
                    "trace": str(trace_path.resolve()),
                    "trace_sha256": _digest(trace_path),
                    "trace_status": trace.get("status"),
                    "candidate": candidate,
                    "candidate_sha256": _digest(candidate_path) if candidate else None,
                    "candidate_error": error,
                })
    return rows


def validate_freeze(run_root: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Validate the complete 18-attempt matrix before executing any candidate."""
    freeze_path = run_root / "freeze.json"
    freeze = _read(freeze_path)
    if freeze.get("attempts") != EXPECTED_ATTEMPTS:
        raise ValueError(f"freeze.json must declare exactly {EXPECTED_ATTEMPTS} attempts")
    if not isinstance(freeze.get("statuses"), dict):
        raise ValueError("freeze.json must contain statuses")
    frozen = freeze.get("sha256")
    if not isinstance(frozen, dict):
        raise ValueError("freeze.json must contain artifact hashes")
    for relative, expected in frozen.items():
        artifact = run_root / relative
        if not artifact.is_file() or _digest(artifact) != expected:
            raise ValueError(f"artifact differs from generation freeze: {relative}")
    rows = _matrix_rows(run_root)
    resolved_root = run_root.resolve()
    for row in rows:
        for field in ("trace", "candidate"):
            if row[field] is None:
                continue
            artifact = Path(row[field])
            relative = artifact.relative_to(resolved_root).as_posix()
            if frozen.get(relative) != row[f"{field}_sha256"]:
                raise ValueError(f"artifact is not bound by generation freeze: {relative}")
    return freeze, rows


def _missing_variants(row: dict[str, Any], mutants: tuple[str, ...]) -> list[dict[str, Any]]:
    status = "invalid_candidate" if row.get("candidate") is not None else "missing_candidate_generation"
    return [{
        "mutant": mutant,
        "status": status,
        "candidate_error": row.get("candidate_error"),
        "trace_status": row.get("trace_status"),
    } for mutant in mutants]


def _evaluate_jib(row: dict[str, Any], work_root: Path, old_root: Path) -> list[dict[str, Any]]:
    if row.get("candidate") is None or row.get("candidate_error"):
        return _missing_variants(row, JIB_MUTANTS)
    variants = []
    for mutant in JIB_MUTANTS:
        output = work_root / "runs" / row["subject_id"] / mutant
        try:
            receipt = java_evaluate.evaluate(
                "jib", mutant, output, old_root, Path(row["candidate"]),
            )
        except Exception as error:  # Preserve the other frozen subjects.
            receipt = {
                "status": "driver_error",
                "error_type": type(error).__name__,
                "message": str(error),
            }
            _write(output / "result.json", receipt)
        variants.append({
            "mutant": mutant,
            "status": receipt.get("status", "driver_error"),
            "receipt": str((output / "result.json").resolve()),
        })
        if mutant == "original" and receipt.get("status") in {"compile_failed", "compile_timeout"}:
            variants.extend({
                "mutant": skipped,
                "status": "not_run_original_compile_failed",
                "receipt": None,
            } for skipped in JIB_MUTANTS[1:])
            break
    return variants


def _flask_wrappers(
    rows: list[dict[str, Any]], wrapper_dir: Path,
) -> dict[str, str]:
    """Wrap new top-level candidates for the unchanged historical evaluator."""
    available = [
        row for row in rows
        if row.get("candidate") is not None and row.get("candidate_error") is None
    ]
    if not available:
        return {}
    fallback = available[0]
    source_for_variant: dict[str, str] = {}
    for row in rows:
        variant = FLASK_VARIANTS[row["arm"]]
        source = row if row.get("candidate") is not None and row.get("candidate_error") is None else fallback
        _write(wrapper_dir / f"generation-{variant}.json", {"flask": _read(Path(source["candidate"]))})
        source_for_variant[variant] = source["subject_id"]
    return source_for_variant


def _evaluate_flask_repeat(
    rows: list[dict[str, Any]], work_root: Path, old_root: Path,
) -> dict[str, list[dict[str, Any]]]:
    repeat = rows[0]["repeat"]
    wrapper_dir = work_root / "flask-wrapper" / str(repeat)
    mapping = _flask_wrappers(rows, wrapper_dir)
    results: dict[str, list[dict[str, Any]]] = {}
    if mapping:
        legacy_output = work_root / "flask-legacy" / str(repeat)
        try:
            # The Java evaluator uses this same host lock.  Holding it around the
            # legacy Flask batch makes all containers serial across both cases.
            with java_evaluate.docker_lock(), redirect_stdout(io.StringIO()):
                flask_evaluate.run(
                    old_root, wrapper_dir, legacy_output, separate_timeouts=True,
                )
            summary = _read(legacy_output / "generation-metrics.json")
        except Exception as error:  # Preserve diagnostics for every other repeat.
            summary = {"runs": {}, "candidate_errors": {
                variant: {"failure_category": "driver_error", "type": type(error).__name__,
                          "message": str(error)} for variant in FLASK_VARIANTS.values()
            }}
    else:
        summary = {"runs": {}, "candidate_errors": {}}

    for row in rows:
        if row.get("candidate") is None or row.get("candidate_error"):
            results[row["subject_id"]] = _missing_variants(row, FLASK_MUTANTS)
            continue
        variant = FLASK_VARIANTS[row["arm"]]
        candidate_error = summary.get("candidate_errors", {}).get(variant)
        if candidate_error:
            invalid = dict(row)
            invalid["candidate_error"] = candidate_error
            results[row["subject_id"]] = _missing_variants(invalid, FLASK_MUTANTS)
            continue
        variants = []
        for mutant in FLASK_MUTANTS:
            receipt = summary.get("runs", {}).get(f"{variant}/{mutant}")
            if receipt is None:
                receipt = {"failure_category": "driver_error", "valid_execution": False,
                           "passed": 0, "failed": 0, "parameter_rows": []}
            variants.append({
                "mutant": mutant,
                "status": receipt.get("failure_category", "driver_error"),
                "valid_execution": receipt.get("valid_execution", False),
                "passed_rows": receipt.get("passed", 0),
                "failed_rows": receipt.get("failed", 0),
                "parameter_rows": receipt.get("parameter_rows", []),
                "receipt": str((legacy_output / f"eval-{variant}" / mutant / "execution.json").resolve()),
            })
        results[row["subject_id"]] = variants
    return results


def _summarize_jib(row: dict[str, Any], variants: list[dict[str, Any]]) -> dict[str, Any]:
    original = next(item for item in variants if item["mutant"] == "original")
    eligible = original["status"] == "passed"
    observed = [item for item in variants if item["mutant"] != "original" and item["status"] in {
        "passed", "test_assertion_or_verification_failed",
    }]
    detected = sum(item["status"] == "test_assertion_or_verification_failed" for item in observed)
    return {
        **{key: row[key] for key in ("case", "repeat", "arm", "subject_id")},
        "candidate_available": row.get("candidate") is not None,
        "candidate_error": row.get("candidate_error"),
        "original_passed_rows": 1 if eligible else 0,
        "original_total_rows": 1,
        "original_passed": eligible,
        "main_score_eligible": eligible,
        "mutation_detected": detected if eligible else None,
        "mutation_valid": len(observed) if eligible else 0,
        "mutation_denominator": 3,
        "mutation_score": detected / 3 if eligible and len(observed) == 3 else None,
        "variants": variants,
    }


def _summarize_flask(row: dict[str, Any], variants: list[dict[str, Any]]) -> dict[str, Any]:
    original = next(item for item in variants if item["mutant"] == "original")
    eligible = original.get("valid_execution", False) and original.get("passed_rows") == 5
    detected = 0
    observed = 0
    if eligible:
        for mutant in variants[1:]:
            if not mutant.get("valid_execution", False):
                continue
            observed += 1
            rows = mutant.get("parameter_rows", [])
            if any(item.get("outcome") == "failed" and item.get("exception_type") == "AssertionError"
                   for item in rows):
                detected += 1
    return {
        **{key: row[key] for key in ("case", "repeat", "arm", "subject_id")},
        "candidate_available": row.get("candidate") is not None,
        "candidate_error": row.get("candidate_error"),
        "original_passed_rows": original.get("passed_rows", 0),
        "original_total_rows": 5,
        "original_passed": eligible,
        "main_score_eligible": eligible,
        "mutation_detected": detected if eligible else None,
        "mutation_valid": observed if eligible else 0,
        "mutation_denominator": 3,
        "mutation_score": detected / 3 if eligible and observed == 3 else None,
        "variants": variants,
    }


def evaluate(run_root: Path, output: Path, old_root: Path) -> dict[str, Any]:
    freeze, rows = validate_freeze(run_root)
    work_root = output.parent / f"{output.stem}-artifacts"
    if output.exists() or work_root.exists():
        raise FileExistsError("evaluation output already exists")
    work_root.mkdir(parents=True)
    subjects = []
    for row in (item for item in rows if item["case"] == "jib"):
        subjects.append(_summarize_jib(row, _evaluate_jib(row, work_root, old_root)))
        _write(work_root / "progress.json", {"subjects": subjects})
    for repeat in REPEATS:
        repeat_rows = [row for row in rows if row["case"] == "flask_308" and row["repeat"] == repeat]
        repeat_results = _evaluate_flask_repeat(repeat_rows, work_root, old_root)
        for row in repeat_rows:
            subjects.append(_summarize_flask(row, repeat_results[row["subject_id"]]))
        _write(work_root / "progress.json", {"subjects": subjects})
    result = {
        "schema_version": "mockspec.navigation-v2-evaluation/v1",
        "generation_run_root": str(run_root.resolve()),
        "generation_freeze_sha256": _digest(run_root / "freeze.json"),
        "attempts": EXPECTED_ATTEMPTS,
        "statuses": freeze["statuses"],
        "cases": list(CASES),
        "arms": list(ARMS),
        "repeats": list(REPEATS),
        "fixed_mutation_denominator": 3,
        "container_execution": "serial",
        "subjects": subjects,
    }
    _write(output, result)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, help="default: RUN_ROOT/evaluation.json")
    parser.add_argument("--old-root", type=Path, default=Path("/home/haon/projects/Stub-Tester"))
    parser.add_argument("--validate-only", action="store_true")
    args = parser.parse_args()
    if args.validate_only:
        freeze, rows = validate_freeze(args.run_root)
        result = {"attempts": freeze["attempts"], "validated_rows": len(rows)}
    else:
        result = evaluate(args.run_root, args.output or args.run_root / "evaluation.json", args.old_root)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
