import asyncio
import json
from copy import deepcopy
from types import SimpleNamespace

import pytest
from agents import AgentOutputSchema

import mock_spec.inference as inference
from mock_spec.schemas import UnifiedTask
from mock_spec.scenario import ScenarioContract
from test_scenario import contract_data
from test_schemas import valid_task_data


def _task(language: str = "python") -> UnifiedTask:
    data = valid_task_data()
    data["language"] = language
    return UnifiedTask.from_dict(data)


def _contract(language: str = "python") -> ScenarioContract:
    data = contract_data()
    data["language"] = language
    return ScenarioContract.model_validate(data)


def test_build_agent_uses_one_tool_free_structured_configuration() -> None:
    python_agent = inference.build_agent("test-model")
    java_agent = inference.build_agent("test-model")

    for agent in (python_agent, java_agent):
        assert agent.model == "test-model"
        assert agent.output_type is ScenarioContract
        assert agent.tools == []
        assert agent.handoffs == []
        assert agent.model_settings.store is False
        assert inference.PROMPT_VERSION in agent.instructions
        assert "Identify Test Expectations" in agent.instructions
        assert "Trace Dependencies" in agent.instructions
        assert "Infer Scenarios" in agent.instructions
        assert "Evaluate Evidence" in agent.instructions
        assert "Generate Contract" in agent.instructions


def test_scenario_contract_is_agents_sdk_structured_output_compatible() -> None:
    output_schema = AgentOutputSchema(ScenarioContract)

    parsed = output_schema.validate_json(json.dumps(contract_data()))

    assert output_schema.is_strict_json_schema() is True
    assert parsed == _contract()


@pytest.mark.parametrize("language", ["java", "python"])
def test_infer_scenario_sends_only_serialized_unified_task(
    monkeypatch: pytest.MonkeyPatch, language: str
) -> None:
    task = _task(language)
    expected = _contract(language)
    captured: dict[str, object] = {}

    async def fake_run(agent, input, *, max_turns, run_config):
        captured.update(
            agent=agent,
            input=input,
            max_turns=max_turns,
            run_config=run_config,
        )
        return SimpleNamespace(final_output=expected)

    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-key")
    monkeypatch.setattr(inference.Runner, "run", fake_run)

    actual = asyncio.run(
        inference.infer_scenario(task, model="test-model", timeout_seconds=1)
    )

    assert actual == expected
    assert json.loads(captured["input"]) == task.to_dict()
    assert captured["input"] == json.dumps(
        task.to_dict(), ensure_ascii=False, separators=(",", ":")
    )
    assert captured["max_turns"] == 1
    assert captured["agent"].output_type is ScenarioContract
    run_config = captured["run_config"]
    assert run_config.tracing_disabled is True
    assert run_config.trace_include_sensitive_data is False
    assert run_config.workflow_name == inference.PROMPT_VERSION


def test_infer_scenario_requires_api_key_before_model_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    called = False

    async def fake_run(*args, **kwargs):
        nonlocal called
        called = True

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(inference.Runner, "run", fake_run)

    with pytest.raises(inference.InferenceError, match="OPENAI_API_KEY is required"):
        asyncio.run(inference.infer_scenario(_task(), model="test-model"))
    assert called is False


def test_infer_scenario_reports_timeout_without_response_details(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def slow_run(*args, **kwargs):
        await asyncio.sleep(1)

    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-key")
    monkeypatch.setattr(inference.Runner, "run", slow_run)

    with pytest.raises(inference.InferenceError, match="^scenario inference timed out$"):
        asyncio.run(
            inference.infer_scenario(
                _task(), model="test-model", timeout_seconds=0.001
            )
        )


def test_infer_scenario_sanitizes_sdk_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret = "sensitive-request-body"

    async def failing_run(*args, **kwargs):
        raise RuntimeError(secret)

    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-key")
    monkeypatch.setattr(inference.Runner, "run", failing_run)

    with pytest.raises(inference.InferenceError) as caught:
        asyncio.run(inference.infer_scenario(_task(), model="test-model"))
    assert str(caught.value) == "scenario inference model call failed"
    assert secret not in str(caught.value)


def test_infer_scenario_rejects_invalid_structured_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    invalid = deepcopy(contract_data())
    invalid["case_id"] = "a-different-case"

    async def fake_run(*args, **kwargs):
        return SimpleNamespace(final_output=invalid)

    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-key")
    monkeypatch.setattr(inference.Runner, "run", fake_run)

    with pytest.raises(inference.InferenceError) as caught:
        asyncio.run(inference.infer_scenario(_task(), model="test-model"))
    assert str(caught.value) == "scenario inference returned an invalid contract"


@pytest.mark.parametrize("model", ["", "   ", None])
def test_build_agent_requires_explicit_model(model) -> None:
    with pytest.raises(inference.InferenceError, match="model must be configured"):
        inference.build_agent(model)


@pytest.mark.parametrize("timeout", [0, -1, float("nan"), float("inf")])
def test_infer_scenario_requires_finite_positive_timeout(
    monkeypatch: pytest.MonkeyPatch, timeout: float
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-key")
    with pytest.raises(inference.InferenceError, match="timeout must be positive"):
        asyncio.run(
            inference.infer_scenario(
                _task(), model="test-model", timeout_seconds=timeout
            )
        )
