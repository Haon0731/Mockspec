"""Read-only, source-id-scoped program evidence for one reviewed task."""

from __future__ import annotations

import asyncio
import json
import math

from .schemas import UnifiedTask
from .validation import SourceStore, InputValidationError


class SourceAnalysis:
    """Analyze only text already admitted by the host Adapter, never model paths."""

    def __init__(
        self,
        task: UnifiedTask,
        *,
        max_output_chars: int = 16000,
        analysis_timeout_seconds: float = 15,
    ):
        if type(max_output_chars) is not int or max_output_chars < 512:
            raise ValueError("max_output_chars must be an integer >= 512")
        if (not isinstance(analysis_timeout_seconds, (int, float))
                or isinstance(analysis_timeout_seconds, bool)
                or not math.isfinite(analysis_timeout_seconds)
                or analysis_timeout_seconds <= 0):
            raise ValueError("analysis_timeout_seconds must be positive")
        self.store = SourceStore(task)
        self.language = task.language
        self.max_output_chars = max_output_chars
        self.analysis_timeout_seconds = float(analysis_timeout_seconds)

    def bounded(self, result: dict) -> dict:
        """Never truncate serialized JSON or silently return partial evidence."""
        if len(json.dumps(result, ensure_ascii=False, allow_nan=False)) <= self.max_output_chars:
            return result
        if result.get("status") == "error":
            compact = {
                "status": "error",
                "backend": result.get("backend"),
                "source_id": result.get("source_id"),
                "error_code": result.get("error_code", "analysis_error"),
                "facts": [],
                "diagnostics": ["Analysis error details omitted to fit the output limit."],
                "limitations": ["Analysis failure does not establish absence of behavior."],
            }
            if len(json.dumps(compact, ensure_ascii=False)) > self.max_output_chars:
                compact.pop("source_id")
            if len(json.dumps(compact, ensure_ascii=False)) > self.max_output_chars:
                compact["backend"] = None
                compact["error_code"] = "analysis_error"
            return compact
        if result.get("backend") == "source_store":
            omitted = {
                "status": "incomplete", "backend": "source_store",
                "source_id": result["source_id"], "start_line": result["start_line"],
                "end_line": result["end_line"], "error_code": "output_limit",
                "diagnostics": ["Requested source text omitted; request a smaller range."],
                "limitations": ["Omission does not establish absence of behavior."],
            }
            if len(json.dumps(omitted, ensure_ascii=False)) > self.max_output_chars:
                omitted.pop("source_id")
            return omitted
        limitations = ["Syntax only, not data flow or feasible paths; omitted evidence does not prove absence."]
        bounded: dict = {
            "status": "incomplete",
            "backend": result.get("backend"),
            "source_id": result.get("source_id"),
            "error_code": "output_limit",
            "facts": [],
            "diagnostics": ["Result exceeds the configured output limit."],
            "limitations": limitations,
            "returned_fact_count": 0,
            "total_fact_count": len(result.get("facts", [])),
        }
        for fact in result.get("facts", []):
            candidate = {**bounded, "facts": [*bounded["facts"], fact]}
            candidate["returned_fact_count"] = len(candidate["facts"])
            if len(json.dumps(candidate, ensure_ascii=False)) > self.max_output_chars:
                break
            bounded = candidate
        if len(json.dumps(bounded, ensure_ascii=False)) > self.max_output_chars:
            bounded = {
                "status": "incomplete",
                "backend": result.get("backend"),
                "error_code": "output_limit",
                "facts": [],
                "diagnostics": ["Result exceeds the output limit."],
                "limitations": ["Syntax only; omitted evidence does not prove absence."],
            }
        return bounded

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

    async def inspect_program_structure(self, source_id: str) -> dict:
        """Return syntax structure for one visible source, never a flow proof."""
        result = await self._analyze(source_id)
        return self.bounded(result)

    async def inspect_dependency_usage(self, source_id: str) -> dict:
        """Return calls and reliable syntax-local use relationships."""
        result = await self._analyze(source_id)
        if result.get("status") == "error":
            return self.bounded(result)
        allowed_kinds = {"call", "named_expression", "local_reference", "variable_read"}
        facts = [
            fact for fact in result.get("facts", [])
            if fact.get("kind") in allowed_kinds
        ]
        filtered = {**result, "facts": facts}
        if not facts and result.get("status") != "incomplete":
            filtered["status"] = "unresolved"
            filtered["diagnostics"] = [
                *result.get("diagnostics", []),
                "No supported dependency-usage facts were present in the visible source.",
            ]
        filtered["limitations"] = [
            *result.get("limitations", []),
            "Dependency usage is syntax-local; call targets, aliases, and reaching definitions may be unresolved.",
        ]
        return self.bounded(filtered)

    async def _analyze(self, source_id: str) -> dict:
        try:
            source = self.store.get(source_id)
        except (InputValidationError, TypeError):
            return {
                "status": "error",
                "backend": None,
                "source_id": None,
                "error_code": "invalid_source",
                "facts": [],
                "diagnostics": ["Use a visible source_id from the supplied UnifiedTask."],
                "limitations": ["No source was analyzed."],
            }

        if self.language == "python":
            from .python_analysis import analyze_source_isolated

            try:
                result = await analyze_source_isolated(
                    source,
                    timeout_seconds=self.analysis_timeout_seconds,
                )
                return self._normalize(result, source_id)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                return {
                    "status": "error",
                    "backend": "cpython_ast",
                    "source_id": source_id,
                    "error_code": "analysis_error",
                    "facts": [],
                    "diagnostics": [f"Python analysis failed: {type(exc).__name__}"],
                    "limitations": ["Analysis failure does not establish absence of behavior."],
                }
        if self.language == "java":
            try:
                from .java_analysis import analyze_source

                result = await analyze_source(
                    source,
                    timeout_seconds=self.analysis_timeout_seconds,
                )
                return self._normalize(result, source_id)
            except Exception as exc:
                return {
                    "status": "error",
                    "backend": "spoon",
                    "source_id": source_id,
                    "error_code": "analysis_error",
                    "facts": [],
                    "diagnostics": [f"Java analysis failed: {type(exc).__name__}"],
                    "limitations": ["Analysis failure does not establish absence of behavior."],
                }
        return {
            "status": "error",
            "backend": None,
            "source_id": source_id,
            "error_code": "unsupported",
            "facts": [],
            "diagnostics": ["The task language has no configured analysis backend."],
            "limitations": ["Unsupported analysis does not establish absence of behavior."],
        }

    @staticmethod
    def _normalize(result: object, source_id: str) -> dict:
        malformed = {
            "status": "error",
            "backend": None,
            "source_id": source_id,
            "error_code": "analysis_error",
            "facts": [],
            "diagnostics": ["Analysis backend returned malformed common fields."],
            "limitations": ["Analysis failure does not establish absence of behavior."],
        }
        if not isinstance(result, dict):
            return malformed
        normalized = dict(result)
        normalized.setdefault("status", "error")
        normalized.setdefault("backend", None)
        normalized["source_id"] = source_id
        normalized.setdefault("facts", [])
        normalized.setdefault("diagnostics", [])
        normalized.setdefault("limitations", [])

        status = normalized["status"]
        backend = normalized["backend"]
        facts = normalized["facts"]
        diagnostics = normalized["diagnostics"]
        limitations = normalized["limitations"]
        error_code = normalized.get("error_code")
        common_fields_valid = (
            isinstance(status, str)
            and status in {"ok", "incomplete", "unresolved", "error"}
            and (
                backend is None
                or (isinstance(backend, str) and bool(backend.strip()))
            )
            and (
                error_code is None
                or (isinstance(error_code, str) and bool(error_code.strip()))
            )
            and (status != "error" or error_code is not None)
            and isinstance(facts, list)
            and all(
                isinstance(fact, dict)
                and isinstance(fact.get("kind"), str)
                and bool(fact["kind"].strip())
                for fact in facts
            )
            and isinstance(diagnostics, list)
            and all(isinstance(item, (str, dict)) for item in diagnostics)
            and isinstance(limitations, list)
            and all(isinstance(item, str) for item in limitations)
        )
        if not common_fields_valid:
            return malformed
        try:
            json.dumps(normalized, ensure_ascii=False, allow_nan=False)
        except (TypeError, ValueError):
            return malformed
        return normalized
