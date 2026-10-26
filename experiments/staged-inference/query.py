"""Auditable, task-scoped read-only queries for staged inference research."""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from typing import Any

from mock_spec.source_analysis import (
    SourceAnalysis, normalize_source_arguments, normalize_analysis_arguments,
)
from mock_spec.schemas import UnifiedTask
from mock_spec.validation import SourceStore, decode_json


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
        source_store: SourceStore | None = None,
        max_attempts: int | None,
        max_output_chars: int,
        analysis_timeout_seconds: float,
        deadline: float,
        max_no_progress_attempts: int = 3,
    ) -> None:
        if type(max_no_progress_attempts) is not int or max_no_progress_attempts < 1:
            raise ValueError("max_no_progress_attempts must be a positive integer")
        self.analysis = SourceAnalysis(
            task,
            source_store=source_store,
            max_output_chars=max_output_chars,
            analysis_timeout_seconds=analysis_timeout_seconds,
        )
        self.max_attempts = max_attempts
        self.deadline = deadline
        self.max_no_progress_attempts = max_no_progress_attempts
        self.analysis.store.limit_deadline(deadline)
        self.attempts: list[dict[str, Any]] = []
        self._seen_operations: set[str] = set()
        self._seen_evidence: set[str] = set()
        self._operation_questions: dict[str, set[str]] = {}
        self._consecutive_no_progress = 0

    @property
    def observed_attempts(self) -> int:
        return len(self.attempts)

    @property
    def budgeted_attempts(self) -> int:
        return (
            self.observed_attempts
            if self.max_attempts is None
            else min(self.observed_attempts, self.max_attempts)
        )

    def register(self, request: object) -> dict[str, Any]:
        """Record an observed attempt before envelope or protocol validation."""
        sequence = self.observed_attempts + 1
        record: dict[str, Any] = {
            "sequence": sequence,
            "response_id": f"query:{sequence}",
            "within_budget": self.max_attempts is None or sequence <= self.max_attempts,
            "question": None,
            "scenario_id": None,
            "tool": None,
            "arguments_json": None,
            "raw_request": request,
            "status": "registered",
            "semantic_category": None,
            "result": None,
            "assessment": None,
            "assessment_status": "incomplete",
            "duration_seconds": None,
            "exploration_progress": None,
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
                self._error("query_budget_exhausted", "The configured query-attempt limit is exhausted."),
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
            call_arguments = dict(validated)
            if record["tool"] == "inspect_source":
                call_arguments["response_id"] = record["response_id"]
            result = await asyncio.wait_for(call(**call_arguments), timeout=remaining)
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
        return self._finish(
            record, started, status, result, normalized_arguments=validated,
        )

    @property
    def repeated_exploration_triggered(self) -> bool:
        """Whether the controller should stop this run as a protective termination."""
        return any(
            attempt.get("status") == "repeated_exploration"
            for attempt in self.attempts
        )

    def _record_exploration_progress(
        self,
        record: dict[str, Any],
        result: dict[str, Any],
        *,
        normalized_arguments: dict[str, Any] | None = None,
    ) -> None:
        """Detect sustained query loops without imposing a total query limit."""
        arguments = (
            normalized_arguments
            if normalized_arguments is not None
            else self._best_effort_arguments(record.get("arguments_json"))
        )
        operation = self._digest({
            "scenario_id": record["scenario_id"],
            "tool": record["tool"],
            "arguments": arguments,
        })
        question = self._digest(self._normalize_question(record.get("question")))
        evidence = self._digest(self._evidence_payload(result))
        operation_is_new = operation not in self._seen_operations
        evidence_is_new = evidence not in self._seen_evidence
        prior_questions = self._operation_questions.setdefault(operation, set())
        # One explicitly different question may justify reviewing the same location.
        # Further paraphrases cannot keep an otherwise stagnant loop alive.
        question_review_is_new = question not in prior_questions and len(prior_questions) < 2
        made_progress = operation_is_new or evidence_is_new or question_review_is_new
        self._seen_operations.add(operation)
        self._seen_evidence.add(evidence)
        prior_questions.add(question)
        if made_progress:
            self._consecutive_no_progress = 0
        else:
            self._consecutive_no_progress += 1

        triggered = self._consecutive_no_progress >= self.max_no_progress_attempts
        record["exploration_progress"] = {
            "made_progress": made_progress,
            "new_operation_or_position": operation_is_new,
            "new_evidence": evidence_is_new,
            "new_question_review": question_review_is_new,
            "operation_digest": operation,
            "evidence_digest": evidence,
            "consecutive_no_progress": self._consecutive_no_progress,
            "threshold": self.max_no_progress_attempts,
            "triggered": triggered,
            "termination_initiator": "controller" if triggered else None,
            "termination_reason": "repeated_exploration" if triggered else None,
        }
        if triggered:
            record["backend_status"] = record["status"]
            record["backend_semantic_category"] = record["semantic_category"]
            record["status"] = "repeated_exploration"
            record["semantic_category"] = "repeated_exploration"

    @staticmethod
    def _normalize_question(question: object) -> str:
        return (
            " ".join(question.casefold().split())
            if isinstance(question, str) else "<invalid-question>"
        )

    @staticmethod
    def _best_effort_arguments(arguments_json: object) -> object:
        if not isinstance(arguments_json, str):
            return {"invalid_arguments_json_type": type(arguments_json).__name__}
        try:
            return decode_json(arguments_json)
        except (ValueError, TypeError):
            return {"malformed_arguments_json": arguments_json}

    @classmethod
    def _evidence_payload(cls, value: object) -> object:
        """Remove accounting fields that do not represent new repository evidence."""
        if isinstance(value, dict):
            return {
                key: cls._evidence_payload(item)
                for key, item in sorted(value.items())
                if key not in {"usage", "duration_seconds", "next_cursor"}
            }
        if isinstance(value, list):
            return [cls._evidence_payload(item) for item in value]
        return value

    @staticmethod
    def _digest(value: object) -> str:
        encoded = json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
            default=lambda item: f"<{type(item).__name__}>",
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    @staticmethod
    def _semantic_category(status: str, result: dict[str, Any]) -> str:
        code = result.get("error_code")
        limit = result.get("limit_reason")
        failure = result.get("failure_kind")
        failure_categories = {
            "parse_error": "unparseable", "input_limit": "scope_limit",
            "invalid_request": "invalid_request", "timeout": "timeout",
            "output_limit": "output_limit", "backend_failure": "backend_error",
            "malformed_result": "analysis_error",
        }
        if failure in failure_categories:
            return failure_categories[failure]
        if code == "run_deadline_exhausted":
            return "run_timeout"
        if status == "budget_exhausted":
            return "budget_exhausted"
        if status == "cancelled" or code == "cancelled":
            return "cancelled"
        if status == "timeout" or code == "timeout" or limit == "timeout":
            return "timeout"
        if code in {"source_changed", "content_changed"}:
            return "content_changed"
        if code == "identity_mismatch":
            return "identity_mismatch"
        if code in {"path_violation", "access_denied", "not_found"}:
            return code
        if code == "output_limit" or limit == "output_limit":
            return "output_limit"
        if limit in {
            "entry_limit", "match_limit", "byte_limit", "file_size_limit",
            "content_unavailable",
        }:
            return "scope_limit"
        if status == "incomplete":
            diagnostics = result.get("diagnostics", [])
            if any(isinstance(item, dict) and item.get("category") == "unsupported"
                   for item in diagnostics):
                return "unresolved"
            return "scope_limit"
        if status == "unresolved":
            return "unresolved"
        if code in {"unsupported", "unparseable"}:
            return "unparseable"
        if status in {"invalid_request", "protocol_error"} or code in {
            "wrong_type", "invalid_request_fields", "missing_association",
            "unknown_candidate", "unknown_tool", "malformed_json",
            "invalid_arguments", "invalid_cursor", "invalid_request",
            "invalid_source", "invalid_source_or_range",
        }:
            return "invalid_request"
        if status == "backend_error" or code in {
            "analysis_failure", "analysis_error", "malformed_result",
        }:
            return "analysis_error"
        if code in {"backend_unavailable", "backend_error"} or status == "error":
            return "backend_error"
        return "success" if status == "ok" else status

    @staticmethod
    def _validate_arguments(tool: str, arguments: dict[str, object]) -> dict[str, Any]:
        if tool == "inspect_source":
            try:
                return normalize_source_arguments(arguments)
            except (TypeError, ValueError):
                return QueryExecutor._error(
                    "invalid_arguments",
                    "inspect_source arguments must describe one valid search or read.",
                )
        try:
            return normalize_analysis_arguments(arguments)
        except (TypeError, ValueError):
            return QueryExecutor._error(
                "invalid_arguments", "Analysis requires source_id and an optional symbol."
            )

    @staticmethod
    def _error(code: str, diagnostic: str) -> dict[str, Any]:
        return {
            "status": "error",
            "error_code": code,
            "facts": [],
            "diagnostics": [diagnostic],
            "limitations": ["A failed query does not establish absence of behavior."],
        }

    def _finish(
        self, record: dict[str, Any], started: float, status: str,
        result: dict[str, Any], *,
        normalized_arguments: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        record.update(
            status=status,
            semantic_category=QueryExecutor._semantic_category(status, result),
            result=result,
            duration_seconds=time.monotonic() - started,
        )
        if "raw_request" in record and record["semantic_category"] not in {
            "budget_exhausted", "run_timeout", "cancelled", "content_changed",
            "identity_mismatch",
        }:
            self._record_exploration_progress(
                record, result, normalized_arguments=normalized_arguments,
            )
        return record


__all__ = ["QUERY_TOOLS", "QueryExecutor"]
