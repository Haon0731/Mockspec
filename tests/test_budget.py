"""Global token and cost gates for V0.2 inference."""

from __future__ import annotations

import asyncio
from decimal import Decimal
from types import SimpleNamespace

import pytest
from agents.usage import Usage

import mock_spec.inference_v02 as inference_v02
from mock_spec.budget import (
    InferenceBudget,
    UsageLedger,
    estimate_known_request_tokens,
)
from mock_spec.inference import InferenceError
from mock_spec.scenario import ScenarioContract
from mock_spec.structured_output import PydanticOutputSchema
from test_inference_v02 import (
    ScriptedModel,
    _install_model,
    _message,
    _task,
    _tool_call,
)
from test_scenario import contract_data


def test_budget_remains_available_from_existing_inference_import() -> None:
    assert inference_v02.InferenceBudget is InferenceBudget


def test_request_estimate_includes_native_structured_output_schema() -> None:
    without_schema = estimate_known_request_tokens(
        system_prompt="prompt",
        input_items=[],
        agent=SimpleNamespace(tools=[], output_type=None),
    )
    with_schema = estimate_known_request_tokens(
        system_prompt="prompt",
        input_items=[],
        agent=SimpleNamespace(
            tools=[], output_type=PydanticOutputSchema(ScenarioContract)
        ),
    )

    assert with_schema[0] > without_schema[0]
    assert with_schema[1] > without_schema[1] + 1_000


def test_budget_requires_complete_explicit_prices_for_cost_limit() -> None:
    with pytest.raises(InferenceError, match="requires explicit"):
        inference_v02.InferenceBudget(max_cost_usd=Decimal("1"))
    with pytest.raises(InferenceError, match="configured together"):
        inference_v02.InferenceBudget(input_cost_per_million=Decimal("1"))


@pytest.mark.parametrize("value", [0, -1, 1.5, "100"])
def test_optional_total_token_limit_still_rejects_invalid_values(value) -> None:
    with pytest.raises(InferenceError, match="max_total_tokens must be a positive integer"):
        inference_v02.InferenceBudget(max_total_tokens=value)


def test_no_total_token_limit_keeps_accounting_and_allows_later_calls(monkeypatch) -> None:
    budget = inference_v02.InferenceBudget(
        max_output_tokens=100,
        max_total_tokens=None,
    )
    ledger = UsageLedger(budget)
    agent = SimpleNamespace(tools=[], output_type=None)
    monkeypatch.setattr(
        "mock_spec.budget.estimate_known_request_tokens",
        lambda **_kwargs: (30_000, 120_000),
    )

    ledger.before_model_call(system_prompt=None, input_items=[], agent=agent)
    ledger.after_model_call(SimpleNamespace(
        usage=Usage(
            requests=1, input_tokens=30_000, output_tokens=2_000, total_tokens=32_000
        ),
        raw_usage={"total_tokens": 32_000},
    ))
    ledger.before_model_call(system_prompt=None, input_items=[], agent=agent)

    assert ledger.requests_started == 2
    assert ledger.total_tokens == 32_000
    assert ledger.gate_events[-1]["status"] == "allowed"


def test_no_total_token_limit_still_enforces_explicit_cost_limit(monkeypatch) -> None:
    budget = inference_v02.InferenceBudget(
        max_output_tokens=100,
        max_total_tokens=None,
        max_cost_usd=Decimal("0.01"),
        input_cost_per_million=Decimal("1"),
        output_cost_per_million=Decimal("1"),
    )
    ledger = UsageLedger(budget)
    monkeypatch.setattr(
        "mock_spec.budget.estimate_known_request_tokens",
        lambda **_kwargs: (20_000, 80_000),
    )

    with pytest.raises(InferenceError) as caught:
        ledger.before_model_call(
            system_prompt=None,
            input_items=[],
            agent=SimpleNamespace(tools=[], output_type=None),
        )

    assert caught.value.category == "budget_exhausted"
    assert ledger.gate_events[-1]["reason"] == "projected_cost_limit"


def test_preflight_token_gate_blocks_before_model_call(monkeypatch) -> None:
    task = _task()
    budget = inference_v02.InferenceBudget(
        max_output_tokens=1, max_total_tokens=1
    )
    model = ScriptedModel(_message(contract_data()))
    ledger = UsageLedger(budget)
    _install_model(monkeypatch, task, model, budget=budget)

    with pytest.raises(InferenceError) as caught:
        asyncio.run(inference_v02.infer_scenario(
            task, model="offline-model", budget=budget, usage_ledger=ledger
        ))

    assert caught.value.category == "budget_exhausted"
    assert model.calls == 0
    assert ledger.gate_events[-1]["reason"] == "projected_token_limit"


def test_preflight_cost_gate_blocks_before_model_call(monkeypatch) -> None:
    task = _task()
    budget = inference_v02.InferenceBudget(
        max_cost_usd=Decimal("0.000001"),
        input_cost_per_million=Decimal("1"),
        output_cost_per_million=Decimal("1"),
    )
    model = ScriptedModel(_message(contract_data()))
    ledger = UsageLedger(budget)
    _install_model(monkeypatch, task, model, budget=budget)

    with pytest.raises(InferenceError) as caught:
        asyncio.run(inference_v02.infer_scenario(
            task, model="offline-model", budget=budget, usage_ledger=ledger
        ))

    assert caught.value.category == "budget_exhausted"
    assert model.calls == 0
    assert ledger.gate_events[-1]["reason"] == "projected_cost_limit"


def test_actual_usage_exhaustion_preserves_response_and_stops(monkeypatch) -> None:
    task = _task()
    budget = inference_v02.InferenceBudget(
        max_output_tokens=100, max_total_tokens=20_000
    )
    response = _message(contract_data())
    response.usage = Usage(
        requests=1, input_tokens=19_000, output_tokens=2_000, total_tokens=21_000
    )
    model = ScriptedModel(response)
    ledger = UsageLedger(budget)
    raw = []
    _install_model(monkeypatch, task, model, budget=budget)

    with pytest.raises(InferenceError) as caught:
        asyncio.run(inference_v02.infer_scenario(
            task,
            model="offline-model",
            budget=budget,
            usage_ledger=ledger,
            responses=raw,
        ))

    assert caught.value.category == "budget_exhausted"
    assert model.calls == 1
    assert len(raw) == 1
    assert ledger.gate_events[-1]["reason"] == "actual_token_limit"


def test_cumulative_usage_blocks_next_call_without_closing_call(monkeypatch) -> None:
    task = _task()
    budget = inference_v02.InferenceBudget(
        max_output_tokens=100, max_total_tokens=20_000
    )
    first = _tool_call(
        "call-1", source_id="mut:0", start_line=10, end_line=10
    )
    first.usage = Usage(
        requests=1, input_tokens=18_000, output_tokens=100, total_tokens=18_100
    )
    model = ScriptedModel(first, _message(contract_data()))
    ledger = UsageLedger(budget)
    _install_model(monkeypatch, task, model, budget=budget)

    with pytest.raises(InferenceError) as caught:
        asyncio.run(inference_v02.infer_scenario(
            task, model="offline-model", budget=budget, usage_ledger=ledger
        ))

    assert caught.value.category == "budget_exhausted"
    assert model.calls == 1
    assert ledger.gate_events[-1]["reason"] == "projected_token_limit"


@pytest.mark.parametrize("raw_usage,total", [(None, 0), ({"metadata": "only"}, 0), (None, 100)])
def test_unknown_usage_is_not_zero_and_blocks_next_call(monkeypatch, raw_usage, total) -> None:
    task = _task()
    first = _tool_call(
        "call-1", source_id="mut:0", start_line=10, end_line=10
    )
    first.usage = Usage(total_tokens=total)
    first.raw_usage = raw_usage
    model = ScriptedModel(first, _message(contract_data()))
    budget = inference_v02.InferenceBudget()
    ledger = UsageLedger(budget)
    _install_model(monkeypatch, task, model, budget=budget)

    with pytest.raises(InferenceError) as caught:
        asyncio.run(inference_v02.infer_scenario(
            task, model="offline-model", budget=budget, usage_ledger=ledger
        ))

    audit = ledger.to_dict()
    assert caught.value.category == "budget_exhausted"
    assert model.calls == 1
    assert audit["usage_complete"] is False
    assert audit["total_tokens"] is None
    assert audit["known_usage_subtotal"]["total_tokens"] == total
    assert audit["rate_estimated_cost_usd"] is None
    assert ledger.gate_events[-1]["reason"] == "prior_usage_unknown"


def test_configured_rates_produce_labelled_cost_estimate(monkeypatch) -> None:
    task = _task()
    budget = inference_v02.InferenceBudget(
        input_cost_per_million=Decimal("2"),
        output_cost_per_million=Decimal("4"),
    )
    model = ScriptedModel(_message(contract_data()))
    ledger = UsageLedger(budget)
    _install_model(monkeypatch, task, model, budget=budget)

    asyncio.run(inference_v02.infer_scenario(
        task, model="offline-model", budget=budget, usage_ledger=ledger
    ))

    audit = ledger.to_dict()
    assert audit["rate_estimated_cost_usd"] == "0.0001"
    assert audit["pricing"]["basis"] == "host_supplied_per_million_token_prices"


def test_started_but_cancelled_request_marks_accounting_incomplete() -> None:
    budget = inference_v02.InferenceBudget()
    ledger = UsageLedger(budget)
    ledger.requests_started = 1

    audit = ledger.to_dict()

    assert audit["usage_complete"] is False
    assert audit["total_tokens"] is None


def test_zero_requests_with_prices_has_known_zero_cost() -> None:
    ledger = UsageLedger(inference_v02.InferenceBudget(
        input_cost_per_million=Decimal("2"),
        output_cost_per_million=Decimal("4"),
    ))
    assert ledger.to_dict()["rate_estimated_cost_usd"] == "0"
    ledger.requests_started = 1
    ledger.rate_estimated_cost_usd = Decimal("0.01")
    assert ledger.to_dict()["rate_estimated_cost_usd"] is None
