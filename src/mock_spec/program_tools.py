"""Read-only, source-id-scoped program evidence for one reviewed task."""

from __future__ import annotations

import json

from .schemas import UnifiedTask
from .validation import SourceStore, InputValidationError


class ProgramTools:
    """Analyze only text already admitted by the host Adapter, never model paths."""

    def __init__(self, task: UnifiedTask, *, max_output_chars: int = 16000):
        if type(max_output_chars) is not int or max_output_chars < 512:
            raise ValueError("max_output_chars must be an integer >= 512")
        self.store = SourceStore(task)
        self.language = task.language
        self.max_output_chars = max_output_chars

    def bounded(self, result: dict) -> dict:
        """Never truncate serialized JSON or silently return partial evidence."""
        if len(json.dumps(result, ensure_ascii=False)) <= self.max_output_chars:
            return result
        return {
            "status": "incomplete", "backend": result.get("backend"),
            "error_code": "output_limit", "facts": [],
            "diagnostics": ["Result exceeds output limit; request a smaller range."],
            "limitations": ["Omitted evidence does not establish absence of behavior."],
        }

    async def inspect_source(self, source_id: str, start_line: int, end_line: int) -> dict:
        """Return exact complete lines in the source's original coordinate system."""
        try:
            source = self.store.get(source_id)
            if (type(start_line) is not int or type(end_line) is not int
                    or not source.start_line <= start_line <= end_line <= source.end_line):
                raise InputValidationError("invalid visible line range")
        except (InputValidationError, TypeError):
            return {"status": "error", "error_code": "invalid_source_or_range",
                    "diagnostics": ["Use a visible source_id and its allowed line range."]}
        lines = source.text.splitlines()
        return self.bounded({
            "status": "ok", "backend": "source_store", "source_id": source_id,
            "start_line": start_line, "end_line": end_line,
            "text": "\n".join(lines[start_line-source.start_line:end_line-source.start_line+1]),
            "limitations": [],
        })
