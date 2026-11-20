"""Compatibility imports for the promoted production tagged-slot transport."""

from mock_spec.tagged_slots import (
    build_tagged_prompt,
    parse_tagged_candidate,
    parse_tagged_slots,
    tagged_instruction,
)

__all__ = [
    "build_tagged_prompt",
    "parse_tagged_candidate",
    "parse_tagged_slots",
    "tagged_instruction",
]
