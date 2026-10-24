"""Entry-boundary process cleanup checks for staged QUERY analysis."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import sys

import pytest

import mock_spec.analysis_process as analysis_process


STAGED_ROOT = Path(__file__).parents[1] / "experiments" / "staged-inference"
sys.path.insert(0, str(STAGED_ROOT))

from controller import StagedRunConfig, execute_staged_run  # noqa: E402
from scripted import FixedScriptModel, query_script, synthetic_task  # noqa: E402


def _program_query_outputs() -> list[dict]:
    outputs = query_script(1)
    attempt = outputs[1]["query_attempt"]
    attempt["tool"] = "inspect_program_structure"
    attempt["arguments_json"] = json.dumps({"source_id": "mut:0"})
    return outputs


def test_entry_parser_timeout_reaps_child_and_records_recoverable_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}
    original_spawn = analysis_process.asyncio.create_subprocess_exec

    async def spawn(*args, **kwargs):
        captured["command"] = args
        captured["environment"] = kwargs["env"]
        process = await original_spawn(
            sys.executable, "-I", "-c", "import time; time.sleep(60)", **kwargs
        )
        captured["process"] = process
        return process

    monkeypatch.setattr(analysis_process.asyncio, "create_subprocess_exec", spawn)
    output_dir = tmp_path / "parser-timeout"

    result = asyncio.run(execute_staged_run(
        synthetic_task(),
        output_dir=output_dir,
        model=FixedScriptModel(_program_query_outputs()),
        config=StagedRunConfig(parsing_timeout_seconds=0.1),
    ))

    receipt = result["record"]["query_log"][0]
    process = captured["process"]
    assert process.returncode is not None
    with pytest.raises(ProcessLookupError):
        os.kill(process.pid, 0)
    assert captured["environment"] == {"LANG": "C.UTF-8", "LC_ALL": "C.UTF-8"}
    assert "-I" in captured["command"]
    assert receipt["status"] == "error"
    assert receipt["result"]["error_code"] == "timeout"
    assert receipt["assessment_status"] == "complete"
    assert result["manifest"]["result_status"] == "SELECTED"
    assert result["manifest"]["stop_reason"] == "completed"
    assert (output_dir / "inference_record.json").is_file()
    assert (output_dir / "run_manifest.json").is_file()


def test_entry_cancellation_reaps_child_and_publishes_incomplete_assessment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    spawned = asyncio.Event()
    captured: dict[str, object] = {}
    original_spawn = analysis_process.asyncio.create_subprocess_exec

    async def spawn(*args, **kwargs):
        process = await original_spawn(
            sys.executable, "-I", "-c", "import time; time.sleep(60)", **kwargs
        )
        captured["process"] = process
        spawned.set()
        return process

    monkeypatch.setattr(analysis_process.asyncio, "create_subprocess_exec", spawn)
    output_dir = tmp_path / "cancelled-query"

    async def cancel_active_analysis() -> dict:
        task = asyncio.create_task(execute_staged_run(
            synthetic_task(),
            output_dir=output_dir,
            model=FixedScriptModel(_program_query_outputs()),
            config=StagedRunConfig(parsing_timeout_seconds=30),
        ))
        await asyncio.wait_for(spawned.wait(), timeout=2)
        task.cancel()
        return await task

    result = asyncio.run(cancel_active_analysis())

    receipt = result["record"]["query_log"][0]
    process = captured["process"]
    assert process.returncode is not None
    with pytest.raises(ProcessLookupError):
        os.kill(process.pid, 0)
    assert receipt["status"] == "cancelled"
    assert receipt["result"]["error_code"] == "cancelled"
    assert receipt["assessment"] is None
    assert receipt["assessment_status"] == "incomplete"
    assert result["record"]["result_status"] == "INCONCLUSIVE"
    assert result["manifest"]["stop_reason"] == "budget_exhausted"
    assert result["manifest"]["error_stage"] == "QUERY"
    assert result["manifest"]["usage"]["requests_started"] == 2
    assert result["manifest"]["usage"]["requests_completed"] == 2
    assert result["manifest"]["artifact_sha256"]["inference_record.json"]
    assert (output_dir / "inference_record.json").is_file()
    assert (output_dir / "run_manifest.json").is_file()
    assert not (output_dir / "scenario_contract.json").exists()
