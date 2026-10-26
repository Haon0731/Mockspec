"""Real parsers with scripted SDK responses: integration, not autonomous behavior."""

import asyncio
import json
from pathlib import Path

import pytest
from agents.models.interface import ModelResponse
from agents.usage import Usage
from openai.types.responses import ResponseFunctionToolCall

from mock_spec import inference_v02
from mock_spec.scenario import validate_contract
from test_inference_v02 import ScriptedModel, _install_model, _message
from test_real_data import EXAMPLE_REVIEWS, load_java, load_python, snapshot


@pytest.mark.real_data
@pytest.mark.parametrize("review", EXAMPLE_REVIEWS, ids=lambda review: review["case_id"])
def test_real_analysis_tools_through_sdk_to_validated_contract(review, monkeypatch):
    loader = load_java if review["language"] == "java" else load_python
    task = loader(snapshot(review), case_id=review["case_id"])
    expected = json.loads((Path(__file__).parents[1] / "examples/scenarios" /
                           f"{task.language}.synthetic.json").read_text())
    requests = []
    for i, tool in enumerate(("inspect_program_structure", "inspect_dependency_usage")):
        requests.append(ModelResponse(output=[ResponseFunctionToolCall(
            name=tool, type="function_call", call_id=f"scripted-{i}",
            arguments=json.dumps({"source_id": task.mut_source.source_id}),
        )], usage=Usage(input_tokens=1, output_tokens=1), response_id=None))
    model = ScriptedModel(*requests, _message(expected))
    trace = []
    _install_model(monkeypatch, task, model, tool_trace=trace)
    contract = asyncio.run(inference_v02.infer_scenario(
        task, model="scripted-offline", tool_trace=trace,
    ))
    assert contract == validate_contract(expected, task)
    assert model.calls == 3
    assert len(trace) == 2
    for item in trace:
        result = item["result"]
        if result.get("error_code") == "backend_unavailable":
            pytest.skip("Pinned Spoon/JDK is unavailable: SDK exercised error recovery only")
        assert result["status"] in {"ok", "incomplete"}, result
        assert result["facts"]
        assert any(f["kind"] == "call" for f in result["facts"])
        assert result["limitations"]
    assert "function_call_output" in json.dumps(model.inputs[-1], default=str)
