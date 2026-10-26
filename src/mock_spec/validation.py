"""Content checks and an in-memory, source-id-only analysis boundary."""

from __future__ import annotations

import ast
import hashlib
import io
import json
import re
import uuid
import tokenize
from types import MappingProxyType
from typing import TYPE_CHECKING

from .schemas import Source, SourceSlice, UnifiedTask

if TYPE_CHECKING:
    from .source_repository import ReviewedRepository


class InputValidationError(ValueError):
    """Input identity, review, or masking could not be established."""


def decode_json(text: str) -> object:
    """Reject duplicate fields instead of allowing last-value-wins parsing."""
    def unique(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise InputValidationError(f"duplicate JSON field: {key}")
            result[key] = value
        return result

    return json.loads(text, object_pairs_hook=unique)


def validate_python_mask(text: str, expected_slots: tuple[str, ...]) -> None:
    """Require exact reviewed slots, each containing only a pass statement."""
    if not expected_slots or len(set(expected_slots)) != len(expected_slots):
        raise InputValidationError("Python masking requires unique reviewed slots")
    marker = re.compile(r"# === PY-SLOT-(START|END):([a-z0-9_]+) ===")
    seen: list[str] = []
    opened: tuple[str, int] | None = None
    lines = text.splitlines()
    try:
        tokens = tokenize.generate_tokens(io.StringIO(text).readline)
        for token in tokens:
            if token.type != tokenize.COMMENT or "PY-SLOT-" not in token.string:
                continue
            match = marker.fullmatch(token.string)
            if match is None or lines[token.start[0] - 1].strip() != token.string:
                raise InputValidationError("malformed Python slot marker")
            kind, slot = match.groups()
            if kind == "START":
                if opened is not None or slot in seen:
                    raise InputValidationError("nested or duplicate Python slot")
                opened = (slot, token.start[0])
            else:
                if opened is None or opened[0] != slot:
                    raise InputValidationError("unmatched Python slot end")
                body = "\n".join(lines[opened[1]:token.start[0] - 1]).strip()
                if body != "pass":
                    raise InputValidationError(f"non-placeholder Python slot: {slot}")
                seen.append(slot)
                opened = None
        if opened is not None or tuple(seen) != expected_slots:
            raise InputValidationError("Python slots differ from reviewed contract")
        tree = ast.parse(text)
    except (SyntaxError, tokenize.TokenError) as exc:
        raise InputValidationError(f"invalid masked Python syntax: {exc}") from exc
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and (
            node.attr in {"return_value", "side_effect"}
            or node.attr.startswith(("assert_called", "assert_not_called", "assert_any_call",
                                     "assert_has_calls", "assert_awaited", "assert_not_awaited",
                                     "assert_any_await", "assert_has_awaits"))
        ):
            raise InputValidationError(f"residual Python mock behavior: {node.attr}")
        if isinstance(node, ast.Call):
            name = node.func.attr if isinstance(node.func, ast.Attribute) else (
                node.func.id if isinstance(node.func, ast.Name) else ""
            )
            if name in {"Mock", "MagicMock", "AsyncMock", "patch", "spy", "stub"}:
                raise InputValidationError(f"residual Python mock construction: {name}")


def validate_java_mask(text: str) -> None:
    """Check dual regions and reject residual Mockito behavior in this policy."""
    expected = (
        "// === MOCK-STUB-START ===", "// === MOCK-STUB-END ===",
        "// === VERIFY-START ===", "// === VERIFY-END ===",
    )
    lines = text.splitlines()
    positions: list[int] = []
    for marker in expected:
        hits = [index for index, line in enumerate(lines) if line.strip() == marker]
        if len(hits) != 1:
            raise InputValidationError(f"missing or duplicate Java marker: {marker}")
        positions.append(hits[0])
    if positions != sorted(positions):
        raise InputValidationError("Java mask regions are out of order")
    for start, end in (positions[:2], positions[2:]):
        if any(line.strip() for line in lines[start + 1:end]):
            raise InputValidationError("Java generated region is not empty")
    # Ignore literals/comments for the residual-call check; these are not a Java parser.
    code = re.sub(r'//[^\n]*|/\*.*?\*/|"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'',
                  " ", text, flags=re.DOTALL)
    if re.search(r"\b(mock|spy|when|verify|given|doReturn|doThrow|doAnswer|doNothing|"
                 r"thenReturn|thenThrow|thenAnswer|verifyNoMoreInteractions|"
                 r"verifyZeroInteractions|verifyNoInteractions|inOrder)\s*\(", code):
        raise InputValidationError("residual Java mock behavior")


class SourceStore:
    """Run-scoped initial and observed source evidence."""

    def __init__(
        self, task: UnifiedTask, *, repository: ReviewedRepository | None = None
    ) -> None:
        # Structural validation is not a new masking approval. Call after an adapter.
        task = UnifiedTask.from_dict(task.to_dict())
        self._task_data = task.to_dict()
        self._sources = MappingProxyType({
            source.source_id: source
            for source in (task.mut_source, task.masked_test, *task.supporting_sources)
        })
        self._repository = repository
        if repository is not None:
            repository.validate_task(task)
        self._observations: list[dict[str, object]] = []
        self._observed_sources: dict[str, dict[int, str]] = {}
        self._observed_paths: dict[str, str] = {}
        self._repository_sources: dict[str, str] = {}
        self._run_id = uuid.uuid4().hex
        self.last_read_usage: dict = {"bytes": 0, "elapsed_seconds": 0.0}
        self._deadline: float | None = None

    def limit_deadline(self, deadline: float) -> None:
        """Keep the earliest host deadline across tool reconstruction."""
        self._deadline = deadline if self._deadline is None else min(self._deadline, deadline)

    @property
    def repository(self) -> ReviewedRepository | None:
        return self._repository

    def validate_task(self, task: UnifiedTask) -> None:
        """Reject accidental reuse of a run-scoped store with a different task."""
        if not isinstance(task, UnifiedTask) or task.to_dict() != self._task_data:
            raise InputValidationError("source store task identity mismatch")
        if self._repository is not None:
            self._repository.assert_valid()

    def search(
        self, *, path_pattern: str | None = None, text: str | None = None,
        cursor: str | None = None, max_output_chars: int = 16000,
        response_id: str | None = None,
    ) -> dict:
        """Publish only complete returned match lines as observed evidence."""
        if self._repository is None:
            return {"status": "error", "error_code": "repository_search_unavailable",
                    "complete": False, "matches": [], "next_cursor": None}
        result = self._repository.search(
            path_pattern=path_pattern, text=text, cursor=cursor,
            max_output_chars=max_output_chars, run_id=self._run_id,
            deadline=self._deadline,
        )
        for match in result["matches"]:
            self._repository_sources[match["source_id"]] = match["logical_path"]
            if match["text"] is not None:
                self.record_observation(SourceSlice(
                    source_id=match["source_id"], logical_path=match["logical_path"],
                    start_line=match["start_line"], end_line=match["end_line"],
                    text=match["text"], symbol=None, resolution_status="resolved",
                    target_precision="file",
                ), response_id=response_id)
        return result

    def get(self, source_id: str) -> Source:
        if type(source_id) is not str:
            raise InputValidationError("unknown source_id")
        if source_id in self._sources:
            return self._sources[source_id]
        observed = self._observed_sources.get(source_id)
        if not observed:
            raise InputValidationError("unknown source_id")
        start_line, end_line = min(observed), max(observed)
        if set(observed) != set(range(start_line, end_line + 1)):
            raise InputValidationError("observed source has an unread gap")
        text = "\n".join(observed[line] for line in range(start_line, end_line + 1))
        if not text.strip():
            raise InputValidationError("observed source has no analyzable text")
        if observed[end_line] == "":
            text += "\n"
        return Source(
            source_id=source_id,
            logical_path=self._observed_paths[source_id],
            text=text,
            symbol=None,
            start_line=start_line,
            end_line=end_line,
            resolution_status="resolved",
            target_precision="file",
        )

    def read(self, locator: str, start_line: int, end_line: int) -> SourceSlice:
        """Read an initial fragment by ID or an allowed whole file by logical path."""
        self.last_read_usage = {"bytes": 0, "elapsed_seconds": 0.0}
        if type(locator) is not str:
            raise InputValidationError("unknown source_id or logical path")
        if locator in self._sources:
            source = self._sources[locator]
            if type(start_line) is int and type(end_line) is int and (
                source.start_line <= start_line <= end_line <= source.end_line
            ):
                lines = source.text.splitlines()
                return SourceSlice(
                    source_id=source.source_id,
                    logical_path=source.logical_path,
                    text="\n".join(lines[
                        start_line - source.start_line:end_line - source.start_line + 1
                    ]),
                    symbol=source.symbol,
                    start_line=start_line,
                    end_line=end_line,
                    resolution_status=source.resolution_status,
                    target_precision=source.target_precision,
                )
            if self._repository is not None:
                repository_source = self._read_repository(
                    source.logical_path, start_line, end_line
                )
                self._repository_sources[
                    repository_source.source_id
                ] = repository_source.logical_path
                return repository_source
            raise InputValidationError("invalid visible line range")
        if locator in self._repository_sources and self._repository is not None:
            return self._read_repository(
                self._repository_sources[locator], start_line, end_line
            )
        if self._repository is None:
            raise InputValidationError("unknown source_id or logical path")
        repository_slice = self._read_repository(locator, start_line, end_line)
        self._repository_sources[
            repository_slice.source_id
        ] = repository_slice.logical_path
        return repository_slice

    def _read_repository(self, locator: str, start_line: int, end_line: int) -> SourceSlice:
        assert self._repository is not None
        try:
            return self._repository.read(
                locator, start_line, end_line, run_id=self._run_id, deadline=self._deadline
            )
        finally:
            self.last_read_usage = self._repository.last_read_usage

    def record_observation(
        self, source: Source | SourceSlice, *, response_id: str | None = None
    ) -> None:
        """Register only source text that was actually returned to the model."""
        if not isinstance(source, (Source, SourceSlice)):
            raise InputValidationError("observation must be a Source")
        if response_id is not None and (
            type(response_id) is not str or not response_id.strip()
        ):
            raise InputValidationError("response_id must be a non-empty string or null")
        known = self._sources.get(source.source_id)
        if known is not None:
            if known.logical_path != source.logical_path:
                raise InputValidationError("observation source identity mismatch")
            expected = self.read(source.source_id, source.start_line, source.end_line)
        else:
            if self._repository is None:
                raise InputValidationError("observation source identity mismatch")
            expected = self._repository.authenticate_observation(source, run_id=self._run_id)
        if (
            expected.source_id != source.source_id
            or expected.logical_path != source.logical_path
            or expected.text != source.text
        ):
            raise InputValidationError("observed source content or identity changed")
        lines = source.text.split("\n")
        if len(lines) != source.end_line - source.start_line + 1:
            raise InputValidationError("observation line range does not match text")
        observed = self._observed_sources.setdefault(source.source_id, {})
        self._observed_paths.setdefault(source.source_id, source.logical_path)
        if self._observed_paths[source.source_id] != source.logical_path:
            raise InputValidationError("observation source identity mismatch")
        for offset, line in enumerate(lines):
            line_number = source.start_line + offset
            previous = observed.get(line_number)
            if previous is not None and previous != line:
                raise InputValidationError("observed source content changed")
            observed[line_number] = line
        self._observations.append({
            "run_id": self._run_id,
            "case_id": self._task_data["case_id"],
            "snapshot_id": (
                self._repository.snapshot_id if self._repository is not None else None
            ),
            "source_id": source.source_id,
            "logical_path": source.logical_path,
            "start_line": source.start_line,
            "end_line": source.end_line,
            "text": source.text,
            "sha256": hashlib.sha256(source.text.encode("utf-8")).hexdigest(),
            "response_id": response_id,
        })

    def observations(self) -> tuple[dict[str, object], ...]:
        return tuple(dict(item) for item in self._observations)

    def quote(self, source_id: str, start_line: int, end_line: int) -> str:
        """Return exact citeable text; dynamic evidence must have been observed."""
        source = self._sources.get(source_id)
        if source is not None:
            if not source.start_line <= start_line <= end_line <= source.end_line:
                raise InputValidationError("evidence range outside allowed source")
            lines = source.text.splitlines()
            return "\n".join(lines[
                start_line - source.start_line:end_line - source.start_line + 1
            ])
        observed = self._observed_sources.get(source_id)
        if observed is None:
            raise InputValidationError("unknown source_id")
        try:
            return "\n".join(observed[line] for line in range(start_line, end_line + 1))
        except KeyError as exc:
            raise InputValidationError("evidence range contains unobserved lines") from exc

    def ids(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys((*self._sources, *self._observed_sources)))
