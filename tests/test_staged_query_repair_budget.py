"""Exercise QUERY repair's shared, irreversible allowance and budget gates."""

from __future__ import annotations

import asyncio
from copy import deepcopy
from pathlib import Path
import sys
import time

import pytest
from agents.models.interface import Model

from mock_spec.budget import InferenceBudget, UsageLedger
from mock_spec.schemas import UnifiedTask
from mock_spec.validation import SourceStore


sys.path.insert(0, str(Path(__file__).parents[1] / "experiments" / "staged-inference"))
from controller import (  # noqa: E402
    FormatRepairAllowance,
    StagedInferenceError,
    StagedRunConfig,
    _repair_query_output,
)
from scripted import FixedScriptModel, synthetic_task  # noqa: E402


def _raw_query() -> dict:
    return {
        "schema_version": "staged-query/v0.3",
        "decision": "request_query",
        "query_attempt": {
            "question": "Which exact behavior constrains the candidate?",
            "scenario_id": "candidate",
            "tool": "inspect_source",
            "arguments_json": (
                '{"source_id":"mut:0","start_line":10,"end_line":10}'
            ),
        },
        "previous_receipt_assessment": {
            "impact": "no_new_information",
            "rationale": "The declaration alone does not establish behavior.",
            "attempt_sequence_note": None,
        },
        "impact": "A behavior read remains necessary.",
        "stop_explanation": None,
    }


def _snapshot(raw: dict) -> dict:
    snapshot = deepcopy(raw)
    del snapshot["previous_receipt_assessment"]["attempt_sequence_note"]
    return snapshot


def _pending_receipt() -> dict[str, object]:
    return {
        "sequence": 1,
        "response_id": "query:1",
        "within_budget": True,
        "status": "ok",
        "result": {"status": "ok", "facts": []},
        "assessment": None,
        "assessment_status": "incomplete",
    }


def _config(
    *,
    max_output_tokens: int = 8_192,
    max_total_tokens: int = 200_000,
    parsing_timeout_seconds: float = 120,
) -> StagedRunConfig:
    return StagedRunConfig(
        max_output_tokens=max_output_tokens,
        max_total_tokens=max_total_tokens,
        parsing_timeout_seconds=parsing_timeout_seconds,
    )


def _ledger(config: StagedRunConfig) -> UsageLedger:
    return UsageLedger(InferenceBudget(
        max_turns=config.max_model_calls,
        max_tool_calls=config.max_query_attempts,
        max_output_tokens=config.max_output_tokens,
        max_total_tokens=config.max_total_tokens,
        max_tool_output_chars=config.max_tool_output_chars,
        analysis_timeout_seconds=config.parsing_timeout_seconds,
    ))


async def _repair(
    *, model: Model, config: StagedRunConfig, ledger: UsageLedger,
    allowance: FormatRepairAllowance, model_call_limit: int = -1,
    artifacts: dict[str, list[dict]] | None = None,
) -> tuple[list[dict], list[dict], list[dict], list[dict]]:
    task = UnifiedTask.from_dict(synthetic_task())
    responses: list[dict] = []
    calls: list[dict] = []
    stages: list[dict] = []
    repair_log: list[dict] = []
    if artifacts is not None:
        artifacts.update(
            responses=responses,
            calls=calls,
            stages=stages,
            repair_log=repair_log,
        )
    await _repair_query_output(
        _raw_query(),
        original_call_sequence=1,
        original_attempt_sequence=2,
        pending_receipt=_pending_receipt(),
        run_id="run",
        case_id=task.case_id,
        candidate_ids={"candidate"},
        task=task,
        source_store=SourceStore(task),
        model=model,
        config=config,
        ledger=ledger,
        responses=responses,
        model_calls=calls,
        stages=stages,
        repair_log=repair_log,
        allowance=allowance,
        deadline=time.monotonic() + 10,
        model_call_limit=model_call_limit,
    )
    return responses, calls, stages, repair_log


def test_repository_unlimited_sentinel_allows_one_qualified_repair() -> None:
    raw = _raw_query()
    model = FixedScriptModel([_snapshot(raw)])
    config = _config()
    ledger = _ledger(config)
    allowance = FormatRepairAllowance()

    responses, calls, stages, log = asyncio.run(_repair(
        model=model,
        config=config,
        ledger=ledger,
        allowance=allowance,
        model_call_limit=-1,
    ))

    assert model.calls == ledger.requests_started == ledger.requests_completed == 1
    assert len(responses) == len(calls) == len(stages) == 1
    assert calls[0]["status"] == "completed"
    assert log[0]["outcome"] == "completed"
    assert allowance.state == "reserved"
    assert allowance.category == "QUERY_FORMAT_REPAIR"


def test_pre_send_budget_rejection_consumes_allowance_without_provider_request() -> None:
    config = _config(max_output_tokens=1, max_total_tokens=1)
    ledger = _ledger(config)
    model = FixedScriptModel([_snapshot(_raw_query())])
    allowance = FormatRepairAllowance()
    artifacts: dict[str, list[dict]] = {}

    with pytest.raises(StagedInferenceError) as raised:
        asyncio.run(_repair(
            model=model,
            config=config,
            ledger=ledger,
            allowance=allowance,
            artifacts=artifacts,
        ))

    assert raised.value.stop_reason == "budget_exhausted"
    assert model.calls == ledger.requests_started == 0
    assert allowance.state == "reserved"
    assert allowance.category == "QUERY_FORMAT_REPAIR"
    assert artifacts["calls"][0]["status"] == "failed"
    assert artifacts["calls"][0]["model_request_started"] is False
    assert artifacts["repair_log"][0]["outcome"] == "blocked_budget"
    assert artifacts["repair_log"][0]["repair_call_sequence"] == 1


class _BlockingModel(Model):
    def __init__(self) -> None:
        self.started = asyncio.Event()

    async def get_response(self, *args, **kwargs):
        self.started.set()
        await asyncio.Future()

    async def stream_response(self, *args, **kwargs):
        raise AssertionError("streaming is not used")
        yield


def test_cancelled_repair_keeps_allowance_reserved_and_blocks_a_second_repair() -> None:
    async def exercise() -> tuple[
        FormatRepairAllowance, UsageLedger, FixedScriptModel,
        dict[str, list[dict]], dict[str, list[dict]],
    ]:
        config = _config(parsing_timeout_seconds=5)
        ledger = _ledger(config)
        allowance = FormatRepairAllowance()
        blocking = _BlockingModel()
        first_artifacts: dict[str, list[dict]] = {}
        task = asyncio.create_task(_repair(
            model=blocking,
            config=config,
            ledger=ledger,
            allowance=allowance,
            artifacts=first_artifacts,
        ))
        await asyncio.wait_for(blocking.started.wait(), timeout=1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        second = FixedScriptModel([_snapshot(_raw_query())])
        second_artifacts: dict[str, list[dict]] = {}
        with pytest.raises(StagedInferenceError, match="allowance is exhausted"):
            await _repair(
                model=second,
                config=config,
                ledger=ledger,
                allowance=allowance,
                artifacts=second_artifacts,
            )
        return allowance, ledger, second, first_artifacts, second_artifacts

    allowance, ledger, second, first_artifacts, second_artifacts = asyncio.run(exercise())

    assert allowance.state == "reserved"
    assert allowance.category == "QUERY_FORMAT_REPAIR"
    assert ledger.requests_started == 1
    assert second.calls == 0
    assert first_artifacts["calls"][0]["status"] == "cancelled"
    assert first_artifacts["repair_log"][0]["outcome"] == "cancelled"
    assert second_artifacts["calls"] == []
    assert second_artifacts["repair_log"][0]["outcome"] == "allowance_exhausted"


def test_allowance_is_shared_between_query_and_delivery_categories() -> None:
    allowance = FormatRepairAllowance()

    assert allowance.reserve("QUERY_FORMAT_REPAIR") is True
    assert allowance.reserve("FORMAT_REPAIR") is False
    assert allowance.state == "reserved"
    assert allowance.category == "QUERY_FORMAT_REPAIR"
