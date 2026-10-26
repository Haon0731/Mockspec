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

from controller import (  # noqa: E402
    CLOUDFOUNDRY_APPLICATION_URI_CASE_ID,
    StagedRunConfig,
    execute_staged_run,
)
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


def test_live_cli_rejects_unknown_case_before_credentials(tmp_path) -> None:
    output_dir = tmp_path / "unknown-live"
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
            "--case-id", "unreviewed-case",
            "--output-dir", str(output_dir),
        ],
        cwd=Path(__file__).parents[1],
        env={**env, "PYTHONPATH": "src"},
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 2
    assert "explicitly reviewed case" in completed.stderr
    assert "OPENAI_API_KEY" not in completed.stderr
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


def test_live_controller_rejects_unknown_case_before_model_call(tmp_path) -> None:
    model = OpenAIResponsesModel(
        model="unused-model",
        openai_client=AsyncOpenAI(api_key="unused-test-key"),
    )

    result = asyncio.run(execute_staged_run(
        output_dir=tmp_path / "unknown-case",
        model=model,
        config=StagedRunConfig(
            run_mode="live_inference", model_name="unused-model", variant="C",
            max_cost_usd=Decimal("1"),
            input_cost_per_million=Decimal("1"),
            output_cost_per_million=Decimal("1"),
        ),
        requested_case_id="unreviewed-case",
        reviewed_snapshot=tmp_path / "unused-snapshot",
    ))

    manifest = result["manifest"]
    assert manifest["result_status"] == "ERROR"
    assert manifest["error_stage"] == "INPUT"
    assert manifest["adapter_reviewed"] is False
    assert manifest["usage"]["requests_started"] == 0


def test_live_java_case_uses_reviewed_java_adapter_before_model_call(
    tmp_path, monkeypatch,
) -> None:
    from mock_spec.adapters import java
    from mock_spec.schemas import UnifiedTask

    payload = synthetic_task()
    payload["language"] = "java"
    payload["case_id"] = CLOUDFOUNDRY_APPLICATION_URI_CASE_ID
    reviewed_task = UnifiedTask.from_dict(payload)
    seen: list[tuple[Path, str]] = []

    def load_task(snapshot: Path, *, case_id: str) -> UnifiedTask:
        seen.append((snapshot, case_id))
        return reviewed_task

    monkeypatch.setattr(java, "load_task", load_task)
    model = OpenAIResponsesModel(
        model="unused-model",
        openai_client=AsyncOpenAI(api_key="unused-test-key"),
    )
    snapshot = tmp_path / "reviewed-java-snapshot"

    result = asyncio.run(execute_staged_run(
        output_dir=tmp_path / "java-live",
        model=model,
        config=StagedRunConfig(
            run_mode="live_inference", model_name="unused-model", variant="C",
            max_cost_usd=Decimal("0.000000001"),
            input_cost_per_million=Decimal("100"),
            output_cost_per_million=Decimal("100"),
        ),
        requested_case_id=CLOUDFOUNDRY_APPLICATION_URI_CASE_ID,
        reviewed_snapshot=snapshot,
    ))

    manifest = result["manifest"]
    assert seen == [(snapshot, CLOUDFOUNDRY_APPLICATION_URI_CASE_ID)]
    assert manifest["input_origin"] == "reviewed_java_adapter"
    assert manifest["adapter_reviewed"] is True
    assert manifest["result_status"] == "INCONCLUSIVE"
    assert manifest["usage"]["requests_started"] == 0


def test_live_java_case_with_wrong_snapshot_fails_adapter_before_model_call(tmp_path) -> None:
    model = OpenAIResponsesModel(
        model="unused-model",
        openai_client=AsyncOpenAI(api_key="unused-test-key"),
    )

    result = asyncio.run(execute_staged_run(
        output_dir=tmp_path / "wrong-java-snapshot",
        model=model,
        config=StagedRunConfig(
            run_mode="live_inference", model_name="unused-model", variant="C",
            max_cost_usd=Decimal("1"),
            input_cost_per_million=Decimal("1"),
            output_cost_per_million=Decimal("1"),
        ),
        requested_case_id=CLOUDFOUNDRY_APPLICATION_URI_CASE_ID,
        reviewed_snapshot=tmp_path / "missing-snapshot",
    ))

    manifest = result["manifest"]
    assert manifest["result_status"] == "ERROR"
    assert manifest["error_stage"] == "INPUT"
    assert manifest["adapter_reviewed"] is False
    assert manifest["usage"]["requests_started"] == 0


def test_live_java_repository_mode_routes_to_reviewed_repository_adapter(
    tmp_path, monkeypatch,
) -> None:
    from mock_spec.adapters import java

    def reject_repository_load(snapshot: Path, *, case_id: str):
        assert snapshot == tmp_path / "reviewed-java-snapshot"
        assert case_id == CLOUDFOUNDRY_APPLICATION_URI_CASE_ID
        raise ValueError("java repository adapter reached")

    monkeypatch.setattr(java, "load_task_with_repository", reject_repository_load)
    model = OpenAIResponsesModel(
        model="unused-model",
        openai_client=AsyncOpenAI(api_key="unused-test-key"),
    )

    result = asyncio.run(execute_staged_run(
        output_dir=tmp_path / "java-repository-live",
        model=model,
        config=StagedRunConfig(
            run_mode="live_inference", model_name="unused-model", variant="C",
            repository_read=True,
            max_cost_usd=Decimal("1"),
            input_cost_per_million=Decimal("1"),
            output_cost_per_million=Decimal("1"),
        ),
        requested_case_id=CLOUDFOUNDRY_APPLICATION_URI_CASE_ID,
        reviewed_snapshot=tmp_path / "reviewed-java-snapshot",
    ))

    manifest = result["manifest"]
    assert manifest["result_status"] == "ERROR"
    assert manifest["error_stage"] == "INPUT"
    assert manifest["adapter_reviewed"] is False
    assert manifest["usage"]["requests_started"] == 0


def test_live_mode_rejects_injected_loader_before_model_call(tmp_path) -> None:
    model = OpenAIResponsesModel(
        model="unused-model",
        openai_client=AsyncOpenAI(api_key="unused-test-key"),
    )

    result = asyncio.run(execute_staged_run(
        output_dir=tmp_path / "loader-live",
        model=model,
        config=StagedRunConfig(
            run_mode="live_inference", model_name="unused-model", variant="C",
            max_cost_usd=Decimal("1"),
            input_cost_per_million=Decimal("1"),
            output_cost_per_million=Decimal("1"),
        ),
        requested_case_id="flask_308",
        task_loader=lambda: synthetic_task(),
        reviewed_snapshot=tmp_path / "unused-snapshot",
    ))

    manifest = result["manifest"]
    assert manifest["result_status"] == "ERROR"
    assert manifest["error_stage"] == "CONFIG"
    assert manifest["usage"] is None
    assert not (tmp_path / "loader-live" / "input_snapshot.json").exists()


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
