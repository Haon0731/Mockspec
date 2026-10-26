"""The production SDK and staged controller share repository search/read semantics."""

from __future__ import annotations

import asyncio
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import sys
import time

import pytest


STAGED_ROOT = Path(__file__).parents[1] / "experiments" / "staged-inference"
sys.path.insert(0, str(STAGED_ROOT))

from controller import StagedRunConfig, execute_staged_run  # noqa: E402
from query import QueryExecutor  # noqa: E402
from scripted import FixedScriptModel, query_script, synthetic_task  # noqa: E402
import mock_spec.inference_v02 as inference_v02  # noqa: E402
from mock_spec.scenario import validate_contract  # noqa: E402
from mock_spec.schemas import UnifiedTask  # noqa: E402
from mock_spec.source_analysis import SourceAnalysis  # noqa: E402
from mock_spec.source_repository import ReviewedRepository  # noqa: E402
from mock_spec.validation import SourceStore  # noqa: E402
from test_inference_v02 import ScriptedModel, _message, _tool_call  # noqa: E402
from test_repository_sdk import _dynamic_contract  # noqa: E402


def _repository(tmp_path: Path) -> tuple[UnifiedTask, ReviewedRepository, Path]:
    task = UnifiedTask.from_dict(synthetic_task())
    repository_root = tmp_path / "snapshot"
    source_root = repository_root / "tree" / "repo" / "src"
    source_root.mkdir(parents=True)
    initial = b"def target(value):\n    return dependency(value)\n"
    (source_root / "example.py").write_bytes(initial)
    added = source_root / "new_context.py"
    added.write_text(
        "SEARCH_CONTRACT = 'dependency result is 2'\n"
        "def helper(value):\n"
        "    checked = dependency(value)\n"
        "    return checked\n"
        "# terminal context\n"
        "# final context\n",
        encoding="utf-8",
    )
    root_fd = os.open(repository_root, os.O_RDONLY | os.O_DIRECTORY)
    try:
        repository = ReviewedRepository(
            root_fd,
            task=task,
            snapshot_id="synthetic-search-snapshot",
            discovery_root="tree/repo",
            files_by_logical_path={
                "src/example.py": (
                    "tree/repo/src/example.py", hashlib.sha256(initial).hexdigest()
                )
            },
        )
    finally:
        os.close(root_fd)
    return task, repository, added


def _request(tool: str, arguments: dict[str, object]) -> dict[str, str]:
    return {
        "question": "Does new reviewed context constrain the candidate?",
        "scenario_id": "primary",
        "tool": tool,
        "arguments_json": json.dumps(arguments),
    }


def test_production_sdk_searches_reads_analyzes_and_cites_after_seven_calls(
    tmp_path: Path, monkeypatch
) -> None:
    task, repository, _ = _repository(tmp_path)
    store = SourceStore(task, repository=repository)
    logical_path = "src/new_context.py"
    dynamic_id = (
        "repository:"
        + hashlib.sha256(
            f"{repository.snapshot_id}\0{logical_path}".encode("utf-8")
        ).hexdigest()[:24]
    )
    contract = deepcopy(_dynamic_contract(task, repository, "src/example.py"))
    contract["evidence"][0].update(
        source_id=task.masked_test.source_id,
        start_line=5,
        end_line=5,
        quote="    assert target(1) == 2",
    )
    contract["evidence"][1].update(
        source_id=dynamic_id,
        start_line=1,
        end_line=1,
        quote="SEARCH_CONTRACT = 'dependency result is 2'",
    )
    model = ScriptedModel(
        _tool_call(
            "search-contract", operation="search", path_pattern="src/*.py",
            text="SEARCH_CONTRACT",
        ),
        _tool_call(
            "read-helper", operation="read", path=logical_path,
            start_line=2, end_line=4,
        ),
        _tool_call(
            "structure", tool_name="inspect_program_structure", source_id=dynamic_id,
        ),
        _tool_call(
            "usage", tool_name="inspect_dependency_usage", source_id=dynamic_id,
        ),
        _tool_call(
            "search-call", operation="search", text="dependency(value)",
        ),
        _tool_call(
            "read-return", operation="read", source_id=dynamic_id,
            start_line=4, end_line=4,
        ),
        _tool_call(
            "read-terminal", source_id=dynamic_id, start_line=6, end_line=6,
        ),
        _message(contract),
    )
    trace: list[dict] = []
    agent = inference_v02.build_agent(
        "offline-model", task, tool_trace=trace, source_store=store
    )
    agent.model = model
    monkeypatch.setattr(inference_v02, "build_agent", lambda *args, **kwargs: agent)
    monkeypatch.setenv("OPENAI_API_KEY", "offline-key-never-log")
    try:
        result = asyncio.run(inference_v02.infer_scenario(
            task, model="offline-model", tool_trace=trace, source_store=store,
        ))
    finally:
        repository.close()

    assert result.evidence[1].source_id == dynamic_id
    assert model.calls == 8
    assert [item["status"] for item in trace[:2]] == ["ok", "ok"]
    assert len(trace) == 7
    assert trace[0]["observations"][0]["text"] == contract["evidence"][1]["quote"]
    assert trace[-1]["arguments"] == {
        "source_id": dynamic_id, "start_line": 6, "end_line": 6,
    }


def test_public_staged_controller_mixes_seven_repository_queries_and_cites(
    tmp_path: Path,
) -> None:
    task, repository, _ = _repository(tmp_path)
    logical_path = "src/new_context.py"
    dynamic_id = (
        "repository:"
        + hashlib.sha256(
            f"{repository.snapshot_id}\0{logical_path}".encode("utf-8")
        ).hexdigest()[:24]
    )
    outputs = query_script(7)
    operations = [
        ("inspect_source", {
            "operation": "search", "path_pattern": logical_path,
        }),
        ("inspect_source", {
            "operation": "read", "path": logical_path,
            "start_line": 2, "end_line": 4,
        }),
        ("inspect_program_structure", {"source_id": dynamic_id}),
        ("inspect_dependency_usage", {"source_id": dynamic_id}),
        ("inspect_source", {
            "operation": "search", "path_pattern": "src/*.py",
            "text": "SEARCH_CONTRACT",
        }),
        ("inspect_source", {
            "operation": "read", "source_id": dynamic_id,
            "start_line": 5, "end_line": 5,
        }),
        ("inspect_source", {
            "operation": "read", "source_id": dynamic_id,
            "start_line": 6, "end_line": 6,
        }),
    ]
    for output, (tool, arguments) in zip(outputs[1:8], operations, strict=True):
        output["query_attempt"]["tool"] = tool
        output["query_attempt"]["arguments_json"] = json.dumps(arguments)

    dynamic_evidence = {
        "id": "searched-contract",
        "source_id": dynamic_id,
        "start_line": 1,
        "end_line": 1,
        "rationale": "A complete line returned by repository text search.",
    }
    expected_quote = "SEARCH_CONTRACT = 'dependency result is 2'"
    construct = outputs[9]
    deliver = outputs[10]
    construct["contract"] = deepcopy(construct["contract"])
    construct["contract"]["evidence"].append(dynamic_evidence)
    construct["contract"]["dependency_constraints"][0]["evidence_ids"].append(
        dynamic_evidence["id"]
    )
    deliver["contract"] = deepcopy(construct["contract"])

    result = asyncio.run(execute_staged_run(
        output_dir=tmp_path / "staged-search-run",
        model=FixedScriptModel(outputs),
        config=StagedRunConfig(),
        task_loader=lambda: (task, repository),
        requested_case_id=task.case_id,
    ))

    assert result["manifest"]["result_status"] == "SELECTED", result["record"]["error"]
    attempts = result["record"]["query_log"]
    assert len(attempts) == 7
    assert all(item["status"] in {"ok", "incomplete"} for item in attempts)
    assert attempts[0]["result"]["matches"][0]["text"] is None
    assert attempts[1]["result"]["text"].startswith("def helper")
    assert attempts[2]["result"]["facts"]
    assert attempts[3]["result"]["facts"]
    assert attempts[4]["result"]["matches"][0]["text"] == expected_quote
    assert attempts[5]["result"]["text"] == "# terminal context"
    assert attempts[6]["result"]["text"] == "# final context"
    observations = result["record"]["source_observations"]
    assert [item["response_id"] for item in observations] == [
        "query:2", "query:5", "query:6", "query:7",
    ]
    assert result["contract"].evidence[-1].model_dump() == {
        **dynamic_evidence, "quote": expected_quote,
    }
    constructed = next(item for item in result["record"]["stages"]
                       if item["stage"] == "CONSTRUCT")
    assert constructed["wire_result"]["contract"]["evidence"][-1] == dynamic_evidence
    assert constructed["result"]["contract"]["evidence"][-1]["quote"] == expected_quote


def test_staged_executor_uses_same_search_read_and_cursor_validation(
    tmp_path: Path,
) -> None:
    task, repository, _ = _repository(tmp_path)
    store = SourceStore(task, repository=repository)
    executor = QueryExecutor(
        task,
        source_store=store,
        max_attempts=None,
        max_output_chars=16000,
        analysis_timeout_seconds=2,
        deadline=time.monotonic() + 10,
    )
    async def exercise() -> tuple[dict, dict, dict, dict]:
        search = executor.register(_request("inspect_source", {
            "operation": "search", "path_pattern": "src/*.py",
            "text": "SEARCH_CONTRACT",
        }))
        await executor.execute_registered(search, candidate_ids={"primary"})
        match = search["result"]["matches"][0]
        read = executor.register(_request("inspect_source", {
            "operation": "read", "source_id": match["source_id"],
            "start_line": 2, "end_line": 3,
        }))
        await executor.execute_registered(read, candidate_ids={"primary"})
        invalid = executor.register(_request("inspect_source", {
            "operation": "search", "text": "SEARCH_CONTRACT",
            "cursor": "not-a-issued-cursor",
        }))
        await executor.execute_registered(invalid, candidate_ids={"primary"})
        retry = executor.register(_request("inspect_source", {
            "operation": "search", "text": "SEARCH_CONTRACT",
        }))
        await executor.execute_registered(retry, candidate_ids={"primary"})
        return search, read, invalid, retry

    try:
        search, read, invalid, retry = asyncio.run(exercise())
    finally:
        repository.close()

    assert search["status"] == "ok"
    assert search["result"]["complete"] is True
    assert read["status"] == "ok"
    assert read["result"]["text"] == "def helper(value):\n    checked = dependency(value)"
    assert invalid["status"] == "error"
    assert invalid["result"]["error_code"] == "invalid_cursor"
    assert retry["status"] == "ok"
    assert [item["sequence"] for item in executor.attempts] == [1, 2, 3, 4]
    assert [item["response_id"] for item in store.observations()] == [
        "query:1", "query:2", "query:4",
    ]


@pytest.mark.parametrize("unobserved_kind", ["path_only", "output_omitted"])
def test_search_does_not_authorize_unreturned_text(
    tmp_path: Path, unobserved_kind: str,
) -> None:
    task, repository, added = _repository(tmp_path)
    store = SourceStore(task, repository=repository)
    logical_path = "src/new_context.py"
    if unobserved_kind == "path_only":
        result = store.search(path_pattern=logical_path, response_id="path-only")
        assert result["matches"][0]["text"] is None
    else:
        logical_path = "src/large.py"
        added.with_name("large.py").write_text(
            "UNIQUE_OMITTED = '" + "x" * 2000 + "'\n", encoding="utf-8"
        )
        result = asyncio.run(SourceAnalysis(
            task, source_store=store, max_output_chars=512
        ).inspect_source(
            operation="search", text="UNIQUE_OMITTED", response_id="omitted"
        ))
        assert result["status"] == "incomplete"
        assert result["limit_reason"] == "output_limit"
        assert result["matches"] == []

    source_id = (
        "repository:"
        + hashlib.sha256(
            f"{repository.snapshot_id}\0{logical_path}".encode("utf-8")
        ).hexdigest()[:24]
    )
    contract = deepcopy(_dynamic_contract(task, repository, "src/example.py"))
    contract["evidence"][0].update(
        source_id=task.masked_test.source_id,
        start_line=5,
        end_line=5,
        quote="    assert target(1) == 2",
    )
    contract["evidence"][1].update(
        source_id=source_id,
        start_line=1,
        end_line=1,
        quote=(
            "SEARCH_CONTRACT = 'dependency result is 2'"
            if unobserved_kind == "path_only" else
            "UNIQUE_OMITTED = '" + "x" * 2000 + "'"
        ),
    )
    try:
        with pytest.raises(ValueError, match="unknown source_id|unobserved"):
            validate_contract(contract, task, source_store=store)
    finally:
        repository.close()


def test_staged_executor_keeps_content_change_visible_after_search(
    tmp_path: Path,
) -> None:
    task, repository, added = _repository(tmp_path)
    store = SourceStore(task, repository=repository)
    executor = QueryExecutor(
        task,
        source_store=store,
        max_attempts=None,
        max_output_chars=16000,
        analysis_timeout_seconds=2,
        deadline=time.monotonic() + 10,
    )
    contract = deepcopy(_dynamic_contract(task, repository, "src/example.py"))

    async def exercise() -> tuple[dict, dict]:
        search = executor.register(_request("inspect_source", {
            "operation": "search", "text": "SEARCH_CONTRACT",
        }))
        await executor.execute_registered(search, candidate_ids={"primary"})
        source_id = search["result"]["matches"][0]["source_id"]
        added.write_text("changed after search\n", encoding="utf-8")
        read = executor.register(_request("inspect_source", {
            "operation": "read", "source_id": source_id,
            "start_line": 1, "end_line": 1,
        }))
        await executor.execute_registered(read, candidate_ids={"primary"})
        return search, read

    try:
        search, read = asyncio.run(exercise())
    finally:
        repository.close()

    assert search["status"] == "ok"
    assert read["status"] == "error"
    assert read["result"]["error_code"] == "source_changed"
    assert len(store.observations()) == 1
    contract["evidence"][0].update(
        source_id=task.masked_test.source_id,
        start_line=5,
        end_line=5,
        quote="    assert target(1) == 2",
    )
    contract["evidence"][1].update(
        source_id=search["result"]["matches"][0]["source_id"],
        start_line=1,
        end_line=1,
        quote="SEARCH_CONTRACT = 'dependency result is 2'",
    )
    with pytest.raises(ValueError, match="repository evidence is invalid"):
        validate_contract(contract, task, source_store=store)
