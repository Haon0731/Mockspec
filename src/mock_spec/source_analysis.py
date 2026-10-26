"""Read-only, source-id-scoped program evidence for one reviewed task."""

from __future__ import annotations

import asyncio
import json
import math

from .schemas import UnifiedTask
from .validation import SourceStore, InputValidationError


def normalize_source_arguments(arguments: dict) -> dict:
    """Validate the shared SDK/research read-search wire protocol without coercion."""
    allowed = {"operation", "source_id", "path", "start_line", "end_line",
               "path_pattern", "text", "cursor"}
    if not isinstance(arguments, dict) or set(arguments) - allowed:
        raise ValueError("Unknown source operation arguments")
    values = {key: value for key, value in arguments.items() if value is not None}
    operation = values.pop("operation", "read")
    if operation == "read":
        locator_keys = set(values) & {"source_id", "path"}
        if len(locator_keys) != 1 or set(values) != locator_keys | {"start_line", "end_line"}:
            raise ValueError("Read requires one locator and an inclusive line range")
        locator = values[next(iter(locator_keys))]
        if type(locator) is not str or not locator.strip():
            raise ValueError("Read locator must be a non-empty string")
        if any(type(values[key]) is not int for key in ("start_line", "end_line")):
            raise ValueError("Line bounds must be integers")
    elif operation == "search":
        if set(values) - {"path_pattern", "text", "cursor"} or not (
            set(values) & {"path_pattern", "text"}
        ):
            raise ValueError("Search requires a path pattern or literal text")
        if any(type(value) is not str or not value for value in values.values()):
            raise ValueError("Search arguments must be non-empty strings")
    else:
        raise ValueError("Unknown source operation")
    return {"operation": operation, **values}


class SourceAnalysis:
    """Analyze only text already admitted by the host Adapter, never model paths."""

    def __init__(
        self,
        task: UnifiedTask,
        *,
        source_store: SourceStore | None = None,
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
        self.store = source_store if source_store is not None else SourceStore(task)
        self.store.validate_task(task)
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
                "usage": result.get("usage", {}),
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

    async def inspect_source(
        self, source_id: str | None = None, start_line: int | None = None,
        end_line: int | None = None, *, operation: str = "read",
        path: str | None = None, path_pattern: str | None = None,
        text: str | None = None, cursor: str | None = None,
        response_id: str | None = None,
    ) -> dict:
        """Read initial IDs or bound logical paths, recording only returned lines."""
        try:
            arguments = normalize_source_arguments({
                "operation": operation, "source_id": source_id, "path": path,
                "start_line": start_line, "end_line": end_line,
                "path_pattern": path_pattern, "text": text, "cursor": cursor,
            })
        except ValueError:
            return {"status": "error", "error_code": "invalid_arguments",
                    "diagnostics": ["Invalid source operation arguments."]}
        try:
            if arguments["operation"] == "search":
                return self.store.search(
                    path_pattern=path_pattern, text=text, cursor=cursor,
                    max_output_chars=self.max_output_chars, response_id=response_id,
                )
            assert start_line is not None and end_line is not None
            locator = source_id if source_id is not None else path
            assert locator is not None
            source = self.store.read(locator, start_line, end_line)
            result = self.bounded({
                "status": "ok", "backend": "source_store", "source_id": source.source_id,
                "logical_path": source.logical_path,
                "start_line": start_line, "end_line": end_line,
                "text": source.text, "limitations": [],
                "usage": self.store.last_read_usage,
            })
            if result.get("status") == "ok" and "text" in result:
                self.store.record_observation(source, response_id=response_id)
            return result
        except TimeoutError:
            return {"status": "error", "error_code": "timeout", "complete": False,
                    "usage": self.store.last_read_usage,
                    "diagnostics": ["The source operation deadline was exhausted."]}
        except (InputValidationError, ValueError, TypeError, OSError) as exc:
            code = ("source_changed" if "identity changed" in str(exc)
                    or "content changed" in str(exc) or "evidence is invalid" in str(exc)
                    else "invalid_source_or_range")
            return {"status": "error", "error_code": code,
                    "usage": self.store.last_read_usage if operation == "read" else {},
                    "diagnostics": [
                        "Source unavailable: invalid range, denied path, or frozen identity changed."
                    ]}

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
