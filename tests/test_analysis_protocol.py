"""Keep production SDK and staged research analysis requests on one protocol."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
import sys
import time

import pytest
from agents.tool_context import ToolContext
from agents.usage import Usage

from mock_spec.budget import InferenceBudget
from mock_spec.schemas import UnifiedTask
from mock_spec.source_tools import build_source_tools
from test_schemas import valid_task_data


sys.path.insert(0, str(Path(__file__).parents[1] / "experiments" / "staged-inference"))
from query import QueryExecutor  # noqa: E402


def _task() -> UnifiedTask:
    return UnifiedTask.from_dict(valid_task_data())


async def _sdk_call(tool_name: str, arguments: dict) -> dict:
    tools = {tool.name: tool for tool in build_source_tools(
        _task(), budget=InferenceBudget()
    )}
    encoded = json.dumps(arguments)
    context = ToolContext(
        context=None,
        usage=Usage(),
        tool_name=tool_name,
        tool_call_id=f"sdk:{tool_name}",
        tool_arguments=encoded,
    )
    return await tools[tool_name].on_invoke_tool(context, encoded)


async def _research_call(tool_name: str, arguments: dict) -> dict:
    executor = QueryExecutor(
        _task(), max_attempts=1,
        max_output_chars=InferenceBudget().max_tool_output_chars,
        analysis_timeout_seconds=InferenceBudget().analysis_timeout_seconds,
        deadline=time.monotonic() + 10,
    )
    record = executor.register({
        "question": "Which exact syntax facts mention this symbol?",
        "scenario_id": "candidate",
        "tool": tool_name,
        "arguments_json": json.dumps(arguments),
    })
    await executor.execute_registered(record, candidate_ids={"candidate"})
    return record["result"]


@pytest.mark.parametrize(
    "tool_name,symbol",
    [
        ("inspect_program_structure", "target"),
        ("inspect_dependency_usage", "dependency"),
    ],
)
def test_sdk_and_research_execute_the_same_symbol_analysis_with_facts(
    tool_name: str, symbol: str,
) -> None:
    arguments = {"source_id": "mut:0", "symbol": symbol}

    sdk_result, research_result = asyncio.run(_both(tool_name, arguments))

    for result in (sdk_result, research_result):
        assert result["status"] == "ok"
        assert result["query"] == {"symbol": symbol}
        assert result["facts"]
        assert all(fact["match_precision"] in {
            "syntax_match", "resolved_target", "unresolved",
        } for fact in result["facts"])
    assert sdk_result == research_result


@pytest.mark.parametrize(
    "arguments",
    [
        {"source_id": "mut:0", "symbol": None},
        {"source_id": "mut:0", "unknown": "target"},
        {"source_id": "mut:0", "symbol": True},
        {"source_id": "mut:0", "symbol": ""},
    ],
    ids=["null-optional", "unknown-field", "bool-symbol", "empty-symbol"],
)
@pytest.mark.parametrize(
    "tool_name", ["inspect_program_structure", "inspect_dependency_usage"]
)
def test_sdk_and_research_accept_or_reject_the_same_analysis_arguments(
    tool_name: str, arguments: dict,
) -> None:
    sdk_result, research_result = asyncio.run(_both(tool_name, arguments))

    if arguments.get("symbol", object()) is None:
        assert sdk_result["status"] in {"ok", "incomplete"}
        assert research_result["status"] == sdk_result["status"]
        assert sdk_result["facts"] == research_result["facts"]
        assert sdk_result.get("query") == research_result.get("query")
    else:
        assert sdk_result["status"] == research_result["status"] == "error"
        assert sdk_result["error_code"] == research_result["error_code"] == (
            "invalid_arguments"
        )


async def _both(tool_name: str, arguments: dict) -> tuple[dict, dict]:
    return await asyncio.gather(
        _sdk_call(tool_name, arguments),
        _research_call(tool_name, arguments),
    )
