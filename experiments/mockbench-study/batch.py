"""Run the model-free MockBench syntax baseline over an admitted inventory.

This research driver records what the existing syntax analyzers observed.  It does
not claim that a data flow, control flow, or Mock dependency was recovered.
"""

from __future__ import annotations

import asyncio
from collections import Counter
import hashlib
from importlib.resources import files
import json
import math
from pathlib import Path
from typing import Any, Callable

from mock_spec.adapters import java, python
from mock_spec.source_analysis import SourceAnalysis
from mock_spec.validation import InputValidationError, decode_json


INVENTORY_SCHEMA = "mockbench-study.inventory/v1"
RESULTS_SCHEMA = "mockbench-study.results/v1"
SUMMARY_SCHEMA = "mockbench-study.summary/v1"
ELIGIBLE_ADMISSION = "reviewed_candidate"
SEMANTIC_DIMENSIONS = ("data_flow", "control_flow", "mock_dependency")


def _require_positive_finite(value: float, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a positive finite number")
    normalized = float(value)
    if normalized <= 0 or not math.isfinite(normalized):
        raise ValueError(f"{name} must be a positive finite number")
    return normalized


def _validate_limit(limit: int | None) -> int | None:
    if limit is not None and (isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0):
        raise ValueError("limit must be a positive integer or null")
    return limit


def _validate_inventory(inventory: dict[str, Any]) -> list[dict[str, Any]]:
    if not isinstance(inventory, dict) or inventory.get("schema_version") != INVENTORY_SCHEMA:
        raise ValueError(f"inventory must use {INVENTORY_SCHEMA}")
    cases = inventory.get("cases")
    if not isinstance(cases, list):
        raise ValueError("inventory cases must be a list")
    identities: set[tuple[str, str]] = set()
    for case in cases:
        if not isinstance(case, dict):
            raise ValueError("each inventory case must be an object")
        for field in ("language", "dataset", "case_id", "admission_status"):
            if not isinstance(case.get(field), str) or not case[field]:
                raise ValueError(f"case {field} must be a non-empty string")
        if case["language"] not in {"java", "python"}:
            raise ValueError("case language must be java or python")
        snapshot_root = case.get("snapshot_root")
        if snapshot_root is not None and (not isinstance(snapshot_root, str) or not snapshot_root):
            raise ValueError("case snapshot_root must be a non-empty string or null")
        snapshot_id = case.get("snapshot_id")
        if snapshot_id is not None and (not isinstance(snapshot_id, str) or not snapshot_id):
            raise ValueError("case snapshot_id must be a non-empty string or null")
        identity = (case["language"], case["case_id"])
        if identity in identities:
            raise ValueError("inventory case identities must be unique")
        identities.add(identity)
    return cases


def _identity(case: dict[str, Any]) -> dict[str, Any]:
    return {
        key: case.get(key)
        for key in ("language", "dataset", "case_id", "snapshot_id", "snapshot_root")
        if key in case
    }


def _semantic_evaluation() -> dict[str, dict[str, str]]:
    return {
        dimension: {
            "status": "not_evaluated",
            "reason": "syntax_baseline_requires_independent_semantic_evaluation",
        }
        for dimension in SEMANTIC_DIMENSIONS
    }


def _base_result(case: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": "mockbench-study.case-result/v1",
        "analysis_profile": "syntax_baseline",
        "identity": _identity(case),
        "admission_status": case["admission_status"],
        "admission_verified": False,
        "analysis_attempted": False,
        "semantic_evaluation": _semantic_evaluation(),
        "observations": [],
        "failure_stage": None,
        "failure_category": None,
        "limitations": [
            "The per-case deadline cooperatively cancels asynchronous analysis only; it "
            "cannot preempt synchronous bounded Adapter reads."
        ],
    }


def _failure(code: str, message: str, *, kind: str) -> dict[str, str]:
    return {"kind": kind, "code": code, "message": message}


def _set_failure(
    result: dict[str, Any], failure: dict[str, str], *, stage: str, category: str
) -> None:
    result["failure"] = failure
    result["failure_stage"] = stage
    result["failure_category"] = category


def _loader(language: str) -> Callable[..., Any]:
    return java.load_task if language == "java" else python.load_task


def _validate_admission_identity(case: dict[str, Any], root: Path) -> None:
    if root.parent.name != case["dataset"]:
        raise InputValidationError("snapshot root dataset differs from inventory row")
    snapshot_id = case.get("snapshot_id")
    if snapshot_id is not None and root.name != snapshot_id:
        raise InputValidationError("snapshot root identity differs from inventory row")
    revision = case.get("inventory_revision")
    if revision is None:
        return
    catalog = decode_json(files("mock_spec").joinpath("reviewed_inputs.json").read_text())
    matches = [
        entry for entry in catalog
        if entry.get("language") == case["language"] and entry.get("case_id") == case["case_id"]
    ]
    if len(matches) != 1:
        raise InputValidationError("inventory row has no unique installed input review")
    review = matches[0]
    if (
        review.get("dataset") != case["dataset"]
        or review.get("snapshot_id") != root.name
        or review.get("inventory_revision") != revision
    ):
        raise InputValidationError("inventory row identity differs from installed input review")


async def _observe_case(case: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    snapshot_root = case.get("snapshot_root")
    if snapshot_root is None:
        result["run_status"] = "input_rejected"
        _set_failure(
            result,
            _failure(
                "missing_snapshot_root", "Eligible case has no snapshot root.",
                kind="input_admission",
            ),
            stage="admission",
            category="missing_input",
        )
        return result
    try:
        root = Path(snapshot_root)
        _validate_admission_identity(case, root)
        # The inventory is descriptive, not an authorization source.  The installed
        # adapter rechecks the reviewed catalog, frozen identity, and masking.
        task = _loader(case["language"])(root, case_id=case["case_id"])
        if task.language != case["language"] or task.case_id != case["case_id"]:
            raise InputValidationError("adapter task identity differs from inventory row")
    except (InputValidationError, OSError, ValueError) as exc:
        result["run_status"] = "input_rejected"
        _set_failure(
            result,
            _failure(type(exc).__name__, str(exc), kind="input_admission"),
            stage="admission",
            category="input_rejected",
        )
        return result

    result["admission_verified"] = True
    result["analysis_attempted"] = True
    analysis = SourceAnalysis(task)
    sources = (task.masked_test, task.mut_source, *task.supporting_sources)
    saw_error = False
    saw_incomplete = False
    first_error: dict[str, str] | None = None
    for source in sources:
        source_result = {
            "source_id": source.source_id,
            "logical_path": source.logical_path,
            "role": next(
                entry.role for entry in task.sources if entry.source_id == source.source_id
            ),
            "tools": {},
        }
        result["observations"].append(source_result)
        for tool_name, operation in (
            ("program_structure", analysis.inspect_program_structure),
            ("dependency_usage", analysis.inspect_dependency_usage),
        ):
            try:
                observation = await operation(source.source_id)
            except Exception as exc:
                observation = {
                    "status": "error",
                    "error_code": type(exc).__name__,
                    "facts": [],
                    "diagnostics": [str(exc)],
                    "limitations": [],
                }
            source_result["tools"][tool_name] = observation
            if observation.get("status") == "error":
                saw_error = True
                if first_error is None:
                    first_error = _failure(
                        str(observation.get("error_code", "analysis_error")),
                        _diagnostic_text(observation),
                        kind=_analysis_failure_kind(observation),
                    )
            elif observation.get("status") == "incomplete":
                saw_incomplete = True
    if saw_error:
        result["run_status"] = "analysis_failed"
        assert first_error is not None
        _set_failure(
            result,
            first_error,
            stage="analysis",
            category=(
                "backend_unavailable"
                if first_error["kind"] == "backend_unavailable"
                else "parse_error"
                if first_error["kind"] == "parse_error"
                else "analysis_error"
            ),
        )
    elif saw_incomplete:
        result["run_status"] = "analysis_incomplete"
    else:
        result["run_status"] = "observed"
    return result


def _diagnostic_text(observation: dict[str, Any]) -> str:
    diagnostics = observation.get("diagnostics", [])
    if isinstance(diagnostics, list) and diagnostics:
        first = diagnostics[0]
        if isinstance(first, str):
            return first
        if isinstance(first, dict) and isinstance(first.get("message"), str):
            return first["message"]
    return "Analysis returned an error without a diagnostic."


def _analysis_failure_kind(observation: dict[str, Any]) -> str:
    if observation.get("error_code") == "backend_unavailable":
        return "backend_unavailable"
    failure_kind = observation.get("failure_kind")
    return failure_kind if isinstance(failure_kind, str) else "analysis_error"


async def _run_selected(case: dict[str, Any], timeout_seconds: float) -> dict[str, Any]:
    result = _base_result(case)
    try:
        return await asyncio.wait_for(_observe_case(case, result), timeout=timeout_seconds)
    except asyncio.TimeoutError:
        result["run_status"] = "timeout"
        _set_failure(
            result,
            _failure(
                "case_timeout", "Per-case syntax baseline deadline was exhausted.", kind="timeout"
            ),
            stage="runtime",
            category="timeout",
        )
        return result
    except Exception as exc:
        result["run_status"] = "unexpected_error"
        _set_failure(
            result,
            _failure(type(exc).__name__, str(exc), kind="unexpected_error"),
            stage="runtime",
            category="unexpected_error",
        )
        return result


def _blocked(
    case: dict[str, Any], status: str, code: str, message: str, *, stage: str
) -> dict[str, Any]:
    result = _base_result(case)
    result["run_status"] = status
    _set_failure(
        result,
        _failure(code, message, kind="selection_or_admission"),
        stage=stage,
        category=code,
    )
    return result


def _case_filename(result: dict[str, Any]) -> str:
    identity = result["identity"]
    key = "\0".join(
        str(identity.get(field, "")) for field in ("language", "dataset", "case_id")
    )
    return f"case-{hashlib.sha256(key.encode('utf-8')).hexdigest()[:20]}.json"


def _counts(results: list[dict[str, Any]]) -> dict[str, Any]:
    statuses = Counter(result["run_status"] for result in results)
    review_candidates = sum(
        result["admission_status"] == ELIGIBLE_ADMISSION for result in results
    )
    admitted = sum(result["admission_verified"] for result in results)
    selected = sum(
        result["run_status"] not in {"not_selected", "not_admitted"}
        for result in results
    )
    attempted = sum(
        result["run_status"]
        not in {"not_selected", "not_admitted"}
        for result in results
    )
    analysis_attempted = sum(result["analysis_attempted"] for result in results)
    observed = statuses["observed"]
    partial = statuses["analysis_incomplete"]
    failed_statuses = {"input_rejected", "analysis_failed", "timeout", "unexpected_error"}
    return {
        "total": len(results),
        "review_candidates": review_candidates,
        "selected": selected,
        "attempted": attempted,
        "admitted": admitted,
        "analysis_attempted": analysis_attempted,
        "observed": observed,
        "partial": partial,
        "failed": sum(statuses[status] for status in failed_statuses),
        "blocked": len(results) - attempted,
        "by_status": dict(sorted(statuses.items())),
    }


def _summary(results: list[dict[str, Any]]) -> dict[str, Any]:
    languages = sorted({result["identity"]["language"] for result in results})
    return {
        "schema_version": SUMMARY_SCHEMA,
        "analysis_profile": "syntax_baseline",
        "counts": _counts(results),
        "by_language": {
            language: _counts(
                [result for result in results if result["identity"]["language"] == language]
            )
            for language in languages
        },
        "by_failure_stage": dict(sorted(Counter(
            result["failure_stage"] for result in results
            if result["failure_stage"] is not None
        ).items())),
        "by_failure_category": dict(sorted(Counter(
            result["failure_category"] for result in results
            if result["failure_category"] is not None
        ).items())),
        "semantic_evaluation": _semantic_evaluation(),
        "count_definitions": {
            "review_candidates": "Inventory rows labelled reviewed_candidate.",
            "selected": "Review candidates selected within the case limit.",
            "attempted": "Selected rows for which admission loading was attempted.",
            "admitted": "Rows whose Adapter and returned task identity were verified.",
            "analysis_attempted": "Admitted rows for which syntax analysis started.",
            "observed": "Analysis completed without error or incomplete tool output.",
            "partial": "Analysis returned at least one incomplete tool output and no error.",
            "failed": "Selected rows ending in an admission, analysis, timeout, or runtime error.",
            "blocked": "Rows not attempted because they were unadmitted or outside the limit.",
        },
        "interpretation": (
            "Observed means the syntax tools returned without an error. It does not establish "
            "correct recovery of data flow, control flow, or Mock dependencies."
        ),
    }


async def _run_cases(
    cases: list[dict[str, Any]], timeout_seconds: float, limit: int | None,
    output_dir: Path,
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    selected_count = 0
    for case in cases:
        if case["admission_status"] != ELIGIBLE_ADMISSION:
            result = _blocked(
                case, "not_admitted", "not_admitted",
                "Case remains in the study denominator but is not admitted for analysis.",
                stage="admission",
            )
            results.append(result)
            _write_json(output_dir / _case_filename(result), result)
            continue
        if limit is not None and selected_count >= limit:
            result = _blocked(
                case, "not_selected", "batch_limit",
                "Eligible case was outside this run's case limit.",
                stage="selection",
            )
            results.append(result)
            _write_json(output_dir / _case_filename(result), result)
            continue
        selected_count += 1
        result = await _run_selected(case, timeout_seconds)
        results.append(result)
        _write_json(output_dir / _case_filename(result), result)
    return results


def _write_json(path: Path, value: Any) -> None:
    content = json.dumps(
        value, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False
    ) + "\n"
    with path.open("x", encoding="utf-8") as stream:
        stream.write(content)


def _run_manifest(
    inventory: dict[str, Any], timeout_seconds: float, limit: int | None
) -> dict[str, Any]:
    canonical_inventory = json.dumps(
        inventory, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")
    return {
        "schema_version": "mockbench-study.run-manifest/v1",
        "analysis_profile": "syntax_baseline",
        "budget": {"per_case_deadline_seconds": timeout_seconds, "case_limit": limit},
        "inventory_sha256": hashlib.sha256(canonical_inventory).hexdigest(),
        "implementation": {
            "path": "experiments/mockbench-study/batch.py",
            "sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "tools": ["SourceAnalysis.inspect_program_structure",
                      "SourceAnalysis.inspect_dependency_usage"],
        },
        "limitations": [
            "The deadline cooperatively cancels asynchronous analysis only and cannot preempt "
            "synchronous bounded Adapter reads.",
            "The syntax baseline does not semantically evaluate recovered data flow, control "
            "flow, or Mock dependencies.",
        ],
    }


def run_batch(
    inventory: dict[str, Any], output_dir: Path, *, timeout_seconds: float = 60.0,
    limit: int | None = None,
) -> dict[str, Any]:
    """Run admitted cases sequentially and write non-overwritable research evidence."""
    if inventory.get("input_policy", "reviewed") != "reviewed":
        raise ValueError("Use check-inputs for frozen-agent data; syntax baseline needs an explicit MUT")
    cases = _validate_inventory(inventory)
    deadline = _require_positive_finite(timeout_seconds, "timeout_seconds")
    case_limit = _validate_limit(limit)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=False)

    _write_json(output_dir / "inventory.json", inventory)
    _write_json(output_dir / "run-manifest.json", _run_manifest(inventory, deadline, case_limit))
    results = asyncio.run(_run_cases(cases, deadline, case_limit, output_dir))
    summary = _summary(results)
    _write_json(output_dir / "results.json", {
        "schema_version": RESULTS_SCHEMA,
        "analysis_profile": "syntax_baseline",
        "results": results,
    })
    _write_json(output_dir / "summary.json", summary)
    return summary


def check_inputs(inventory: dict[str, Any], output_dir: Path) -> dict[str, Any]:
    """Actually load all frozen inputs; neither requires a MUT nor invokes analysis."""
    from mock_spec.adapters.frozen_dataset import FrozenDataset

    cases = _validate_inventory(inventory)
    if inventory.get("input_policy") != "frozen-agent":
        raise ValueError("check-inputs requires a frozen-agent inventory")
    registry = inventory.get("registry_root")
    if not isinstance(registry, str) or not registry:
        raise ValueError("Frozen input inventory requires a registry root")
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=False)
    _write_json(output_dir / "inventory.json", inventory)
    _write_json(output_dir / "run-manifest.json", {
        "schema_version": "mockbench-study.input-check/v1",
        "input_policy": "stub-tester-frozen-agent/v1",
        "implementation_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "loader_sha256": hashlib.sha256(
            files("mock_spec").joinpath("adapters/frozen_dataset.py").read_bytes()
        ).hexdigest(),
        "mask_validation_sha256": hashlib.sha256(
            files("mock_spec").joinpath("validation.py").read_bytes()
        ).hexdigest(),
        "inventory_sha256": hashlib.sha256(json.dumps(
            inventory, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False,
        ).encode()).hexdigest(),
        "scope": "metadata, masked targets and injectors; no source-tree or dependency scan",
    })
    datasets: dict[tuple[str, str], Any] = {}
    results: list[dict[str, Any]] = []
    for case in cases:
        result: dict[str, Any] = {"identity": _identity(case), "status": "input_rejected"}
        try:
            key = (case["dataset"], case["inventory_revision"])
            if key not in datasets:
                datasets[key] = FrozenDataset(
                    Path(registry), dataset=key[0], inventory_revision=key[1],
                )
            with datasets[key].load_case(case["case_id"]) as loaded:
                if loaded.language != case["language"] or loaded.snapshot_id != case.get("snapshot_id"):
                    raise InputValidationError("Loaded case differs from inventory identity")
                source = loaded.masked_test
                result.update({
                    "status": "loaded", "target_selector": loaded.target_selector,
                    "masked_test": {"logical_path": source.logical_path,
                                    "lines": source.end_line,
                                    "sha256": hashlib.sha256(source.text.encode()).hexdigest()},
                    "available_source_files": len(loaded.source_paths),
                    "mut_selection": "not_requested",
                })
        except Exception as exc:
            result["error"] = {"type": type(exc).__name__, "message": str(exc)}
        results.append(result)
        _write_json(output_dir / _case_filename(result), result)

    def counts(rows):
        loaded = sum(row["status"] == "loaded" for row in rows)
        return {"total": len(rows), "loaded": loaded, "failed": len(rows) - loaded}

    summary = {
        "schema_version": "mockbench-study.input-check-summary/v1",
        "counts": counts(results),
        "by_language": {
            language: counts([r for r in results if r["identity"]["language"] == language])
            for language in sorted({r["identity"]["language"] for r in results})
        },
        "scope": "Input integration only; no inference, relation recovery or target-test execution.",
    }
    _write_json(output_dir / "results.json", {"results": results})
    _write_json(output_dir / "summary.json", summary)
    return summary
