"""Offline boundary tests for the staged live-inference entry."""

from __future__ import annotations

import asyncio
from decimal import Decimal
import os
from pathlib import Path
import subprocess
import sys

from agents.models.openai_responses import OpenAIResponsesModel
from openai import AsyncOpenAI
import pytest


STAGED_ROOT = Path(__file__).parents[1] / "experiments" / "staged-inference"
sys.path.insert(0, str(STAGED_ROOT))

from controller import StagedRunConfig, execute_staged_run  # noqa: E402
from scripted import FixedScriptModel, scripted_outputs, synthetic_task  # noqa: E402


def test_live_configuration_requires_priced_cost_budget() -> None:
    with pytest.raises(ValueError, match="cost limit and input/output prices"):
        StagedRunConfig(run_mode="live_inference", model_name="unused-model")


def test_live_cli_missing_configuration_never_creates_run(tmp_path) -> None:
    output_dir = tmp_path / "live"
    env = os.environ.copy()
    env.pop("OPENAI_API_KEY", None)
    env.pop("MOCKSPEC_MODEL", None)

    completed = subprocess.run(
        [
            sys.executable,
            str(STAGED_ROOT / "run.py"),
            "--mode", "live_inference",
            "--variant", "C",
            "--snapshot", str(tmp_path / "missing-snapshot"),
            "--case-id", "flask_308",
            "--output-dir", str(output_dir),
        ],
        cwd=Path(__file__).parents[1],
        env={**env, "PYTHONPATH": "src"},
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 2
    assert "live inference requires model and OPENAI_API_KEY" in completed.stderr
    assert not output_dir.exists()


def test_live_mode_rejects_direct_synthetic_payload_before_model_call(tmp_path) -> None:
    model = OpenAIResponsesModel(
        model="unused-model",
        openai_client=AsyncOpenAI(api_key="unused-test-key"),
    )

    result = asyncio.run(execute_staged_run(
        synthetic_task(),
        output_dir=tmp_path / "live",
        model=model,
        config=StagedRunConfig(
            run_mode="live_inference", model_name="unused-model", variant="C",
            max_cost_usd=Decimal("1"),
            input_cost_per_million=Decimal("1"),
            output_cost_per_million=Decimal("1"),
        ),
        requested_case_id="synthetic_python_delegation",
    ))

    manifest = result["manifest"]
    assert manifest["result_status"] == "ERROR"
    assert manifest["error_stage"] == "CONFIG"
    assert manifest["usage"] is None
    assert not (tmp_path / "live" / "input_snapshot.json").exists()


def test_failed_live_input_review_is_not_marked_reviewed(tmp_path) -> None:
    model = OpenAIResponsesModel(
        model="unused-model",
        openai_client=AsyncOpenAI(api_key="unused-test-key"),
    )

    result = asyncio.run(execute_staged_run(
        output_dir=tmp_path / "failed-review",
        model=model,
        config=StagedRunConfig(
            run_mode="live_inference", model_name="unused-model", variant="C",
            max_cost_usd=Decimal("1"),
            input_cost_per_million=Decimal("1"),
            output_cost_per_million=Decimal("1"),
        ),
        requested_case_id="flask_308",
        reviewed_snapshot=tmp_path / "missing-snapshot",
    ))

    manifest = result["manifest"]
    assert manifest["result_status"] == "ERROR"
    assert manifest["error_stage"] == "INPUT"
    assert manifest["adapter_reviewed"] is False
    assert manifest["input_origin"] == "unvalidated_review_request"
    assert manifest["usage"]["requests_started"] == 0


def test_priced_budget_is_serialized_and_blocks_before_model_request(tmp_path) -> None:
    model = FixedScriptModel(scripted_outputs("selected"))

    result = asyncio.run(execute_staged_run(
        synthetic_task(),
        output_dir=tmp_path / "priced",
        model=model,
        config=StagedRunConfig(
            max_cost_usd=Decimal("0.000000001"),
            input_cost_per_million=Decimal("100"),
            output_cost_per_million=Decimal("100"),
        ),
    ))

    manifest = result["manifest"]
    assert manifest["result_status"] == "INCONCLUSIVE"
    assert manifest["stop_reason"] == "budget_exhausted"
    assert manifest["configuration"]["max_cost_usd"] == "1E-9"
    assert manifest["budget"]["input_cost_per_million"] == "100"
    assert manifest["usage"]["requests_started"] == 0
    assert manifest["usage"]["gate_events"][0]["reason"] == "projected_cost_limit"
    assert model.calls == 0
