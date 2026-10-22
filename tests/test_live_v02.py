"""Audited V0.1/V0.2 artifact runner tests; no provider calls."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from agents.usage import Usage
from test_inference_v02 import ScriptedModel, _install_model, _message, _task
from test_scenario import contract_data

from mock_spec import inference, inference_v02, live_v02, run_evidence
from mock_spec.inference import InferenceError
from mock_spec.scenario import validate_contract


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
    assert "run_evidence.py" in artifact["code_files_sha256"]
    assert "program_tools.py" in artifact["code_files_sha256"]
    assert "inference.py" in artifact["code_files_sha256"]
    assert "scenario.py" in artifact["code_files_sha256"]
    assert "schemas.py" in artifact["code_files_sha256"]
    assert "validation.py" in artifact["code_files_sha256"]
    assert "adapters/_frozen.py" in artifact["code_files_sha256"]
    assert "reviewed_inputs.json" in artifact["code_files_sha256"]
    assert "analysis_process.py" in artifact["code_files_sha256"]
    assert "python_analysis_runner.py" in artifact["code_files_sha256"]
    assert artifact["runtime_identity"]["python"]["implementation"]
    assert artifact["runtime_identity"]["python"]["executable_sha256"]
    assert artifact["runtime_identity"]["spoon_toolchain"]["spoon_version"] == "11.2.1"
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
    artifact = json.loads((tmp_path / "run-v0.2/result.json").read_text())

    assert state["attempts"][0]["status"] == "completed"
    assert state["attempts"][0]["valid_contract"] is True
    assert state["attempts"][0]["rate_estimated_cost_usd"] == "0.0001"
    assert state["configuration"]["model"] == "offline-model"
    assert set(state["configuration"]["prompt_sha256"]) == {"v0.1", "v0.2"}
    assert state["configuration"]["configured_limits"]["max_total_tokens"] == 24_000
    assert "budget.py" in state["configuration"]["code_files_sha256"]
    assert state["configuration"]["anchored_git_commit"] == artifact["git_commit"]
    assert state["configuration"]["runtime_identity"] == artifact["runtime_identity"]
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


@pytest.mark.parametrize("error_type", [InferenceError, OSError])
def test_artifact_is_prepared_before_ledger_completion(
    tmp_path, monkeypatch, error_type
) -> None:
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
        pending = tmp_path / "run-v0.2/result.pending.json"
        assert json.loads(pending.read_text())["contract"] == contract_data()
        raise error_type("ledger unavailable")

    monkeypatch.setattr(run_evidence, "complete_attempt", fail_completion)

    if error_type is OSError:
        with pytest.raises(OSError, match="ledger unavailable"):
            live_v02.main(args)
        assert (tmp_path / "run-v0.2/result.pending.json").exists()
        assert not (tmp_path / "run-v0.2/result.json").exists()
        state = json.loads(ledger.read_text())
        assert state["attempts"][0]["status"] == "reserved"
        return

    assert live_v02.main(args) == 2
    artifact = json.loads((tmp_path / "run-v0.2/result.json").read_text())
    state = json.loads(ledger.read_text())
    assert artifact["status"] == "ledger_error"
    assert artifact["contract"] is None
    assert state["attempts"][0]["status"] == "reserved"
    assert not (tmp_path / "run-v0.2/result.pending.json").exists()


def test_git_identity_is_anchored_when_cwd_is_an_unrelated_repo(
    tmp_path, monkeypatch
) -> None:
    expected_identity = live_v02._git_identity()
    expected_dirty = live_v02._git_dirty()
    unrelated = tmp_path / "unrelated"
    unrelated.mkdir()
    subprocess.run(
        ["git", "init", "-q", str(unrelated)], check=True, capture_output=True
    )
    monkeypatch.chdir(unrelated)

    assert live_v02._git_identity() == expected_identity
    assert live_v02._git_dirty() == expected_dirty


def test_installed_source_outside_project_checkout_has_no_git_identity(
    tmp_path, monkeypatch
) -> None:
    installed = tmp_path / "site-packages/mock_spec/live_v02.py"
    installed.parent.mkdir(parents=True)
    installed.write_text("# installed wheel\n")
    monkeypatch.setattr(live_v02, "__file__", str(installed))

    assert live_v02._project_git_root() is None
    assert live_v02._git_identity() is None
    assert live_v02._git_dirty() is None


def test_runtime_identity_marks_unavailable_spoon_toolchain(monkeypatch) -> None:
    from mock_spec import java_analysis

    monkeypatch.setattr(java_analysis, "_backend_paths", lambda: None)

    identity = live_v02._runtime_identity()

    assert identity["python"]["implementation"]
    assert identity["python"]["version_detail"]
    assert identity["spoon_toolchain"]["available"] is False
    assert identity["spoon_toolchain"]["java"] is None
    assert identity["spoon_toolchain"]["limitations"]


def _formal_args(tmp_path):
    args = _args(tmp_path) + [
        "--max-cost-usd", "1",
        "--input-cost-per-million", "2",
        "--output-cost-per-million", "4",
        "--experiment-ledger", str(tmp_path / "experiment.json"),
        "--attempt-id", "python-v02-a1",
        "--run-key", "python-v0.2",
    ]
    args.remove("--standalone")
    return args


@pytest.mark.parametrize("stage", ["pending_write", "publish"])
def test_evidence_io_failure_preserves_ledger_and_available_artifact(
    tmp_path, monkeypatch, capsys, stage
) -> None:
    task = _task()
    monkeypatch.setattr(live_v02.python, "load_task", lambda *args, **kwargs: task)
    model = ScriptedModel(_message(contract_data()))
    _install_model(monkeypatch, task, model)
    output_dir = tmp_path / "run-v0.2"
    pending = output_dir / "result.pending.json"
    original_open = Path.open
    original_rename = Path.rename

    def open_file(path, *args, **kwargs):
        if path == pending and args and args[0] == "x":
            raise OSError("injected pending write failure")
        return original_open(path, *args, **kwargs)

    def rename_file(path, target):
        if path == pending:
            raise OSError("injected publication failure")
        return original_rename(path, target)

    if stage == "pending_write":
        monkeypatch.setattr(Path, "open", open_file)
    else:
        monkeypatch.setattr(Path, "rename", rename_file)

    assert live_v02.main(_formal_args(tmp_path)) == 2
    state = json.loads((tmp_path / "experiment.json").read_text())
    assert not (output_dir / "result.json").exists()
    assert model.calls == 1
    if stage == "pending_write":
        assert state["attempts"][0]["status"] == "reserved"
        assert not pending.exists()
        assert "could not persist pending run evidence" in capsys.readouterr().err
    else:
        assert state["attempts"][0]["status"] == "completed"
        artifact = json.loads(pending.read_text())
        assert artifact["status"] == "completed"
        assert artifact["contract"] == contract_data()
        assert artifact["usage"]["total_tokens"] == 30
        assert "could not finalize run evidence" in capsys.readouterr().err


@pytest.mark.parametrize("interrupted", [False, True])
def test_failed_run_finishes_evidence_and_closes_unknown_accounting(
    tmp_path, monkeypatch, interrupted
) -> None:
    from mock_spec.experiment_ledger import reserve_attempt

    task = _task()
    monkeypatch.setattr(live_v02.python, "load_task", lambda *args, **kwargs: task)
    monkeypatch.setenv("OPENAI_API_KEY", "offline-key")

    async def failure(task, **kwargs):
        kwargs["tool_trace"].append({"status": "error", "sequence": 1})
        # A dispatched request without a response has unknown provider charges.
        kwargs["usage_ledger"].requests_started += 1
        if interrupted:
            raise KeyboardInterrupt
        raise InferenceError("scenario inference timed out", "model_call_failure")

    monkeypatch.setattr(inference_v02, "infer_scenario", failure)
    assert live_v02.main(_formal_args(tmp_path)) == (130 if interrupted else 2)
    ledger = tmp_path / "experiment.json"
    state = json.loads(ledger.read_text())
    artifact = json.loads((tmp_path / "run-v0.2/result.json").read_text())
    assert artifact["status"] == ("interrupted" if interrupted else "model_call_failure")
    assert artifact["contract"] is None
    assert artifact["tool_trace"] == [{"status": "error", "sequence": 1}]
    assert artifact["usage"]["usage_complete"] is False
    assert artifact["usage"]["rate_estimated_cost_usd"] is None
    assert not (tmp_path / "run-v0.2/result.pending.json").exists()
    assert state["attempts"][0]["status"] == "completed"
    assert state["attempts"][0]["outcome_status"] == (
        "interrupted" if interrupted else "technical_timeout"
    )
    assert state["accounting_complete"] is False
    with pytest.raises(InferenceError, match="unknown prior charges"):
        reserve_attempt(
            ledger, attempt_id="next-attempt", run_key="java-v0.2",
            max_cost_usd="1", configuration=state["configuration"],
            task_identity="another-task",
        )
