"""Auditable, task-scoped read-only queries for staged inference research."""

from __future__ import annotations

import asyncio
import time
from typing import Any

from mock_spec.source_analysis import SourceAnalysis
from mock_spec.schemas import UnifiedTask
from mock_spec.validation import decode_json


QUERY_TOOLS = {
    "inspect_source",
    "inspect_program_structure",
    "inspect_dependency_usage",
}


class QueryExecutor:
    """Execute QUERY-stage calls while registering every observed attempt first."""

    def __init__(
        self,
        task: UnifiedTask,
        *,
        max_attempts: int,
        max_output_chars: int,
        analysis_timeout_seconds: float,
        deadline: float,
    ) -> None:
        self.analysis = SourceAnalysis(
            task,
            max_output_chars=max_output_chars,
            analysis_timeout_seconds=analysis_timeout_seconds,
        )
        self.max_attempts = max_attempts
        self.deadline = deadline
        self.attempts: list[dict[str, Any]] = []

    @property
    def observed_attempts(self) -> int:
        return len(self.attempts)

    @property
    def budgeted_attempts(self) -> int:
        return min(self.observed_attempts, self.max_attempts)

    def register(self, request: object) -> dict[str, Any]:
        """Record an observed attempt before envelope or protocol validation."""
        sequence = self.observed_attempts + 1
        record: dict[str, Any] = {
            "sequence": sequence,
            "within_budget": sequence <= self.max_attempts,
            "question": None,
            "scenario_id": None,
            "tool": None,
            "arguments_json": None,
            "raw_request": request,
            "status": "registered",
            "result": None,
            "assessment": None,
            "assessment_status": "incomplete",
            "duration_seconds": None,
        }
        self.attempts.append(record)
        started = time.monotonic()

        if isinstance(request, dict):
            for field in ("question", "scenario_id", "tool", "arguments_json"):
                record[field] = request.get(field)
        if not record["within_budget"]:
            return self._finish(
                record,
                started,
                "budget_exhausted",
                self._error("query_budget_exhausted", "The shared six-attempt query budget is exhausted."),
            )
        return record

    def reject(
        self,
        record: dict[str, Any],
        *,
        status: str,
        code: str,
        diagnostic: str,
    ) -> dict[str, Any]:
        """Refuse an observed attempt without executing a read-only backend."""
        if record["status"] == "budget_exhausted":
            return record
        return self._finish(
            record,
            time.monotonic(),
            status,
            self._error(code, diagnostic),
        )

    async def execute_registered(
        self, record: dict[str, Any], *, candidate_ids: set[str]
    ) -> dict[str, Any]:
        """Validate and dispatch a previously registered, budgeted attempt."""
        if record.get("status") != "registered":
            return record
        request = record["raw_request"]
        started = time.monotonic()

        if not isinstance(request, dict):
            return self._finish(
                record, started, "invalid_request",
                self._error("wrong_type", "The query request must be an object."),
            )

        expected_request_fields = {"question", "scenario_id", "tool", "arguments_json"}
        if set(request) != expected_request_fields:
            return self._finish(
                record, started, "invalid_request",
                self._error(
                    "invalid_request_fields",
                    "The query request has missing or unknown fields.",
                ),
            )
        missing = [
            field for field in ("question", "scenario_id", "tool", "arguments_json")
            if not isinstance(record[field], str) or not record[field].strip()
        ]
        if missing:
            return self._finish(
                record, started, "invalid_request",
                self._error("missing_association", f"Missing or invalid fields: {', '.join(missing)}."),
            )
        if record["scenario_id"] not in candidate_ids:
            return self._finish(
                record, started, "invalid_request",
                self._error("unknown_candidate", "The query must name a current candidate."),
            )
        if record["tool"] not in QUERY_TOOLS:
            return self._finish(
                record, started, "invalid_request",
                self._error("unknown_tool", "Only the three staged read-only tools are allowed."),
            )
        try:
            arguments = decode_json(record["arguments_json"])
        except (ValueError, TypeError):
            return self._finish(
                record, started, "invalid_request",
                self._error("malformed_json", "Tool arguments are not valid JSON."),
            )
        if not isinstance(arguments, dict):
            return self._finish(
                record, started, "invalid_request",
                self._error("wrong_type", "Tool arguments must decode to an object."),
            )

        validated = self._validate_arguments(record["tool"], arguments)
        if isinstance(validated, dict) and "error_code" in validated:
            return self._finish(record, started, "invalid_request", validated)

        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            return self._finish(
                record, started, "budget_exhausted",
                self._error("run_deadline_exhausted", "The shared run deadline is exhausted."),
            )

        try:
            call = getattr(self.analysis, record["tool"])
            result = await asyncio.wait_for(call(**validated), timeout=remaining)
        except asyncio.TimeoutError:
            return self._finish(
                record, started, "timeout",
                self._error("run_deadline_exhausted", "The query was cancelled at the run deadline."),
            )
        except asyncio.CancelledError:
            self._finish(
                record, started, "cancelled",
                self._error("cancelled", "The query was cancelled and its analysis was reclaimed."),
            )
            raise
        except Exception as exc:
            return self._finish(
                record, started, "backend_error",
                self._error("analysis_failure", f"Read-only analysis failed: {type(exc).__name__}."),
            )
        status = result.get("status") if isinstance(result, dict) else None
        if status not in {"ok", "incomplete", "unresolved", "error"}:
            return self._finish(
                record, started, "backend_error",
                self._error("malformed_result", "The analysis backend returned an invalid result."),
            )
        return self._finish(record, started, status, result)

    @staticmethod
    def _validate_arguments(tool: str, arguments: dict[str, object]) -> dict[str, Any]:
        expected = (
            {"source_id", "start_line", "end_line"}
            if tool == "inspect_source" else {"source_id"}
        )
        if set(arguments) != expected:
            return QueryExecutor._error(
                "invalid_arguments", "Tool arguments have missing or unknown fields."
            )
        source_id = arguments.get("source_id")
        if not isinstance(source_id, str) or not source_id.strip():
            return QueryExecutor._error("wrong_type", "source_id must be a non-empty string.")
        if tool == "inspect_source":
            start = arguments.get("start_line")
            end = arguments.get("end_line")
            if type(start) is not int or type(end) is not int:
                return QueryExecutor._error("wrong_type", "Source line bounds must be integers.")
        return arguments

    @staticmethod
    def _error(code: str, diagnostic: str) -> dict[str, Any]:
        return {
            "status": "error",
            "error_code": code,
            "facts": [],
            "diagnostics": [diagnostic],
            "limitations": ["A failed query does not establish absence of behavior."],
        }

    @staticmethod
    def _finish(
        record: dict[str, Any], started: float, status: str, result: dict[str, Any]
    ) -> dict[str, Any]:
        record.update(
            status=status,
            result=result,
            duration_seconds=time.monotonic() - started,
        )
        return record


__all__ = ["QUERY_TOOLS", "QueryExecutor"]
