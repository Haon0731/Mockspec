"""Host-only, read-only access to one reviewed frozen repository."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import errno
import fnmatch
import hashlib
import json
import math
import os
from pathlib import PurePosixPath
import secrets
import stat
from time import monotonic
from types import MappingProxyType
from typing import Any, Mapping

from .schemas import Source, SourceSlice, UnifiedTask
from .validation import InputValidationError


_MAX_SOURCE_BYTES = 8 * 1024 * 1024
_DENIED_COMPONENTS = frozenset({
    ".git", ".omx", ".codex", ".agents", ".aws", ".ssh",
    "__pycache__", "node_modules", ".venv", "venv", "site-packages",
    "control", "controls", "archive", "archives", "artifacts",
    "evaluation", "evaluations", "grading", "grader", "reference_answers",
})
_SEARCH_POLICY_ID = "reviewed-repository-search/v1"


def _relative_path(value: str) -> PurePosixPath:
    if (
        type(value) is not str
        or "\\" in value
        or "\x00" in value
        or (len(value) >= 2 and value[0].isalpha() and value[1] == ":")
    ):
        raise InputValidationError("invalid reviewed relative path")
    path = PurePosixPath(value)
    if (
        not path.parts
        or path.is_absolute()
        or path.as_posix() != value
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise InputValidationError("invalid reviewed relative path")
    return path


def _denied(path: PurePosixPath) -> bool:
    return any(
        part in _DENIED_COMPONENTS or part == ".env" or part.startswith(".env.")
        for part in path.parts
    )


def open_directory_without_links(path: os.PathLike[str] | str) -> int:
    """Open an absolute directory by walking every component without following links."""
    absolute = os.path.abspath(os.fspath(path))
    parts = PurePosixPath(absolute).parts
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW
    descriptor = os.open("/", flags)
    try:
        for part in parts[1:]:
            next_descriptor = os.open(part, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = next_descriptor
        return descriptor
    except (OSError, TypeError, ValueError) as exc:
        os.close(descriptor)
        raise InputValidationError(
            "snapshot root must be an existing non-symlink directory"
        ) from exc


def _read_regular_file(
    root_fd: int,
    relative: str,
    *,
    maximum_bytes: int,
    remaining_bytes: int | None = None,
    deadline: float | None = None,
) -> tuple[bytes, os.stat_result]:
    path = _relative_path(relative)
    directory_fd = os.dup(root_fd)
    file_fd: int | None = None
    size = 0
    try:
        for part in path.parts[:-1]:
            next_fd = os.open(
                part,
                os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW,
                dir_fd=directory_fd,
            )
            os.close(directory_fd)
            directory_fd = next_fd
        file_fd = os.open(
            path.parts[-1],
            os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK,
            dir_fd=directory_fd,
        )
        metadata = os.fstat(file_fd)
        if not stat.S_ISREG(metadata.st_mode):
            raise InputValidationError(f"reviewed source is not a regular file: {relative}")
        if metadata.st_size > maximum_bytes:
            raise InputValidationError(f"reviewed source exceeds size limit: {relative}")
        if remaining_bytes is not None and metadata.st_size > remaining_bytes:
            raise _SearchReadLimit("byte_limit", 0)
        chunks: list[bytes] = []
        while True:
            if deadline is not None and monotonic() >= deadline:
                raise _SearchReadLimit("timeout", size)
            read_limit = maximum_bytes - size
            if remaining_bytes is not None:
                read_limit = min(read_limit, remaining_bytes - size)
            chunk = os.read(file_fd, min(65536, read_limit))
            if not chunk:
                break
            chunks.append(chunk)
            size += len(chunk)
            if size > maximum_bytes:
                raise InputValidationError(f"reviewed source exceeds size limit: {relative}")
            if remaining_bytes is not None and size > remaining_bytes:
                raise _SearchReadLimit("byte_limit", size)
        final_metadata = os.fstat(file_fd)
        before = (
            metadata.st_dev, metadata.st_ino, metadata.st_size, metadata.st_mtime_ns
        )
        after = (
            final_metadata.st_dev,
            final_metadata.st_ino,
            final_metadata.st_size,
            final_metadata.st_mtime_ns,
        )
        if before != after:
            raise _IdentityReadError(
                f"reviewed file content changed: {relative}", size
            )
        return b"".join(chunks), final_metadata
    except InputValidationError:
        raise
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            raise _IdentityReadError(
                f"symlink in reviewed source: {relative}", size
            ) from exc
        raise _IdentityReadError(
            f"missing reviewed file: {relative}", size
        ) from exc
    finally:
        if file_fd is not None:
            os.close(file_fd)
        os.close(directory_fd)


def read_regular_file(
    root_fd: int,
    relative: str,
    expected_digest: str,
    *,
    deadline: float | None = None,
) -> bytes:
    """Read and authenticate a regular file through a descriptor-relative path."""
    payload, _ = _read_regular_file(
        root_fd, relative, maximum_bytes=_MAX_SOURCE_BYTES, deadline=deadline
    )
    if type(expected_digest) is not str or hashlib.sha256(payload).hexdigest() != expected_digest:
        raise _IdentityReadError(
            f"reviewed file identity changed: {relative}", len(payload)
        )
    return payload


def _open_relative_directory(root_fd: int, relative: str) -> int:
    path = _relative_path(relative)
    directory_fd = os.dup(root_fd)
    try:
        for part in path.parts:
            next_fd = os.open(
                part,
                os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW,
                dir_fd=directory_fd,
            )
            os.close(directory_fd)
            directory_fd = next_fd
        return directory_fd
    except OSError as exc:
        os.close(directory_fd)
        raise InputValidationError("reviewed directory is unavailable") from exc


class _SearchReadLimit(InputValidationError):
    def __init__(self, reason: str, bytes_read: int) -> None:
        super().__init__(reason)
        self.reason = reason
        self.bytes_read = bytes_read


class _IdentityReadError(InputValidationError):
    def __init__(self, message: str, bytes_read: int) -> None:
        super().__init__(message)
        self.bytes_read = bytes_read


class RepositoryTimeoutError(TimeoutError):
    def __init__(self, bytes_read: int) -> None:
        super().__init__("reviewed repository read timed out")
        self.bytes_read = bytes_read


@dataclass(frozen=True)
class SearchLimits:
    max_entries: int = 2000
    max_bytes: int = 2 * 1024 * 1024
    max_file_bytes: int = 256 * 1024
    max_matches: int = 50
    timeout_seconds: float = 2.0

    def __post_init__(self) -> None:
        for name in ("max_entries", "max_bytes", "max_file_bytes", "max_matches"):
            value = getattr(self, name)
            if type(value) is not int or value <= 0:
                raise ValueError(f"{name} must be a positive integer")
        if (
            isinstance(self.timeout_seconds, bool)
            or not isinstance(self.timeout_seconds, (int, float))
            or not math.isfinite(self.timeout_seconds)
            or self.timeout_seconds <= 0
        ):
            raise ValueError("timeout_seconds must be finite and positive")


@dataclass(frozen=True)
class _ReviewedFile:
    relative_path: str
    sha256: str


@dataclass(frozen=True)
class _DiscoveredFile:
    relative_path: str
    identity: tuple[int, int, int, int]
    sha256: str


@dataclass
class _Cursor:
    query: tuple[str | None, str | None]
    candidates: tuple[str, ...]
    position: int
    match_offset: int
    repository_identity: str
    run_id: str
    scope_limit: str | None


class ReviewedRepository:
    """Capability for reviewed files and an optional bounded discovery tree."""

    def __init__(
        self,
        root_fd: int,
        *,
        snapshot_id: str,
        files_by_logical_path: Mapping[str, tuple[str, str]],
        task: UnifiedTask,
        discovery_root: str | None = None,
        excluded_paths: tuple[str, ...] = (),
        search_limits: SearchLimits | None = None,
    ) -> None:
        self._root_fd: int | None = None
        self._snapshot_id = snapshot_id
        self._task_data = task.to_dict()
        self._files = MappingProxyType({
            logical_path: _ReviewedFile(relative, digest)
            for logical_path, (relative, digest) in files_by_logical_path.items()
        })
        self._discovery_root = (
            None if discovery_root is None else _relative_path(discovery_root).as_posix()
        )
        if self._discovery_root is not None and _denied(PurePosixPath(self._discovery_root)):
            raise InputValidationError("discovery root is denied by repository policy")
        if not isinstance(excluded_paths, tuple) or any(
            type(value) is not str for value in excluded_paths
        ):
            raise InputValidationError("excluded repository paths must be a tuple of strings")
        self._excluded_paths = tuple(sorted({
            _relative_path(value).as_posix() for value in excluded_paths
        }))
        for logical_path in self._files:
            if self._review_excludes(PurePosixPath(logical_path)):
                raise InputValidationError(
                    "reviewed source is excluded from repository binding"
                )
        self._search_limits = search_limits or SearchLimits()
        self._scanned: dict[str, _DiscoveredFile] = {}
        self._discovered: dict[str, _DiscoveredFile] = {}
        self._cursors: dict[str, _Cursor] = {}
        self._authenticated_observations: dict[
            str, dict[tuple[str, int, int, str], SourceSlice]
        ] = {}
        self._default_run_id = secrets.token_urlsafe(24)
        self._last_read_usage: dict[str, int | float] = {
            "bytes": 0, "elapsed_seconds": 0.0
        }
        self._invalid_reason: str | None = None
        identity = json.dumps(
            {"snapshot_id": snapshot_id, "task": self._task_data,
             "discovery_root": self._discovery_root, "policy": _SEARCH_POLICY_ID,
             "excluded_paths": self._excluded_paths,
             "limits": asdict(self._search_limits)},
            sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        ).encode("utf-8")
        self._repository_identity = hashlib.sha256(identity).hexdigest()
        # Duplicate only after all validation and pure construction can no longer fail.
        self._root_fd = os.dup(root_fd)

    @property
    def snapshot_id(self) -> str:
        return self._snapshot_id

    @property
    def search_limits(self) -> SearchLimits:
        return self._search_limits

    @property
    def last_read_usage(self) -> dict[str, int | float]:
        return dict(self._last_read_usage)

    def assert_valid(self) -> None:
        if self._invalid_reason is not None:
            raise InputValidationError(
                f"reviewed repository evidence is invalid: {self._invalid_reason}"
            )
        if self._root_fd is None:
            raise InputValidationError("reviewed repository binding is closed")

    def _invalidate(self, reason: str) -> None:
        self._invalid_reason = reason
        self._cursors.clear()
        self._authenticated_observations.clear()

    def _validated_run_id(self, run_id: str | None) -> str:
        resolved = self._default_run_id if run_id is None else run_id
        if type(resolved) is not str or not resolved.strip():
            raise InputValidationError("run_id must be a non-empty string")
        return resolved

    def _cache_observations(
        self, run_id: str, observations: list[SourceSlice]
    ) -> None:
        cache = self._authenticated_observations.setdefault(run_id, {})
        for item in observations:
            key = (item.source_id, item.start_line, item.end_line, item.text)
            cache.pop(key, None)
            cache[key] = item
        while (
            len(cache) > max(50, self._search_limits.max_matches)
            or sum(len(item.text.encode("utf-8")) for item in cache.values())
            > max(_MAX_SOURCE_BYTES, self._search_limits.max_bytes)
        ):
            cache.pop(next(iter(cache)))

    def authenticate_observation(
        self, source: Source | SourceSlice, *, run_id: str | None = None
    ) -> SourceSlice:
        """Authenticate recently returned text from this run without extra file I/O."""
        self.assert_valid()
        if not isinstance(source, (Source, SourceSlice)):
            raise InputValidationError("observation must be a Source")
        resolved_run_id = self._validated_run_id(run_id)
        key = (source.source_id, source.start_line, source.end_line, source.text)
        authenticated = self._authenticated_observations.get(resolved_run_id, {}).get(key)
        if authenticated is None or authenticated.logical_path != source.logical_path:
            raise InputValidationError("observed source content or identity changed")
        return authenticated

    def validate_task(self, task: UnifiedTask) -> None:
        self.assert_valid()
        if not isinstance(task, UnifiedTask) or task.to_dict() != self._task_data:
            raise InputValidationError("repository binding task identity mismatch")

    def source_id_for(self, logical_path: str) -> str:
        self.assert_valid()
        if (
            type(logical_path) is not str
            or logical_path not in self._files and logical_path not in self._discovered
        ):
            raise InputValidationError("logical path is outside the reviewed repository binding")
        return self._source_id(logical_path)

    def _source_id(self, logical_path: str) -> str:
        identity = f"{self._snapshot_id}\0{logical_path}".encode("utf-8")
        return "repository:" + hashlib.sha256(identity).hexdigest()[:24]

    def _review_excludes(self, path: PurePosixPath) -> bool:
        return any(
            path == PurePosixPath(excluded)
            or PurePosixPath(excluded) in path.parents
            for excluded in self._excluded_paths
        )

    def _resolve_locator(self, locator: str) -> str:
        if type(locator) is str:
            try:
                locator_path = _relative_path(locator)
            except InputValidationError:
                locator_path = None
            if locator_path is not None and self._review_excludes(locator_path):
                raise InputValidationError(
                    "logical path is excluded from the reviewed repository binding"
                )
        if locator in self._files or locator in self._discovered:
            return locator
        if locator.startswith("repository:"):
            for logical_path in (*self._files, *self._discovered):
                if self.source_id_for(logical_path) == locator:
                    return logical_path
        raise InputValidationError(
            "logical path is outside the reviewed repository binding"
        )

    def _read_discovered(
        self, logical_path: str, *, deadline: float | None = None
    ) -> bytes:
        self.assert_valid()
        discovered = self._discovered[logical_path]
        assert self._root_fd is not None
        try:
            payload, metadata = _read_regular_file(
                self._root_fd,
                discovered.relative_path,
                maximum_bytes=self._search_limits.max_file_bytes,
                deadline=deadline,
            )
        except _SearchReadLimit:
            raise
        except InputValidationError as exc:
            self._invalidate("discovered source changed")
            bytes_read = getattr(exc, "bytes_read", 0)
            raise _IdentityReadError(
                f"reviewed file identity changed: {discovered.relative_path}",
                bytes_read,
            ) from exc
        identity = (metadata.st_dev, metadata.st_ino, metadata.st_size, metadata.st_mtime_ns)
        if (
            identity != discovered.identity
            or hashlib.sha256(payload).hexdigest() != discovered.sha256
        ):
            self._invalidate("discovered source changed")
            raise _IdentityReadError(
                f"reviewed file identity changed: {discovered.relative_path}",
                len(payload),
            )
        return payload

    def read(
        self,
        logical_path: str,
        start_line: int,
        end_line: int,
        *,
        run_id: str | None = None,
        deadline: float | None = None,
    ) -> SourceSlice:
        self.assert_valid()
        started = monotonic()
        self._last_read_usage = {"bytes": 0, "elapsed_seconds": 0.0}
        if deadline is not None and (
            isinstance(deadline, bool)
            or not isinstance(deadline, (int, float))
            or not math.isfinite(deadline)
        ):
            raise InputValidationError("deadline must be finite")
        resolved_run_id = self._validated_run_id(run_id)
        if type(logical_path) is not str:
            raise InputValidationError("logical path is outside the reviewed repository binding")
        if type(start_line) is not int or type(end_line) is not int:
            raise InputValidationError("invalid reviewed source line range")
        logical_path = self._resolve_locator(logical_path)
        try:
            if logical_path in self._files:
                reviewed = self._files[logical_path]
                assert self._root_fd is not None
                payload = read_regular_file(
                    self._root_fd,
                    reviewed.relative_path,
                    reviewed.sha256,
                    deadline=deadline,
                )
            elif logical_path in self._discovered:
                payload = self._read_discovered(logical_path, deadline=deadline)
            else:  # pragma: no cover - locator resolution guarantees membership
                raise InputValidationError("unknown reviewed source")
        except InputValidationError as exc:
            self._last_read_usage = {
                "bytes": getattr(exc, "bytes_read", 0),
                "elapsed_seconds": monotonic() - started,
            }
            if isinstance(exc, _IdentityReadError):
                self._invalidate("reviewed source changed")
            if isinstance(exc, _SearchReadLimit) and exc.reason == "timeout":
                raise RepositoryTimeoutError(exc.bytes_read) from exc
            raise
        self._last_read_usage = {
            "bytes": len(payload),
            "elapsed_seconds": monotonic() - started,
        }
        try:
            decoded = payload.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise InputValidationError("reviewed source is not UTF-8 text") from exc
        lines = decoded.splitlines()
        if not 1 <= start_line <= end_line <= len(lines):
            raise InputValidationError("invalid reviewed source line range")
        source = SourceSlice(
            source_id=self.source_id_for(logical_path),
            logical_path=logical_path,
            text="\n".join(lines[start_line - 1:end_line]),
            symbol=None,
            start_line=start_line,
            end_line=end_line,
            resolution_status="resolved",
            target_precision="file",
        )
        self._cache_observations(resolved_run_id, [source])
        return source

    def read_for_analysis(
        self,
        locator: str,
        *,
        run_id: str | None = None,
        deadline: float | None = None,
    ) -> Source:
        """Read one bound file for parsing without making its text citeable."""
        self.assert_valid()
        started = monotonic()
        self._last_read_usage = {"bytes": 0, "elapsed_seconds": 0.0}
        self._validated_run_id(run_id)
        if deadline is not None and (
            isinstance(deadline, bool)
            or not isinstance(deadline, (int, float))
            or not math.isfinite(deadline)
        ):
            raise InputValidationError("deadline must be finite")
        logical_path = self._resolve_locator(locator)
        try:
            if logical_path in self._files:
                reviewed = self._files[logical_path]
                assert self._root_fd is not None
                payload = read_regular_file(
                    self._root_fd, reviewed.relative_path, reviewed.sha256,
                    deadline=deadline,
                )
            else:
                payload = self._read_discovered(logical_path, deadline=deadline)
        except InputValidationError as exc:
            self._last_read_usage = {
                "bytes": getattr(exc, "bytes_read", 0),
                "elapsed_seconds": monotonic() - started,
            }
            if isinstance(exc, _IdentityReadError):
                self._invalidate("reviewed source changed")
            if isinstance(exc, _SearchReadLimit) and exc.reason == "timeout":
                raise RepositoryTimeoutError(exc.bytes_read) from exc
            raise
        self._last_read_usage = {
            "bytes": len(payload), "elapsed_seconds": monotonic() - started
        }
        try:
            text = payload.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise InputValidationError("reviewed source is not UTF-8 text") from exc
        lines = text.splitlines()
        if not lines:
            raise InputValidationError("reviewed source has no analyzable text")
        analysis_text = "\n".join(lines)
        if lines[-1] == "":
            analysis_text += "\n"
        return Source(
            source_id=self.source_id_for(logical_path),
            logical_path=logical_path,
            text=analysis_text,
            symbol=None,
            start_line=1,
            end_line=len(lines),
            resolution_status="resolved",
            target_precision="file",
        )

    def _enumerate_candidates(
        self, deadline: float
    ) -> tuple[list[str], int, int, str | None]:
        if self._discovery_root is None:
            return [], 0, 0, "repository_search_unavailable"
        assert self._root_fd is not None
        entries = 0
        excluded = 0
        candidates: list[str] = []
        limit_reason: str | None = None
        stack: list[PurePosixPath] = [PurePosixPath()]
        while stack:
            prefix = stack.pop()
            directory = self._discovery_root
            if prefix.parts:
                directory = f"{directory}/{prefix.as_posix()}"
            try:
                directory_fd = _open_relative_directory(self._root_fd, directory)
            except InputValidationError:
                if not prefix.parts:
                    raise
                excluded += 1
                limit_reason = limit_reason or "access_denied"
                continue
            try:
                try:
                    iterator = os.scandir(directory_fd)
                except OSError:
                    excluded += 1
                    limit_reason = limit_reason or "access_denied"
                    continue
                with iterator:
                    while True:
                        if entries >= self._search_limits.max_entries:
                            limit_reason = "entry_limit"
                            break
                        try:
                            entry = next(iterator)
                        except StopIteration:
                            break
                        except OSError:
                            excluded += 1
                            limit_reason = limit_reason or "access_denied"
                            break
                        entries += 1
                        relative = prefix / entry.name
                        if monotonic() >= deadline:
                            limit_reason = "timeout"
                            break
                        # Apply policy before any type/stat operation.
                        if _denied(relative) or self._review_excludes(relative):
                            excluded += 1
                            limit_reason = limit_reason or "access_denied"
                            continue
                        try:
                            if entry.is_symlink():
                                excluded += 1
                                limit_reason = limit_reason or "access_denied"
                            elif entry.is_dir(follow_symlinks=False):
                                stack.append(relative)
                            elif entry.is_file(follow_symlinks=False):
                                candidates.append(relative.as_posix())
                            else:
                                excluded += 1
                                limit_reason = limit_reason or "content_unavailable"
                        except OSError:
                            excluded += 1
                            limit_reason = limit_reason or "access_denied"
                if limit_reason in {"entry_limit", "timeout"}:
                    break
            finally:
                os.close(directory_fd)
        candidates.sort()
        return candidates, entries, excluded, limit_reason

    def _invalid_search(
        self,
        code: str,
        diagnostic: str,
        *,
        usage: dict[str, int | float] | None = None,
    ) -> dict:
        return {
            "status": "error", "complete": False, "error_code": code,
            "limit_reason": None, "matches": [], "next_cursor": None,
            "diagnostics": [diagnostic],
            "usage": usage or {
                "entries": 0, "excluded": 0, "bytes": 0, "elapsed_seconds": 0.0
            },
            "limits": asdict(self._search_limits),
            "policy_id": _SEARCH_POLICY_ID,
        }

    def _save_cursor(self, state: _Cursor) -> str:
        while len(self._cursors) >= 128:
            self._cursors.pop(next(iter(self._cursors)))
        token = secrets.token_urlsafe(24)
        self._cursors[token] = state
        return token

    def search(
        self,
        *,
        path_pattern: str | None = None,
        text: str | None = None,
        cursor: str | None = None,
        max_output_chars: int = 16000,
        run_id: str | None = None,
        deadline: float | None = None,
    ) -> dict:
        """Search bounded relative paths and literal complete lines in the discovery tree."""
        self.assert_valid()
        try:
            resolved_run_id = self._validated_run_id(run_id)
        except InputValidationError:
            return self._invalid_search("invalid_request", "run_id must be non-empty.")
        self._authenticated_observations[resolved_run_id] = {}
        if type(max_output_chars) is not int or max_output_chars < 512:
            return self._invalid_search("invalid_request", "max_output_chars must be >= 512.")
        if path_pattern is not None:
            if type(path_pattern) is not str or not path_pattern:
                return self._invalid_search("invalid_request", "path_pattern must be non-empty.")
            if len(path_pattern) > 4096:
                return self._invalid_search("invalid_request", "path_pattern is too long.")
            try:
                pattern_path = _relative_path(path_pattern)
            except InputValidationError:
                return self._invalid_search("path_violation", "Invalid relative path pattern.")
            if _denied(pattern_path) or self._review_excludes(pattern_path):
                return self._invalid_search("access_denied", "Path is denied by repository policy.")
        if text is not None and (type(text) is not str or not text):
            return self._invalid_search("invalid_request", "text must be non-empty literal text.")
        if text is not None and len(text) > 4096:
            return self._invalid_search("invalid_request", "text is too long.")
        if text is not None and ("\n" in text or "\r" in text):
            return self._invalid_search(
                "invalid_request", "text must be a single-line literal value."
            )
        if path_pattern is None and text is None:
            return self._invalid_search(
                "invalid_request", "At least one path pattern or literal text is required."
            )
        if cursor is not None and (type(cursor) is not str or not cursor):
            return self._invalid_search("invalid_cursor", "Cursor is invalid or expired.")

        started = monotonic()
        if deadline is not None and (
            isinstance(deadline, bool)
            or not isinstance(deadline, (int, float))
            or not math.isfinite(deadline)
        ):
            return self._invalid_search("invalid_request", "deadline must be finite.")
        effective_deadline = started + self._search_limits.timeout_seconds
        if deadline is not None:
            effective_deadline = min(effective_deadline, float(deadline))
        query = (path_pattern, text)
        entries = 0
        excluded = 0
        enumeration_limit: str | None = None
        if cursor is None:
            try:
                candidates, entries, excluded, enumeration_limit = (
                    self._enumerate_candidates(effective_deadline)
                )
            except InputValidationError:
                return self._invalid_search(
                    "access_denied", "Repository discovery root is unavailable."
                )
            if enumeration_limit is None:
                missing = set(self._discovered).difference(candidates)
                if missing:
                    self._invalidate("a returned source disappeared")
                    return self._invalid_search(
                        "content_changed",
                        "A discovered source changed during this run.",
                        usage={
                            "entries": entries,
                            "excluded": excluded,
                            "bytes": 0,
                            "elapsed_seconds": monotonic() - started,
                        },
                    )
            position = 0
            match_offset = 0
        else:
            state = self._cursors.get(cursor)
            if (
                state is None
                or state.query != query
                or state.repository_identity != self._repository_identity
                or state.run_id != resolved_run_id
            ):
                return self._invalid_search("invalid_cursor", "Cursor is invalid or expired.")
            candidates = list(state.candidates)
            position = state.position
            match_offset = state.match_offset
            enumeration_limit = state.scope_limit

        matches: list[dict[str, object]] = []
        resume_points: list[tuple[int, int]] = []
        bytes_read = 0
        limit_reason: str | None = None
        next_position = position
        next_match_offset = match_offset
        while next_position < len(candidates) and limit_reason is None:
            logical_path = candidates[next_position]
            if monotonic() >= effective_deadline:
                limit_reason = "timeout"
                break
            if path_pattern is not None and not fnmatch.fnmatchcase(logical_path, path_pattern):
                next_position += 1
                next_match_offset = 0
                continue
            relative = f"{self._discovery_root}/{logical_path}"
            assert self._root_fd is not None
            try:
                payload, metadata = _read_regular_file(
                    self._root_fd,
                    relative,
                    maximum_bytes=self._search_limits.max_file_bytes,
                    remaining_bytes=self._search_limits.max_bytes - bytes_read,
                    deadline=effective_deadline,
                )
            except _SearchReadLimit as exc:
                bytes_read += exc.bytes_read
                limit_reason = exc.reason
                break
            except InputValidationError as exc:
                bytes_read += getattr(exc, "bytes_read", 0)
                if logical_path in self._scanned:
                    self._invalidate("discovered source became unavailable")
                    return self._invalid_search(
                        "content_changed",
                        "A discovered source changed during this run.",
                        usage={"entries": entries, "excluded": excluded,
                               "bytes": bytes_read,
                               "elapsed_seconds": monotonic() - started},
                    )
                if "size limit" in str(exc):
                    enumeration_limit = enumeration_limit or "file_size_limit"
                else:
                    enumeration_limit = enumeration_limit or "content_unavailable"
                next_position += 1
                next_match_offset = 0
                continue
            bytes_read += len(payload)
            identity = (
                metadata.st_dev, metadata.st_ino, metadata.st_size, metadata.st_mtime_ns
            )
            digest = hashlib.sha256(payload).hexdigest()
            reviewed = self._files.get(logical_path)
            if reviewed is not None and digest != reviewed.sha256:
                self._invalidate("reviewed source changed before search")
                return self._invalid_search(
                    "content_changed",
                    "A reviewed source changed during this run.",
                    usage={"entries": entries, "excluded": excluded,
                           "bytes": bytes_read,
                           "elapsed_seconds": monotonic() - started},
                )
            previous = self._scanned.get(logical_path)
            if previous is not None and (
                previous.identity != identity or previous.sha256 != digest
            ):
                self._invalidate("discovered source changed between searches")
                return self._invalid_search(
                    "content_changed",
                    "A discovered source changed during this run.",
                    usage={"entries": entries, "excluded": excluded,
                           "bytes": bytes_read,
                           "elapsed_seconds": monotonic() - started},
                )
            self._scanned[logical_path] = _DiscoveredFile(relative, identity, digest)
            source_id = self._source_id(logical_path)
            match_items: list[tuple[int, dict[str, object]]]
            file_limit_reason: str | None = None
            if text is None:
                match_items = [(0, {
                    "logical_path": logical_path,
                    "source_id": source_id,
                    "start_line": None,
                    "end_line": None,
                    "text": None,
                })]
            else:
                match_items = []
                try:
                    decoded = payload.decode("utf-8")
                except UnicodeDecodeError:
                    enumeration_limit = enumeration_limit or "content_unavailable"
                    next_position += 1
                    next_match_offset = 0
                    continue
                lines = decoded.splitlines()
                for line_index, line in enumerate(
                    lines[next_match_offset:], next_match_offset
                ):
                    if monotonic() >= effective_deadline:
                        file_limit_reason = "timeout"
                        next_match_offset = line_index
                        break
                    if text not in line:
                        next_match_offset = line_index + 1
                        continue
                    match_items.append((line_index, {
                        "logical_path": logical_path,
                        "source_id": source_id,
                        "start_line": line_index + 1,
                        "end_line": line_index + 1,
                        "text": line,
                    }))
                    next_match_offset = line_index + 1
            for line_offset, match in match_items:
                if len(matches) >= self._search_limits.max_matches:
                    limit_reason = "match_limit"
                    next_match_offset = line_offset
                    break
                provisional = {
                    "status": "ok", "complete": False, "error_code": None,
                    "limit_reason": "output_limit", "matches": [*matches, match],
                    "next_cursor": "pending",
                    "usage": {"entries": entries, "bytes": bytes_read,
                              "excluded": excluded,
                              "elapsed_seconds": monotonic() - started},
                    "limits": asdict(self._search_limits),
                    "policy_id": _SEARCH_POLICY_ID,
                }
                if len(json.dumps(provisional, ensure_ascii=False)) > max_output_chars:
                    limit_reason = "output_limit"
                    next_match_offset = line_offset
                    break
                resume_points.append((next_position, line_offset))
                matches.append(match)
                next_match_offset = line_offset + 1
            if limit_reason is not None:
                break
            if file_limit_reason is not None:
                limit_reason = file_limit_reason
                break
            next_position += 1
            next_match_offset = 0

        complete = (
            next_position >= len(candidates)
            and limit_reason is None
            and enumeration_limit is None
        )
        if next_position >= len(candidates) and limit_reason is None:
            limit_reason = enumeration_limit
        next_cursor: str | None = None
        if next_position < len(candidates) or next_match_offset:
            next_cursor = self._save_cursor(_Cursor(
                query=query,
                candidates=tuple(candidates),
                position=next_position,
                match_offset=next_match_offset,
                repository_identity=self._repository_identity,
                run_id=resolved_run_id,
                scope_limit=enumeration_limit,
            ))
        result: dict[str, Any] = {
            "status": "ok" if complete else "incomplete",
            "complete": complete,
            "error_code": None,
            "limit_reason": limit_reason,
            "matches": matches,
            "next_cursor": next_cursor,
            "usage": {
                "entries": entries,
                "excluded": excluded,
                "bytes": bytes_read,
                "elapsed_seconds": monotonic() - started,
            },
            "limits": asdict(self._search_limits),
            "policy_id": _SEARCH_POLICY_ID,
        }
        if len(json.dumps(result, ensure_ascii=False)) > max_output_chars:
            resume_position = next_position
            resume_offset = next_match_offset
            while result["matches"] and len(json.dumps(result, ensure_ascii=False)) > max_output_chars:
                result["matches"].pop()
                resume_position, resume_offset = resume_points.pop()
            result.update(status="incomplete", complete=False, limit_reason="output_limit")
            if resume_position < len(candidates):
                next_cursor = self._save_cursor(_Cursor(
                    query=query,
                    candidates=tuple(candidates),
                    position=resume_position,
                    match_offset=resume_offset,
                    repository_identity=self._repository_identity,
                    run_id=resolved_run_id,
                    scope_limit=enumeration_limit,
                ))
                result["next_cursor"] = next_cursor
        delivered: list[SourceSlice] = []
        for match in result["matches"]:
            logical_path = match.get("logical_path")
            if isinstance(logical_path, str) and logical_path in self._scanned:
                self._discovered[logical_path] = self._scanned[logical_path]
            if (
                isinstance(match.get("source_id"), str)
                and type(match.get("start_line")) is int
                and type(match.get("end_line")) is int
                and isinstance(match.get("text"), str)
                and isinstance(logical_path, str)
            ):
                delivered.append(SourceSlice(
                    source_id=match["source_id"],
                    logical_path=logical_path,
                    text=match["text"],
                    symbol=None,
                    start_line=match["start_line"],
                    end_line=match["end_line"],
                    resolution_status="resolved",
                    target_precision="file",
                ))
        self._cache_observations(resolved_run_id, delivered)
        return result

    def close(self) -> None:
        descriptor = getattr(self, "_root_fd", None)
        if descriptor is not None:
            os.close(descriptor)
            self._root_fd = None
        getattr(self, "_cursors", {}).clear()
        getattr(self, "_authenticated_observations", {}).clear()

    def __del__(self) -> None:
        self.close()
