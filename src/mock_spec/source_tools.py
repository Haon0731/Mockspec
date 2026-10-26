"""Task-scoped SDK tools: source access, call limits and execution traces.

SourceAnalysis owns source analysis; this module owns the SDK-facing tool contract.
Each build creates independent source access, counters and trace state.
"""

from __future__ import annotations

import asyncio
from contextvars import ContextVar
from time import perf_counter

from agents import FunctionTool, ModelBehaviorError, function_tool
from pydantic import StrictInt

from .budget import InferenceBudget
from .inference import InferenceError
from .source_analysis import SourceAnalysis, normalize_source_arguments
from .schemas import UnifiedTask
from .validation import SourceStore, decode_json


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
    response_id: ContextVar[str | None] = ContextVar("source_response_id", default=None)

    async def invoke_tool(name: str, arguments: str, context, call) -> dict:
        nonlocal tool_call_count
        sequence = tool_call_count + 1
        tool_call_count = sequence
        started = perf_counter()
        try:
            parsed_arguments = decode_json(arguments)
        except (ValueError, TypeError):
            parsed_arguments = None
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
                    normalize_source_arguments(parsed_arguments)
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
            elif set(parsed_arguments) != {"source_id"}:
                result = {"status": "error", "error_code": "invalid_arguments",
                          "diagnostics": ["Tool arguments must match the schema and use unique JSON keys."]}
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
            record["observations"] = [
                item for item in analysis.store.observations()[observation_start:]
                if item["response_id"] == correlation
            ]
            response_id.reset(token)
        status = result.get("status", "error") if isinstance(result, dict) else "error"
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
    async def inspect_program_structure(source_id: str) -> dict:
        """Return syntax-level calls, conditions, branches, returns, and handlers.

        Args:
            source_id: An initial source ID or a dynamic ID returned by inspect_source.
        """
        arguments = {"source_id": source_id}
        return await analysis.inspect_program_structure(**arguments)

    @function_tool(failure_error_function=None)
    async def inspect_dependency_usage(source_id: str) -> dict:
        """Return syntax-level dependency calls and reliable local usage relations.

        Args:
            source_id: An initial source ID or a dynamic ID returned by inspect_source.
        """
        arguments = {"source_id": source_id}
        return await analysis.inspect_dependency_usage(**arguments)

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
