from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from mock_spec.adapters import java, python
from mock_spec.adapters._frozen import _load_approved, _load_approved_with_repository
from mock_spec.validation import InputValidationError


def _encoded(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _digest(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _write(root: Path, relative: str, payload: bytes) -> str:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return _digest(payload)


def _mask(language: str) -> tuple[bytes, list[str]]:
    if language == "python":
        return (
            b"def test_target():\n"
            b"    # === PY-SLOT-START:mock_stub_001 ===\n"
            b"    pass\n"
            b"    # === PY-SLOT-END:mock_stub_001 ===\n"
            b"    assert target() == 1\n",
            ["mock_stub_001"],
        )
    return (
        b"class TargetTest {\n"
        b"  void testTarget() {\n"
        b"    // === MOCK-STUB-START ===\n"
        b"    // === MOCK-STUB-END ===\n"
        b"    assertEquals(1, target());\n"
        b"    // === VERIFY-START ===\n"
        b"    // === VERIFY-END ===\n"
        b"  }\n"
        b"}\n",
        [],
    )


def _build_snapshot(tmp_path: Path, language: str) -> tuple[Path, dict]:
    dataset = f"synthetic-{language}-dataset"
    snapshot_id = f"reviewed-{language}-snapshot"
    root = tmp_path / dataset / snapshot_id
    root.mkdir(parents=True)

    masked, expected_slots = _mask(language)
    extension = "py" if language == "python" else "java"
    masked_path = f"repo/tests/test_target.{extension}"
    mut_path = f"repo/src/target.{extension}"
    support_path = f"repo/src/dependency.{extension}"
    mut = b"def target():\n    return dependency()\n" if language == "python" else (
        b"class Target { int target() { return dependency(); } }\n"
    )
    supporting = b"def dependency(): ...\n" if language == "python" else (
        b"interface Dependency { int dependency(); }\n"
    )
    mask_sha = _write(root, f"tree/{masked_path}", masked)
    mut_sha = _write(root, f"tree/{mut_path}", mut)
    support_sha = _write(root, f"tree/{support_path}", supporting)
    injector_sha = _write(root, "tree/apply_regions.py", b"# reviewed injector evidence\n")

    runtime_digest = f"synthetic-{language}-runtime-digest"
    runtime = {
        "digest": runtime_digest,
        "language": language,
        "runtime_name": language,
        "runtime_version": "test-version",
        "private_runtime_path": "/must/not/leak",
    }
    ref = {
        "case_id": f"synthetic_{language}_case",
        "language": language,
        "snapshot_id": snapshot_id,
        "dataset": dataset,
        "inventory_revision": "private-inventory-revision",
        "protocol_version": "developer_realistic_workspace_v1",
        "mask_sha256": mask_sha,
        "slot_contract_digest": "private-slot-contract" if expected_slots else None,
        "target_relpath": masked_path,
        "target_selector": "private-target-selector",
        "state": "frozen",
        "schema_version": "developer-realistic-snapshot-ref/v1",
        "runtime_lock_digest": runtime_digest,
        "private_reference_location": "/must/not/leak",
    }
    ref_payload = _encoded(ref)
    runtime_payload = _encoded(runtime)
    ref_sha = _write(root, "control/snapshot-ref.json", ref_payload)
    runtime_sha = _write(root, "control/runtime-lock.json", runtime_payload)
    symbol = "module.target" if language == "python" else "example.Target#target"
    precision = "function" if language == "python" else "method"
    review = {
        "language": language,
        "case_id": ref["case_id"],
        "snapshot_id": snapshot_id,
        "dataset": dataset,
        "inventory_revision": ref["inventory_revision"],
        "protocol_version": ref["protocol_version"],
        "mask_sha256": mask_sha,
        "slot_contract_digest": ref["slot_contract_digest"],
        "target_relpath": masked_path,
        "target_selector": ref["target_selector"],
        "controls": {
            "snapshot_ref": {"path": "control/snapshot-ref.json", "sha256": ref_sha},
            "runtime_lock": {"path": "control/runtime-lock.json", "sha256": runtime_sha},
        },
        "injector": {"path": "tree/apply_regions.py", "sha256": injector_sha},
        "expected_slots": expected_slots,
        "masking_contract": (
            "python-mask-contract/v3" if language == "python" else "java-dual-region-v1"
        ),
        "test_framework": "pytest" if language == "python" else "JUnit",
        "sources": [
            {
                "role": "masked_test",
                "source_id": "test",
                "path": masked_path,
                "logical_path": f"tests/test_target.{extension}",
                "sha256": mask_sha,
                "start_line": 1,
                "end_line": len(masked.splitlines()),
                "symbol": symbol,
                "target_precision": precision,
                "resolution_status": "resolved",
            },
            {
                "role": "mut",
                "source_id": "mut",
                "path": mut_path,
                "logical_path": f"src/target.{extension}",
                "sha256": mut_sha,
                "start_line": 1,
                "end_line": len(mut.splitlines()),
                "symbol": symbol,
                "target_precision": precision,
                "resolution_status": "resolved",
            },
            {
                "role": "supporting",
                "source_id": "dependency",
                "path": support_path,
                "logical_path": f"src/dependency.{extension}",
                "sha256": support_sha,
                "start_line": 1,
                "end_line": len(supporting.splitlines()),
                "symbol": "module.dependency",
                "target_precision": precision,
                "resolution_status": "partial",
            },
        ],
        "permitted_interfaces": [
            {
                "symbol": "module.dependency",
                "signature": "dependency()",
                "source_id": "dependency",
                "start_line": 1,
                "end_line": 1,
                "resolution_status": "partial",
            }
        ],
    }
    return root, review


def _replace_mask_and_repin(root: Path, review: dict, replacement: bytes) -> None:
    source = next(item for item in review["sources"] if item["role"] == "masked_test")
    mask_sha = _write(root, f"tree/{source['path']}", replacement)
    source["sha256"] = mask_sha
    source["end_line"] = len(replacement.splitlines())
    review["mask_sha256"] = mask_sha
    ref_path = root / review["controls"]["snapshot_ref"]["path"]
    ref = json.loads(ref_path.read_text(encoding="utf-8"))
    ref["mask_sha256"] = mask_sha
    ref_payload = _encoded(ref)
    ref_path.write_bytes(ref_payload)
    review["controls"]["snapshot_ref"]["sha256"] = _digest(ref_payload)


@pytest.mark.parametrize("language", ["java", "python"])
def test_internal_approved_loader_accepts_reviewed_frozen_inputs(
    tmp_path: Path, language: str
) -> None:
    root, review = _build_snapshot(tmp_path, language)

    task = _load_approved(root, review)

    assert task.language == language
    assert task.case_id == review["case_id"]
    assert task.input_policy.visibility == "reviewed_frozen_sources"
    assert task.masked_test.text == (root / "tree" / review["target_relpath"]).read_text()


def test_adapter_passes_reviewed_repository_exclusions(tmp_path: Path) -> None:
    root, review = _build_snapshot(tmp_path, "python")
    review["repository_excluded_paths"] = ["out", "metadata.yml"]
    (root / "tree/repo/out").mkdir()
    (root / "tree/repo/out/build.txt").write_text("hidden\n", encoding="utf-8")
    (root / "tree/repo/metadata.yml").write_text("private\n", encoding="utf-8")

    task, repository = _load_approved_with_repository(root, review)
    try:
        assert task.case_id == review["case_id"]
        assert repository.search(path_pattern="out/**")["error_code"] == "access_denied"
        assert repository.search(path_pattern="metadata.yml")["error_code"] == "access_denied"
    finally:
        repository.close()


@pytest.mark.parametrize(
    "excluded_paths",
    [["../outside"], [""], "out", [1]],
)
def test_adapter_rejects_invalid_repository_exclusions(
    tmp_path: Path, excluded_paths: object
) -> None:
    root, review = _build_snapshot(tmp_path, "python")
    review["repository_excluded_paths"] = excluded_paths

    with pytest.raises(InputValidationError, match="excluded repository paths|relative path"):
        _load_approved_with_repository(root, review)


def test_adapter_rejects_exclusion_of_initial_reviewed_source(tmp_path: Path) -> None:
    root, review = _build_snapshot(tmp_path, "python")
    review["repository_excluded_paths"] = ["src"]

    with pytest.raises(InputValidationError, match="reviewed source is excluded"):
        _load_approved_with_repository(root, review)


@pytest.mark.parametrize(
    ("adapter", "language"),
    [(java.load_task, "java"), (python.load_task, "python")],
)
def test_public_adapter_rejects_case_without_installed_approval(
    tmp_path: Path, adapter, language: str
) -> None:
    root, review = _build_snapshot(tmp_path, language)

    with pytest.raises(InputValidationError, match="no unique approved input review"):
        adapter(root, case_id=review["case_id"])


def test_missing_control_file_fails_closed(tmp_path: Path) -> None:
    root, review = _build_snapshot(tmp_path, "python")
    (root / "control/runtime-lock.json").unlink()

    with pytest.raises(InputValidationError, match="missing reviewed file"):
        _load_approved(root, review)


def test_modified_source_file_fails_identity_check(tmp_path: Path) -> None:
    root, review = _build_snapshot(tmp_path, "java")
    mut = next(item for item in review["sources"] if item["role"] == "mut")
    (root / "tree" / mut["path"]).write_text("changed\n", encoding="utf-8")

    with pytest.raises(InputValidationError, match="reviewed file identity changed"):
        _load_approved(root, review)


@pytest.mark.parametrize("language", ["java", "python"])
def test_re_pinned_non_placeholder_mask_still_fails_content_check(
    tmp_path: Path, language: str
) -> None:
    root, review = _build_snapshot(tmp_path, language)
    if language == "python":
        replacement = _mask(language)[0].replace(b"    pass\n", b"    mock.return_value = 1\n")
    else:
        replacement = _mask(language)[0].replace(
            b"    // === MOCK-STUB-END ===",
            b"    when(dependency.call()).thenReturn(1);\n    // === MOCK-STUB-END ===",
        )
    _replace_mask_and_repin(root, review, replacement)

    with pytest.raises(InputValidationError, match="not empty|non-placeholder"):
        _load_approved(root, review)


def test_traversal_in_reviewed_path_fails_closed(tmp_path: Path) -> None:
    root, review = _build_snapshot(tmp_path, "python")
    review["injector"]["path"] = "../apply_regions.py"

    with pytest.raises(InputValidationError, match="invalid reviewed relative path"):
        _load_approved(root, review)


def test_symlinked_reviewed_source_fails_closed(tmp_path: Path) -> None:
    root, review = _build_snapshot(tmp_path, "python")
    mut = next(item for item in review["sources"] if item["role"] == "mut")
    source_path = root / "tree" / mut["path"]
    outside = tmp_path / "outside.py"
    outside.write_bytes(source_path.read_bytes())
    source_path.unlink()
    source_path.symlink_to(outside)

    with pytest.raises(InputValidationError, match="symlink in reviewed source"):
        _load_approved(root, review)


def test_public_task_serialization_excludes_private_review_metadata(tmp_path: Path) -> None:
    root, review = _build_snapshot(tmp_path, "python")

    serialized = json.dumps(_load_approved(root, review).to_dict(), sort_keys=True)

    for private_value in (
        review["snapshot_id"],
        review["dataset"],
        review["inventory_revision"],
        review["mask_sha256"],
        review["slot_contract_digest"],
        review["target_relpath"],
        review["target_selector"],
        review["controls"]["snapshot_ref"]["sha256"],
        "/must/not/leak",
    ):
        assert private_value not in serialized
    assert "controls" not in serialized
    assert "injector" not in serialized


def test_loading_does_not_modify_snapshot_files(tmp_path: Path) -> None:
    root, review = _build_snapshot(tmp_path, "java")
    before = {
        path.relative_to(root): (path.read_bytes(), path.stat().st_mtime_ns)
        for path in root.rglob("*")
        if path.is_file()
    }

    _load_approved(root, review)

    after = {
        path.relative_to(root): (path.read_bytes(), path.stat().st_mtime_ns)
        for path in root.rglob("*")
        if path.is_file()
    }
    assert after == before
