"""Content checks and an in-memory, source-id-only analysis boundary."""

from __future__ import annotations

import ast
import io
import json
import re
import tokenize
from types import MappingProxyType

from .schemas import Source, UnifiedTask


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
    """In-memory view for future tools; holds no filesystem roots or reader."""

    def __init__(self, task: UnifiedTask) -> None:
        # Structural validation is not a new masking approval. Call after an adapter.
        task = UnifiedTask.from_dict(task.to_dict())
        self._sources = MappingProxyType({
            source.source_id: source
            for source in (task.mut_source, task.masked_test, *task.supporting_sources)
        })

    def get(self, source_id: str) -> Source:
        if type(source_id) is not str or source_id not in self._sources:
            raise InputValidationError("unknown source_id")
        return self._sources[source_id]

    def ids(self) -> tuple[str, ...]:
        return tuple(self._sources)
