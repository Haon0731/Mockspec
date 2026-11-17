"""Fixed context, control-flow integrity, and frozen receipts gate evaluation."""
import hashlib
import json
from pathlib import Path
import subprocess

import pytest

import evaluate_candidate as subject
from evaluate_candidate import extract_regions


@pytest.mark.parametrize("language,start,end", [
    ("python", "# === PY-SLOT-START:mock_stub_001 ===", "# === PY-SLOT-END:mock_stub_001 ==="),
    ("java", "// === MOCK-STUB-START ===", "// === MOCK-STUB-END ==="),
])
def test_fixed_context_and_marker_changes_rejected(language, start, end):
    original = f"{start}\npass\n{end}\nassert True\n"
    if language == "java":
        original += "// === VERIFY-START ===\n// === VERIFY-END ===\n"
    candidate = original.replace("pass", "stub = 1")
    assert "stub = 1" in extract_regions(original, candidate, language)[0]
    with pytest.raises(ValueError, match="fixed test context"):
        extract_regions(original, candidate.replace("assert True", "assert False"), language)
    with pytest.raises(ValueError, match="identities"):
        extract_regions(original, candidate.replace(start, "removed"), language)


def test_python_bad_syntax_is_not_execution_failure():
    original = "# === PY-SLOT-START:mock_stub_001 ===\npass\n# === PY-SLOT-END:mock_stub_001 ===\n"
    with pytest.raises(SyntaxError):
        extract_regions(original, original.replace("pass", "if ???"), "python")


@pytest.mark.parametrize(
    "body",
    ["return", "yield 1", "pytest.skip('x')", "pytest.assume(False)",
     "hypothesis.assume(False)", "sys.exit(0)", "raise SystemExit(0)",
     "raise unittest.SkipTest()"],
)
def test_python_slot_cannot_bypass_original_observations(body: str) -> None:
    original = '''def test_case():
    # === PY-SLOT-START:mock_stub_001 ===
    pass
    # === PY-SLOT-END:mock_stub_001 ===
    assert observed
'''
    candidate = original.replace("    pass\n", f"    {body}\n")
    with pytest.raises(ValueError, match="forbidden"):
        extract_regions(original, candidate, "python")


def test_python_local_helper_may_return_normally() -> None:
    original = '''def test_case():
    # === PY-SLOT-START:mock_stub_001 ===
    pass
    # === PY-SLOT-END:mock_stub_001 ===
    assert observed
'''
    candidate = original.replace(
        "    pass\n", "    def helper():\n        return 1\n    value = helper()\n",
    )
    stub, _verify = extract_regions(original, candidate, "python")
    assert "return 1" in stub


def test_java_slot_rejects_early_return_assume_and_process_exit() -> None:
    original = '''// === MOCK-STUB-START ===
// === MOCK-STUB-END ===
assertTrue(observed);
// === VERIFY-START ===
// === VERIFY-END ===
'''
    for body in ("return;", "Assume.assumeTrue(false);", "System.exit(0);"):
        candidate = original.replace("// === MOCK-STUB-END ===", body + "\n// === MOCK-STUB-END ===")
        with pytest.raises(ValueError, match="forbidden"):
            extract_regions(original, candidate, "java")


def test_java_lambda_helper_may_return_normally() -> None:
    original = '''// === MOCK-STUB-START ===
// === MOCK-STUB-END ===
assertTrue(observed);
// === VERIFY-START ===
// === VERIFY-END ===
'''
    candidate = original.replace(
        "// === MOCK-STUB-END ===",
        'Answer<String> answer = invocation -> { return "ok"; };\n// === MOCK-STUB-END ===',
    )
    stub, _verify = extract_regions(original, candidate, "java")
    assert "return" in stub


def _evaluation_fixture(tmp_path: Path) -> tuple[Path, Path, Path, dict]:
    snapshot = tmp_path / "snapshot"
    registry = tmp_path / "registry"
    arm = tmp_path / "arm"
    target = "tests/test_case.py"
    masked = '''def test_case():
    # === PY-SLOT-START:mock_stub_001 ===
    pass
    # === PY-SLOT-END:mock_stub_001 ===
    assert True
'''
    candidate = masked.replace("    pass\n", "    value = 1\n")
    files = {
        f"repo/{target}": masked,
        "apply_regions.py": '''MOCK_EXPECTED = ["mock_stub_001"]
VERIFY_EXPECTED = []
def parse_slots(text, expected):
    result = {}
    for slot in expected:
        start = f"# === PY-SLOT-START:{slot} ==="
        end = f"# === PY-SLOT-END:{slot} ==="
        result[slot] = text.split(start, 1)[1].split(end, 1)[0].strip()
    return result
def replace_region(source, slot, body):
    start = f"# === PY-SLOT-START:{slot} ==="
    end = f"# === PY-SLOT-END:{slot} ==="
    start_line = source.rfind("\\n", 0, source.index(start)) + 1
    end_line = source.index("\\n", source.index(end)) + 1
    indent = source[start_line:source.index(start)]
    replacement = "\\n".join(indent + line for line in body.splitlines()) + "\\n"
    return source[:start_line] + replacement + source[end_line:]
''',
        "run_target_test.py": "# frozen runner\n",
    }
    entries = []
    for relative, content in files.items():
        path = snapshot / "tree" / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        entries.append({"relpath": relative, "kind": "file",
                        "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
    control = snapshot / "control"
    control.mkdir()
    (control / "tree-manifest.json").write_text(json.dumps({"entries": entries}))
    bundle = "a" * 64
    (control / "runtime-lock.json").write_text(json.dumps({
        "digest": "lock", "environment": [["STUBTESTER_PYTHON_DEPENDENCY_BUNDLE", bundle]],
    }))
    (registry / "formal-snapshots/runtime-dependencies/python" / bundle / "site-packages").mkdir(
        parents=True,
    )
    workspace = arm / "workspace"
    workspace.mkdir(parents=True)
    (workspace / "candidate.py").write_text(candidate, encoding="utf-8")
    manifest = {"case_id": "case", "candidate_path": "candidate.py",
                "target_path": target,
                "source_hashes": {target: hashlib.sha256(masked.encode()).hexdigest()}}
    return snapshot, registry, arm, manifest


def _fake_docker(
    monkeypatch: pytest.MonkeyPatch, arm: Path, *, returncode: int,
    receipt: dict | None, injection: bool, preview_matches: bool = True,
) -> None:
    def run(command, **_kwargs):
        if command[:3] == ["docker", "image", "inspect"]:
            return subprocess.CompletedProcess(command, 0, stdout="sha256:image\n", stderr="")
        runtime = arm / "evaluation/runtime"
        if injection:
            preview = runtime / "control_outputs/candidate_preview"
            preview.mkdir(parents=True, exist_ok=True)
            expected = subject._expected_injected_candidate(
                runtime,
                (runtime / "repo/tests/test_case.py").read_text(),
                (runtime / "mock_stub.txt").read_text(),
                (runtime / "verify.txt").read_text(),
                "python",
            )
            if not preview_matches:
                expected += "# drift\n"
            (preview / "candidate_test.py").write_text(expected)
            (preview / "result.json").write_text(json.dumps({
                "ok": True,
                "candidate_test": "control_outputs/candidate_preview/candidate_test.py",
            }))
        if receipt is not None:
            output = runtime / "control_outputs/feedback_compile"
            output.mkdir(parents=True, exist_ok=True)
            (output / "result.json").write_text(json.dumps(receipt))
        return subprocess.CompletedProcess(command, returncode, stdout="", stderr="")

    monkeypatch.setattr(subject.subprocess, "run", run)


def test_zero_exit_requires_receipts_and_reports_only_original_test_pass(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot, registry, arm, manifest = _evaluation_fixture(tmp_path)
    _fake_docker(monkeypatch, arm, returncode=0,
                 receipt={"returncode": 0, "stage": "pytest", "target": manifest["target_path"]},
                 injection=True)
    result = subject.evaluate(snapshot, registry, arm, manifest, "python")
    assert result["status"] == "original_test_pass"
    assert result["original_test_pass"] is True
    assert result["semantic_correctness"] == "not_evaluated"
    assert result["defect_detection"] == "not_evaluated"
    assert result["candidate_regions_injected"] is True


def test_zero_exit_without_runner_receipt_is_infrastructure_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot, registry, arm, manifest = _evaluation_fixture(tmp_path)
    _fake_docker(monkeypatch, arm, returncode=0, receipt=None, injection=True)
    result = subject.evaluate(snapshot, registry, arm, manifest, "python")
    assert result["status"] == "infrastructure_failure"
    assert "receipt" in result["diagnostic"]


def test_zero_exit_with_inconsistent_injected_candidate_is_infrastructure_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot, registry, arm, manifest = _evaluation_fixture(tmp_path)
    _fake_docker(
        monkeypatch, arm, returncode=0,
        receipt={"returncode": 0, "stage": "pytest", "target": manifest["target_path"]},
        injection=True, preview_matches=False,
    )
    result = subject.evaluate(snapshot, registry, arm, manifest, "python")
    assert result["status"] == "infrastructure_failure"
    assert "differs" in result["diagnostic"]


def test_nonzero_test_receipt_is_original_test_failure_not_semantic_verdict(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot, registry, arm, manifest = _evaluation_fixture(tmp_path)
    _fake_docker(
        monkeypatch, arm, returncode=1,
        receipt={"returncode": 1, "stage": "pytest", "target": manifest["target_path"]},
        injection=True,
    )
    result = subject.evaluate(snapshot, registry, arm, manifest, "python")
    assert result["status"] == "original_test_failed"
    assert result["failure_stage"] == "test"
    assert result["semantic_correctness"] == "not_evaluated"


@pytest.mark.parametrize(
    ("stage", "expected"),
    [("apply_regions", "compile"), ("import", "import"),
     ("pytest", "test"), (None, "unknown")],
)
def test_nonzero_classification_uses_only_receipt_stage(stage, expected) -> None:
    assert subject._failure_category({"stage": stage}) == expected
