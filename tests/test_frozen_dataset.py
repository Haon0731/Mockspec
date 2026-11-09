from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from mock_spec.adapters.frozen_dataset import FrozenDataset
from mock_spec.validation import InputValidationError


REVISION = "a" * 64
DATASET = "synthetic-agent-inputs"
CASE_ID = "case-without-legacy-review"
SNAPSHOT_ID = "b" * 64


def _canonical(value: object) -> str:
    payload = json.dumps(
        value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")
    ).encode()
    return hashlib.sha256(payload).hexdigest()


def _write(path: Path, payload: bytes) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return hashlib.sha256(payload).hexdigest()


def _mask(language: str, *, interleaved: bool = False) -> bytes:
    if language == "python":
        body = (
            b"def test_target():\n"
            b"    # === PY-SLOT-START:mock_stub_001 ===\n"
            b"    pass\n"
            b"    # === PY-SLOT-END:mock_stub_001 ===\n"
            b"    result = target()\n"
            b"    # === PY-SLOT-START:verify_001 ===\n"
            b"    pass\n"
            b"    # === PY-SLOT-END:verify_001 ===\n"
        )
        if interleaved:
            body += (
                b"    # === PY-SLOT-START:mock_stub_002 ===\n"
                b"    pass\n"
                b"    # === PY-SLOT-END:mock_stub_002 ===\n"
            )
        return body + b"    assert result == 1\n"
    return (
        b"class TargetTest { void testTarget() {\n"
        b"// === MOCK-STUB-START ===\n// === MOCK-STUB-END ===\n"
        b"assert target() == 1;\n"
        b"// === VERIFY-START ===\n// === VERIFY-END ===\n} }\n"
    )


def _snapshot(
    tmp_path: Path, language: str = "python", *, slot_mismatch: bool = False,
    private_source: bool = False, interleaved: bool = False,
) -> tuple[Path, Path]:
    registry = tmp_path / "registry"
    root = registry / "formal-snapshots" / DATASET / SNAPSHOT_ID
    extension = "py" if language == "python" else "java"
    target = f"repo/tests/test_target.{extension}"
    mut = f"repo/src/target.{extension}"
    support = f"repo/src/dependency.{extension}"
    files = {
        target: _mask(language, interleaved=interleaved),
        mut: (b"def target():\n    return dependency()\n" if language == "python"
              else b"class Target { int target() { return dependency(); } }\n"),
        support: (b"def dependency():\n    return 1\n" if language == "python"
                  else b"class Dependency { int dependency() { return 1; } }\n"),
        "apply_regions.py": (
            (
                "MOCK_EXPECTED = ['wrong_slot']\n"
                if slot_mismatch else (
                    "MOCK_EXPECTED = ['mock_stub_001', 'mock_stub_002']\n"
                    if interleaved else "MOCK_EXPECTED = ['mock_stub_001']\n"
                )
            ) + "VERIFY_EXPECTED = ['verify_001']\n"
        ).encode() if language == "python" else b"# Java region injector\n",
        "repo/libs/ignored.java": b"class MustNotBeExposed {}\n",
    }
    if private_source:
        files["repo/.artifacts/secret.py"] = b"secret = 'gold'\n"
    entries = []
    for relative, payload in files.items():
        entries.append({
            "relpath": relative, "kind": "file", "mode": 0o444,
            "sha256": _write(root / "tree" / relative, payload),
        })
    entries.sort(key=lambda entry: entry["relpath"])
    manifest = {"entries": entries, "digest": _canonical(entries)}
    _write(
        root / "control/tree-manifest.json",
        json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode(),
    )
    runtime_content = {
        "schema_version": "developer-realistic-runtime-lock/v1", "language": language,
        "runtime_name": "cpython" if language == "python" else "openjdk-javac",
        "runtime_version": "3.10" if language == "python" else "11.0.31",
        "executable_sha256": "c" * 64, "dependency_hashes": [], "environment": [],
    }
    runtime = {**runtime_content, "digest": _canonical(runtime_content)}
    _write(
        root / "control/runtime-lock.json",
        json.dumps(runtime, sort_keys=True, separators=(",", ":")).encode(),
    )
    ref = {
        "schema_version": "developer-realistic-snapshot-ref/v1",
        "protocol_version": "developer_realistic_workspace_v1",
        "snapshot_id": SNAPSHOT_ID, "dataset": DATASET,
        "inventory_revision": REVISION, "case_id": CASE_ID, "language": language,
        "tree_manifest_digest": manifest["digest"],
        "runtime_lock_digest": runtime["digest"], "target_relpath": target,
        "target_selector": "tests.test_target::test_target",
        "mask_sha256": next(e["sha256"] for e in entries if e["relpath"] == target),
        "slot_contract_digest": "d" * 64 if language == "python" else None,
        "identity_inputs_digest": SNAPSHOT_ID, "state": "frozen",
    }
    _write(
        root / "control/snapshot-ref.json",
        json.dumps(ref, sort_keys=True, separators=(",", ":")).encode(),
    )
    index_content = {
        "schema_version": "developer-realistic-snapshot-index/v1",
        "protocol_version": "developer_realistic_workspace_v1", "dataset": DATASET,
        "inventory_revision": REVISION, "cases": {CASE_ID: SNAPSHOT_ID},
    }
    index = {**index_content, "index_digest": _canonical(index_content)}
    _write(
        registry / "formal-snapshots/indexes" / DATASET / f"{REVISION}.json",
        json.dumps(index, sort_keys=True, separators=(",", ":")).encode(),
    )
    return registry, root


@pytest.mark.parametrize("language", ["java", "python"])
def test_loads_upstream_case_without_legacy_review(language, tmp_path):
    registry, _ = _snapshot(tmp_path, language)
    dataset = FrozenDataset(registry, dataset=DATASET, inventory_revision=REVISION)
    assert dataset.case_ids == (CASE_ID,)
    assert dict(dataset.snapshot_ids) == {CASE_ID: SNAPSHOT_ID}
    with pytest.raises(TypeError):
        dataset.snapshot_ids[CASE_ID] = "e" * 64
    with dataset.load_case(CASE_ID) as case:
        assert case.case_id == CASE_ID and case.snapshot_id == SNAPSHOT_ID
        assert case.language == language
        assert case.masked_test.source_id == "test"
        assert case.masked_test.logical_path in case.source_paths
        assert not any("libs" in path or ".artifacts" in path for path in case.source_paths)
        task = case.to_task(
            f"src/target.{case.source_paths[0].rsplit('.', 1)[-1]}",
            supporting_paths=(f"src/dependency.{case.source_paths[0].rsplit('.', 1)[-1]}",),
        )
        serialized = json.dumps(task.to_dict())
        assert task.input_policy.policy_id == "stub-tester-frozen-agent/v1"
        assert task.permitted_interfaces == ()
        assert "control/" not in serialized and str(registry) not in serialized
    with pytest.raises(InputValidationError, match="closed"):
        case.read_source(case.masked_test.logical_path)


def test_private_and_dependency_paths_are_not_exposed(tmp_path):
    registry, _ = _snapshot(tmp_path, private_source=True)
    with FrozenDataset(registry, dataset=DATASET, inventory_revision=REVISION).load_case(
        CASE_ID
    ) as case:
        assert ".artifacts/secret.py" not in case.source_paths
        with pytest.raises(InputValidationError):
            case.read_source(".artifacts/secret.py")
        with pytest.raises(InputValidationError):
            case.read_source("../control/snapshot-ref.json")


@pytest.mark.parametrize(
    "directory",
    ["raw", "outputs", "gold", "gold_metadata", "preparation-private",
     "semantic_ir", "masked_templates"],
)
def test_control_and_answer_source_directories_are_not_exposed(tmp_path, directory):
    registry, root = _snapshot(tmp_path)
    relative = f"repo/{directory}/answer.py"
    payload = b"answer = 'private'\n"
    digest = _write(root / "tree" / relative, payload)
    manifest_path = root / "control/tree-manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["entries"].append({
        "relpath": relative, "kind": "file", "mode": 0o444, "sha256": digest,
    })
    manifest["entries"].sort(key=lambda entry: entry["relpath"])
    manifest["digest"] = _canonical(manifest["entries"])
    manifest_path.write_text(json.dumps(manifest))
    ref_path = root / "control/snapshot-ref.json"
    ref = json.loads(ref_path.read_text())
    ref["tree_manifest_digest"] = manifest["digest"]
    ref_path.write_text(json.dumps(ref))
    with FrozenDataset(registry, dataset=DATASET, inventory_revision=REVISION).load_case(
        CASE_ID
    ) as case:
        assert f"{directory}/answer.py" not in case.source_paths
        with pytest.raises(InputValidationError, match="denied"):
            case.read_source(f"{directory}/answer.py")


def test_unregistered_case_is_rejected(tmp_path):
    registry, _ = _snapshot(tmp_path)
    dataset = FrozenDataset(registry, dataset=DATASET, inventory_revision=REVISION)
    with pytest.raises(InputValidationError, match="absent"):
        dataset.load_case("not-in-index")


@pytest.mark.parametrize("part", ["index", "ref", "manifest", "runtime", "mask"])
def test_identity_changes_fail_closed(tmp_path, part):
    registry, root = _snapshot(tmp_path)
    paths = {
        "index": registry / "formal-snapshots/indexes" / DATASET / f"{REVISION}.json",
        "ref": root / "control/snapshot-ref.json",
        "manifest": root / "control/tree-manifest.json",
        "runtime": root / "control/runtime-lock.json",
        "mask": root / "tree/repo/tests/test_target.py",
    }
    path = paths[part]
    if part == "mask":
        path.write_bytes(path.read_bytes() + b"# changed\n")
    else:
        data = json.loads(path.read_text())
        key = {
            "index": "index_digest", "ref": "mask_sha256",
            "manifest": "digest", "runtime": "digest",
        }[part]
        data[key] = "f" * 64
        path.write_text(json.dumps(data))
    if part == "index":
        with pytest.raises(InputValidationError):
            FrozenDataset(registry, dataset=DATASET, inventory_revision=REVISION)
    else:
        dataset = FrozenDataset(registry, dataset=DATASET, inventory_revision=REVISION)
        with pytest.raises(InputValidationError):
            dataset.load_case(CASE_ID)


def test_python_slots_are_checked_against_pinned_injector(tmp_path):
    registry, _ = _snapshot(tmp_path, slot_mismatch=True)
    dataset = FrozenDataset(registry, dataset=DATASET, inventory_revision=REVISION)
    with pytest.raises(InputValidationError, match="slots differ"):
        dataset.load_case(CASE_ID)


def test_python_mock_and_verify_slots_may_be_interleaved(tmp_path):
    registry, _ = _snapshot(tmp_path, interleaved=True)
    with FrozenDataset(registry, dataset=DATASET, inventory_revision=REVISION).load_case(
        CASE_ID
    ) as case:
        assert case.language == "python"


def test_symlink_substitution_is_rejected(tmp_path):
    registry, root = _snapshot(tmp_path)
    target = root / "tree/repo/tests/test_target.py"
    replacement = root / "tree/repo/tests/replacement.py"
    replacement.write_bytes(target.read_bytes())
    target.unlink()
    target.symlink_to(replacement.name)
    dataset = FrozenDataset(registry, dataset=DATASET, inventory_revision=REVISION)
    with pytest.raises(InputValidationError, match="symlink"):
        dataset.load_case(CASE_ID)


def test_duplicate_manifest_paths_are_rejected_even_when_rehashed(tmp_path):
    registry, root = _snapshot(tmp_path)
    manifest_path = root / "control/tree-manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["entries"].append(dict(manifest["entries"][-1]))
    manifest["digest"] = _canonical(manifest["entries"])
    manifest_path.write_text(json.dumps(manifest))
    ref_path = root / "control/snapshot-ref.json"
    ref = json.loads(ref_path.read_text())
    ref["tree_manifest_digest"] = manifest["digest"]
    ref_path.write_text(json.dumps(ref))
    dataset = FrozenDataset(registry, dataset=DATASET, inventory_revision=REVISION)
    with pytest.raises(InputValidationError, match="manifest identity"):
        dataset.load_case(CASE_ID)


def test_duplicate_metadata_fields_are_rejected(tmp_path):
    registry, _ = _snapshot(tmp_path)
    index = registry / "formal-snapshots/indexes" / DATASET / f"{REVISION}.json"
    index.write_text('{"schema_version":"one","schema_version":"two"}')
    with pytest.raises(InputValidationError, match="invalid frozen metadata"):
        FrozenDataset(registry, dataset=DATASET, inventory_revision=REVISION)


def test_lazy_source_read_rechecks_manifest_digest(tmp_path):
    registry, root = _snapshot(tmp_path)
    case = FrozenDataset(registry, dataset=DATASET, inventory_revision=REVISION).load_case(
        CASE_ID
    )
    (root / "tree/repo/src/target.py").write_text("def changed():\n    return 2\n")
    try:
        with pytest.raises(InputValidationError, match="identity changed"):
            case.read_source("src/target.py")
    finally:
        case.close()


def test_to_task_rejects_masked_test_as_supporting_source(tmp_path):
    registry, _ = _snapshot(tmp_path)
    with FrozenDataset(registry, dataset=DATASET, inventory_revision=REVISION).load_case(
        CASE_ID
    ) as case:
        with pytest.raises(InputValidationError, match="distinct"):
            case.to_task(
                "src/target.py", supporting_paths=(case.masked_test.logical_path,)
            )


def test_to_task_rechecks_masked_target_identity(tmp_path):
    registry, root = _snapshot(tmp_path)
    case = FrozenDataset(registry, dataset=DATASET, inventory_revision=REVISION).load_case(
        CASE_ID
    )
    (root / "tree/repo/tests/test_target.py").write_text(
        "def test_target():\n    assert False\n"
    )
    try:
        with pytest.raises(InputValidationError, match="identity changed"):
            case.to_task("src/target.py")
    finally:
        case.close()
