"""Read only specifically reviewed snapshot files, never a dataset tree."""

from __future__ import annotations

import hashlib
from importlib.resources import files
from pathlib import Path, PurePosixPath

from ..schemas import UnifiedTask
from ..validation import (
    InputValidationError, decode_json, validate_java_mask, validate_python_mask,
)


def _read(root: Path, relative: str, expected_digest: str) -> bytes:
    """Read a single pinned regular file; symlinks are not input capabilities."""
    path = PurePosixPath(relative)
    if (path.is_absolute() or path.as_posix() != relative or "\\" in relative
            or ".." in path.parts or not path.parts):
        raise InputValidationError("invalid reviewed relative path")
    current = root
    for part in path.parts:
        current = current / part
        if current.is_symlink():
            raise InputValidationError(f"symlink in reviewed source: {relative}")
    if not current.is_file():
        raise InputValidationError(f"missing reviewed file: {relative}")
    payload = current.read_bytes()
    if hashlib.sha256(payload).hexdigest() != expected_digest:
        raise InputValidationError(f"reviewed file identity changed: {relative}")
    return payload


def _require_equal(actual: object, expected: object, label: str) -> None:
    if actual != expected:
        raise InputValidationError(f"{label} differs from reviewed input")


def load_reviewed_task(snapshot_root: Path, *, case_id: str, language: str) -> UnifiedTask:
    """Host-only entry: review admission comes from the installed code package."""
    catalog = decode_json(files("mock_spec").joinpath("reviewed_inputs.json").read_text())
    if not isinstance(catalog, list):
        raise InputValidationError("invalid installed review catalog")
    matches = [entry for entry in catalog
               if entry["case_id"] == case_id and entry["language"] == language]
    if len(matches) != 1:
        raise InputValidationError("case has no unique approved input review")
    return _load_approved(snapshot_root, matches[0])


def _load_approved(snapshot_root: Path, review: dict) -> UnifiedTask:
    """Internal loader. `review` is trusted, code-reviewed host configuration."""
    root = Path(snapshot_root).absolute()
    if root.is_symlink() or not root.is_dir():
        raise InputValidationError("snapshot root must be an existing non-symlink directory")
    if root.name != review["snapshot_id"] or root.parent.name != review["dataset"]:
        raise InputValidationError("expected the exact reviewed frozen snapshot root")

    controls = {
        key: decode_json(_read(root, entry["path"], entry["sha256"]).decode("utf-8"))
        for key, entry in review["controls"].items()
    }
    ref = controls["snapshot_ref"]
    runtime = controls["runtime_lock"]
    for key in ("case_id", "language", "snapshot_id", "dataset", "inventory_revision",
                "protocol_version", "mask_sha256", "slot_contract_digest",
                "target_relpath", "target_selector"):
        _require_equal(ref.get(key), review[key], key)
    _require_equal(ref.get("state"), "frozen", "snapshot state")
    _require_equal(ref.get("schema_version"), "developer-realistic-snapshot-ref/v1",
                   "snapshot schema")
    _require_equal(runtime.get("digest"), ref.get("runtime_lock_digest"), "runtime identity")
    _require_equal(runtime.get("language"), review["language"], "runtime language")

    # Review pins the injector as evidence for marker semantics. It is never executed
    # or passed to the Agent, and contains no credentials or reference output.
    injector = review["injector"]
    _read(root, injector["path"], injector["sha256"])
    role_sources: dict[str, list[dict]] = {"mut": [], "masked_test": [], "supporting": []}
    source_index: list[dict] = []
    cached: dict[str, tuple[str, bytes]] = {}
    for item in review["sources"]:
        relative = "tree/" + item["path"]
        if relative not in cached:
            cached[relative] = (item["sha256"], _read(root, relative, item["sha256"]))
        else:
            _require_equal(cached[relative][0], item["sha256"], "source identity")
        raw = cached[relative][1]
        text = raw.decode("utf-8")
        lines = text.splitlines(keepends=True)
        start, end = item["start_line"], item["end_line"]
        if type(start) is not int or type(end) is not int or not 1 <= start <= end <= len(lines):
            raise InputValidationError("reviewed source range is invalid")
        if item["role"] == "masked_test":
            _require_equal(item["path"], ref["target_relpath"], "masked target")
            _require_equal(hashlib.sha256(raw).hexdigest(), ref["mask_sha256"], "mask digest")
            _require_equal((start, end), (1, len(lines)), "complete masked test context")
            if review["language"] == "python":
                validate_python_mask(text, tuple(review["expected_slots"]))
            else:
                validate_java_mask(text)
        source = {
            "source_id": item["source_id"],
            "logical_path": item["logical_path"],
            "text": "".join(lines[start - 1:end]),
            "symbol": item["symbol"],
            "start_line": start,
            "end_line": end,
            "resolution_status": item["resolution_status"],
            "target_precision": item["target_precision"],
        }
        role_sources[item["role"]].append(source)
        source_index.append({"source_id": source["source_id"],
                             "logical_path": source["logical_path"], "role": item["role"]})
    if len(role_sources["mut"]) != 1 or len(role_sources["masked_test"]) != 1:
        raise InputValidationError("review must select exactly one MUT and one masked test")
    # Construct a public allowlist, never spread a reference/manifest object into it.
    return UnifiedTask.from_dict({
        "schema_version": "mock-spec.task/v1",
        "language": review["language"],
        "case_id": review["case_id"],
        "mut_source": role_sources["mut"][0],
        "masked_test": role_sources["masked_test"][0],
        "permitted_interfaces": review["permitted_interfaces"],
        "supporting_sources": role_sources["supporting"],
        "execution_context": {
            "runtime_name": runtime["runtime_name"],
            "runtime_version": runtime["runtime_version"],
            "test_framework": review["test_framework"],
            "test_framework_version": None,
        },
        "input_policy": {
            "policy_id": "mock-spec-reviewed-frozen/v1",
            "protocol_version": review["protocol_version"],
            "masking_contract": review["masking_contract"],
            "visibility": "reviewed_frozen_sources",
        },
        "sources": source_index,
    })
