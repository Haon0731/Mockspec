"""Audited V0.1/V0.2 artifact runner tests; no provider calls."""

from __future__ import annotations

import json

from agents.usage import Usage

from mock_spec import inference, inference_v02, live_v02
from mock_spec.inference import InferenceError
from mock_spec.scenario import validate_contract
from test_inference_v02 import ScriptedModel, _install_model, _message, _task
from test_scenario import contract_data


def _args(tmp_path, version="v0.2"):
    return [
        "--language", "python",
        "--snapshot", str(tmp_path / "snapshot"),
        "--case-id", "example_1",
        "--model", "offline-model",
        "--agent-version", version,
        "--output-dir", str(tmp_path / f"run-{version}"),
        "--standalone",
    ]


def test_v02_audit_artifact_uses_sdk_and_refuses_overwrite(tmp_path, monkeypatch) -> None:
    task = _task()
    monkeypatch.setattr(live_v02.python, "load_task", lambda *args, **kwargs: task)
    model = ScriptedModel(_message(contract_data()))
    _install_model(monkeypatch, task, model)

    args = _args(tmp_path)
    assert live_v02.main(args) == 0
    artifact = json.loads((tmp_path / "run-v0.2/result.json").read_text())

    assert artifact["agent_version"] == "v0.2"
    assert artifact["mode"] == "live_inference"
    assert artifact["status"] == "completed"
    assert artifact["contract"] == contract_data()
    assert artifact["usage"]["total_tokens"] == 30
    assert artifact["parameters"]["transport_retries"] == "disabled (max_retries=0)"
    assert artifact["parameters"]["max_tool_output_chars"] == 16_000
    assert artifact["parameters"]["analysis_timeout_seconds"] == 15
    assert artifact["parameters"]["max_cost_usd"] is None
    assert isinstance(artifact["git_dirty"], bool)
    assert "budget.py" in artifact["code_files_sha256"]
    assert "program_tools.py" in artifact["code_files_sha256"]
    assert len(artifact["raw_responses"]) == 1
    assert artifact["tool_trace"] == []
    assert len(artifact["prompt_sha256"]) == len(artifact["task_sha256"]) == 64
    assert live_v02.main(args) == 2
    assert model.calls == 1


def test_v01_audit_records_unequal_caps_without_changing_baseline(tmp_path, monkeypatch) -> None:
    task = _task()
    monkeypatch.setattr(live_v02.python, "load_task", lambda *args, **kwargs: task)

    async def baseline(task, **kwargs):
        kwargs["responses"].append({
            "output": [{"offline": True}],
            "usage": {"requests": 1, "input_tokens": 10, "output_tokens": 20,
                      "total_tokens": 30},
            "response_id": None,
        })
        return validate_contract(contract_data(), task)

    monkeypatch.setattr(inference, "infer_scenario", baseline)
    monkeypatch.setenv("OPENAI_API_KEY", "offline-key")

    assert live_v02.main(_args(tmp_path, "v0.1")) == 0
    artifact = json.loads((tmp_path / "run-v0.1/result.json").read_text())

    assert artifact["prompt_version"] == "scenario-inference/v0.1"
    assert artifact["parameters"]["max_output_tokens"] is None
    assert artifact["parameters"]["max_total_tokens"] is None
    assert artifact["parameters"]["transport_retries"].startswith("frozen baseline")


def test_failed_v02_artifact_keeps_trace_usage_and_no_contract(tmp_path, monkeypatch) -> None:
    task = _task()
    monkeypatch.setattr(live_v02.python, "load_task", lambda *args, **kwargs: task)

    async def failure(task, **kwargs):
        kwargs["tool_trace"].append({
            "sequence": 1, "tool": "inspect_source", "arguments": {},
            "status": "error", "result": {"status": "error"},
            "duration_seconds": 0.1,
        })
        kwargs["responses"].append({
            "output": [{"offline": True}],
            "usage": {"requests": 1, "input_tokens": 1, "output_tokens": 1,
                      "total_tokens": 2},
            "response_id": None,
        })
        raise InferenceError("invalid", "evidence_error")

    monkeypatch.setattr(inference_v02, "infer_scenario", failure)
    monkeypatch.setenv("OPENAI_API_KEY", "offline-key")

    assert live_v02.main(_args(tmp_path)) == 2
    artifact = json.loads((tmp_path / "run-v0.2/result.json").read_text())

    assert artifact["status"] == "evidence_error"
    assert artifact["validation"] == "evidence_error"
    assert artifact["contract"] is None
    assert artifact["tool_trace"][0]["status"] == "error"


def test_experiment_ledger_is_completed_by_audited_run(tmp_path, monkeypatch) -> None:
    task = _task()
    monkeypatch.setattr(live_v02.python, "load_task", lambda *args, **kwargs: task)
    response = _message(contract_data())
    response.usage = Usage(requests=1, input_tokens=10, output_tokens=20, total_tokens=30)
    model = ScriptedModel(response)
    _install_model(monkeypatch, task, model)
    ledger = tmp_path / "experiment.json"
    args = _args(tmp_path) + [
        "--max-cost-usd", "1",
        "--input-cost-per-million", "2",
        "--output-cost-per-million", "4",
        "--experiment-ledger", str(ledger),
        "--attempt-id", "python-v02-a1",
        "--run-key", "python-v0.2",
    ]
    args.remove("--standalone")

    assert live_v02.main(args) == 0
    state = json.loads(ledger.read_text())

    assert state["attempts"][0]["status"] == "completed"
    assert state["attempts"][0]["valid_contract"] is True
    assert state["attempts"][0]["rate_estimated_cost_usd"] == "0.0001"
    assert state["configuration"]["model"] == "offline-model"
    assert set(state["configuration"]["prompt_sha256"]) == {"v0.1", "v0.2"}
    assert state["configuration"]["configured_limits"]["max_total_tokens"] == 24_000
    assert "budget.py" in state["configuration"]["code_files_sha256"]
    artifact = json.loads((tmp_path / "run-v0.2/result.json").read_text())
    assert artifact["experiment"]["configuration_sha256"] == state["configuration_sha256"]
    assert state["run_key_task_identities"]["python-v0.2"] == artifact["task_sha256"]


def test_formal_run_rejects_user_selected_key_for_same_case(tmp_path, monkeypatch) -> None:
    task = _task()
    monkeypatch.setattr(live_v02.python, "load_task", lambda *args, **kwargs: task)
    response = _message(contract_data())
    response.usage = Usage(requests=1, input_tokens=10, output_tokens=20, total_tokens=30)
    model = ScriptedModel(response)
    _install_model(monkeypatch, task, model)
    ledger = tmp_path / "experiment.json"
    first = _args(tmp_path) + [
        "--max-cost-usd", "1",
        "--input-cost-per-million", "2",
        "--output-cost-per-million", "4",
        "--experiment-ledger", str(ledger),
        "--attempt-id", "python-v02-a1",
        "--run-key", "python-v0.2",
    ]
    first.remove("--standalone")
    assert live_v02.main(first) == 0

    repeated = list(first)
    repeated[repeated.index("python-v02-a1")] = "python-v02-a2"
    repeated[repeated.index("python-v0.2")] = "quality-retry-disguised-as-new-key"
    repeated[repeated.index(str(tmp_path / "run-v0.2"))] = str(tmp_path / "run-repeat")

    assert live_v02.main(repeated) == 2
    assert not (tmp_path / "run-repeat").exists()
    assert len(json.loads(ledger.read_text())["attempts"]) == 1


def test_runner_requires_explicit_standalone_or_formal_ledger(tmp_path, monkeypatch) -> None:
    task = _task()
    monkeypatch.setattr(live_v02.python, "load_task", lambda *args, **kwargs: task)
    monkeypatch.setenv("OPENAI_API_KEY", "offline-key")
    args = _args(tmp_path)
    args.remove("--standalone")

    assert live_v02.main(args) == 2
    assert not (tmp_path / "run-v0.2").exists()


def test_artifact_is_prepared_before_ledger_completion(tmp_path, monkeypatch) -> None:
    task = _task()
    monkeypatch.setattr(live_v02.python, "load_task", lambda *args, **kwargs: task)
    model = ScriptedModel(_message(contract_data()))
    _install_model(monkeypatch, task, model)
    ledger = tmp_path / "experiment.json"
    args = _args(tmp_path) + [
        "--max-cost-usd", "1",
        "--input-cost-per-million", "2",
        "--output-cost-per-million", "4",
        "--experiment-ledger", str(ledger),
        "--attempt-id", "python-v02-a1",
        "--run-key", "python-v0.2",
    ]
    args.remove("--standalone")

    def fail_completion(*args, **kwargs):
        raise InferenceError("ledger unavailable")

    monkeypatch.setattr(live_v02, "complete_attempt", fail_completion)

    assert live_v02.main(args) == 2
    artifact = json.loads((tmp_path / "run-v0.2/result.json").read_text())
    state = json.loads(ledger.read_text())
    assert artifact["status"] == "ledger_error"
    assert artifact["contract"] is None
    assert state["attempts"][0]["status"] == "reserved"
    assert not (tmp_path / "run-v0.2/result.pending.json").exists()
