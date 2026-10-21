"""Exercise the real SDK runner offline on the two reviewed inputs."""

import asyncio
import json
from pathlib import Path

import pytest
from agents.models.interface import Model, ModelResponse
from agents.usage import Usage
from openai.types.responses import ResponseOutputMessage, ResponseOutputText

from mock_spec import inference
from mock_spec.scenario import validate_contract
from test_real_data import REVIEWS, load_java, load_python, snapshot


class SyntheticModel(Model):
    def __init__(self, response):
        self.response = response
        self.inputs = []

    async def get_response(self, *args, **kwargs):
        self.inputs.append(kwargs["input"])
        assert kwargs["tools"] == []
        assert kwargs["handoffs"] == []
        assert kwargs["output_schema"].is_strict_json_schema()
        return ModelResponse(
            output=[ResponseOutputMessage(
                id="synthetic", type="message", role="assistant", status="completed",
                content=[ResponseOutputText(
                    type="output_text", text=self.response, annotations=[],
                )],
            )],
            usage=Usage(), response_id=None,
        )

    async def stream_response(self, *args, **kwargs):
        raise AssertionError("streaming is not used")
        yield  # pragma: no cover


@pytest.mark.real_data
@pytest.mark.parametrize("review", REVIEWS, ids=["java", "python"])
def test_real_inputs_through_sdk_runner_with_synthetic_model(review, monkeypatch):
    loader = load_java if review["language"] == "java" else load_python
    task = loader(snapshot(review), case_id=review["case_id"])
    response = (
        Path(__file__).parents[1] / "examples/scenarios" /
        f"{review['language']}.synthetic.json"
    ).read_text()
    model = SyntheticModel(response)
    agent = inference.build_agent("synthetic-model")
    agent.model = model
    monkeypatch.setattr(inference, "build_agent", lambda model: agent)
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-not-a-real-key")

    contract = asyncio.run(inference.infer_scenario(task, model="synthetic-model"))

    assert len(model.inputs) == 1
    sent = model.inputs[0]
    if isinstance(sent, list):
        assert len(sent) == 1
        sent = sent[0]["content"]
    assert json.loads(sent) == task.to_dict()
    assert contract == validate_contract(response, task)
    assert all(item.assertion_policy == "not_implied"
               for item in contract.expected_interactions)
