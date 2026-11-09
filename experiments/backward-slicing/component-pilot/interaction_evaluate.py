"""Run case-specific interaction mutants through the existing Java evaluator.

Only the evaluator's temporary, admitted source copy is changed.  The reviewed
repository and SourceStore results remain untouched.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import difflib
import hashlib
import json
from pathlib import Path
import sys
from typing import Iterator


HERE = Path(__file__).resolve().parent
JAVA_EVALUATOR = HERE.parent / "path-pilot"
sys.path.insert(0, str(JAVA_EVALUATOR))
import java_evaluate  # noqa: E402


TARGET = Path(
    "src/main/java/de/codecentric/boot/admin/client/registration/"
    "DefaultApplicationFactory.java"
)
MUTATIONS = {
    "duplicate_health_lookup": (
        '        String health = pathMappedEndpoints.getPath(EndpointId.of("health"));\n',
        '        String health = pathMappedEndpoints.getPath(EndpointId.of("health"));\n'
        '        pathMappedEndpoints.getPath(EndpointId.of("health"));\n',
    ),
    "unexpected_status_lookup": (
        "        if (StringUtils.hasText(health)) {\n"
        "            return health;\n",
        "        if (StringUtils.hasText(health)) {\n"
        '            pathMappedEndpoints.getPath(EndpointId.of("status"));\n'
        "            return health;\n",
    ),
}


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _write_json(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def mutate_source(text: str, mutant: str) -> tuple[str, dict[str, object]]:
    """Apply exactly one source-level interaction mutation."""
    if mutant not in MUTATIONS:
        raise ValueError(f"unknown interaction mutant: {mutant}")
    before, after = MUTATIONS[mutant]
    occurrences = text.count(before)
    if occurrences != 1:
        raise ValueError(
            f"{mutant} requires exactly one target statement, found {occurrences}"
        )
    mutated = text.replace(before, after, 1)
    diff = "".join(
        difflib.unified_diff(
            text.splitlines(keepends=True),
            mutated.splitlines(keepends=True),
            fromfile=str(TARGET),
            tofile=f"{TARGET}::{mutant}",
        )
    )
    return mutated, {
        "mutant": mutant,
        "target_logical_path": str(TARGET),
        "match_count": occurrences,
        "original_source_sha256": _digest(text.encode("utf-8")),
        "mutated_source_sha256": _digest(mutated.encode("utf-8")),
        "unified_diff": diff,
    }


def validate_baseline(
    candidate_path: Path, baseline_result_path: Path
) -> dict[str, object]:
    baseline = json.loads(baseline_result_path.read_text(encoding="utf-8"))
    expected = _digest(candidate_path.read_bytes())
    if baseline.get("candidate_sha256") != expected:
        raise ValueError("baseline candidate identity does not match candidate")
    if baseline.get("status") != "passed":
        raise ValueError("interaction mutants require a passing original baseline")
    test = baseline.get("test")
    if not isinstance(test, dict) or test.get("junit_ok") is not True:
        raise ValueError("baseline must contain a successful JUnit receipt")
    return baseline


def _work_mount(command: list[str]) -> Path:
    mounts = [
        command[index + 1]
        for index, item in enumerate(command[:-1])
        if item == "-v"
    ]
    matches = [item for item in mounts if item.endswith(":/work:rw")]
    if len(matches) != 1:
        raise RuntimeError("expected exactly one evaluator work mount")
    return Path(matches[0][: -len(":/work:rw")])


@contextmanager
def _patched_run(mutant: str) -> Iterator[dict[str, object]]:
    original_run = java_evaluate._run
    evidence: dict[str, object] = {}
    mutated = False

    def run(command: list[str], timeout: float = java_evaluate.EVALUATION_TIMEOUT_SECONDS):
        nonlocal mutated
        if "javac" in command and not mutated:
            work = _work_mount(command)
            target = work / TARGET
            if not target.is_file() or target.is_symlink():
                raise RuntimeError("temporary admitted target source is unavailable")
            original = target.read_text(encoding="utf-8")
            changed, mutation = mutate_source(original, mutant)
            target.write_text(changed, encoding="utf-8")
            evidence.update(mutation)
            evidence["mutation_applied_before_command"] = list(command)
            mutated = True
        return original_run(command, timeout=timeout)

    java_evaluate._run = run
    try:
        yield evidence
    finally:
        java_evaluate._run = original_run
        if not mutated:
            evidence["mutation_not_applied"] = True


def classify_failure(receipt: dict[str, object], output: Path) -> dict[str, object]:
    if receipt.get("status") == "passed":
        return {"kind": "survived", "mock_assertion_detected": False}
    compile_result = receipt.get("compile")
    if not isinstance(compile_result, dict) or compile_result.get("exit_code") != 0:
        return {"kind": "compile_or_infrastructure_failure", "mock_assertion_detected": False}
    stdout = (output / "test.stdout.txt").read_text(encoding="utf-8")
    stderr = (output / "test.stderr.txt").read_text(encoding="utf-8")
    combined = stdout + "\n" + stderr
    mock_markers = (
        "org.mockito.exceptions.verification.",
        "TooManyActualInvocations",
        "WantedButNotInvoked",
        "NeverWantedButInvoked",
        "Wanted 1 time",
    )
    if any(marker in combined for marker in mock_markers):
        return {"kind": "mock_assertion_failure", "mock_assertion_detected": True}
    if receipt.get("status") == "test_failed":
        return {"kind": "other_test_failure", "mock_assertion_detected": False}
    return {"kind": "execution_failure", "mock_assertion_detected": False}


def evaluate(
    candidate_path: Path,
    output: Path,
    old_root: Path,
    mutant: str,
    baseline_result_path: Path,
) -> dict[str, object]:
    """Evaluate one interaction mutant, preserving the existing evaluator contract."""
    baseline = validate_baseline(candidate_path, baseline_result_path)
    if output.exists():
        raise FileExistsError("output directory must not exist")
    with _patched_run(mutant) as mutation:
        receipt = java_evaluate.evaluate(candidate_path, output, old_root)
    if mutation.get("mutation_not_applied"):
        raise RuntimeError("evaluator never reached the source compilation step")
    classification = classify_failure(receipt, output)
    receipt["interaction_mutation"] = {
        **mutation,
        "mutated_program": True,
        "case_specific_research_mutant": True,
        "baseline_result_sha256": _digest(baseline_result_path.read_bytes()),
        "baseline_candidate_sha256": baseline["candidate_sha256"],
        "baseline_status": baseline["status"],
        "candidate_injected_source_sha256": receipt["candidate_source_sha256"],
        "wrapper_sha256": _digest(Path(__file__).read_bytes()),
        "failure_classification": classification,
        "detected_by_mock_assertion": classification["mock_assertion_detected"],
    }
    _write_json(output / "result.json", receipt)
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidate", required=True, type=Path)
    parser.add_argument("--baseline-result", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--old-root", required=True, type=Path)
    parser.add_argument("--mutant", required=True, choices=sorted(MUTATIONS))
    args = parser.parse_args()
    result = evaluate(
        args.candidate,
        args.output,
        args.old_root,
        args.mutant,
        args.baseline_result,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    classification = result["interaction_mutation"]["failure_classification"]
    return 0 if classification["kind"] in {"survived", "mock_assertion_failure"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
