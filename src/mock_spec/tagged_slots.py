"""Ordered tagged transport for model-authored source-code regions."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from typing import Any


_SAFE_SLOT = re.compile(r"[A-Za-z0-9_-]+")
_ANY_DELIMITER = re.compile(r"</?[A-Za-z0-9_-]+>\r?\n?\Z")


def _encode(value: Any) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )


def _validated_slots(required_slots: Sequence[str]) -> tuple[str, ...]:
    if isinstance(required_slots, (str, bytes)):
        raise ValueError("expected a sequence of slot ids")
    slots = tuple(required_slots)
    if not slots or len(slots) != len(set(slots)):
        raise ValueError("slot ids must be non-empty and unique")
    if any(_SAFE_SLOT.fullmatch(slot) is None for slot in slots):
        raise ValueError("slot ids contain characters unsafe for tagged transport")
    return slots


def tagged_instruction(required_slots: Sequence[str]) -> str:
    """Return the response-format instruction for ordered source slots."""
    slots = _validated_slots(required_slots)
    envelope = "\n".join(f"<{slot}>\n</{slot}>" for slot in slots)
    return (
        "Complete the marked regions of the masked test using the supplied test, product "
        "sources, and audited read/search tools. Preserve the code outside the regions. "
        "Decide how to complete the test from the visible scenario. Return only the regions "
        "as verbatim source between the delimiter lines below, exactly once and in this "
        "order. A region may be empty. Preserve all indentation inside each region. Do not "
        "wrap the response in Markdown. For Python, the host inserts a region at the marker's "
        "existing indentation. Make its first line relative to that marker and do not repeat "
        "the marker's indentation; preserve any additional indentation on later lines. You "
        "have up to ten read/search calls.\n" + envelope + "\nInput:\n"
    )


def build_tagged_prompt(
    base_payload: Mapping[str, Any],
    navigation_evidence: Mapping[str, Any] | None,
    required_slots: Sequence[str],
) -> str:
    """Build the common generation prompt used with or without navigation."""
    return (
        tagged_instruction(required_slots)
        + _encode(base_payload)
        + "\nNavigation evidence:\n"
        + _encode(navigation_evidence)
        + "\n"
    )


def parse_tagged_slots(raw: str, required_slots: Sequence[str]) -> dict[str, str]:
    """Extract exact slot contents, removing only delimiter framing newlines."""
    slots = _validated_slots(required_slots)
    lines = raw.splitlines(keepends=True)
    position = 0
    result: dict[str, str] = {}
    for slot in slots:
        opening = f"<{slot}>"
        if position >= len(lines) or lines[position].rstrip("\r\n") != opening:
            raise ValueError("invalid tagged slot envelope")
        if not lines[position].endswith(("\n", "\r\n")):
            raise ValueError("opening delimiter requires a framing newline")
        position += 1
        content: list[str] = []
        closing = f"</{slot}>"
        while position < len(lines) and lines[position].rstrip("\r\n") != closing:
            if _ANY_DELIMITER.fullmatch(lines[position]):
                raise ValueError(
                    "duplicate, unexpected, or out-of-order slot delimiter"
                )
            content.append(lines[position])
            position += 1
        if position >= len(lines):
            raise ValueError("missing closing slot delimiter")
        source = "".join(content)
        if source.endswith("\r\n"):
            source = source[:-2]
        elif source.endswith("\n"):
            source = source[:-1]
        result[slot] = source
        position += 1
    if position != len(lines):
        raise ValueError("text outside tagged slot envelope")
    return result


def parse_tagged_candidate(
    raw: str, required_slots: Sequence[str]
) -> dict[str, object]:
    """Wrap extracted source slots in the generation evaluator's host envelope."""
    return {"slots": parse_tagged_slots(raw, required_slots), "rationale": []}


__all__ = [
    "build_tagged_prompt",
    "parse_tagged_candidate",
    "parse_tagged_slots",
    "tagged_instruction",
]
