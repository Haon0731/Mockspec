"""Persistent Step 6 experiment budget and retry policy."""

from __future__ import annotations

import json

import pytest

from mock_spec.experiment_ledger import (
    complete_attempt,
    reserve_attempt as _reserve_attempt,
)
from mock_spec.inference import InferenceError


CONFIGURATION = {
    "model": "offline-model",
    "timeout_seconds": 120,
    "prompt_sha256": {"v0.1": "one", "v0.2": "two"},
    "code_files_sha256": {"inference_v02.py": "code"},
}


def reserve_attempt(path, **kwargs):
    kwargs.setdefault("configuration", CONFIGURATION)
    kwargs.setdefault("task_identity", f"task-for-{kwargs['run_key']}")
    return _reserve_attempt(path, **kwargs)


def _complete(
    path, attempt_id, status="completed", *, diagnostic=None, valid=True, cost="0.1"
):
    return complete_attempt(
        path,
        attempt_id=attempt_id,
        outcome_status=status,
        valid_contract=valid,
        usage_complete=True,
        rate_estimated_cost_usd=cost,
        diagnostic_type=diagnostic,
    )


def test_unfinished_attempt_blocks_a_second_reservation(tmp_path) -> None:
    ledger = tmp_path / "ledger.json"
    reserve_attempt(
        ledger, attempt_id="a1", run_key="java-v01", max_cost_usd="1"
    )

    with pytest.raises(InferenceError, match="unfinished"):
        reserve_attempt(
            ledger, attempt_id="a2", run_key="python-v01", max_cost_usd="1"
        )


def test_unknown_charge_closes_shared_ledger(tmp_path) -> None:
    ledger = tmp_path / "ledger.json"
    reserve_attempt(
        ledger, attempt_id="a1", run_key="java-v01", max_cost_usd="1"
    )
    complete_attempt(
        ledger,
        attempt_id="a1",
        outcome_status="model_call_failure",
        valid_contract=False,
        usage_complete=True,
        rate_estimated_cost_usd=None,
        diagnostic_type="APIConnectionError",
    )

    with pytest.raises(InferenceError, match="unknown prior charges"):
        reserve_attempt(
            ledger, attempt_id="a2", run_key="python-v01", max_cost_usd="1"
        )
    assert json.loads(ledger.read_text())["accounting_complete"] is False


def test_retry_requires_allowlisted_technical_failure(tmp_path) -> None:
    ledger = tmp_path / "ledger.json"
    reserve_attempt(ledger, attempt_id="a1", run_key="java-v01", max_cost_usd="1")
    _complete(
        ledger, "a1", "model_call_failure", diagnostic="ValueError", valid=False
    )

    with pytest.raises(InferenceError, match="technical failure"):
        reserve_attempt(
            ledger,
            attempt_id="retry1",
            run_key="java-v01",
            max_cost_usd="1",
            retry_of="a1",
        )


def test_retry_must_follow_latest_attempt_and_is_capped(tmp_path) -> None:
    ledger = tmp_path / "ledger.json"
    reserve_attempt(ledger, attempt_id="a1", run_key="java-v01", max_cost_usd="1")
    _complete(
        ledger, "a1", "model_call_failure",
        diagnostic="APIConnectionError", valid=False,
    )
    reserve_attempt(
        ledger,
        attempt_id="r1",
        run_key="java-v01",
        max_cost_usd="1",
        retry_of="a1",
    )
    _complete(
        ledger, "r1", "technical_timeout", diagnostic="APITimeoutError", valid=False
    )

    with pytest.raises(InferenceError, match="latest"):
        reserve_attempt(
            ledger,
            attempt_id="stale",
            run_key="java-v01",
            max_cost_usd="1",
            retry_of="a1",
        )
    reserve_attempt(
        ledger,
        attempt_id="r2",
        run_key="java-v01",
        max_cost_usd="1",
        retry_of="r1",
    )
    _complete(
        ledger, "r2", "technical_timeout", diagnostic="APITimeoutError", valid=False
    )
    with pytest.raises(InferenceError, match="retry budget"):
        reserve_attempt(
            ledger,
            attempt_id="r3",
            run_key="java-v01",
            max_cost_usd="1",
            retry_of="r2",
        )


def test_four_run_keys_and_five_dollar_target_are_enforced(tmp_path) -> None:
    ledger = tmp_path / "ledger.json"
    for index in range(4):
        attempt = f"a{index}"
        reserve_attempt(
            ledger, attempt_id=attempt, run_key=f"run-{index}", max_cost_usd="1"
        )
        _complete(ledger, attempt, cost="1")

    with pytest.raises(InferenceError, match="valid-run plan"):
        reserve_attempt(
            ledger, attempt_id="a4", run_key="run-4", max_cost_usd="1"
        )


def test_five_dollar_cost_target_is_reserved_before_attempt(tmp_path) -> None:
    ledger = tmp_path / "ledger.json"
    reserve_attempt(ledger, attempt_id="a1", run_key="run-1", max_cost_usd="4.9")
    _complete(ledger, "a1", cost="4.9")

    with pytest.raises(InferenceError, match="cost target"):
        reserve_attempt(
            ledger, attempt_id="a2", run_key="run-2", max_cost_usd="0.2"
        )


def test_duplicate_run_key_cannot_be_retried_for_quality(tmp_path) -> None:
    ledger = tmp_path / "ledger.json"
    reserve_attempt(ledger, attempt_id="a1", run_key="java-v02", max_cost_usd="1")
    _complete(ledger, "a1", status="completed", valid=True)

    with pytest.raises(InferenceError, match="quality or duplicate"):
        reserve_attempt(
            ledger, attempt_id="a2", run_key="java-v02", max_cost_usd="1"
        )


def test_configuration_is_frozen_by_first_attempt(tmp_path) -> None:
    ledger = tmp_path / "ledger.json"
    reserve_attempt(ledger, attempt_id="a1", run_key="java-v01", max_cost_usd="1")
    _complete(ledger, "a1")

    with pytest.raises(InferenceError, match="frozen plan"):
        _reserve_attempt(
            ledger,
            attempt_id="a2",
            run_key="python-v01",
            max_cost_usd="1",
            configuration={**CONFIGURATION, "model": "changed-model"},
            task_identity="task-for-python-v01",
        )


def test_run_key_is_bound_to_task_identity(tmp_path) -> None:
    ledger = tmp_path / "ledger.json"
    reserve_attempt(ledger, attempt_id="a1", run_key="java-v01", max_cost_usd="1")
    _complete(
        ledger,
        "a1",
        status="model_call_failure",
        diagnostic="APIConnectionError",
        valid=False,
    )

    with pytest.raises(InferenceError, match="task identity"):
        _reserve_attempt(
            ledger,
            attempt_id="retry",
            run_key="java-v01",
            max_cost_usd="1",
            retry_of="a1",
            configuration=CONFIGURATION,
            task_identity="different-task",
        )
