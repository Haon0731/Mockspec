"""Compatibility import for the promoted navigation-facts projector."""

import json
from typing import Any

from mock_spec.navigation_facts import (
    SCHEMA_VERSION,
    project_navigation_facts,
)

format_facts = project_navigation_facts


def _encoded(value: Any) -> bytes:
    """Retain the historical research size-check helper outside production."""
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode()


__all__ = ["SCHEMA_VERSION", "format_facts", "project_navigation_facts"]
