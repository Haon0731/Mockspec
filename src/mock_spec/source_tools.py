"""Task-scoped SDK tools: source access, call limits and execution traces.

SourceAnalysis owns source analysis; this module owns the SDK-facing tool contract.
Each build creates independent source access, counters and trace state.
"""

from __future__ import annotations

import asyncio
from time import perf_counter

from agents import FunctionTool, function_tool
from pydantic import StrictInt

from .budget import InferenceBudget
from .inference import InferenceError
from .source_analysis import SourceAnalysis
from .schemas import UnifiedTask


def build_source_tools(
    task: UnifiedTask,
    *,
    budget: InferenceBudget,
    tool_trace: list | None = None,
) -> list[FunctionTool]:
    """Register the three read-only tools for one validated inference setup."""

    analysis = SourceAnalysis(
        task,
        max_output_chars=budget.max_tool_output_chars,
        analysis_timeout_seconds=budget.analysis_timeout_seconds,
    )
    trace = [] if tool_trace is None else tool_trace
    tool_call_count = 0

    async def invoke_tool(name: str, arguments: dict, call) -> dict:
        nonlocal tool_call_count
        sequence = tool_call_count + 1
        tool_call_count = sequence
        started = perf_counter()
        record = {
            "sequence": sequence,
            "tool": name,
            "arguments": arguments,
            "status": "running",
            "result": None,
            "duration_seconds": None,
        }
        trace.append(record)
        if sequence > budget.max_tool_calls:
            record.update(
                status="budget_exhausted",
                duration_seconds=perf_counter() - started,
            )
            raise InferenceError(
                "scenario inference tool-call budget exhausted", "budget_exhausted"
            )
        try:
            result = await call()
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
        status = result.get("status", "error") if isinstance(result, dict) else "error"
        record.update(
            status=status,
            result=result,
            duration_seconds=perf_counter() - started,
        )
        return result

    @function_tool(failure_error_function=None)
    async def inspect_source(
        source_id: str, start_line: StrictInt, end_line: StrictInt
    ) -> dict:
        """Return an allowed source range with exact line numbers and original text.

        Args:
            source_id: An opaque source identifier present in the supplied UnifiedTask.
            start_line: First requested source line, inclusive.
            end_line: Last requested source line, inclusive.
        """
        arguments = {
            "source_id": source_id,
            "start_line": start_line,
            "end_line": end_line,
        }
        return await invoke_tool(
            "inspect_source",
            arguments,
            lambda: analysis.inspect_source(**arguments),
        )

    @function_tool(failure_error_function=None)
    async def inspect_program_structure(source_id: str) -> dict:
        """Return syntax-level calls, conditions, branches, returns, and handlers.

        Args:
            source_id: An opaque source identifier present in the supplied UnifiedTask.
        """
        arguments = {"source_id": source_id}
        return await invoke_tool(
            "inspect_program_structure",
            arguments,
            lambda: analysis.inspect_program_structure(**arguments),
        )

    @function_tool(failure_error_function=None)
    async def inspect_dependency_usage(source_id: str) -> dict:
        """Return syntax-level dependency calls and reliable local usage relations.

        Args:
            source_id: An opaque source identifier present in the supplied UnifiedTask.
        """
        arguments = {"source_id": source_id}
        return await invoke_tool(
            "inspect_dependency_usage",
            arguments,
            lambda: analysis.inspect_dependency_usage(**arguments),
        )

    return [inspect_source, inspect_program_structure, inspect_dependency_usage]
