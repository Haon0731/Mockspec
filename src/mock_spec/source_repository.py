"""Host-only, read-only access to reviewed source files for one frozen snapshot."""

from __future__ import annotations

from dataclasses import dataclass
import errno
import hashlib
import os
from pathlib import PurePosixPath
import stat
from types import MappingProxyType
from typing import Mapping

from .schemas import SourceSlice, UnifiedTask
from .validation import InputValidationError


_MAX_SOURCE_BYTES = 8 * 1024 * 1024


def _relative_path(value: str) -> PurePosixPath:
    if type(value) is not str or "\\" in value or "\x00" in value:
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
        raise InputValidationError("snapshot root must be an existing non-symlink directory") from exc


def read_regular_file(root_fd: int, relative: str, expected_digest: str) -> bytes:
    """Read and authenticate a regular file through a descriptor-relative path."""
    path = _relative_path(relative)
    directory_fd = os.dup(root_fd)
    file_fd: int | None = None
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
        if metadata.st_size > _MAX_SOURCE_BYTES:
            raise InputValidationError(f"reviewed source exceeds size limit: {relative}")
        chunks: list[bytes] = []
        size = 0
        while True:
            chunk = os.read(file_fd, min(65536, _MAX_SOURCE_BYTES + 1 - size))
            if not chunk:
                break
            chunks.append(chunk)
            size += len(chunk)
            if size > _MAX_SOURCE_BYTES:
                raise InputValidationError(f"reviewed source exceeds size limit: {relative}")
        payload = b"".join(chunks)
    except InputValidationError:
        raise
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            raise InputValidationError(f"symlink in reviewed source: {relative}") from exc
        raise InputValidationError(f"missing reviewed file: {relative}") from exc
    finally:
        if file_fd is not None:
            os.close(file_fd)
        os.close(directory_fd)
    if type(expected_digest) is not str or hashlib.sha256(payload).hexdigest() != expected_digest:
        raise InputValidationError(f"reviewed file identity changed: {relative}")
    return payload


@dataclass(frozen=True)
class _ReviewedFile:
    relative_path: str
    sha256: str


class ReviewedRepository:
    """Capability for the whole reviewed files that supplied initial task fragments."""

    def __init__(
        self,
        root_fd: int,
        *,
        snapshot_id: str,
        files_by_logical_path: Mapping[str, tuple[str, str]],
        task: UnifiedTask,
    ) -> None:
        self._root_fd: int | None = os.dup(root_fd)
        self._snapshot_id = snapshot_id
        self._task_data = task.to_dict()
        self._files = MappingProxyType({
            logical_path: _ReviewedFile(relative, digest)
            for logical_path, (relative, digest) in files_by_logical_path.items()
        })

    @property
    def snapshot_id(self) -> str:
        return self._snapshot_id

    def validate_task(self, task: UnifiedTask) -> None:
        if not isinstance(task, UnifiedTask) or task.to_dict() != self._task_data:
            raise InputValidationError("repository binding task identity mismatch")

    def source_id_for(self, logical_path: str) -> str:
        if type(logical_path) is not str or logical_path not in self._files:
            raise InputValidationError("logical path is outside the reviewed repository binding")
        identity = f"{self._snapshot_id}\0{logical_path}".encode("utf-8")
        return "repository:" + hashlib.sha256(identity).hexdigest()[:24]

    def read(self, logical_path: str, start_line: int, end_line: int) -> SourceSlice:
        if type(logical_path) is not str or logical_path not in self._files:
            raise InputValidationError("logical path is outside the reviewed repository binding")
        if type(start_line) is not int or type(end_line) is not int:
            raise InputValidationError("invalid reviewed source line range")
        reviewed = self._files[logical_path]
        if self._root_fd is None:
            raise InputValidationError("reviewed repository binding is closed")
        payload = read_regular_file(self._root_fd, reviewed.relative_path, reviewed.sha256)
        try:
            text = payload.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise InputValidationError("reviewed source is not UTF-8 text") from exc
        lines = text.splitlines()
        if not 1 <= start_line <= end_line <= len(lines):
            raise InputValidationError("invalid reviewed source line range")
        return SourceSlice(
            source_id=self.source_id_for(logical_path),
            logical_path=logical_path,
            text="\n".join(lines[start_line - 1:end_line]),
            symbol=None,
            start_line=start_line,
            end_line=end_line,
            resolution_status="resolved",
            target_precision="file",
        )

    def close(self) -> None:
        descriptor = getattr(self, "_root_fd", None)
        if descriptor is not None:
            os.close(descriptor)
            self._root_fd = None

    def __del__(self) -> None:
        self.close()
