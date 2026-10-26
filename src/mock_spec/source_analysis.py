"""Read-only, source-id-scoped program evidence for one reviewed task."""

from __future__ import annotations

import asyncio
import json
import math
import re

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


def normalize_analysis_arguments(arguments: dict) -> dict:
    """Validate the shared SDK/research analysis protocol without coercion."""
    if not isinstance(arguments, dict) or set(arguments) - {"source_id", "symbol"}:
        raise ValueError("Unknown analysis arguments")
    source_id = arguments.get("source_id")
    if type(source_id) is not str or not source_id.strip():
        raise ValueError("source_id must be a non-empty string")
    normalized = {"source_id": source_id}
    symbol = arguments.get("symbol")
    if symbol is not None:
        if type(symbol) is not str or not symbol.strip():
            raise ValueError("symbol must be a non-empty string or null")
        normalized["symbol"] = symbol
    return normalized


class SourceAnalysis:
    """Analyze initial snippets or discovered files within the host repository binding."""

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
                "error_code": result.get("error_code", "analysis_error"),
                "facts": [],
                **self._classification(result),
                "backend": result.get("backend"),
                "source_id": result.get("source_id"),
                "diagnostics": ["Analysis error details omitted."],
                "limitations": ["Failure does not establish absence of behavior."],
            }
            for optional in ("source_id", "backend", "diagnostics", "limitations"):
                if len(json.dumps(compact, ensure_ascii=False)) <= self.max_output_chars:
                    break
                compact.pop(optional, None)
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
            **self._classification(result),
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
                "error_code": "output_limit",
                "facts": [],
                **self._classification(result),
            }
        return bounded

    @staticmethod
    def _classification(result: dict) -> dict:
        """Retain small machine-readable failure and resource fields under truncation."""
        classification: dict = {}
        for key in ("failure_kind", "complete", "limit_reason"):
            value = result.get(key)
            if isinstance(value, (str, bool)) and (
                not isinstance(value, str) or len(value) <= 128
            ):
                classification[key] = value
        usage = result.get("usage")
        if isinstance(usage, dict):
            compact_usage = {
                key: usage[key]
                for key in ("bytes", "elapsed_seconds")
                if isinstance(usage.get(key), (int, float))
                and not isinstance(usage.get(key), bool)
                and math.isfinite(usage[key])
            }
            if compact_usage:
                classification["usage"] = compact_usage
        return classification

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

    async def inspect_program_structure(
        self, source_id: str, symbol: str | None = None
    ) -> dict:
        """Return syntax structure for one visible source, never a flow proof."""
        result, source = await self._analyze(source_id)
        if result.get("status") != "error":
            result = self._select_symbol(result, symbol, source)
        return self.bounded(result)

    async def inspect_dependency_usage(
        self, source_id: str, symbol: str | None = None
    ) -> dict:
        """Return calls and reliable syntax-local use relationships."""
        result, source = await self._analyze(source_id)
        if result.get("status") == "error":
            return self.bounded(result)
        allowed_kinds = {"call", "named_expression", "local_reference", "variable_read"}
        facts = [
            fact for fact in result.get("facts", [])
            if fact.get("kind") in allowed_kinds
        ]
        filtered = self._select_symbol({**result, "facts": facts}, symbol, source)
        if not filtered["facts"] and result.get("status") != "incomplete":
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

    @staticmethod
    def _fact_names(fact: dict) -> tuple[str, ...]:
        values = []
        for key in (
            "callee", "name", "qualified_name", "bound_name", "exception"
        ):
            value = fact.get(key)
            if isinstance(value, str) and value:
                values.append(value)
        return tuple(values)

    @classmethod
    def _matches_symbol(cls, fact: dict, symbol: str) -> bool:
        """Match complete syntax names while excluding substring lookalikes."""
        if fact.get("kind") == "named_expression" and fact.get("target") == symbol:
            fact["match_precision"] = "syntax_match"
            return True
        for candidate in cls._fact_names(fact):
            if candidate == symbol:
                return True
            if "." not in symbol and candidate.rsplit(".", 1)[-1] == symbol:
                return True
        return False

    @staticmethod
    def _text_matches(source, symbol: str) -> list[dict]:
        pattern = re.compile(
            rf"(?<![A-Za-z0-9_$]){re.escape(symbol)}(?![A-Za-z0-9_$])"
        )
        return [
            {
                "kind": "text_match",
                "source_id": source.source_id,
                "start_line": source.start_line + offset,
                "end_line": source.start_line + offset,
                "symbol": symbol,
                "match_precision": "text_match",
            }
            for offset, line in enumerate(source.text.splitlines())
            if pattern.search(line)
        ]

    def _select_symbol(self, result: dict, symbol: str | None, source) -> dict:
        facts = [dict(fact) for fact in result.get("facts", [])]
        if symbol is None:
            return {**result, "facts": facts}

        matched = [fact for fact in facts if self._matches_symbol(fact, symbol)]
        if not matched and source is not None:
            matched = self._text_matches(source, symbol)
        selected = {
            **result,
            "facts": matched,
            "query": {"symbol": symbol},
        }
        if not matched and result.get("status") != "incomplete":
            selected["status"] = "unresolved"
            selected["diagnostics"] = [
                *result.get("diagnostics", []),
                "No exact syntax-name or complete-token text match was found for the symbol.",
            ]
        selected["limitations"] = [
            *result.get("limitations", []),
            "Symbol matching is file-local; text and syntax matches do not prove a cross-file target or feasible path.",
        ]
        return selected

    async def _analyze(self, source_id: str) -> tuple[dict, object | None]:
        try:
            source = self.store.analysis_source(source_id)
        except TimeoutError:
            return {
                "status": "error", "backend": None, "source_id": source_id,
                "error_code": "timeout", "facts": [],
                "failure_kind": "timeout",
                "diagnostics": ["Repository analysis read timed out."],
                "limitations": ["Timed-out analysis does not establish absence of behavior."],
                **self._usage(),
            }, None
        except (InputValidationError, TypeError) as exc:
            changed = "changed" in str(exc) or "evidence is invalid" in str(exc)
            return {
                "status": "error",
                "backend": None,
                "source_id": source_id,
                "error_code": "source_changed" if changed else "invalid_source",
                "failure_kind": "source_changed" if changed else "invalid_source",
                "facts": [],
                "diagnostics": [
                    "Source identity changed during analysis."
                    if changed else "Use a visible source_id from the supplied UnifiedTask."
                ],
                "limitations": ["No source was analyzed."],
                **self._usage(),
            }, None

        if self.language == "python":
            from .python_analysis import analyze_source_isolated

            try:
                result = await analyze_source_isolated(
                    source,
                    timeout_seconds=self.analysis_timeout_seconds,
                )
                normalized = self._hide_repository_quotes(
                    self._normalize(result, source.source_id)
                )
                normalized.update(self._usage())
                return normalized, source
            except asyncio.CancelledError:
                raise
            except asyncio.TimeoutError:
                return {
                    "status": "error", "backend": "cpython_ast",
                    "source_id": source_id, "error_code": "timeout", "facts": [],
                    "failure_kind": "timeout",
                    "diagnostics": ["Python analysis timed out."],
                    "limitations": ["Timed-out analysis does not establish absence of behavior."],
                    **self._usage(),
                }, source
            except Exception as exc:
                return {
                    "status": "error",
                    "backend": "cpython_ast",
                    "source_id": source_id,
                    "error_code": "analysis_error",
                    "failure_kind": "backend_failure",
                    "facts": [],
                    "diagnostics": [f"Python analysis failed: {type(exc).__name__}"],
                    "limitations": ["Analysis failure does not establish absence of behavior."],
                    **self._usage(),
                }, source
        if self.language == "java":
            try:
                from .java_analysis import analyze_source

                result = await analyze_source(
                    source,
                    timeout_seconds=self.analysis_timeout_seconds,
                )
                normalized = self._hide_repository_quotes(
                    self._normalize(result, source.source_id)
                )
                normalized.update(self._usage())
                return normalized, source
            except asyncio.CancelledError:
                raise
            except asyncio.TimeoutError:
                return {
                    "status": "error", "backend": "spoon",
                    "source_id": source_id, "error_code": "timeout", "facts": [],
                    "failure_kind": "timeout",
                    "diagnostics": ["Java analysis timed out."],
                    "limitations": ["Timed-out analysis does not establish absence of behavior."],
                    **self._usage(),
                }, source
            except Exception as exc:
                return {
                    "status": "error",
                    "backend": "spoon",
                    "source_id": source_id,
                    "error_code": "analysis_error",
                    "failure_kind": "backend_failure",
                    "facts": [],
                    "diagnostics": [f"Java analysis failed: {type(exc).__name__}"],
                    "limitations": ["Analysis failure does not establish absence of behavior."],
                    **self._usage(),
                }, source
        return {
            "status": "error",
            "backend": None,
            "source_id": source_id,
            "error_code": "unsupported",
            "failure_kind": "unsupported_backend",
            "facts": [],
            "diagnostics": ["The task language has no configured analysis backend."],
            "limitations": ["Unsupported analysis does not establish absence of behavior."],
            **self._usage(),
        }, source

    def _usage(self) -> dict:
        return (
            {"usage": self.store.last_read_usage}
            if self.store.repository is not None else {}
        )

    def _hide_repository_quotes(self, result: dict) -> dict:
        """Internal full-file parsing must not reveal or authorize repository text."""
        if self.store.repository is None:
            return result
        return {
            **result,
            "facts": [
                {key: value for key, value in fact.items() if key != "quote"}
                for fact in result.get("facts", [])
            ],
            "limitations": [
                *result.get("limitations", []),
                "Analysis locations are not citeable source text; use inspect_source read for evidence quotes.",
            ],
        }

    @staticmethod
    def _normalize(result: object, source_id: str) -> dict:
        malformed = {
            "status": "error",
            "backend": None,
            "source_id": source_id,
            "error_code": "analysis_error",
            "failure_kind": "malformed_result",
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
                or (
                    isinstance(error_code, str)
                    and bool(error_code.strip())
                    and len(error_code) <= 128
                )
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
