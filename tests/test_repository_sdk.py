"""SDK-level coverage for reviewed whole-file reads and dynamic evidence."""

from __future__ import annotations

import asyncio
from copy import deepcopy
import hashlib
import json

import pytest
from agents.models.interface import ModelResponse
from agents.usage import Usage
from openai.types.responses import ResponseFunctionToolCall

import mock_spec.inference_v02 as inference_v02
from mock_spec import audited_run
from mock_spec.budget import UsageLedger
from mock_spec.adapters._frozen import _load_approved_with_repository
from mock_spec.inference import InferenceError
from mock_spec.scenario import validate_contract
from mock_spec.source_analysis import SourceAnalysis
from mock_spec.validation import SourceStore
from test_adapters import _build_snapshot
from test_inference_v02 import ScriptedModel, _message, _tool_call
from test_scenario import contract_data


def _repository_task(tmp_path):
    root, review = _build_snapshot(tmp_path, "python")
    logical_path = "src/target.py"
    source = next(
        item for item in review["sources"] if item["logical_path"] == logical_path
    )
    full_text = (
        "def target():\n"
        "    return dependency()\n"
        "# repository context 3\n"
        "# repository context 4\n"
        "# repository context 5\n"
        "# repository context 6\n"
        "# repository context 7\n"
        "# repository context 8\n"
        "# repository context 9\n"
        "# repository context 10\n"
        "DYNAMIC_EVIDENCE = dependency_contract()\n"
    ).encode()
    (root / "tree" / source["path"]).write_bytes(full_text)
    source["sha256"] = hashlib.sha256(full_text).hexdigest()
    source["start_line"] = 1
    source["end_line"] = 2
    task, repository = _load_approved_with_repository(root, review)
    store = SourceStore(task, repository=repository)
    return task, repository, store, logical_path


def _dynamic_contract(task, repository, logical_path):
    data = deepcopy(contract_data())
    data["case_id"] = task.case_id
    data["evidence"][0] = {
        "id": "expectation",
        "source_id": task.masked_test.source_id,
        "start_line": 5,
        "end_line": 5,
        "quote": "    assert target() == 1",
        "rationale": "Visible result assertion.",
    }
    data["evidence"][1] = {
        "id": "call",
        "source_id": repository.source_id_for(logical_path),
        "start_line": 11,
        "end_line": 11,
        "quote": "DYNAMIC_EVIDENCE = dependency_contract()",
        "rationale": "Repository evidence returned through the source tool.",
    }
    return data


def _install_repository_model(
    monkeypatch, task, store, model, *, budget, trace
):
    agent = inference_v02.build_agent(
        "offline-model",
        task,
        budget=budget,
        tool_trace=trace,
        source_store=store,
    )
    agent.model = model
    monkeypatch.setattr(inference_v02, "build_agent", lambda *args, **kwargs: agent)
    monkeypatch.setenv("OPENAI_API_KEY", "offline-key-never-log")


def _raw_tool_call(call_id: str, arguments: str) -> ModelResponse:
    return ModelResponse(
        output=[ResponseFunctionToolCall(
            arguments=arguments,
            call_id=call_id,
            name="inspect_source",
            type="function_call",
            status="completed",
        )],
        usage=Usage(input_tokens=10, output_tokens=5),
        response_id=None,
    )


def test_repository_mode_reads_beyond_fragment_and_count_limits(
    tmp_path, monkeypatch
) -> None:
    task, repository, store, logical_path = _repository_task(tmp_path)
    budget = inference_v02.InferenceBudget(max_turns=1, max_tool_calls=1)
    trace = []
    model = ScriptedModel(*[
        _tool_call(
            f"read-{line}",
            source_id=logical_path,
            start_line=line,
            end_line=line,
        )
        for line in range(3, 12)
    ], _message(_dynamic_contract(task, repository, logical_path)))
    _install_repository_model(
        monkeypatch, task, store, model, budget=budget, trace=trace
    )

    contract = asyncio.run(inference_v02.infer_scenario(
        task,
        model="offline-model",
        budget=budget,
        tool_trace=trace,
        source_store=store,
    ))

    assert contract.evidence[1].quote == "DYNAMIC_EVIDENCE = dependency_contract()"
    assert model.calls == 10
    assert len(trace) == 9
    assert [item["sequence"] for item in trace] == list(range(1, 10))
    assert all(item["status"] == "ok" for item in trace)
    assert trace[-1]["response_id"] == "read-11"
    assert trace[-1]["observations"][0]["response_id"] == "read-11"
    assert trace[-1]["observations"][0]["text"] == contract.evidence[1].quote
    assert repository.snapshot_id not in json.dumps(task.to_dict())


def test_repository_mode_logs_malformed_and_wrong_typed_requests(
    tmp_path, monkeypatch
) -> None:
    task, _repository, store, _logical_path = _repository_task(tmp_path)
    budget = inference_v02.InferenceBudget()
    trace = []
    model = ScriptedModel(
        _raw_tool_call("malformed", "{not-json"),
        _raw_tool_call(
            "duplicate-key",
            '{"source_id":"src/target.py","source_id":"mut",'
            '"start_line":1,"end_line":1}',
        ),
        _raw_tool_call("extra-field", json.dumps({
            "source_id": "src/target.py", "start_line": 1, "end_line": 1,
            "unexpected": "must be rejected",
        })),
        _raw_tool_call("wrong-type", json.dumps({
            "source_id": 3, "start_line": 1, "end_line": 1,
        })),
        _message({**_dynamic_contract(
            task, store.repository, task.mut_source.logical_path
        ), "evidence": [{
            "id": "expectation",
            "source_id": task.masked_test.source_id,
            "start_line": 5,
            "end_line": 5,
            "quote": "    assert target() == 1",
            "rationale": "Visible result assertion.",
        }]}),
    )
    # Remove dangling references to the omitted dynamic evidence.
    final = model.responses[-1]
    output = json.loads(final.output[0].content[0].text)
    for field in (
        "test_objectives", "scenario_hypotheses", "dependency_constraints",
        "expected_interactions",
    ):
        for item in output[field]:
            item["evidence_ids"] = ["expectation"]
    final.output[0].content[0].text = json.dumps(output)
    _install_repository_model(
        monkeypatch, task, store, model, budget=budget, trace=trace
    )

    contract = asyncio.run(inference_v02.infer_scenario(
        task,
        model="offline-model",
        budget=budget,
        tool_trace=trace,
        source_store=store,
    ))

    assert contract.case_id == task.case_id
    assert [item["response_id"] for item in trace] == [
        "malformed", "duplicate-key", "extra-field", "wrong-type",
    ]
    assert trace[0]["raw_arguments"] == "{not-json"
    assert trace[0]["arguments"] is None
    assert trace[1]["arguments"] is None
    assert trace[2]["arguments"]["unexpected"] == "must be rejected"
    assert trace[3]["arguments"]["source_id"] == 3
    assert [item["result"]["error_code"] for item in trace] == [
        "invalid_arguments", "invalid_arguments", "invalid_arguments",
        "invalid_arguments",
    ]
    assert all(item["status"] == "error" for item in trace)
    assert all(item["observations"] == [] for item in trace)


def test_repository_mode_timeout_covers_followup_model_call(
    tmp_path, monkeypatch
) -> None:
    task, _repository, store, logical_path = _repository_task(tmp_path)

    class ReadThenSlow(ScriptedModel):
        async def get_response(self, *args, **kwargs):
            if self.calls == 1:
                await asyncio.sleep(1)
            return await super().get_response(*args, **kwargs)

    budget = inference_v02.InferenceBudget(max_turns=1, max_tool_calls=1)
    trace = []
    model = ReadThenSlow(
        _tool_call("read-3", source_id=logical_path, start_line=3, end_line=3),
        _message(_dynamic_contract(task, store.repository, logical_path)),
    )
    _install_repository_model(
        monkeypatch, task, store, model, budget=budget, trace=trace
    )

    with pytest.raises(InferenceError) as caught:
        asyncio.run(inference_v02.infer_scenario(
            task,
            model="offline-model",
            budget=budget,
            tool_trace=trace,
            source_store=store,
            timeout_seconds=0.05,
        ))

    assert isinstance(caught.value, inference_v02.InferenceTimeoutError)
    assert model.calls == 1
    assert trace[0]["status"] == "ok"
    assert trace[0]["observations"][0]["response_id"] == "read-3"


def test_repository_mode_keeps_token_usage_across_calls(
    tmp_path, monkeypatch
) -> None:
    task, _repository, store, logical_path = _repository_task(tmp_path)
    budget = inference_v02.InferenceBudget(
        max_turns=1,
        max_tool_calls=1,
        max_output_tokens=1,
        max_total_tokens=61_000,
    )
    ledger = UsageLedger(budget)
    first = _tool_call(
        "read-3", source_id=logical_path, start_line=3, end_line=3
    )
    first.usage = Usage(input_tokens=60_000, output_tokens=5)
    model = ScriptedModel(first, _message(
        _dynamic_contract(task, store.repository, logical_path)
    ))
    trace = []
    _install_repository_model(
        monkeypatch, task, store, model, budget=budget, trace=trace
    )

    with pytest.raises(InferenceError) as caught:
        asyncio.run(inference_v02.infer_scenario(
            task,
            model="offline-model",
            budget=budget,
            usage_ledger=ledger,
            tool_trace=trace,
            source_store=store,
        ))

    assert caught.value.category == "budget_exhausted"
    assert model.calls == 1
    assert ledger.requests_started == ledger.requests_completed == 1
    assert ledger.total_tokens == 60_005
    assert ledger.gate_events[-1]["reason"] == "projected_token_limit"
    assert trace[0]["status"] == "ok"


def test_repository_mode_cancellation_keeps_tool_trace(
    tmp_path, monkeypatch
) -> None:
    async def exercise() -> None:
        task, _repository, store, logical_path = _repository_task(tmp_path)
        started = asyncio.Event()
        stopped = asyncio.Event()

        async def wait_for_cancellation(self, **arguments):
            started.set()
            try:
                await asyncio.Future()
            finally:
                stopped.set()

        monkeypatch.setattr(SourceAnalysis, "inspect_source", wait_for_cancellation)
        budget = inference_v02.InferenceBudget(max_turns=1, max_tool_calls=1)
        model = ScriptedModel(
            _tool_call(
                "cancelled-read", source_id=logical_path,
                start_line=3, end_line=3,
            )
        )
        trace = []
        _install_repository_model(
            monkeypatch, task, store, model, budget=budget, trace=trace
        )
        running = asyncio.create_task(inference_v02.infer_scenario(
            task,
            model="offline-model",
            budget=budget,
            tool_trace=trace,
            source_store=store,
        ))
        try:
            await asyncio.wait_for(started.wait(), timeout=2)
        finally:
            running.cancel()
            with pytest.raises(asyncio.CancelledError):
                await running

        assert stopped.is_set()
        assert len(trace) == 1
        assert trace[0]["response_id"] == "cancelled-read"
        assert trace[0]["status"] == "cancelled"
        assert trace[0]["result"] is None
        assert trace[0]["observations"] == []

    asyncio.run(exercise())


@pytest.mark.parametrize(
    "fail, expected_code, expected_status",
    [(False, 0, "completed"), (True, 2, "evidence_error")],
)
def test_audited_repository_run_forwards_store_and_persists_trace(
    tmp_path, monkeypatch, fail, expected_code, expected_status
) -> None:
    task, repository, _store, logical_path = _repository_task(tmp_path)
    monkeypatch.setattr(
        audited_run.python,
        "load_task_with_repository",
        lambda *args, **kwargs: (task, repository),
    )
    monkeypatch.setenv("OPENAI_API_KEY", "offline-key-never-log")

    async def infer_with_repository(task_arg, **kwargs):
        store = kwargs["source_store"]
        assert isinstance(store, SourceStore)
        assert store.repository is repository
        analysis = SourceAnalysis(task_arg, source_store=store)
        result = await analysis.inspect_source(
            logical_path, 11, 11, response_id="audited-read-11"
        )
        observations = list(store.observations())
        kwargs["tool_trace"].append({
            "sequence": 1,
            "tool": "inspect_source",
            "arguments": {
                "source_id": logical_path, "start_line": 11, "end_line": 11,
            },
            "raw_arguments": json.dumps({
                "source_id": logical_path, "start_line": 11, "end_line": 11,
            }),
            "response_id": "audited-read-11",
            "status": result["status"],
            "result": result,
            "observations": observations,
            "duration_seconds": 0.0,
        })
        if fail:
            raise InferenceError("invalid dynamic evidence", "evidence_error")
        return validate_contract(
            _dynamic_contract(task_arg, repository, logical_path),
            task_arg,
            source_store=store,
        )

    monkeypatch.setattr(inference_v02, "infer_scenario", infer_with_repository)
    output_dir = tmp_path / f"audited-repository-{'failure' if fail else 'success'}"
    args = [
        "--language", "python",
        "--snapshot", str(tmp_path / "unused-snapshot-path"),
        "--case-id", task.case_id,
        "--model", "offline-model",
        "--agent-version", "v0.2",
        "--output-dir", str(output_dir),
        "--standalone",
        "--repository-read",
        "--max-turns", "1",
        "--max-tool-calls", "1",
    ]

    assert audited_run.main(args) == expected_code
    artifact = json.loads((output_dir / "result.json").read_text())

    assert artifact["repository_read"] is True
    assert artifact["parameters"]["max_turns"] is None
    assert artifact["parameters"]["max_tool_calls"] is None
    assert artifact["status"] == expected_status
    assert artifact["tool_trace"][0]["response_id"] == "audited-read-11"
    observation = artifact["tool_trace"][0]["observations"][0]
    assert observation["response_id"] == "audited-read-11"
    assert observation["text"] == "DYNAMIC_EVIDENCE = dependency_contract()"
    if fail:
        assert artifact["contract"] is None
    else:
        assert artifact["contract"]["evidence"][1]["source_id"].startswith(
            "repository:"
        )
