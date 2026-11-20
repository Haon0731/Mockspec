import importlib.util
import json
from pathlib import Path
import sys


HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
SPEC = importlib.util.spec_from_file_location(
    "issue35_java_batch_evaluate", HERE / "java_batch_evaluate.py"
)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


def candidate():
    return {"slots": {"MOCK-STUB": "", "VERIFY": ""}, "rationale": []}


def add_non_java_traces(run_root, files):
    for case in ("ansible_119", "click_402", "click_423", "langflow_189"):
        for repeat in MODULE.REPEATS:
            for arm in MODULE.ARMS:
                trace = run_root / case / str(repeat) / arm / "trace.json"
                trace.parent.mkdir(parents=True)
                trace.write_text(json.dumps({"status": "generated"}))
                files.append(trace)


def test_prepare_freezes_exact_18_candidates(tmp_path):
    run_root = tmp_path / "run"
    for case in MODULE.CASES:
        for repeat in MODULE.REPEATS:
            for arm in MODULE.ARMS:
                path = run_root / case / str(repeat) / arm / "candidate.json"
                path.parent.mkdir(parents=True)
                path.write_text(json.dumps(candidate()))
                (path.parent / "trace.json").write_text(json.dumps({"status": "generated"}))
    files = [*run_root.glob("*/*/*/candidate.json"), *run_root.glob("*/*/*/trace.json")]
    add_non_java_traces(run_root, files)
    (run_root / "protocol.json").write_text("{}")
    files.append(run_root / "protocol.json")
    freeze = {
        "attempts": 54,
        "sha256": {
            str(path.relative_to(run_root)): MODULE.digest(path) for path in files
        },
    }
    (run_root / "freeze.json").write_text(json.dumps(freeze))
    output = tmp_path / "output"
    manifest = MODULE.prepare(run_root, output)
    assert manifest["candidate_count"] == 18
    assert manifest["total_runs"] == 80
    MODULE.validate_freeze(run_root, output)
    changed = run_root / "jib/1/A/candidate.json"
    changed.write_text(json.dumps({**candidate(), "rationale": ["changed"]}))
    try:
        MODULE.validate_freeze(run_root, output)
    except ValueError as error:
        assert "differs from freeze" in str(error)
    else:
        raise AssertionError("changed candidate was accepted")


def test_missing_candidate_is_retained_from_global_freeze(tmp_path):
    run_root = tmp_path / "run"
    files = []
    for case in MODULE.CASES:
        for repeat in MODULE.REPEATS:
            for arm in MODULE.ARMS:
                parent = run_root / case / str(repeat) / arm
                parent.mkdir(parents=True)
                trace = parent / "trace.json"
                trace.write_text(json.dumps({
                    "status": "failed", "error_type": "JSONDecodeError",
                    "reason": "invalid JSON",
                }))
                files.append(trace)
    protocol = run_root / "protocol.json"
    add_non_java_traces(run_root, files)
    protocol.write_text("{}")
    files.append(protocol)
    freeze = {"attempts": 54, "sha256": {
        str(path.relative_to(run_root)): MODULE.digest(path) for path in files
    }}
    (run_root / "freeze.json").write_text(json.dumps(freeze))
    output = tmp_path / "output"
    manifest = MODULE.prepare(run_root, output)
    assert manifest["missing_candidate_count"] == 18
    rows = MODULE.execute_subject(
        "zipkin", manifest["candidates"][0], output, tmp_path
    )
    assert len(rows) == 4
    assert {row["status"] for row in rows} == {"missing_candidate_generation"}


def test_compile_failure_skips_mutant_with_diagnostic(tmp_path):
    original = {
        "receipt": "runs/jib/x/original/result.json", "status": "compile_failed"
    }
    result = MODULE.skipped_result(
        "jib", {"subject_id": "repeat-1-a"}, "omit_create_layers", original
    )
    assert result["status"] == "not_run_original_compile_failed"
    assert result["primary_mutation_score_eligible"] is False


def test_exception_type_uses_failing_stage_and_recognizes_mockito():
    result = {
        "status": "test_assertion_or_verification_failed",
        "compile": {
            "stdout": "",
            "stderr": "warning: javax.annotation.processing.FilerException",
        },
        "test": {
            "stdout": (
                "org.mockito.exceptions.verification.WantedButNotInvoked: "
                "Wanted but not invoked"
            ),
            "stderr": "",
        },
    }
    assert MODULE.exception_type(result) == (
        "org.mockito.exceptions.verification.WantedButNotInvoked"
    )


def test_paired_results_keeps_reference_fixed_denominator(tmp_path):
    def receipt(subject_id, mutant, status):
        path = tmp_path / subject_id / mutant / "result.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"status": status}))
        return {
            "case": "jib", "subject_id": subject_id, "mutant": mutant,
            "status": status, "receipt": str(path),
        }

    rows = [
        receipt("developer-reference", "original", "passed"),
        receipt("developer-reference", "m1", "test_assertion_or_verification_failed"),
        receipt("developer-reference", "m2", "passed"),
        receipt("candidate", "original", "passed"),
        receipt("candidate", "m1", "test_assertion_or_verification_failed"),
        receipt("candidate", "m2", "test_exception_or_fixture_failed"),
    ]
    paired = MODULE.paired_results(tmp_path, rows)
    candidate_result = next(
        item for item in paired["subjects"] if item["subject_id"] == "candidate"
    )
    assert paired["fixed_valid_mutants"] == {"jib": ["m1", "m2"]}
    assert candidate_result["valid_mutation_denominator"] == 2
    assert candidate_result["mutation_detected"] == 1
    assert candidate_result["missing_fixed_mutants"] == ["m2"]
    assert candidate_result["mutation_score"] is None
