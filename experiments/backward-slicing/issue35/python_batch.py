"""Run and summarize the frozen #35 Python reference/candidate matrix."""

from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
from typing import Any

import python_eval


HERE = Path(__file__).resolve().parent
RUN = HERE / "run"
REGISTRATION = HERE / "python_eval"
PYTHON_CASES = tuple(python_eval.CASES)
ARMS = "ABC"
REPEATS = (1, 2, 3)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def verify_freeze() -> dict[str, Any]:
    freeze_path = RUN / "freeze.json"
    freeze = json.loads(freeze_path.read_text(encoding="utf-8"))
    if freeze.get("attempts") != 54:
        raise ValueError("generation freeze summary is incomplete")
    for relative, expected in freeze["sha256"].items():
        path = RUN / relative
        if not path.is_file() or _sha256(path) != expected:
            raise ValueError(f"frozen generation artifact differs: {relative}")
    statuses: Counter[str] = Counter()
    for case in ("zipkin", "jib", *PYTHON_CASES):
        for repeat in REPEATS:
            for arm in ARMS:
                directory = RUN / case / str(repeat) / arm
                trace = json.loads((directory / "trace.json").read_text(encoding="utf-8"))
                statuses[trace["status"]] += 1
                candidate = directory / "candidate.json"
                if trace["status"] == "generated" and not candidate.is_file():
                    raise ValueError(f"generated attempt lacks candidate: {case}/{repeat}/{arm}")
                if trace["status"] == "failed" and candidate.exists():
                    raise ValueError(f"failed attempt unexpectedly has candidate: {case}/{repeat}/{arm}")
    if dict(statuses) != freeze.get("statuses"):
        raise ValueError("generation status summary differs from frozen traces")
    return freeze


def _original_qualified(receipt: dict[str, Any], expected_rows: int) -> bool:
    return (
        receipt.get("valid") is True
        and receipt.get("exit_code") == 0
        and receipt.get("collected") == expected_rows
        and receipt.get("passed") == expected_rows
        and receipt.get("failed") == 0
        and len(receipt.get("row_identity", [])) == expected_rows
    )


def _original_has_pairable_row(receipt: dict[str, Any]) -> bool:
    return (
        receipt.get("valid") is True
        and receipt.get("passed", 0) > 0
        and any(
            frame.get("filename_matches") and frame.get("variant_matches")
            for frame in receipt.get("mut_frames", [])
        )
    )


def _run_variant(
    case: str, envelope: Path, variant: str, destination: Path, registry_root: Path,
) -> dict[str, Any]:
    expected_rows = int(json.loads((REGISTRATION / case / "registration.json").read_text())["identity"].get("parameter_rows", 0) or 0)
    # Contract identity does not expand parameters. Use the successful reference
    # calibration as the frozen row count and identities.
    reference_summary = json.loads((HERE / "python_eval_calibration/summary.json").read_text())
    reference = next(row for row in reference_summary["runs"] if row["case_key"] == case)
    expected_rows = reference["collected"]
    original_dir = destination / "original"
    try:
        original = python_eval.run_case(
            case, envelope, variant, "original", original_dir, registry_root,
        )
    except (SyntaxError, ValueError, KeyError, TypeError) as error:
        destination.mkdir(parents=True, exist_ok=True)
        failure = {
            "status": "configuration_failure",
            "error_type": type(error).__name__,
            "message": str(error),
        }
        _write(destination / "configuration-failure.json", failure)
        return failure
    result = {"status": "original_executed", "original": original, "mutants": {}}
    if _original_has_pairable_row(original):
        for mutant in python_eval.CASES[case]["mutants"]:
            result["mutants"][mutant] = python_eval.run_case(
                case, envelope, variant, mutant, destination / mutant, registry_root,
            )
    else:
        result["status"] = "original_ineligible"
    _write(destination / "variant.json", result)
    return result


def run_all(output: Path, registry_root: Path) -> None:
    freeze = verify_freeze()
    output.mkdir(parents=True, exist_ok=False)
    original_manifest = json.loads((REGISTRATION / "manifest.json").read_text(encoding="utf-8"))
    annotated = {
        **original_manifest,
        "evaluation_annotation": {
            "generation_freeze": "run/freeze.json",
            "generation_freeze_sha256": _sha256(RUN / "freeze.json"),
            "verified_attempts": freeze["attempts"],
            "verified_statuses": freeze["statuses"],
            "candidate_gate": "original must pass every frozen parameter row before mutants run",
            "docker_lock": "/tmp/mockspec-issue35-docker.lock",
        },
    }
    _write(output / "annotated-manifest.json", annotated)
    ledger: dict[str, Any] = {"references": {}, "candidates": {}, "generation_failures": {}}
    for case in PYTHON_CASES:
        reference = REGISTRATION / case / "reference.json"
        ledger["references"][case] = _run_variant(
            case, reference, "reference", output / "raw/reference" / case, registry_root,
        )
        for repeat in REPEATS:
            for arm in ARMS:
                variant = f"{case}/{repeat}/{arm}"
                attempt = RUN / variant
                candidate = attempt / "candidate.json"
                key = f"{repeat}/{arm}"
                if not candidate.is_file():
                    trace = attempt / "trace.json"
                    ledger["generation_failures"][variant] = {
                        "status": "generation_failure",
                        "trace": str(trace.relative_to(HERE)),
                        "trace_sha256": _sha256(trace),
                    }
                    continue
                ledger["candidates"][variant] = _run_variant(
                    case, candidate, key, output / "raw/candidate" / variant, registry_root,
                )
        _write(output / "progress.json", ledger)
    _write(output / "execution-ledger.json", ledger)


def supplement_partial_variants(output: Path, registry_root: Path) -> None:
    """Run omitted mutants for originals with at least one pairable passing row."""

    ledger_path = output / "execution-ledger.json"
    ledger = json.loads(ledger_path.read_text())
    variants: list[tuple[str, str, Path, Path, dict[str, Any]]] = []
    for case, state in ledger["references"].items():
        variants.append((
            case, "reference", REGISTRATION / case / "reference.json",
            output / "raw/reference" / case, state,
        ))
    for identity, state in ledger["candidates"].items():
        case, repeat, arm = identity.split("/")
        variants.append((
            case, f"{repeat}/{arm}", RUN / identity / "candidate.json",
            output / "raw/candidate" / identity, state,
        ))
    for case, variant, envelope, directory, state in variants:
        original = state.get("original")
        if not original or not _original_has_pairable_row(original):
            continue
        for mutant in python_eval.CASES[case]["mutants"]:
            if mutant in state.setdefault("mutants", {}):
                continue
            state["mutants"][mutant] = python_eval.run_case(
                case, envelope, variant, mutant, directory / mutant, registry_root,
            )
        _write(directory / "variant.json", state)
        _write(output / "progress.json", ledger)
    _write(ledger_path, ledger)


def _row_phases(execution: dict[str, Any]) -> dict[str, dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    for phase in execution.get("phases", []):
        rows.setdefault(phase["nodeid"], {})[phase["when"]] = phase
    frames: dict[str, list[dict[str, Any]]] = {}
    for frame in execution.get("mut_frames", []):
        frames.setdefault(frame["nodeid"], []).append(frame)
    for nodeid, row in rows.items():
        row["mut_frames"] = frames.get(nodeid, [])
    return rows


def _load_execution(path: Path) -> dict[str, Any] | None:
    return json.loads(path.read_text()) if path.is_file() else None


def _applicable(case: str, mutant: str, nodeid: str) -> bool:
    if case == "ansible_119":
        return "[python_bin_none-" not in nodeid
    if case == "click_402":
        if mutant == "wrong_basename_argument":
            return "[shorten_path-" in nodeid
        return "[bytes_filename_" in nodeid
    if case == "langflow_189":
        return "[empty_value_returns_false_without_settings_lookup-" not in nodeid
    return True


def _pair_rows(
    case: str, mutant: str, expected_rows: list[str],
    original: dict[str, Any], mutated: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    original_rows = _row_phases(original)
    mutant_rows = _row_phases(mutated or {})
    pairs = []
    for nodeid in expected_rows:
        applicable = _applicable(case, mutant, nodeid)
        baseline = original_rows.get(nodeid, {})
        changed = mutant_rows.get(nodeid, {})
        original_call = baseline.get("call")
        mutant_call = changed.get("call")
        reached = any(
            frame.get("filename_matches") and frame.get("variant_matches")
            for frame in changed.get("mut_frames", [])
        )
        if not applicable:
            classification = "not_applicable"
        elif original_call is None:
            classification = "original_row_missing"
        elif original_call.get("outcome") != "passed":
            classification = "original_row_not_passed"
        elif mutated is None:
            classification = "mutant_not_run"
        elif mutant_call is None:
            setup = changed.get("setup")
            classification = "fixture_or_infrastructure_failure" if setup and setup.get("outcome") == "failed" else "row_missing"
        elif mutant_call.get("outcome") == "passed":
            classification = "survived"
        elif not reached:
            classification = "failed_before_mut"
        elif mutant_call.get("exception_type") == "AssertionError":
            classification = "killed_assertion"
        elif mutant == "duplicate_importlib_probe" and mutant_call.get("exception_type") == "StopIteration":
            classification = "killed_stub_exhaustion"
        else:
            classification = "non_expected_exception"
        pairs.append({
            "nodeid": nodeid,
            "applicable": applicable,
            "original_setup_outcome": baseline.get("setup", {}).get("outcome"),
            "original_outcome": original_call and original_call.get("outcome"),
            "original_teardown_outcome": baseline.get("teardown", {}).get("outcome"),
            "mutant_setup_outcome": changed.get("setup", {}).get("outcome"),
            "mutant_outcome": mutant_call and mutant_call.get("outcome"),
            "mutant_teardown_outcome": changed.get("teardown", {}).get("outcome"),
            "mutant_exception_type": mutant_call and mutant_call.get("exception_type"),
            "mut_reached": reached,
            "classification": classification,
        })
    return pairs


def summarize(output: Path) -> dict[str, Any]:
    ledger = json.loads((output / "execution-ledger.json").read_text())
    calibration = json.loads((HERE / "python_eval_calibration/summary.json").read_text())
    reference_rows = {row["case_key"]: row["row_identity"] for row in calibration["runs"]}
    variants: list[tuple[str, str, dict[str, Any], Path]] = []
    for case, state in ledger["references"].items():
        variants.append((case, "reference", state, output / "raw/reference" / case))
    for identity, state in ledger["candidates"].items():
        case = identity.split("/", 1)[0]
        variants.append((case, identity, state, output / "raw/candidate" / identity))
    results = []
    for case, identity, state, directory in variants:
        registration_path = REGISTRATION / case / "registration.json"
        registration = json.loads(registration_path.read_text())
        original_receipt = state.get("original")
        original_execution = _load_execution(directory / "original/execution.json")
        expected_rows = reference_rows[case]
        original_rows = _row_phases(original_execution or {})
        original_full_pass = bool(
            original_receipt
            and original_receipt.get("valid")
            and original_receipt.get("row_identity") == expected_rows
            and original_receipt.get("passed") == len(expected_rows)
            and original_receipt.get("failed") == 0
            and (original_execution or {}).get("exitstatus") == 0
            and all(
                all(original_rows.get(nodeid, {}).get(phase, {}).get("outcome") == "passed"
                    for phase in ("setup", "call", "teardown"))
                and any(
                    frame.get("filename_matches") and frame.get("variant_matches")
                    for frame in original_rows.get(nodeid, {}).get("mut_frames", [])
                )
                for nodeid in expected_rows
            )
        )
        mutants = []
        detected = 0
        assertion_detected = 0
        missing_mutants = 0
        for mutant_row in registration["mutants"]:
            mutant = mutant_row["mutant"]
            mutant_receipt = state.get("mutants", {}).get(mutant)
            mutant_execution = _load_execution(directory / mutant / "execution.json")
            pairs = _pair_rows(
                case, mutant, expected_rows, original_execution or {}, mutant_execution,
            )
            killed_assertion = original_full_pass and any(
                row["classification"] == "killed_assertion" for row in pairs
            )
            killed_stub_exhaustion = original_full_pass and any(
                row["classification"] == "killed_stub_exhaustion" for row in pairs
            )
            killed = killed_assertion or killed_stub_exhaustion
            incomplete = any(
                row["applicable"] and row["classification"] in {
                    "mutant_not_run", "fixture_or_infrastructure_failure", "row_missing",
                    "failed_before_mut", "non_expected_exception", "original_row_missing",
                    "original_row_not_passed",
                }
                for row in pairs
            )
            detection = True if killed else None if incomplete else False
            detected += int(detection is True)
            assertion_detected += int(killed_assertion)
            missing_mutants += int(detection is None)
            mutants.append({
                "mutant": mutant,
                "input_identity": mutant_row,
                "receipt": mutant_receipt,
                "detected": detection,
                "detected_by_assertion": killed_assertion,
                "detected_by_stub_exhaustion": killed_stub_exhaustion,
                "missing": detection is None,
                "rows": pairs,
            })
        denominator = len(registration["mutants"])
        results.append({
            "case_key": case,
            "variant": identity,
            "status": state["status"],
            "original": original_receipt,
            "original_full_pass": original_full_pass,
            "original_qualified": original_full_pass,
            "qualified": original_full_pass and denominator > 0 and missing_mutants == 0,
            "raw_detected": detected if original_full_pass else None,
            "assertion_detected": assertion_detected if original_full_pass else None,
            "legal_denominator": denominator,
            "missing_mutants": missing_mutants,
            "mutation_score": (
                detected / denominator
                if original_full_pass and missing_mutants == 0 else None
            ),
            "assertion_only_score": (
                assertion_detected / denominator
                if original_full_pass and missing_mutants == 0 else None
            ),
            "reference_contract": registration["reference_contract"],
            "mutants": mutants,
        })
    for identity, failure in sorted(ledger["generation_failures"].items()):
        case = identity.split("/", 1)[0]
        registration = json.loads((REGISTRATION / case / "registration.json").read_text())
        mutants = []
        for mutant_row in registration["mutants"]:
            mutant = mutant_row["mutant"]
            mutants.append({
                "mutant": mutant,
                "input_identity": mutant_row,
                "detected": None,
                "detected_by_assertion": False,
                "detected_by_stub_exhaustion": False,
                "missing": True,
                "rows": [
                    {
                        "nodeid": nodeid,
                        "applicable": _applicable(case, mutant, nodeid),
                        "original_setup_outcome": None,
                        "original_outcome": None,
                        "original_teardown_outcome": None,
                        "mutant_setup_outcome": None,
                        "mutant_outcome": None,
                        "mutant_teardown_outcome": None,
                        "mutant_exception_type": None,
                        "mut_reached": False,
                        "classification": "generation_failure",
                    }
                    for nodeid in reference_rows[case]
                ],
            })
        results.append({
            "case_key": case,
            "variant": identity,
            "status": "generation_failure",
            "generation_failure": failure,
            "original": None,
            "original_full_pass": False,
            "original_qualified": False,
            "qualified": False,
            "raw_detected": None,
            "assertion_detected": None,
            "legal_denominator": len(registration["mutants"]),
            "missing_mutants": len(registration["mutants"]),
            "mutation_score": None,
            "assertion_only_score": None,
            "reference_contract": registration["reference_contract"],
            "mutants": mutants,
        })
    results.sort(key=lambda row: (row["case_key"], row["variant"] != "reference", row["variant"]))
    paired = {
        "schema": "mock-spec.issue35-python-paired-results/v1",
        "generation_freeze_sha256": _sha256(RUN / "freeze.json"),
        "same_case_denominators": {
            case: len(python_eval.CASES[case]["mutants"]) for case in PYTHON_CASES
        },
        "generation_failures": ledger["generation_failures"],
        "results": results,
    }
    _write(output / "paired.json", paired)
    return paired


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("run", "supplement", "summarize"))
    parser.add_argument("--output", type=Path, default=HERE / "python_results")
    parser.add_argument("--registry-root", type=Path, default=python_eval.inputs.DEFAULT_REGISTRY_ROOT)
    arguments = parser.parse_args()
    if arguments.action == "run":
        run_all(arguments.output, arguments.registry_root)
    elif arguments.action == "supplement":
        supplement_partial_variants(arguments.output, arguments.registry_root)
    else:
        summarize(arguments.output)


if __name__ == "__main__":
    main()
