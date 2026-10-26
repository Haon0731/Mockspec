"""Optional read-only integration tests: no registry scan or dependency access."""

import hashlib
import json
import os
from pathlib import Path

import pytest

from mock_spec.adapters.java import load_task as load_java
from mock_spec.adapters.python import load_task as load_python
from mock_spec.schemas import UnifiedTask
from mock_spec.validation import (
    InputValidationError, SourceStore, validate_java_mask, validate_python_mask,
)


OLD_ROOT = Path(os.environ.get("MOCKSPEC_STUB_TESTER", "/home/haon/projects/Stub-Tester"))
CATALOG = Path(__file__).parents[1] / "src/mock_spec/reviewed_inputs.json"
REVIEWS = json.loads(CATALOG.read_text())
# Scripted scenario fixtures describe specific cases, not every case of a language.
EXAMPLE_CASE_IDS = {
    json.loads(path.read_text())["case_id"]
    for path in (Path(__file__).parents[1] / "examples/scenarios").glob("*.synthetic.json")
}
EXAMPLE_REVIEWS = [review for review in REVIEWS if review["case_id"] in EXAMPLE_CASE_IDS]


def snapshot(review):
    suffix = "max" if review["language"] == "java" else "max-fix23"
    path = (OLD_ROOT / f".omx/developer-realistic-registry-{suffix}" /
            "formal-snapshots" / review["dataset"] / review["snapshot_id"])
    if not path.is_dir() and "MOCKSPEC_STUB_TESTER" not in os.environ:
        pytest.skip("external frozen MockBench snapshot is not available")
    assert path.is_dir(), f"configured real snapshot missing: {path}"
    return path


def fingerprints(paths):
    # Only the small, selected source/control files; never dependency directories.
    return {str(p): (p.stat().st_size, p.stat().st_mtime_ns,
                    hashlib.sha256(p.read_bytes()).hexdigest()) for p in paths}


@pytest.mark.real_data
@pytest.mark.parametrize("review", REVIEWS, ids=lambda review: review["case_id"])
def test_real_frozen_case_loads_without_changing_sources(review):
    root = snapshot(review)
    selected = {root / item["path"] for item in review["controls"].values()}
    selected.add(root / review["injector"]["path"])
    selected.update(root / "tree" / item["path"] for item in review["sources"])
    before = fingerprints(selected)
    loader = load_java if review["language"] == "java" else load_python
    task = loader(root, case_id=review["case_id"])
    assert fingerprints(selected) == before
    assert task.case_id == review["case_id"]
    assert UnifiedTask.from_dict(task.to_dict()) == task
    assert task.masked_test.text == (root / "tree" / review["target_relpath"]).read_text()
    assert task.execution_context.runtime_name in {"cpython", "openjdk-javac"}
    text = json.dumps(task.to_dict())
    for forbidden in (str(OLD_ROOT), "gold_input_path", "gold_mock_stub_text",
                      "candidate_semantic_ir", "runtime_lock_digest", "snapshot_id"):
        assert forbidden not in text
    store = SourceStore(task)
    assert store.get("mut") == task.mut_source
    with pytest.raises(InputValidationError):
        store.get(str(root / "control/runtime-lock.json"))


@pytest.mark.real_data
def test_flask_308_archive_is_not_the_frozen_input():
    review = next(r for r in REVIEWS if r["language"] == "python")
    root = snapshot(review)
    archive = Path("/home/haon/data/experiment-registry/rq234-repeat/runs/"
                   "python-benchmark-new/agent_claudecode__developer_realistic_workspace_v1/"
                   "deepseek-v4-pro/budget_300s/run_1_full_106/raw/oneshot/"
                   "feedback_repo_claudecode__developer_realistic_workspace_v1/"
                   "masked_templates/flask_308.py")
    if not archive.is_file():
        pytest.skip("historical archive is not available on this host")
    raw = archive.read_bytes()
    assert hashlib.sha256(raw).hexdigest() != review["mask_sha256"]
    assert "mock_aborter.side_effect" in raw.decode()
    with pytest.raises(InputValidationError):
        validate_python_mask(raw.decode(), tuple(review["expected_slots"]))
    task = load_python(root, case_id="flask_308")
    assert "mock_aborter.side_effect" not in task.masked_test.text
    assert task.masked_test.text.count("# === PY-SLOT-START:") == 10
    with pytest.raises(InputValidationError):
        load_python(archive.parent, case_id="flask_308")


@pytest.mark.real_data
def test_java_archive_is_not_the_frozen_input():
    review = next(r for r in REVIEWS if r["language"] == "java")
    snapshot(review)
    archive = Path("/home/haon/data/experiment-registry/rq234-repeat/runs/"
                   "java-bechmark-strong/agent_opencode__developer_realistic_workspace_v1/"
                   "deepseek-v4-pro/budget_300s/run_3/raw/masked_templates/"
                   "feedback_repo_opencode__developer_realistic_workspace_v1/"
                   "com.google.cloud.tools.jib.cli.jar.JarFilesTest_"
                   "testToJibContainerBuilder_explodedStandard_basicInfo_69ae66df_e89a13fb5a.java")
    if not archive.is_file():
        pytest.skip("historical archive is not available on this host")
    raw = archive.read_bytes()
    assert hashlib.sha256(raw).hexdigest() != review["mask_sha256"]
    with pytest.raises(InputValidationError):
        validate_java_mask(raw.decode())
