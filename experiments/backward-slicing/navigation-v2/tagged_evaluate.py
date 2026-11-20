"""Evaluate the frozen seven-case tagged-delivery Base/v2 matrix."""

from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import importlib.util
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
import python_batch  # noqa: E402
import python_eval  # noqa: E402


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load evaluator: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


legacy = _load_module("navigation_v2_legacy_evaluate", HERE / "evaluate.py")

CASES = (
    "flask_308",
    "jib",
    "zipkin",
    "langflow_189",
    "ansible_119",
    "click_423",
    "click_402",
)
JAVA_CASES = ("jib", "zipkin")
PYTHON_CASES = tuple(python_eval.CASES)
ARMS = ("base", "v2")
REPEATS = (1, 2, 3)
EXPECTED_ATTEMPTS = len(CASES) * len(ARMS) * len(REPEATS)
MUTANTS = ("original", *legacy.flask_evaluate.MUTANTS)


def _read(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _candidate_error(path: Path) -> dict[str, str] | None:
    try:
        value = _read(path)
        if not isinstance(value, dict) or set(value) != {"slots", "rationale"}:
            raise ValueError("candidate requires exactly slots and rationale")
        if not isinstance(value["slots"], dict) or any(
            not isinstance(item, str) for item in value["slots"].values()
        ):
            raise ValueError("candidate slots must map names to strings")
        if value["rationale"] != []:
            raise ValueError(
                "tagged transport must supply an empty host-side rationale"
            )
    except (OSError, ValueError, json.JSONDecodeError) as error:
        return {"type": type(error).__name__, "message": str(error)}
    return None


def matrix_rows(run_root: Path) -> list[dict[str, Any]]:
    rows = []
    for case in CASES:
        for repeat in REPEATS:
            for arm in ARMS:
                directory = run_root / case / str(repeat) / arm
                trace_path = directory / "trace.json"
                if not trace_path.is_file():
                    raise FileNotFoundError(
                        f"generation trace is missing: {trace_path}"
                    )
                trace = _read(trace_path)
                candidate_path = directory / "candidate.json"
                candidate = candidate_path if candidate_path.is_file() else None
                error = _candidate_error(candidate) if candidate else None
                if trace.get("status") == "generated" and candidate is None:
                    error = {
                        "type": "MissingCandidate",
                        "message": "generated trace has no candidate",
                    }
                rows.append(
                    {
                        "case": case,
                        "repeat": repeat,
                        "arm": arm,
                        "subject_id": f"{case}-repeat-{repeat}-{arm}",
                        "trace": str(trace_path.resolve()),
                        "trace_sha256": _digest(trace_path),
                        "trace_status": trace.get("status"),
                        "candidate": str(candidate.resolve()) if candidate else None,
                        "candidate_sha256": _digest(candidate) if candidate else None,
                        "candidate_error": error,
                    }
                )
    return rows


def validate_freeze(run_root: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    freeze_path = run_root / "freeze.json"
    freeze = _read(freeze_path)
    if freeze.get("attempts") != EXPECTED_ATTEMPTS:
        raise ValueError(f"freeze must declare exactly {EXPECTED_ATTEMPTS} attempts")
    frozen = freeze.get("sha256")
    if not isinstance(freeze.get("statuses"), dict) or not isinstance(frozen, dict):
        raise ValueError("freeze must contain statuses and sha256 maps")
    for relative, expected in frozen.items():
        artifact = run_root / relative
        if not artifact.is_file() or _digest(artifact) != expected:
            raise ValueError(f"artifact differs from generation freeze: {relative}")
    rows = matrix_rows(run_root)
    observed = Counter(row["trace_status"] for row in rows)
    if dict(observed) != freeze["statuses"]:
        raise ValueError("trace statuses differ from freeze summary")
    resolved = run_root.resolve()
    for row in rows:
        for field in ("trace", "candidate"):
            if row[field] is None:
                continue
            relative = Path(row[field]).relative_to(resolved).as_posix()
            if frozen.get(relative) != row[f"{field}_sha256"]:
                raise ValueError(f"artifact is not bound by freeze: {relative}")
    return freeze, rows


def _missing(row: dict[str, Any], mutants: tuple[str, ...]) -> list[dict[str, Any]]:
    status = "invalid_candidate" if row["candidate"] else "missing_candidate_generation"
    return [
        {
            "mutant": mutant,
            "status": status,
            "candidate_error": row["candidate_error"],
            "trace_status": row["trace_status"],
        }
        for mutant in mutants
    ]


def _evaluate_java(
    row: dict[str, Any], work_root: Path, old_root: Path
) -> list[dict[str, Any]]:
    mutants = ("original", *java_evaluate.MUTATIONS[row["case"]])
    if row["candidate"] is None or row["candidate_error"]:
        return _missing(row, mutants)
    results = []
    original_failed = False
    for mutant in mutants:
        if original_failed:
            results.append(
                {"mutant": mutant, "status": "not_run_original_compile_failed"}
            )
            continue
        output = work_root / "java" / row["subject_id"] / mutant
        try:
            receipt = java_evaluate.evaluate(
                row["case"], mutant, output, old_root, Path(row["candidate"])
            )
        except Exception as error:
            receipt = {
                "status": "driver_error",
                "error_type": type(error).__name__,
                "message": str(error),
            }
            _write(output / "result.json", receipt)
        status = receipt.get("status", "driver_error")
        results.append(
            {
                "mutant": mutant,
                "status": status,
                "receipt": str((output / "result.json").resolve()),
            }
        )
        original_failed = mutant == "original" and status in {
            "compile_failed",
            "compile_timeout",
        }
    return results


def _summarize_java(
    row: dict[str, Any], variants: list[dict[str, Any]]
) -> dict[str, Any]:
    original = variants[0]
    eligible = original["status"] == "passed"
    observed = [
        item
        for item in variants[1:]
        if item["status"] in {"passed", "test_assertion_or_verification_failed"}
    ]
    detected = sum(
        item["status"] == "test_assertion_or_verification_failed" for item in observed
    )
    return {
        **{key: row[key] for key in ("case", "repeat", "arm", "subject_id")},
        "candidate_available": row["candidate"] is not None,
        "candidate_error": row["candidate_error"],
        "original_passed_rows": int(eligible),
        "original_total_rows": 1,
        "original_passed": eligible,
        "mutation_detected": detected if eligible else None,
        "mutation_valid": len(observed) if eligible else 0,
        "mutation_denominator": 3,
        "mutation_score": detected / 3 if eligible and len(observed) == 3 else None,
        "variants": variants,
    }


def _evaluate_python(
    rows: list[dict[str, Any]],
    work_root: Path,
    registry_root: Path,
    generation_freeze_sha256: str,
) -> list[dict[str, Any]]:
    output = work_root / "python"
    output.mkdir(parents=True)
    ledger: dict[str, Any] = {
        "references": {},
        "candidates": {},
        "generation_failures": {},
    }
    row_by_variant = {}
    for row in rows:
        variant = f"{row['case']}/{row['repeat']}/{row['arm']}"
        row_by_variant[variant] = row
        if row["candidate"] is None or row["candidate_error"]:
            ledger["generation_failures"][variant] = {
                "status": "generation_failure",
                "trace": row["trace"],
                "trace_sha256": row["trace_sha256"],
                "candidate_error": row["candidate_error"],
            }
            continue
        try:
            ledger["candidates"][variant] = python_batch._run_variant(
                row["case"],
                Path(row["candidate"]),
                f"tagged/{row['repeat']}/{row['arm']}",
                output / "raw/candidate" / variant,
                registry_root,
            )
        except Exception as error:  # Preserve all other frozen subjects.
            ledger["candidates"][variant] = {
                "status": "driver_error",
                "error_type": type(error).__name__,
                "message": str(error),
            }
        _write(output / "execution-ledger.json", ledger)
    _write(output / "execution-ledger.json", ledger)
    summary = python_batch.summarize(output)
    # python_batch is deliberately reused unchanged and therefore initially
    # writes the historical #35 generation hash. Bind this derived copy to the
    # tagged matrix that was actually executed.
    summary["generation_freeze_sha256"] = generation_freeze_sha256
    _write(output / "paired.json", summary)
    by_variant = {item["variant"]: item for item in summary["results"]}
    calibration = _read(ISSUE35 / "python_eval_calibration/summary.json")
    expected_rows = {
        item["case_key"]: len(item["row_identity"]) for item in calibration["runs"]
    }
    subjects = []
    for variant, row in row_by_variant.items():
        result = by_variant[variant]
        subjects.append(
            {
                **{key: row[key] for key in ("case", "repeat", "arm", "subject_id")},
                "candidate_available": row["candidate"] is not None,
                "candidate_error": row["candidate_error"],
                "original_passed_rows": (result.get("original") or {}).get("passed", 0),
                "original_total_rows": expected_rows[row["case"]],
                "original_passed": result["original_full_pass"],
                "mutation_detected": result["raw_detected"],
                "mutation_valid": (
                    result["legal_denominator"] - result["missing_mutants"]
                ),
                "mutation_denominator": result["legal_denominator"],
                "mutation_score": result["mutation_score"],
                "python_summary": result,
            }
        )
    return subjects


def _evaluate_flask(
    rows: list[dict[str, Any]], work_root: Path, old_root: Path
) -> list[dict[str, Any]]:
    subjects = []
    for repeat in REPEATS:
        pair = [row for row in rows if row["repeat"] == repeat]
        translated = []
        for row in pair:
            translated.append({**row, "arm": "v1" if row["arm"] == "base" else "v2"})
        # The unchanged historical runner needs three named variants.  The third
        # wrapper duplicates Base and its receipts are ignored.
        duplicate = {**translated[0], "arm": "ablation", "subject_id": "unused"}
        results = legacy._evaluate_flask_repeat(
            [*translated, duplicate], work_root / "flask", old_root
        )
        for row, projected in zip(pair, translated, strict=True):
            variants = results[projected["subject_id"]]
            summary = legacy._summarize_flask(projected, variants)
            subjects.append(
                {
                    **summary,
                    "arm": row["arm"],
                    "subject_id": row["subject_id"],
                }
            )
    return subjects


def evaluate(run_root: Path, output: Path, old_root: Path) -> dict[str, Any]:
    freeze, rows = validate_freeze(run_root)
    work_root = output.parent / f"{output.stem}-artifacts"
    if output.exists() or work_root.exists():
        raise FileExistsError("evaluation output already exists")
    work_root.mkdir(parents=True)
    subjects = []
    for row in (item for item in rows if item["case"] in JAVA_CASES):
        subjects.append(_summarize_java(row, _evaluate_java(row, work_root, old_root)))
        _write(work_root / "progress.json", {"subjects": subjects})
    python_rows = [item for item in rows if item["case"] in PYTHON_CASES]
    subjects.extend(
        _evaluate_python(
            python_rows,
            work_root,
            old_root / ".omx/developer-realistic-registry-max",
            _digest(run_root / "freeze.json"),
        )
    )
    _write(work_root / "progress.json", {"subjects": subjects})
    flask_rows = [item for item in rows if item["case"] == "flask_308"]
    subjects.extend(_evaluate_flask(flask_rows, work_root, old_root))
    result = {
        "schema_version": "mockspec.navigation-v2-tagged-evaluation/v1",
        "generation_run_root": str(run_root.resolve()),
        "generation_freeze_sha256": _digest(run_root / "freeze.json"),
        "attempts": EXPECTED_ATTEMPTS,
        "statuses": freeze["statuses"],
        "cases": list(CASES),
        "arms": list(ARMS),
        "repeats": list(REPEATS),
        "container_execution": "serial through shared docker lock",
        "subjects": subjects,
    }
    _write(output, result)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-root", type=Path, default=HERE / "tagged/run")
    parser.add_argument("--output", type=Path, default=HERE / "tagged/evaluation.json")
    parser.add_argument(
        "--old-root", type=Path, default=Path("/home/haon/projects/Stub-Tester")
    )
    parser.add_argument("--validate-only", action="store_true")
    args = parser.parse_args()
    if args.validate_only:
        freeze, rows = validate_freeze(args.run_root)
        print(
            json.dumps(
                {
                    "attempts": freeze["attempts"],
                    "statuses": freeze["statuses"],
                    "candidate_errors": sum(
                        row["candidate_error"] is not None for row in rows
                    ),
                },
                sort_keys=True,
            )
        )
    else:
        evaluate(args.run_root, args.output, args.old_root)


if __name__ == "__main__":
    main()
