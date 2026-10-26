"""Read only specifically reviewed snapshot files, never a dataset tree."""

from __future__ import annotations

from importlib.resources import files
import os
from pathlib import Path

from ..schemas import UnifiedTask
from ..source_repository import (
    ReviewedRepository,
    open_directory_without_links,
    read_regular_file,
)
from ..validation import (
    InputValidationError, decode_json, validate_java_mask, validate_python_mask,
)


def _require_equal(actual: object, expected: object, label: str) -> None:
    if actual != expected:
        raise InputValidationError(f"{label} differs from reviewed input")


def _build_task(
    review: dict,
    runtime: dict,
    role_sources: dict[str, list[dict]],
    source_index: list[dict],
) -> UnifiedTask:
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


def load_reviewed_task_with_repository(
    snapshot_root: Path, *, case_id: str, language: str
) -> tuple[UnifiedTask, ReviewedRepository]:
    """Load a task and a host-only capability for its reviewed whole source files."""
    catalog = decode_json(files("mock_spec").joinpath("reviewed_inputs.json").read_text())
    if not isinstance(catalog, list):
        raise InputValidationError("invalid installed review catalog")
    matches = [entry for entry in catalog
               if entry["case_id"] == case_id and entry["language"] == language]
    if len(matches) != 1:
        raise InputValidationError("case has no unique approved input review")
    return _load_approved_with_repository(snapshot_root, matches[0])


def _load_approved(snapshot_root: Path, review: dict) -> UnifiedTask:
    """Internal loader. `review` is trusted, code-reviewed host configuration."""
    task, repository = _load_approved_with_repository(snapshot_root, review)
    repository.close()
    return task


def _load_approved_with_repository(
    snapshot_root: Path, review: dict
) -> tuple[UnifiedTask, ReviewedRepository]:
    """Internal loader returning a task plus its non-serializable source capability."""
    root = Path(snapshot_root).absolute()
    if root.name != review["snapshot_id"] or root.parent.name != review["dataset"]:
        raise InputValidationError("expected the exact reviewed frozen snapshot root")
    root_fd = open_directory_without_links(root)
    try:
        controls = {
            key: decode_json(read_regular_file(
                root_fd, entry["path"], entry["sha256"]
            ).decode("utf-8"))
            for key, entry in review["controls"].items()
        }
        ref = controls["snapshot_ref"]
        runtime = controls["runtime_lock"]
        if not isinstance(ref, dict) or not isinstance(runtime, dict):
            raise InputValidationError("invalid reviewed control material")
        for key in ("case_id", "language", "snapshot_id", "dataset", "inventory_revision",
                    "protocol_version", "mask_sha256", "slot_contract_digest",
                    "target_relpath", "target_selector"):
            _require_equal(ref.get(key), review[key], key)
        _require_equal(ref.get("state"), "frozen", "snapshot state")
        _require_equal(ref.get("schema_version"), "developer-realistic-snapshot-ref/v1",
                       "snapshot schema")
        _require_equal(runtime.get("digest"), ref.get("runtime_lock_digest"), "runtime identity")
        _require_equal(runtime.get("language"), review["language"], "runtime language")

        injector = review["injector"]
        read_regular_file(root_fd, injector["path"], injector["sha256"])
        role_sources: dict[str, list[dict]] = {
            "mut": [], "masked_test": [], "supporting": []
        }
        source_index: list[dict] = []
        cached: dict[str, tuple[str, bytes]] = {}
        repository_files: dict[str, tuple[str, str]] = {}
        for item in review["sources"]:
            relative = "tree/" + item["path"]
            existing = repository_files.get(item["logical_path"])
            identity = (relative, item["sha256"])
            if existing is not None:
                _require_equal(existing, identity, "logical source identity")
            repository_files[item["logical_path"]] = identity
            if relative not in cached:
                cached[relative] = (
                    item["sha256"],
                    read_regular_file(root_fd, relative, item["sha256"]),
                )
            else:
                _require_equal(cached[relative][0], item["sha256"], "source identity")
            raw = cached[relative][1]
            try:
                text = raw.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise InputValidationError("reviewed source is not UTF-8 text") from exc
            lines = text.splitlines(keepends=True)
            start, end = item["start_line"], item["end_line"]
            if type(start) is not int or type(end) is not int or not 1 <= start <= end <= len(lines):
                raise InputValidationError("reviewed source range is invalid")
            if item["role"] == "masked_test":
                _require_equal(item["path"], ref["target_relpath"], "masked target")
                _require_equal(item["sha256"], ref["mask_sha256"], "mask digest")
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
            source_index.append({
                "source_id": source["source_id"],
                "logical_path": source["logical_path"],
                "role": item["role"],
            })
        if len(role_sources["mut"]) != 1 or len(role_sources["masked_test"]) != 1:
            raise InputValidationError("review must select exactly one MUT and one masked test")
        task = _build_task(review, runtime, role_sources, source_index)
        # The frozen protocol places the prepared, masked project under tree/repo.
        # Verify the mapping before granting discovery; control/injector siblings
        # remain outside this capability. No new digest approves other inputs.
        if any(
            item["path"] != "repo/" + item["logical_path"]
            for item in review["sources"]
        ):
            raise InputValidationError("reviewed sources do not share the frozen repository root")
        repository_root_fd = os.dup(root_fd)
    finally:
        os.close(root_fd)
    try:
        repository = ReviewedRepository(
            repository_root_fd,
            snapshot_id=review["snapshot_id"],
            files_by_logical_path=repository_files,
            task=task,
            discovery_root="tree/repo",
        )
    finally:
        os.close(repository_root_fd)
    return task, repository
