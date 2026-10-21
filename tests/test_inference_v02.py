"""Offline SDK-runner coverage for Scenario Inference Agent V0.2."""

from __future__ import annotations

import asyncio
from copy import deepcopy
import json
from pathlib import Path

import pytest
from agents.models.interface import Model, ModelResponse
from agents.usage import Usage
from openai.types.responses import (
    ResponseFunctionToolCall,
    ResponseOutputMessage,
    ResponseOutputText,
)

import mock_spec.inference_v02 as inference_v02
from mock_spec.inference import InferenceError
from mock_spec.scenario import ScenarioContract
from mock_spec.schemas import UnifiedTask
from test_scenario import contract_data
from test_real_data import REVIEWS, load_java, load_python, snapshot
from test_schemas import valid_task_data


def _task() -> UnifiedTask:
    return UnifiedTask.from_dict(valid_task_data())


def _message(data: dict) -> ModelResponse:
    return ModelResponse(
        output=[ResponseOutputMessage(
            id="message", type="message", role="assistant", status="completed",
            content=[ResponseOutputText(
                type="output_text", text=json.dumps(data), annotations=[]
            )],
        )],
        usage=Usage(input_tokens=10, output_tokens=20),
        response_id=None,
    )


def _tool_call(
    call_id: str, *, tool_name: str = "inspect_source", **arguments
) -> ModelResponse:
    return ModelResponse(
        output=[ResponseFunctionToolCall(
            arguments=json.dumps(arguments),
            call_id=call_id,
            name=tool_name,
            type="function_call",
            status="completed",
        )],
        usage=Usage(input_tokens=10, output_tokens=5),
        response_id=None,
    )


class ScriptedModel(Model):
    def __init__(self, *responses: ModelResponse):
        self.responses = list(responses)
        self.inputs = []
        self.calls = 0

    async def get_response(self, *args, **kwargs):
        self.inputs.append(kwargs["input"])
        assert [tool.name for tool in kwargs["tools"]] == [
            "inspect_source",
            "inspect_program_structure",
            "inspect_dependency_usage",
        ]
        assert kwargs["handoffs"] == []
        assert kwargs["output_schema"].is_strict_json_schema()
        response = self.responses[self.calls]
        self.calls += 1
        return response

    async def stream_response(self, *args, **kwargs):
        raise AssertionError("streaming is not used")
        yield  # pragma: no cover


class SlowModel(Model):
    def __init__(self):
        self.calls = 0

    async def get_response(self, *args, **kwargs):
        self.calls += 1
        await asyncio.sleep(1)
        raise AssertionError("timeout should cancel the model call")

    async def stream_response(self, *args, **kwargs):
        raise AssertionError("streaming is not used")
        yield  # pragma: no cover


def _install_model(monkeypatch, task, model, *, budget=None, tool_trace=None):
    agent = inference_v02.build_agent(
        "offline-model", task, budget=budget, tool_trace=tool_trace
    )
    agent.model = model
    monkeypatch.setattr(
        inference_v02,
        "build_agent",
        lambda _model, _task, **_kwargs: agent,
    )
    monkeypatch.setenv("OPENAI_API_KEY", "offline-key-never-log")


def _serialized(value) -> str:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    return json.dumps(value, default=str)


def test_build_agent_is_task_scoped_and_tool_augmented() -> None:
    agent = inference_v02.build_agent(" test-model ", _task())

    assert agent.model == "test-model"
    assert agent.output_type is ScenarioContract
    assert [tool.name for tool in agent.tools] == [
        "inspect_source",
        "inspect_program_structure",
        "inspect_dependency_usage",
    ]
    assert agent.handoffs == []
    assert agent.model_settings.store is False
    assert agent.model_settings.max_tokens == 4_096
    assert agent.model_settings.retry.max_retries == 0
    assert inference_v02.PROMPT_VERSION in agent.instructions
    assert "zero tool calls is valid" in agent.instructions
    assert "not data flow or path feasibility" in agent.instructions


def test_sdk_runner_calls_source_tool_then_validates_contract(monkeypatch) -> None:
    task = _task()
    model = ScriptedModel(
        _tool_call("call-1", source_id="mut:0", start_line=10, end_line=11),
        _message(contract_data()),
    )
    trace = []
    responses = []
    _install_model(monkeypatch, task, model, tool_trace=trace)

    contract = asyncio.run(inference_v02.infer_scenario(
        task,
        model="offline-model",
        responses=responses,
        tool_trace=trace,
    ))

    assert contract == ScenarioContract.model_validate(contract_data())
    assert model.calls == 2
    sent = model.inputs[0]
    if isinstance(sent, list):
        assert len(sent) == 1
        sent = sent[0]["content"]
    assert sent == json.dumps(task.to_dict(), ensure_ascii=False, separators=(",", ":"))
    assert len(trace) == 1
    assert trace[0]["sequence"] == 1
    assert trace[0]["tool"] == "inspect_source"
    assert trace[0]["arguments"] == {
        "source_id": "mut:0", "start_line": 10, "end_line": 11,
    }
    assert trace[0]["status"] == "ok"
    assert trace[0]["result"]["text"] == (
        "def target(value):\n    return dependency(value)"
    )
    assert trace[0]["duration_seconds"] >= 0
    assert len(responses) == 2
    assert "return dependency(value)" in _serialized(model.inputs[1])


def test_sdk_runner_allows_zero_tool_calls(monkeypatch) -> None:
    task = _task()
    model = ScriptedModel(_message(contract_data()))
    trace = []
    _install_model(monkeypatch, task, model, tool_trace=trace)

    contract = asyncio.run(inference_v02.infer_scenario(
        task, model="offline-model", tool_trace=trace
    ))

    assert contract.case_id == task.case_id
    assert trace == []
    assert model.calls == 1


def test_sdk_runner_can_use_all_three_tools(monkeypatch) -> None:
    task = _task()

    async def structure(self, source_id):
        return {"status": "ok", "backend": "synthetic-structure", "facts": []}

    async def dependencies(self, source_id):
        return {"status": "ok", "backend": "synthetic-dependency", "facts": []}

    monkeypatch.setattr(inference_v02.ProgramTools, "inspect_program_structure", structure)
    monkeypatch.setattr(inference_v02.ProgramTools, "inspect_dependency_usage", dependencies)
    model = ScriptedModel(
        _tool_call("source", source_id="mut:0", start_line=10, end_line=10),
        _tool_call(
            "structure", tool_name="inspect_program_structure", source_id="mut:0"
        ),
        _tool_call(
            "dependencies", tool_name="inspect_dependency_usage", source_id="mut:0"
        ),
        _message(contract_data()),
    )
    trace = []
    _install_model(monkeypatch, task, model, tool_trace=trace)

    result = asyncio.run(inference_v02.infer_scenario(
        task, model="offline-model", tool_trace=trace
    ))

    assert result.case_id == task.case_id
    assert [item["tool"] for item in trace] == [
        "inspect_source",
        "inspect_program_structure",
        "inspect_dependency_usage",
    ]
    assert [item["sequence"] for item in trace] == [1, 2, 3]


@pytest.mark.real_data
@pytest.mark.parametrize("review", REVIEWS, ids=["java", "python"])
def test_reviewed_inputs_use_source_tool_through_sdk_runner(review, monkeypatch) -> None:
    loader = load_java if review["language"] == "java" else load_python
    task = loader(snapshot(review), case_id=review["case_id"])
    contract = json.loads((
        Path(__file__).parents[1]
        / "examples/scenarios"
        / f"{review['language']}.synthetic.json"
    ).read_text())
    model = ScriptedModel(
        _tool_call(
            "source-call",
            source_id=task.mut_source.source_id,
            start_line=task.mut_source.start_line,
            end_line=task.mut_source.start_line,
        ),
        _message(contract),
    )
    trace = []
    _install_model(monkeypatch, task, model, tool_trace=trace)

    result = asyncio.run(inference_v02.infer_scenario(
        task, model="offline-model", tool_trace=trace
    ))

    assert result.case_id == review["case_id"]
    assert trace[0]["status"] == "ok"
    assert trace[0]["result"]["source_id"] == task.mut_source.source_id
    assert model.calls == 2


def test_tool_error_is_visible_to_model_and_recoverable(monkeypatch) -> None:
    task = _task()
    model = ScriptedModel(
        _tool_call("call-bad", source_id="private", start_line=1, end_line=1),
        _message(contract_data()),
    )
    trace = []
    _install_model(monkeypatch, task, model, tool_trace=trace)

    contract = asyncio.run(inference_v02.infer_scenario(
        task, model="offline-model", tool_trace=trace
    ))

    assert contract.case_id == task.case_id
    assert trace[0]["status"] == "error"
    assert trace[0]["result"]["error_code"] == "invalid_source_or_range"
    second_input = _serialized(model.inputs[1])
    assert "invalid_source_or_range" in second_input
    assert "offline-key-never-log" not in second_input


def test_tool_call_budget_stops_without_another_model_call(monkeypatch) -> None:
    task = _task()
    budget = inference_v02.InferenceBudget(max_turns=6, max_tool_calls=1)
    model = ScriptedModel(
        _tool_call("call-1", source_id="mut:0", start_line=10, end_line=10),
        _tool_call("call-2", source_id="mut:0", start_line=11, end_line=11),
        _message(contract_data()),
    )
    trace = []
    _install_model(monkeypatch, task, model, budget=budget, tool_trace=trace)

    with pytest.raises(InferenceError) as caught:
        asyncio.run(inference_v02.infer_scenario(
            task, model="offline-model", budget=budget, tool_trace=trace
        ))

    assert caught.value.category == "budget_exhausted"
    assert model.calls == 2
    assert [item["status"] for item in trace] == ["ok", "budget_exhausted"]


def test_invalid_evidence_is_rejected_after_zero_tool_calls(monkeypatch) -> None:
    task = _task()
    invalid = deepcopy(contract_data())
    invalid["evidence"][0]["quote"] = "fabricated"
    model = ScriptedModel(_message(invalid))
    _install_model(monkeypatch, task, model)

    with pytest.raises(InferenceError) as caught:
        asyncio.run(inference_v02.infer_scenario(task, model="offline-model"))

    assert caught.value.category == "evidence_error"


def test_turn_limit_maps_to_budget_exhaustion(monkeypatch) -> None:
    task = _task()
    budget = inference_v02.InferenceBudget(max_turns=1)
    model = ScriptedModel(
        _tool_call("call-1", source_id="mut:0", start_line=10, end_line=10),
    )
    _install_model(monkeypatch, task, model, budget=budget)

    with pytest.raises(InferenceError) as caught:
        asyncio.run(inference_v02.infer_scenario(
            task, model="offline-model", budget=budget
        ))

    assert caught.value.category == "budget_exhausted"
    assert model.calls == 1


def test_timeout_stops_the_active_sdk_run(monkeypatch) -> None:
    task = _task()
    model = SlowModel()
    _install_model(monkeypatch, task, model)

    with pytest.raises(InferenceError) as caught:
        asyncio.run(inference_v02.infer_scenario(
            task, model="offline-model", timeout_seconds=0.05
        ))

    assert caught.value.category == "budget_exhausted"
    assert model.calls == 1


@pytest.mark.parametrize("field", ["max_turns", "max_tool_calls", "max_tool_output_chars"])
def test_budget_requires_positive_integer(field: str) -> None:
    values = {"max_turns": 6, "max_tool_calls": 8, "max_tool_output_chars": 16_000}
    values[field] = 0
    with pytest.raises(InferenceError, match=field):
        inference_v02.InferenceBudget(**values)


def test_tool_output_budget_matches_program_tools_minimum() -> None:
    with pytest.raises(InferenceError, match="max_tool_output_chars"):
        inference_v02.InferenceBudget(max_tool_output_chars=511)
