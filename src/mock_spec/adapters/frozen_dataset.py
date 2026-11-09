"""Read Stub-Tester frozen Agent inputs without the legacy per-case catalog.

This adapter trusts the upstream frozen-workspace protocol and revalidates its
index/control identities plus every file it actually reads.  It never imports or
executes Stub-Tester code, reads private/gold data, or hashes dependency trees.
"""

from __future__ import annotations

import ast
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import tokenize
from types import MappingProxyType
from typing import Any

from ..schemas import Source, UnifiedTask
from ..source_repository import (
    _read_regular_file,
    open_directory_without_links,
    read_regular_file,
)
from ..validation import (
    InputValidationError,
    decode_json,
    validate_java_mask,
    validate_python_mask,
)


_PROTOCOL = "developer_realistic_workspace_v1"
_INDEX_SCHEMA = "developer-realistic-snapshot-index/v1"
_REF_SCHEMA = "developer-realistic-snapshot-ref/v1"
_RUNTIME_SCHEMA = "developer-realistic-runtime-lock/v1"
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_MAX_METADATA_BYTES = 16 * 1024 * 1024
_SOURCE_SUFFIXES = {"java": ".java", "python": ".py"}
_DENIED_SOURCE_PARTS = frozenset({
    ".git", ".omx", ".codex", ".agents", ".aws", ".ssh", ".venv", "venv",
    "__pycache__", "node_modules", "site-packages", "libs", "control",
    "control_outputs", "scratch", "archive", "archives", "artifacts", ".artifacts",
    "evaluation", "evaluations", "grading", "grader", "reference_answers",
    "raw", "outputs", "gold", "gold_metadata", "preparation-private", "semantic_ir",
    "masked_templates",
})


def _canonical_digest(value: object) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _require_digest(value: object, label: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise InputValidationError(f"{label} must be a SHA-256 digest")
    return value


def _require_exact_fields(value: object, expected: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != expected:
        raise InputValidationError(f"{label} fields are invalid")
    return value


def _metadata(root_fd: int, relative: str) -> dict[str, Any]:
    payload, _ = _read_regular_file(root_fd, relative, maximum_bytes=_MAX_METADATA_BYTES)
    try:
        value = decode_json(payload.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise InputValidationError(f"invalid frozen metadata: {relative}") from exc
    if not isinstance(value, dict):
        raise InputValidationError(f"frozen metadata must be an object: {relative}")
    return value


def _relative_path(value: object) -> str:
    if not isinstance(value, str) or "\\" in value or "\x00" in value:
        raise InputValidationError("invalid frozen relative path")
    path = PurePosixPath(value)
    if (
        not path.parts or path.is_absolute() or path.as_posix() != value
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise InputValidationError("invalid frozen relative path")
    return value


def _source_path(value: object) -> str:
    parsed = _relative_path(value)
    if any(
        part in _DENIED_SOURCE_PARTS or part.startswith(".")
        for part in PurePosixPath(parsed).parts
    ):
        raise InputValidationError("denied frozen source path")
    return parsed


def _source_id(prefix: str, logical_path: str) -> str:
    digest = hashlib.sha256(logical_path.encode("utf-8")).hexdigest()[:16]
    return f"{prefix}:{digest}"


def _literal_string_list(tree: ast.Module, name: str) -> tuple[str, ...]:
    matches = [
        node for node in tree.body
        if isinstance(node, (ast.Assign, ast.AnnAssign))
        and (
            any(isinstance(target, ast.Name) and target.id == name for target in node.targets)
            if isinstance(node, ast.Assign)
            else isinstance(node.target, ast.Name) and node.target.id == name
        )
    ]
    if len(matches) != 1:
        raise InputValidationError(f"Python injector must define {name} exactly once")
    value_node = matches[0].value
    if value_node is None:
        raise InputValidationError(f"Python injector {name} must have a value")
    try:
        value = ast.literal_eval(value_node)
    except (ValueError, TypeError) as exc:
        raise InputValidationError(f"Python injector {name} must be a literal list") from exc
    if (
        not isinstance(value, list) or not value
        or any(type(item) is not str or not item for item in value)
        or len(set(value)) != len(value)
    ):
        raise InputValidationError(f"Python injector {name} is invalid")
    return tuple(value)


def _python_slot_order(text: str) -> tuple[str, ...]:
    marker = re.compile(r"# === PY-SLOT-START:([a-z0-9_]+) ===\Z")
    try:
        slots = tuple(
            match.group(1)
            for token in tokenize.generate_tokens(io.StringIO(text).readline)
            if token.type == tokenize.COMMENT
            for match in [marker.fullmatch(token.string)]
            if match is not None
        )
    except (SyntaxError, tokenize.TokenError) as exc:
        raise InputValidationError("invalid masked Python syntax") from exc
    if not slots or len(set(slots)) != len(slots):
        raise InputValidationError("Python mask has invalid slot starts")
    return slots


class FrozenDataset:
    """One immutable Stub-Tester inventory, validated from its closed index."""

    def __init__(
        self, registry_root: Path, *, dataset: str, inventory_revision: str
    ) -> None:
        if (
            type(dataset) is not str or not dataset or "/" in dataset or "\\" in dataset
            or dataset in {".", ".."}
        ):
            raise InputValidationError("dataset must be one path segment")
        _require_digest(inventory_revision, "inventory_revision")
        self.registry_root = Path(registry_root).absolute()
        self.dataset = dataset
        self.inventory_revision = inventory_revision
        root_fd = open_directory_without_links(self.registry_root)
        try:
            relative = (
                f"formal-snapshots/indexes/{dataset}/{inventory_revision}.json"
            )
            index = _metadata(root_fd, relative)
        finally:
            os.close(root_fd)
        expected = {
            "schema_version", "protocol_version", "dataset", "inventory_revision",
            "cases", "index_digest",
        }
        _require_exact_fields(index, expected, "snapshot index")
        if index["schema_version"] != _INDEX_SCHEMA or index["protocol_version"] != _PROTOCOL:
            raise InputValidationError("snapshot index protocol is invalid")
        if index["dataset"] != dataset or index["inventory_revision"] != inventory_revision:
            raise InputValidationError("snapshot index identity differs from request")
        cases = index["cases"]
        if (
            not isinstance(cases, dict) or not cases
            or any(type(case_id) is not str or not case_id for case_id in cases)
            or any(_SHA256.fullmatch(value) is None for value in cases.values()
                   if isinstance(value, str))
            or any(type(value) is not str for value in cases.values())
        ):
            raise InputValidationError("snapshot index cases are invalid")
        content = {key: index[key] for key in expected - {"index_digest"}}
        if index["index_digest"] != _canonical_digest(content):
            raise InputValidationError("snapshot index digest differs from content")
        self._cases = dict(cases)
        self.case_ids = tuple(sorted(cases))
        self.snapshot_ids = MappingProxyType(dict(cases))

    def load_case(self, case_id: str) -> FrozenCase:
        if type(case_id) is not str or case_id not in self._cases:
            raise InputValidationError("case is absent from frozen dataset index")
        snapshot_id = self._cases[case_id]
        root = (
            self.registry_root / "formal-snapshots" / self.dataset / snapshot_id
        )
        return FrozenCase._load(
            root, dataset=self.dataset, inventory_revision=self.inventory_revision,
            indexed_case_id=case_id, indexed_snapshot_id=snapshot_id,
        )


class FrozenCase:
    """Validated, read-only access to one prepared Agent snapshot."""

    def __init__(self) -> None:
        raise TypeError("use FrozenDataset.load_case")

    @classmethod
    def _load(
        cls, root: Path, *, dataset: str, inventory_revision: str,
        indexed_case_id: str, indexed_snapshot_id: str,
    ) -> FrozenCase:
        root_fd = open_directory_without_links(root)
        try:
            ref = _metadata(root_fd, "control/snapshot-ref.json")
            runtime = _metadata(root_fd, "control/runtime-lock.json")
            manifest = _metadata(root_fd, "control/tree-manifest.json")
            obj = object.__new__(cls)
            obj._initialize(
                root_fd, ref=ref, runtime=runtime, manifest=manifest, dataset=dataset,
                inventory_revision=inventory_revision, indexed_case_id=indexed_case_id,
                indexed_snapshot_id=indexed_snapshot_id,
            )
        except BaseException:
            os.close(root_fd)
            raise
        return obj

    def _initialize(
        self, root_fd: int, *, ref: dict[str, Any], runtime: dict[str, Any],
        manifest: dict[str, Any], dataset: str, inventory_revision: str,
        indexed_case_id: str, indexed_snapshot_id: str,
    ) -> None:
        ref_fields = {
            "schema_version", "protocol_version", "snapshot_id", "dataset",
            "inventory_revision", "case_id", "language", "tree_manifest_digest",
            "runtime_lock_digest", "target_relpath", "target_selector", "mask_sha256",
            "slot_contract_digest", "identity_inputs_digest", "state",
        }
        _require_exact_fields(ref, ref_fields, "snapshot ref")
        if ref["schema_version"] != _REF_SCHEMA or ref["protocol_version"] != _PROTOCOL:
            raise InputValidationError("snapshot ref protocol is invalid")
        language = ref["language"]
        if language not in _SOURCE_SUFFIXES:
            raise InputValidationError("snapshot language is invalid")
        for key, expected in (
            ("dataset", dataset), ("inventory_revision", inventory_revision),
            ("case_id", indexed_case_id), ("snapshot_id", indexed_snapshot_id),
            ("identity_inputs_digest", indexed_snapshot_id), ("state", "frozen"),
        ):
            if ref[key] != expected:
                raise InputValidationError(f"snapshot ref {key} differs from index")
        for key in (
            "snapshot_id", "inventory_revision", "tree_manifest_digest",
            "runtime_lock_digest", "mask_sha256", "identity_inputs_digest",
        ):
            _require_digest(ref[key], f"snapshot ref {key}")
        if ref["slot_contract_digest"] is not None:
            _require_digest(ref["slot_contract_digest"], "slot contract digest")
        if type(ref["target_selector"]) is not str or not ref["target_selector"]:
            raise InputValidationError("snapshot target selector is invalid")
        target_relpath = _source_path(ref["target_relpath"])
        if not target_relpath.startswith("repo/"):
            raise InputValidationError("snapshot target must be under repo")

        runtime_fields = {
            "schema_version", "language", "runtime_name", "runtime_version",
            "executable_sha256", "dependency_hashes", "environment", "digest",
        }
        _require_exact_fields(runtime, runtime_fields, "runtime lock")
        if runtime["schema_version"] != _RUNTIME_SCHEMA or runtime["language"] != language:
            raise InputValidationError("runtime lock identity is invalid")
        if any(type(runtime[key]) is not str or not runtime[key]
               for key in ("runtime_name", "runtime_version")):
            raise InputValidationError("runtime lock values are invalid")
        _require_digest(runtime["executable_sha256"], "runtime executable")
        for key, hashes in (("dependency_hashes", True), ("environment", False)):
            pairs = runtime[key]
            if (
                not isinstance(pairs, list)
                or any(not isinstance(pair, list) or len(pair) != 2
                       or any(type(item) is not str for item in pair) for pair in pairs)
                or pairs != sorted(pairs)
                or len({pair[0] for pair in pairs}) != len(pairs)
                or (hashes and any(_SHA256.fullmatch(pair[1]) is None for pair in pairs))
            ):
                raise InputValidationError(f"runtime {key} is invalid")
        runtime_content = {key: runtime[key] for key in runtime_fields - {"digest"}}
        if runtime["digest"] != _canonical_digest(runtime_content):
            raise InputValidationError("runtime lock digest differs from content")
        if ref["runtime_lock_digest"] != runtime["digest"]:
            raise InputValidationError("runtime lock differs from snapshot ref")

        manifest_fields = {"entries", "digest"}
        _require_exact_fields(manifest, manifest_fields, "tree manifest")
        entries = manifest["entries"]
        if not isinstance(entries, list):
            raise InputValidationError("tree manifest entries are invalid")
        normalized: list[dict[str, Any]] = []
        for entry in entries:
            parsed = _require_exact_fields(
                entry, {"relpath", "kind", "mode", "sha256"}, "tree manifest entry"
            )
            relpath = _relative_path(parsed["relpath"])
            if parsed["kind"] not in {"file", "symlink"} or type(parsed["mode"]) is not int:
                raise InputValidationError("tree manifest entry is invalid")
            _require_digest(parsed["sha256"], "tree entry digest")
            normalized.append({**parsed, "relpath": relpath})
        if (
            normalized != sorted(normalized, key=lambda entry: entry["relpath"])
            or len({entry["relpath"] for entry in normalized}) != len(normalized)
            or manifest["digest"] != _canonical_digest(normalized)
            or ref["tree_manifest_digest"] != manifest["digest"]
        ):
            raise InputValidationError("tree manifest identity is invalid")
        by_path = {entry["relpath"]: entry for entry in normalized}
        target_entry = by_path.get(target_relpath)
        injector_entry = by_path.get("apply_regions.py")
        if (
            target_entry is None or target_entry["kind"] != "file"
            or target_entry["sha256"] != ref["mask_sha256"]
            or injector_entry is None or injector_entry["kind"] != "file"
        ):
            raise InputValidationError("snapshot target or injector identity is invalid")

        target_bytes = read_regular_file(root_fd, f"tree/{target_relpath}", target_entry["sha256"])
        injector_bytes = read_regular_file(
            root_fd, "tree/apply_regions.py", injector_entry["sha256"]
        )
        try:
            target_text = target_bytes.decode("utf-8")
            injector_text = injector_bytes.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise InputValidationError("frozen target or injector is not UTF-8") from exc
        if language == "python":
            if ref["slot_contract_digest"] is None:
                raise InputValidationError("Python snapshot lacks a slot contract identity")
            try:
                injector_tree = ast.parse(injector_text)
            except SyntaxError as exc:
                raise InputValidationError("Python injector syntax is invalid") from exc
            mock_slots = _literal_string_list(injector_tree, "MOCK_EXPECTED")
            verify_slots = _literal_string_list(injector_tree, "VERIFY_EXPECTED")
            if set(mock_slots) & set(verify_slots):
                raise InputValidationError("Python injector slot IDs overlap")
            actual_slots = _python_slot_order(target_text)
            if (
                tuple(slot for slot in actual_slots if slot in set(mock_slots)) != mock_slots
                or tuple(slot for slot in actual_slots if slot in set(verify_slots)) != verify_slots
                or set(actual_slots) != set(mock_slots) | set(verify_slots)
            ):
                raise InputValidationError("Python slots differ from frozen injector contract")
            validate_python_mask(
                target_text, actual_slots, reject_residual_behavior=False
            )
            masking_contract = "python-slot-v1"
        else:
            if ref["slot_contract_digest"] is not None:
                raise InputValidationError("Java snapshot has an unexpected slot contract")
            validate_java_mask(target_text, reject_residual_behavior=False)
            masking_contract = "java-dual-region-v1"

        logical_target = target_relpath.removeprefix("repo/")
        source_paths = []
        for entry in normalized:
            relpath = entry["relpath"]
            if entry["kind"] != "file" or not relpath.startswith("repo/"):
                continue
            logical = relpath.removeprefix("repo/")
            try:
                _source_path(logical)
            except InputValidationError:
                continue
            if PurePosixPath(logical).suffix == _SOURCE_SUFFIXES[language]:
                source_paths.append(logical)
        if logical_target not in source_paths:
            raise InputValidationError("masked target is absent from the source inventory")

        self._root_fd: int | None = root_fd
        self._entries = by_path
        self.case_id = indexed_case_id
        self.language = language
        self.snapshot_id = indexed_snapshot_id
        self.target_selector = ref["target_selector"]
        self.source_paths = tuple(sorted(source_paths))
        self._runtime_name = runtime["runtime_name"]
        self._runtime_version = runtime["runtime_version"]
        self._masking_contract = masking_contract
        self.masked_test = Source(
            source_id="test", logical_path=logical_target, text=target_text,
            symbol=self.target_selector, start_line=1,
            end_line=len(target_text.splitlines()), resolution_status="resolved",
            target_precision="file",
        )

    def __enter__(self) -> FrozenCase:
        self._require_open()
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def close(self) -> None:
        if self._root_fd is not None:
            os.close(self._root_fd)
            self._root_fd = None

    def _require_open(self) -> int:
        if self._root_fd is None:
            raise InputValidationError("frozen case is closed")
        return self._root_fd

    def _read_source(self, logical_path: str, source_id: str) -> Source:
        logical_path = _source_path(logical_path)
        if logical_path not in self.source_paths:
            raise InputValidationError("path is not an admitted frozen source")
        entry = self._entries["repo/" + logical_path]
        if entry["kind"] != "file":
            raise InputValidationError("frozen source is not a regular file")
        payload = read_regular_file(
            self._require_open(), "tree/repo/" + logical_path, entry["sha256"]
        )
        try:
            text = payload.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise InputValidationError("frozen source is not UTF-8") from exc
        if not text.strip():
            raise InputValidationError("frozen source is empty")
        return Source(
            source_id=source_id, logical_path=logical_path, text=text, symbol=None,
            start_line=1, end_line=len(text.splitlines()), resolution_status="partial",
            target_precision="file",
        )

    def read_source(self, logical_path: str) -> Source:
        return self._read_source(logical_path, _source_id("source", logical_path))

    def to_task(
        self, mut_path: str, *, supporting_paths: tuple[str, ...] = ()
    ) -> UnifiedTask:
        if type(supporting_paths) is not tuple or any(type(path) is not str for path in supporting_paths):
            raise InputValidationError("supporting_paths must be a tuple of source paths")
        if (
            mut_path == self.masked_test.logical_path or mut_path in supporting_paths
            or self.masked_test.logical_path in supporting_paths
        ):
            raise InputValidationError("task source paths must be distinct")
        if len(set(supporting_paths)) != len(supporting_paths):
            raise InputValidationError("supporting source paths must be unique")
        mut = self._read_source(mut_path, "mut")
        # The target was authenticated when the case was opened, but a case may stay
        # open while its external read-only mount changes. Recheck before producing a
        # task instead of serializing stale cached text.
        current_test = self._read_source(self.masked_test.logical_path, "test")
        if current_test.text != self.masked_test.text:
            raise InputValidationError("masked target changed after case loading")
        supporting = tuple(
            self._read_source(path, f"support:{index}")
            for index, path in enumerate(supporting_paths)
        )
        sources = [
            {"source_id": "mut", "logical_path": mut.logical_path, "role": "mut"},
            {"source_id": "test", "logical_path": self.masked_test.logical_path,
             "role": "masked_test"},
            *(
                {"source_id": source.source_id, "logical_path": source.logical_path,
                 "role": "supporting"}
                for source in supporting
            ),
        ]
        return UnifiedTask.from_dict({
            "schema_version": "mock-spec.task/v1", "language": self.language,
            "case_id": self.case_id, "mut_source": mut.to_dict(),
            "masked_test": self.masked_test.to_dict(), "permitted_interfaces": [],
            "supporting_sources": [source.to_dict() for source in supporting],
            "execution_context": {
                "runtime_name": self._runtime_name,
                "runtime_version": self._runtime_version,
                "test_framework": "pytest" if self.language == "python" else "JUnit",
                "test_framework_version": None,
            },
            "input_policy": {
                "policy_id": "stub-tester-frozen-agent/v1",
                "protocol_version": _PROTOCOL,
                "masking_contract": self._masking_contract,
                "visibility": "reviewed_frozen_sources",
            },
            "sources": sources,
        })


__all__ = ["FrozenCase", "FrozenDataset"]
