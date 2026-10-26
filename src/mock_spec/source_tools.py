"""Task-scoped SDK tools: source access, call limits and execution traces.

SourceAnalysis owns source analysis; this module owns the SDK-facing tool contract.
Each build creates independent source access, counters and trace state.
"""

from __future__ import annotations

import asyncio
from contextvars import ContextVar
import hashlib
import json
from time import perf_counter

from agents import FunctionTool, ModelBehaviorError, function_tool
from pydantic import StrictInt

from .budget import InferenceBudget
from .inference import InferenceError
from .source_analysis import (
    SourceAnalysis, normalize_source_arguments, normalize_analysis_arguments,
)
from .schemas import UnifiedTask
from .validation import SourceStore, decode_json


def _fingerprint(value: object) -> str:
    """Return a stable, content-only fingerprint for repetition accounting."""

    encoded = json.dumps(
        value, ensure_ascii=False, separators=(",", ":"), sort_keys=True,
        default=repr,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def build_source_tools(
    task: UnifiedTask,
    *,
    budget: InferenceBudget,
    tool_trace: list | None = None,
    source_store: SourceStore | None = None,
) -> list[FunctionTool]:
    """Register the three read-only tools for one validated inference setup."""

    analysis = SourceAnalysis(
        task,
        source_store=source_store,
        max_output_chars=budget.max_tool_output_chars,
        analysis_timeout_seconds=budget.analysis_timeout_seconds,
    )
    trace = [] if tool_trace is None else tool_trace
    tool_call_count = 0
    seen_queries: set[str] = set()
    seen_results: set[str] = set()
    seen_observations: set[str] = set()
    consecutive_no_progress = 0
    response_id: ContextVar[str | None] = ContextVar("source_response_id", default=None)

    async def invoke_tool(name: str, arguments: str, context, call) -> dict:
        nonlocal tool_call_count, consecutive_no_progress
        sequence = tool_call_count + 1
        tool_call_count = sequence
        started = perf_counter()
        try:
            parsed_arguments = decode_json(arguments)
        except (ValueError, TypeError):
            parsed_arguments = None
        normalized_arguments: object = parsed_arguments
        correlation = getattr(context, "tool_call_id", None) or f"tool-{sequence}"
        record = {
            "sequence": sequence,
            "tool": name,
            "arguments": parsed_arguments,
            "raw_arguments": arguments,
            "response_id": correlation,
            "status": "running",
            "result": None,
            "duration_seconds": None,
        }
        trace.append(record)
        if analysis.store.repository is None and sequence > budget.max_tool_calls:
            record.update(
                status="budget_exhausted",
                duration_seconds=perf_counter() - started,
            )
            raise InferenceError(
                "scenario inference tool-call budget exhausted", "budget_exhausted"
            )
        observation_start = len(analysis.store.observations())
        token = response_id.set(correlation)
        try:
            if not isinstance(parsed_arguments, dict):
                result = {"status": "error", "error_code": "invalid_arguments",
                          "diagnostics": ["Tool arguments must match the schema and use unique JSON keys."]}
            elif name == "inspect_source":
                try:
                    normalized_arguments = normalize_source_arguments(parsed_arguments)
                except (TypeError, ValueError):
                    result = {
                        "status": "error",
                        "error_code": "invalid_arguments",
                        "diagnostics": [
                            "inspect_source arguments must describe one valid search or read."
                        ],
                    }
                else:
                    result = await call()
            else:
                try:
                    normalized_arguments = normalize_analysis_arguments(parsed_arguments)
                except (TypeError, ValueError):
                    result = {"status": "error", "error_code": "invalid_arguments",
                              "diagnostics": ["Analysis requires source_id and an optional symbol."]}
                else:
                    result = await call()
        except ModelBehaviorError:
            result = {"status": "error", "error_code": "invalid_arguments",
                      "diagnostics": ["Tool arguments do not match the declared schema."]}
        except asyncio.CancelledError:
            record.update(
                status="cancelled",
                duration_seconds=perf_counter() - started,
            )
            raise
        except InferenceError:
            record.update(
                status="budget_exhausted",
                duration_seconds=perf_counter() - started,
            )
            raise
        except Exception as exc:
            result = {
                "status": "error",
                "error_code": "analysis_failure",
                "diagnostics": [type(exc).__name__],
                "limitations": [
                    "Analysis failure does not establish absence of behavior."
                ],
            }
        finally:
            observations = [
                item for item in analysis.store.observations()[observation_start:]
                if item["response_id"] == correlation
            ]
            record["observations"] = observations
            response_id.reset(token)
        status = result.get("status", "error") if isinstance(result, dict) else "error"
        if analysis.store.repository is not None:
            stable_result = (
                {key: value for key, value in result.items() if key != "usage"}
                if isinstance(result, dict) else result
            )
            result_digest = _fingerprint(stable_result)
            observation_digests = {
                _fingerprint({
                    key: value for key, value in observation.items()
                    if key != "response_id"
                })
                for observation in observations
            }
            result_is_new = result_digest not in seen_results
            new_observations = observation_digests - seen_observations
            seen_results.add(result_digest)
            seen_observations.update(observation_digests)
            query_digest = _fingerprint({
                "tool": name,
                "arguments": normalized_arguments,
            })
            query_is_new = query_digest not in seen_queries
            seen_queries.add(query_digest)
            no_progress = (
                not query_is_new and not result_is_new and not new_observations
            )
            if no_progress:
                consecutive_no_progress += 1
            else:
                consecutive_no_progress = 0
            record["repetition_check"] = {
                "policy": "normalized_query_result_and_observation_delta",
                "query_digest": query_digest,
                "result_digest": result_digest,
                "new_query": query_is_new,
                "new_result": result_is_new,
                "new_observations": len(new_observations),
                "consecutive_no_progress": consecutive_no_progress,
                "threshold": budget.max_repeated_no_progress,
                "triggered": (
                    consecutive_no_progress >= budget.max_repeated_no_progress
                ),
                "termination_initiator": (
                    "controller"
                    if consecutive_no_progress >= budget.max_repeated_no_progress
                    else None
                ),
            }
            if consecutive_no_progress >= budget.max_repeated_no_progress:
                record.update(
                    status="repeated_exploration",
                    result=result,
                    duration_seconds=perf_counter() - started,
                    termination_initiator="controller",
                )
                raise InferenceError(
                    "scenario inference repeated exploration without progress",
                    "repeated_exploration",
                )
        record.update(
            status=status,
            result=result,
            duration_seconds=perf_counter() - started,
        )
        return result

    @function_tool(failure_error_function=None)
    async def inspect_source(
        operation: str = "read",
        source_id: str | None = None,
        path: str | None = None,
        start_line: StrictInt | None = None,
        end_line: StrictInt | None = None,
        path_pattern: str | None = None,
        text: str | None = None,
        cursor: str | None = None,
    ) -> dict:
        """Search reviewed paths/text or read one exact allowed line range.

        Args:
            operation: Either "search" or "read"; omitted means the compatible read form.
            source_id: For read, an initial or discovered source ID.
            path: For read, a discovered relative path instead of source_id.
            start_line: For read, first requested line inclusive.
            end_line: For read, last requested line inclusive.
            path_pattern: For search, an optional relative glob pattern.
            text: For search, optional literal single-line text.
            cursor: For search, the opaque continuation cursor from the same query.
        """
        arguments = {
            "operation": operation,
            "source_id": source_id,
            "path": path,
            "start_line": start_line,
            "end_line": end_line,
            "path_pattern": path_pattern,
            "text": text,
            "cursor": cursor,
        }
        normalized = normalize_source_arguments({
            key: value for key, value in arguments.items()
            if value is not None and (key != "operation" or value != "read")
        })
        return await analysis.inspect_source(
            **normalized, response_id=response_id.get()
        )

    @function_tool(failure_error_function=None)
    async def inspect_program_structure(source_id: str, symbol: str | None = None) -> dict:
        """Return syntax-level calls, conditions, branches, returns, and handlers.

        Args:
            source_id: An initial source ID, discovered relative path, or dynamic source ID.
            symbol: Optional exact name or qualified syntax name; never a resolved-target claim.
        """
        return await analysis.inspect_program_structure(source_id, symbol=symbol)

    @function_tool(failure_error_function=None)
    async def inspect_dependency_usage(source_id: str, symbol: str | None = None) -> dict:
        """Return syntax-level dependency calls and reliable local usage relations.

        Args:
            source_id: An initial source ID, discovered relative path, or dynamic source ID.
            symbol: Optional exact name or qualified syntax name; never a resolved-target claim.
        """
        return await analysis.inspect_dependency_usage(source_id, symbol=symbol)

    registered = [inspect_source, inspect_program_structure, inspect_dependency_usage]
    for tool in registered:
        original = tool.on_invoke_tool

        def wrap(name, invoke):
            async def logged(context, arguments):
                return await invoke_tool(
                    name, arguments, context, lambda: invoke(context, arguments)
                )
            return logged

        tool.on_invoke_tool = wrap(tool.name, original)
    return registered
