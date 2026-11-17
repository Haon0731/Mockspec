from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

import pytest


MODULE_PATH = Path(__file__).with_name("run_generation.py")
SPEC = importlib.util.spec_from_file_location("slice_ab_run_generation", MODULE_PATH)
assert SPEC and SPEC.loader
run_generation_module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(run_generation_module)


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def make_inputs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    root = tmp_path / "repo"
    root.mkdir()
    monkeypatch.setattr(run_generation_module, "ROOT", root)
    slice_path = root / "accepted.slice.json"
    slice_path.write_text('{"slice":true}', encoding="utf-8")
    source = b"class Example {}\n"
    slice_hash = digest(slice_path.read_bytes())
    source_hash = digest(source)
    pair = tmp_path / "pair"
    for arm in ("A", "B"):
        workspace = pair / arm / "workspace"
        target = workspace / "repo/src/Example.java"
        target.parent.mkdir(parents=True)
        target.write_bytes(source)
        if arm == "B":
            (workspace / "slice.json").write_bytes(slice_path.read_bytes())
        (pair / arm / "task.txt").write_text("write candidate.java", encoding="utf-8")
    manifest = {
        "case_id": "case-1",
        "snapshot_id": "snapshot-1",
        "slice_sha256": slice_hash,
        "source_hashes": {"src/Example.java": source_hash},
        "target_path": "src/Example.java",
        "order": ["B", "A"],
        "attempts_per_arm": 1,
        "timeout_seconds": 600,
        "model": "deepseek-v4-pro",
        "mutation_feedback": False,
        "candidate_path": "candidate.java",
    }
    gate = {
        "approved": True,
        "cases": [{
            "case_id": "case-1",
            "approved": True,
            "snapshot_id": "snapshot-1",
            "slice_path": "accepted.slice.json",
            "slice_sha256": slice_hash,
            "sources": [{"logical_path": "src/Example.java", "sha256": source_hash}],
        }],
    }
    gate_path = tmp_path / "gate.json"
    write_json(gate_path, gate)
    manifest["gate_sha256"] = digest(gate_path.read_bytes())
    write_json(pair / "manifest.json", manifest)
    dotenv = tmp_path / ".env"
    dotenv.write_text("DEEPSEEK_API_KEY='secret-value'\n", encoding="utf-8")
    return {"gate": gate_path, "pair": pair, "dotenv": dotenv, "root": root}


@pytest.mark.parametrize("approval", ["top", "case"])
def test_unapproved_gate_never_calls_runner(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, approval: str) -> None:
    paths = make_inputs(tmp_path, monkeypatch)
    gate = json.loads(paths["gate"].read_text())
    if approval == "top":
        gate["approved"] = False
    else:
        gate["cases"][0]["approved"] = False
    write_json(paths["gate"], gate)
    manifest = json.loads((paths["pair"] / "manifest.json").read_text())
    manifest["gate_sha256"] = digest(paths["gate"].read_bytes())
    write_json(paths["pair"] / "manifest.json", manifest)
    called = False

    def runner(**_kwargs) -> None:
        nonlocal called
        called = True

    with pytest.raises(run_generation_module.GenerationError, match="approved"):
        run_generation_module.run_generation(
            paths["gate"], paths["pair"], "A", tmp_path / "output",
            dotenv_path=paths["dotenv"], agent_command="claude", agent_runner=runner,
        )
    assert called is False
    assert not (tmp_path / "output").exists()


def test_changed_workspace_never_calls_runner(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    paths = make_inputs(tmp_path, monkeypatch)
    (paths["pair"] / "B/workspace/repo/src/Example.java").write_text("changed", encoding="utf-8")
    called = False

    def runner(**_kwargs) -> None:
        nonlocal called
        called = True

    with pytest.raises(run_generation_module.GenerationError, match="prepared source changed"):
        run_generation_module.run_generation(
            paths["gate"], paths["pair"], "B", tmp_path / "output",
            dotenv_path=paths["dotenv"], agent_command="claude", agent_runner=runner,
        )
    assert called is False


def test_admitted_arm_calls_stub_runner_with_fixed_configuration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = make_inputs(tmp_path, monkeypatch)
    captured: dict = {}

    def runner(**kwargs) -> None:
        captured.update(kwargs)
        kwargs["trajectory_path"].write_text('{"event":"done"}\n', encoding="utf-8")
        (kwargs["workspace_root"] / "candidate.java").write_text(
            "class Example {}\n", encoding="utf-8"
        )

    result = run_generation_module.run_generation(
        paths["gate"], paths["pair"], "B", tmp_path / "output",
        dotenv_path=paths["dotenv"], agent_command="claude-test", agent_runner=runner,
    )
    assert captured["agent_command"] == "claude-test"
    assert captured["agent_model"] == "openai/deepseek-v4-pro"
    assert captured["agent_timeout_sec"] == 600
    assert captured["agent_frame"] == "claudecode"
    assert captured["writable_files"] == ("candidate.java",)
    assert captured["base_environment"]["PYTHONDONTWRITEBYTECODE"] == "1"
    assert captured["env_overrides"]["PYTHONDONTWRITEBYTECODE"] == "1"
    assert captured["env_overrides"]["ANTHROPIC_AUTH_TOKEN"] == "secret-value"
    assert result["candidate_sha256"] == digest(b"class Example {}\n")
    assert "secret-value" not in (tmp_path / "output/trajectory.jsonl").read_text()


def test_source_change_during_runner_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = make_inputs(tmp_path, monkeypatch)

    def runner(**kwargs) -> None:
        (kwargs["workspace_root"] / "repo/src/Example.java").write_text(
            "changed", encoding="utf-8"
        )
        (kwargs["workspace_root"] / "candidate.java").write_text(
            "class Example {}\n", encoding="utf-8"
        )

    with pytest.raises(run_generation_module.GenerationError, match="prepared source changed"):
        run_generation_module.run_generation(
            paths["gate"], paths["pair"], "A", tmp_path / "output",
            dotenv_path=paths["dotenv"], agent_command="claude", agent_runner=runner,
        )


def test_existing_output_or_candidate_is_never_overwritten(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = make_inputs(tmp_path, monkeypatch)
    output = tmp_path / "output"
    output.mkdir()
    called = False

    def runner(**_kwargs) -> None:
        nonlocal called
        called = True

    with pytest.raises(run_generation_module.GenerationError, match="overwrite"):
        run_generation_module.run_generation(
            paths["gate"], paths["pair"], "A", output,
            dotenv_path=paths["dotenv"], agent_command="claude", agent_runner=runner,
        )
    assert called is False

    output.rmdir()
    (paths["pair"] / "A/workspace/candidate.java").write_text("existing", encoding="utf-8")
    with pytest.raises(run_generation_module.GenerationError, match="overwrite"):
        run_generation_module.run_generation(
            paths["gate"], paths["pair"], "A", output,
            dotenv_path=paths["dotenv"], agent_command="claude", agent_runner=runner,
        )
    assert called is False
