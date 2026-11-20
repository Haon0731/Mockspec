"""Prepare blinded review packets and summarize the issue #35 A/B/C study.

No candidate property is inferred from source text.  Behavioural and fidelity
metrics enter the report only through an explicit human review record.
"""

from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
from typing import Any


BLIND_SCHEMA = "mock-spec.issue35-blind-review/v1"
MAP_SCHEMA = "mock-spec.issue35-blind-map/v1"
SUMMARY_SCHEMA = "mock-spec.issue35-summary/v1"
ARMS = ("a", "b", "c")
EXPECTED_CASES = (
    "zipkin", "jib", "langflow_189", "ansible_119", "click_423", "click_402",
)
EXPECTED_REPEATS = ("1", "2", "3")
COMPARISONS = (("a", "c"), ("b", "c"))
METRICS = {
    "candidate_delivered": "higher",
    "original_all_passed": "higher",
    "f_stub_matched": "higher",
    "f_stub_decidable": "higher",
    "f_stub_rate": "higher",
    "mutation_detected": "higher",
    "mutation_valid": "higher",
    "mutation_score": "higher",
    "qualified_candidate": "higher",
    "source_calls": "lower",
    "input_bytes": "lower",
    "input_tokens": "lower",
    "output_tokens": "lower",
    "elapsed_seconds": "lower",
}


class ReportError(ValueError):
    """Generation or review artifacts violate the reporting contract."""


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ReportError(f"cannot read JSON artifact: {path}") from error


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def _attempt_directories(run_root: Path) -> list[tuple[str, str, str, Path]]:
    attempts = []
    if not run_root.is_dir():
        raise ReportError(f"run root is unavailable: {run_root}")
    case_dirs = {path.name: path for path in run_root.iterdir() if path.is_dir()}
    if set(case_dirs) != set(EXPECTED_CASES):
        raise ReportError("run root must contain exactly the six preregistered cases")
    for case in EXPECTED_CASES:
        case_dir = case_dirs[case]
        repeat_dirs = {path.name: path for path in case_dir.iterdir() if path.is_dir()}
        if set(repeat_dirs) != set(EXPECTED_REPEATS):
            raise ReportError(f"case must contain repeats 1, 2 and 3: {case}")
        for repeat in EXPECTED_REPEATS:
            repeat_dir = repeat_dirs[repeat]
            arm_dirs: dict[str, Path] = {}
            for path in repeat_dir.iterdir():
                if not path.is_dir() or path.name.lower() not in ARMS:
                    raise ReportError(f"unexpected arm directory: {path}")
                normalized = path.name.lower()
                if normalized in arm_dirs:
                    raise ReportError(f"duplicate arm directory differing by case: {path}")
                arm_dirs[normalized] = path
            if set(arm_dirs) != set(ARMS):
                raise ReportError(f"repeat must contain A, B and C: {case}/{repeat}")
            for arm in ARMS:
                attempts.append((case, repeat, arm, arm_dirs[arm]))
    if len(attempts) != 54:
        raise ReportError("generation matrix must contain exactly 54 attempts")
    return attempts


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _validate_freeze(
    run_root: Path, attempts: list[tuple[str, str, str, Path]],
) -> dict[str, str]:
    freeze = _read_json(run_root / "freeze.json")
    if freeze.get("attempts") != 54 or not isinstance(freeze.get("sha256"), dict):
        raise ReportError("freeze.json does not bind the complete 54-attempt matrix")
    digests = freeze["sha256"]
    statuses = Counter()
    for _, _, _, directory in attempts:
        trace = directory / "trace.json"
        if not trace.is_file():
            raise ReportError(f"attempt lacks trace.json: {directory}")
        trace_key = str(trace.relative_to(run_root))
        if digests.get(trace_key) != _sha256(trace):
            raise ReportError(f"trace differs from freeze.json: {trace_key}")
        statuses[_read_json(trace).get("status")] += 1
        candidate = directory / "candidate.json"
        candidate_key = str(candidate.relative_to(run_root))
        if candidate.is_file() and digests.get(candidate_key) != _sha256(candidate):
            raise ReportError(f"candidate differs from freeze.json: {candidate_key}")
        if not candidate.is_file() and candidate_key in digests:
            raise ReportError(f"freeze.json binds a missing candidate: {candidate_key}")
    if freeze.get("statuses") != dict(statuses):
        raise ReportError("freeze.json status counts differ from frozen traces")
    return digests


def _blind_id(salt: str, case: str, repeat: str, arm: str) -> str:
    material = f"{salt}\0{case}\0{repeat}\0{arm}".encode()
    return f"candidate-{hashlib.sha256(material).hexdigest()[:16]}"


def build_blind_review(
    run_root: Path, *, salt: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return a public review packet and a separate private arm mapping."""
    if not isinstance(salt, str) or not salt:
        raise ReportError("a non-empty blinding salt is required")
    attempts = _attempt_directories(run_root)
    _validate_freeze(run_root, attempts)
    public = []
    mapping = []
    seen = set()
    for case, repeat, arm, directory in attempts:
        blind_id = _blind_id(salt, case, repeat, arm)
        if blind_id in seen:
            raise ReportError(f"blind identifier collision: {blind_id}")
        seen.add(blind_id)
        candidate_path = directory / "candidate.json"
        candidate = _read_json(candidate_path) if candidate_path.is_file() else None
        candidate_sha256 = _sha256(candidate_path) if candidate_path.is_file() else None
        slots = candidate.get("slots") if isinstance(candidate, dict) else None
        if candidate is not None and (not isinstance(slots, dict) or not all(
            isinstance(key, str) and isinstance(value, str) for key, value in slots.items()
        )):
            raise ReportError(f"candidate lacks a valid slots object: {candidate_path}")
        public.append({
            "blind_id": blind_id,
            "case": case,
            "slots": slots,
            "review": {
                "status": "pending",
                "execution": None,
                "f_stub": None,
                "mutations": None,
                "qualified": None,
                "unsupported_assertions": None,
                "extra_constraints": None,
                "notes": None,
                "missing_reason": None,
            },
        })
        mapping.append({
            "blind_id": blind_id,
            "case": case,
            "repeat": repeat,
            "arm": arm,
            "trace": str((directory / "trace.json").relative_to(run_root)),
            "candidate": str(candidate_path.relative_to(run_root)),
            "candidate_sha256": candidate_sha256,
        })
    return (
        {"schema_version": BLIND_SCHEMA, "candidates": sorted(public, key=lambda row: row["blind_id"])},
        {"schema_version": MAP_SCHEMA, "attempts": mapping},
    )


def _integer(value: Any, label: str) -> int:
    if type(value) is not int or value < 0:
        raise ReportError(f"{label} must be a non-negative integer")
    return value


def _number(value: Any, label: str) -> int | float:
    if type(value) not in {int, float} or value < 0:
        raise ReportError(f"{label} must be a non-negative number")
    return value


def _review_metrics(review: dict[str, Any], blind_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    status = review.get("status")
    if status == "missing":
        reason = review.get("missing_reason")
        if not isinstance(reason, str) or not reason:
            raise ReportError(f"missing review requires missing_reason: {blind_id}")
        return ({
            "original_all_passed": None,
            "original_passed_rows": None,
            "original_total_rows": None,
            "f_stub_matched": None,
            "f_stub_decidable": None,
            "f_stub_rate": None,
            "mutation_detected": None,
            "mutation_valid": None,
            "mutation_score": None,
            "qualified_candidate": None,
            "unsupported_assertions": None,
            "extra_constraints": None,
        }, {"status": "missing", "reason": reason})
    if status != "reviewed":
        raise ReportError(f"review must be explicitly reviewed or missing: {blind_id}")

    execution = review.get("execution")
    f_stub = review.get("f_stub")
    mutations = review.get("mutations")
    if not all(isinstance(value, dict) for value in (execution, f_stub, mutations)):
        raise ReportError(f"reviewed record lacks structured metrics: {blind_id}")
    execution_status = execution.get("status")
    if execution_status not in {"all_passed", "partial", "failed", "not_run"}:
        raise ReportError(f"invalid execution status: {blind_id}")
    passed_rows = _integer(execution.get("passed_rows"), f"{blind_id}.passed_rows")
    total_rows = _integer(execution.get("total_rows"), f"{blind_id}.total_rows")
    if passed_rows > total_rows or (execution_status == "all_passed" and passed_rows != total_rows):
        raise ReportError(f"inconsistent execution row counts: {blind_id}")
    matched = _integer(f_stub.get("matched"), f"{blind_id}.f_stub.matched")
    decidable = _integer(f_stub.get("decidable"), f"{blind_id}.f_stub.decidable")
    detected = mutations.get("detected")
    if detected is not None:
        detected = _integer(detected, f"{blind_id}.mutations.detected")
    valid = _integer(mutations.get("valid"), f"{blind_id}.mutations.valid")
    if matched > decidable or (detected is not None and detected > valid):
        raise ReportError(f"review numerator exceeds denominator: {blind_id}")
    qualified = review.get("qualified")
    if type(qualified) is not bool:
        raise ReportError(f"qualified must be boolean: {blind_id}")
    unsupported = _integer(review.get("unsupported_assertions"), f"{blind_id}.unsupported_assertions")
    extra = _integer(review.get("extra_constraints"), f"{blind_id}.extra_constraints")
    original_all_passed = execution_status == "all_passed"
    if qualified and not original_all_passed:
        raise ReportError(f"qualified candidate did not pass all original rows: {blind_id}")
    if qualified and (valid == 0 or detected is None):
        raise ReportError(f"qualified candidate lacks a mutation denominator: {blind_id}")
    return ({
        "original_all_passed": int(original_all_passed),
        "original_passed_rows": passed_rows,
        "original_total_rows": total_rows,
        "f_stub_matched": matched,
        "f_stub_decidable": decidable,
        "f_stub_rate": matched / decidable if decidable else None,
        "mutation_detected": detected,
        "mutation_valid": valid,
        "mutation_score": detected / valid if qualified else None,
        "qualified_candidate": int(qualified),
        "unsupported_assertions": unsupported,
        "extra_constraints": extra,
    }, {"status": "reviewed", "notes": review.get("notes")})


def _usage(trace: dict[str, Any], field: str) -> int | None:
    values = []
    for turn in trace.get("turns", []):
        usage = turn.get("usage") if isinstance(turn, dict) else None
        if not isinstance(usage, dict) or usage.get(field) is None:
            continue
        values.append(_integer(usage[field], f"trace usage {field}"))
    return sum(values) if values else None


def _trace_metrics(trace: dict[str, Any]) -> dict[str, Any]:
    input_bytes = trace.get("input_bytes", trace.get("prompt_bytes"))
    return {
        "source_calls": (
            _integer(trace["source_calls"], "trace.source_calls")
            if trace.get("source_calls") is not None else None
        ),
        "input_bytes": (
            _integer(input_bytes, "trace.input_bytes") if input_bytes is not None else None
        ),
        "input_tokens": _usage(trace, "input_tokens"),
        "output_tokens": _usage(trace, "output_tokens"),
        "elapsed_seconds": (
            _number(trace["elapsed_seconds"], "trace.elapsed_seconds")
            if trace.get("elapsed_seconds") is not None else None
        ),
    }


def _range(values: list[Any]) -> list[Any] | None:
    present = [value for value in values if type(value) in {int, float}]
    return [min(present), max(present)] if present else None


def _comparison(case_attempts: list[dict[str, Any]], left: str, right: str) -> dict[str, Any]:
    by_arm = {
        arm: {attempt["repeat"]: attempt for attempt in case_attempts if attempt["arm"] == arm}
        for arm in (left, right)
    }
    repeats = sorted(set(by_arm[left]) | set(by_arm[right]))
    metrics = {}
    for metric, preference in METRICS.items():
        raw = []
        left_values = []
        right_values = []
        for repeat in repeats:
            left_value = by_arm[left].get(repeat, {}).get("metrics", {}).get(metric)
            right_value = by_arm[right].get(repeat, {}).get("metrics", {}).get(metric)
            left_values.append(left_value)
            right_values.append(right_value)
            if type(left_value) in {int, float} and type(right_value) in {int, float}:
                delta = right_value - left_value
                direction = "comparison_higher" if delta > 0 else "comparison_lower" if delta < 0 else "equal"
                outcome = (
                    "equal" if delta == 0
                    else "improved" if (delta > 0) == (preference == "higher")
                    else "regressed"
                )
            else:
                delta, direction, outcome = None, "missing", "missing"
            raw.append({
                "repeat": repeat, "baseline": left_value, "comparison": right_value,
                "delta": delta, "direction": direction, "outcome": outcome,
            })
        comparable = [row["direction"] for row in raw if row["direction"] != "missing"]
        metrics[metric] = {
            "preferred": preference,
            "raw": raw,
            "baseline_range": _range(left_values),
            "comparison_range": _range(right_values),
            "direction_consistency": (
                "missing" if not comparable else "consistent" if len(set(comparable)) == 1 else "mixed"
            ),
        }
    return {
        "comparison": f"{left.upper()}/{right.upper()}",
        "baseline_arm": left,
        "comparison_arm": right,
        "metrics": metrics,
    }


def _arm_summary(case_attempts: list[dict[str, Any]], arm: str) -> dict[str, Any]:
    attempts = sorted(
        (attempt for attempt in case_attempts if attempt["arm"] == arm),
        key=lambda attempt: attempt["repeat"],
    )
    metrics = {}
    for metric in METRICS:
        raw = [{"repeat": attempt["repeat"], "value": attempt["metrics"].get(metric)} for attempt in attempts]
        metrics[metric] = {
            "raw": raw,
            "range": _range([row["value"] for row in raw]),
        }
    return {
        "arm": arm,
        "attempt_count": len(attempts),
        "candidate_delivery_count": sum(attempt["metrics"]["candidate_delivered"] for attempt in attempts),
        "original_all_passed_count": sum(
            attempt["metrics"].get("original_all_passed") or 0 for attempt in attempts
        ),
        "qualified_candidate_count": sum(
            attempt["metrics"].get("qualified_candidate") or 0 for attempt in attempts
        ),
        "metrics": metrics,
    }


def build_summary(
    run_root: Path, blind_map: dict[str, Any], review_packet: dict[str, Any],
) -> dict[str, Any]:
    """Combine traces with explicit reviews and return per-case raw comparisons."""
    if blind_map.get("schema_version") != MAP_SCHEMA:
        raise ReportError("unsupported blind map schema")
    if review_packet.get("schema_version") != BLIND_SCHEMA:
        raise ReportError("unsupported blind review schema")
    discovered = _attempt_directories(run_root)
    frozen_digests = _validate_freeze(run_root, discovered)
    reviews = {row["blind_id"]: row for row in review_packet.get("candidates", [])}
    expected = {row["blind_id"] for row in blind_map.get("attempts", [])}
    if set(reviews) != expected:
        raise ReportError("blind review IDs do not exactly cover the private map")
    discovered_paths = {
        (case, repeat, arm): str(directory.relative_to(run_root))
        for case, repeat, arm, directory in discovered
    }
    mapped_paths = {}
    for row in blind_map["attempts"]:
        key = (row.get("case"), row.get("repeat"), row.get("arm"))
        candidate_parent = str(Path(row.get("candidate", "")).parent)
        if key not in discovered_paths or candidate_parent != discovered_paths[key]:
            raise ReportError("private map does not match the frozen generation matrix")
        if row.get("candidate_sha256") != frozen_digests.get(row["candidate"]):
            raise ReportError("private map candidate digest differs from freeze.json")
        mapped_paths[key] = candidate_parent
    if mapped_paths != discovered_paths:
        raise ReportError("private map does not cover the frozen generation matrix")

    attempts = []
    for mapped in blind_map["attempts"]:
        blind_id = mapped["blind_id"]
        trace_path = run_root / mapped["trace"]
        candidate_path = run_root / mapped["candidate"]
        trace = _read_json(trace_path) if trace_path.is_file() else {}
        review_metrics, review_status = _review_metrics(reviews[blind_id]["review"], blind_id)
        delivered = int(trace.get("status") == "generated" and candidate_path.is_file())
        metrics = {
            "candidate_delivered": delivered,
            **_trace_metrics(trace),
            **review_metrics,
        }
        attempts.append({
            "blind_id": blind_id,
            "case": mapped["case"],
            "repeat": mapped["repeat"],
            "arm": mapped["arm"],
            "generation_status": trace.get("status", "missing_trace"),
            "review": review_status,
            "metrics": metrics,
        })

    cases = []
    for case in sorted({attempt["case"] for attempt in attempts}):
        case_attempts = [attempt for attempt in attempts if attempt["case"] == case]
        cases.append({
            "case": case,
            "attempts": case_attempts,
            "arms": [_arm_summary(case_attempts, arm) for arm in ARMS],
            "comparisons": [
                _comparison(case_attempts, left, right) for left, right in COMPARISONS
            ],
        })
    return {
        "schema_version": SUMMARY_SCHEMA,
        "interpretation": {
            "unit": "case",
            "repeats_are_raw_within_case_values": True,
            "candidate_text_classification": "not_performed",
            "missingness_requires_explicit_reason": True,
        },
        "cases": cases,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    blind = subparsers.add_parser("blind")
    blind.add_argument("--run-root", type=Path, required=True)
    blind.add_argument("--salt", required=True)
    blind.add_argument("--review-output", type=Path, required=True)
    blind.add_argument("--map-output", type=Path, required=True)
    summarize = subparsers.add_parser("summarize")
    summarize.add_argument("--run-root", type=Path, required=True)
    summarize.add_argument("--review", type=Path, required=True)
    summarize.add_argument("--blind-map", type=Path, required=True)
    summarize.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "blind":
        review, mapping = build_blind_review(args.run_root, salt=args.salt)
        _write_json(args.review_output, review)
        _write_json(args.map_output, mapping)
    else:
        summary = build_summary(
            args.run_root, _read_json(args.blind_map), _read_json(args.review),
        )
        _write_json(args.output, summary)


if __name__ == "__main__":
    main()
