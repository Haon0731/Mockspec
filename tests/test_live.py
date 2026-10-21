"""Offline transport doubles only: these tests are not live inference evidence."""
import asyncio
import json

import pytest

from mock_spec import inference, live
from test_inference import _task
from test_scenario import contract_data
from test_scenario_real import SyntheticModel


@pytest.mark.parametrize('kind', ['valid', 'structure', 'evidence'])
def test_capture_preserves_response_before_validation(monkeypatch, kind):
    data = contract_data()
    if kind == 'structure':
        data = {'invalid': True}
    elif kind == 'evidence':
        data['case_id'] = 'wrong-case'
    text = json.dumps(data)
    agent = inference.build_agent('offline-test')
    agent.model = SyntheticModel(text)
    monkeypatch.setattr(inference, 'build_agent', lambda _: agent)
    monkeypatch.setenv('OPENAI_API_KEY', 'offline-test-key')
    responses = []
    if kind == 'valid':
        asyncio.run(inference.infer_scenario(_task(), model='offline-test', responses=responses))
    else:
        with pytest.raises(inference.InferenceError) as caught:
            asyncio.run(inference.infer_scenario(_task(), model='offline-test', responses=responses))
        assert caught.value.category == kind + '_error'
    assert responses[0]['output'][0]['content'][0]['text'] == text
    assert 'usage' in responses[0]
    json.dumps(responses, allow_nan=False)


def test_live_missing_credentials_does_not_create_output(monkeypatch, tmp_path):
    monkeypatch.delenv('OPENAI_API_KEY', raising=False)
    out = tmp_path / 'run'
    assert live.main(['--language', 'python', '--snapshot', 'missing', '--case-id', 'x',
                      '--model', 'test', '--output-dir', str(out)]) == 2
    assert not out.exists()


def test_live_sdk_response_persists_usage(monkeypatch, tmp_path):
    monkeypatch.setenv('OPENAI_API_KEY', 'offline-key')
    monkeypatch.setattr(live.python, 'load_task', lambda *a, **kw: _task())
    agent = inference.build_agent('offline-test')
    agent.model = SyntheticModel(json.dumps(contract_data()))
    monkeypatch.setattr(inference, 'build_agent', lambda _: agent)
    out = tmp_path / 'run'
    assert live.main([
        '--language', 'python', '--snapshot', 'snapshot', '--case-id', 'example_1',
        '--model', 'offline-test', '--output-dir', str(out),
    ]) == 0
    data = json.loads((out / 'result.json').read_text())
    assert data['raw_responses'][0]['usage']['input_tokens_details']['cached_tokens'] == 0
    assert data['contract'] == contract_data()


@pytest.mark.parametrize('failure', [None, 'model_call_failure', 'structure_error', 'evidence_error'])
def test_live_artifact_and_no_overwrite(monkeypatch, tmp_path, failure):
    monkeypatch.setenv('OPENAI_API_KEY', 'offline-key-never-log')
    monkeypatch.setattr(live.python, 'load_task', lambda *a, **kw: _task())
    calls = []
    async def fake(task, **kwargs):
        calls.append(task)
        assert kwargs["model"] == "offline-test"
        kwargs["responses"].append({
            "output": [{"text": "original response"}],
            "usage": {"input_tokens": 10, "output_tokens": 20},
            "response_id": "offline-response",
        })
        if failure:
            raise inference.InferenceError('safe', failure)
        from mock_spec.scenario import ScenarioContract
        return ScenarioContract.model_validate(contract_data())
    monkeypatch.setattr(live, 'infer_scenario', fake)
    out = tmp_path / 'run'
    args = ['--language', 'python', '--snapshot', 'snapshot', '--case-id', 'example_1',
            '--model', ' offline-test ', '--output-dir', str(out)]
    assert live.main(args) == (2 if failure else 0)
    raw = (out / 'result.json').read_text()
    data = json.loads(raw)
    assert data['status'] == (failure or 'completed')
    assert data['semantic_review'] == 'pending'
    assert data['model'] == 'offline-test'
    assert data['raw_responses'][0]['output'] == [{"text": "original response"}]
    assert data['raw_responses'][0]['usage']['output_tokens'] == 20
    assert data['raw_responses'][0]['response_id'] == 'offline-response'
    assert len(data['prompt_sha256']) == len(data['task_sha256']) == 64
    assert data['task_schema_version'] == _task().schema_version
    assert data['contract_schema_version'] == 'mock-spec.scenario/v0.1'
    assert data['parameters']['max_turns'] == 1
    assert data['packages']['openai-agents'] == '0.24.0'
    assert data['contract'] == (None if failure else contract_data())
    assert data['validation'] == (
        failure if failure in ('structure_error', 'evidence_error')
        else 'not_run' if failure else 'schema_and_evidence_locations_passed'
    )
    assert 'offline-key-never-log' not in raw
    assert live.main(args) == 2
    assert len(calls) == 1
