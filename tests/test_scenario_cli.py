import json

from mock_spec.cli import main


def test_missing_model_configuration_is_explicit(capsys, monkeypatch):
    monkeypatch.delenv("MOCKSPEC_MODEL", raising=False)
    assert main(["--language", "python", "--snapshot", "missing", "--case-id", "x"]) == 2
    assert "MOCKSPEC_MODEL" in capsys.readouterr().err


def test_replay_uses_reviewed_adapter_and_marks_output_synthetic(
    tmp_path, capsys, monkeypatch
):
    from mock_spec import cli
    from mock_spec.schemas import UnifiedTask
    from test_scenario import contract_data
    from test_schemas import valid_task_data

    calls = []

    def loader(root, *, case_id):
        calls.append((root, case_id))
        return UnifiedTask.from_dict(valid_task_data())

    monkeypatch.setattr(cli.python, "load_task", loader)
    response = tmp_path / "response.json"
    response.write_text(json.dumps(contract_data()))
    assert main([
        "--language", "python", "--snapshot", str(tmp_path),
        "--case-id", "example_1", "--response-json", str(response),
    ]) == 0
    artifact = json.loads(capsys.readouterr().out)
    assert calls == [(tmp_path, "example_1")]
    assert artifact["mode"] == "synthetic_replay"
    assert artifact["semantic_correctness_verified"] is False
    assert artifact["model"] is None
    assert artifact["contract"]["case_id"] == "example_1"


def test_invalid_replay_does_not_write_output(tmp_path, capsys, monkeypatch):
    from mock_spec import cli
    from mock_spec.schemas import UnifiedTask
    from test_schemas import valid_task_data

    monkeypatch.setattr(cli.python, "load_task",
                        lambda *a, **kw: UnifiedTask.from_dict(valid_task_data()))
    response = tmp_path / "response.json"
    response.write_text('{"secret": "do not echo"}')
    output = tmp_path / "result.json"
    assert main([
        "--language", "python", "--snapshot", str(tmp_path),
        "--case-id", "example_1", "--response-json", str(response),
        "--output", str(output),
    ]) == 2
    captured = capsys.readouterr()
    assert "invalid contract" in captured.err
    assert "do not echo" not in captured.err
    assert not output.exists()


def test_v02_cli_routes_explicitly_without_changing_default(tmp_path, capsys, monkeypatch):
    from mock_spec import cli, inference, inference_v02
    from mock_spec.schemas import UnifiedTask
    from mock_spec.scenario import validate_contract
    from test_schemas import valid_task_data
    from test_scenario import contract_data

    task = UnifiedTask.from_dict(valid_task_data())
    monkeypatch.setattr(cli.python, "load_task", lambda *a, **kw: task)
    calls = []

    async def baseline(task, **kwargs):
        calls.append(("v0.1", kwargs))
        return validate_contract(contract_data(), task)

    async def augmented(task, **kwargs):
        calls.append(("v0.2", kwargs))
        return validate_contract(contract_data(), task)

    monkeypatch.setattr(inference, "infer_scenario", baseline)
    monkeypatch.setattr(inference_v02, "infer_scenario", augmented)
    args = ["--language", "python", "--snapshot", str(tmp_path),
            "--case-id", "example_1", "--model", "test"]
    assert main(args) == 0
    assert json.loads(capsys.readouterr().out)["prompt_version"] == "scenario-inference/v0.1"
    assert main(args + ["--agent-version", "v0.2", "--max-turns", "3"]) == 0
    assert json.loads(capsys.readouterr().out)["prompt_version"] == "scenario-inference/v0.2"
    assert [c[0] for c in calls] == ["v0.1", "v0.2"]
    assert calls[1][1]["budget"].max_turns == 3
    assert "budget" not in calls[0][1]
