"""Auditable token and cost accounting for V0.2 model calls."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
import json
import math
from typing import Any

from agents import RunHooks
from agents.usage import Usage
from pydantic import TypeAdapter

from .inference import InferenceError


_MILLION = Decimal(1_000_000)


def _jsonable(value: object) -> object:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return repr(value)


def estimate_known_request_tokens(
    *, system_prompt: str | None, input_items: list, agent
) -> tuple[int, int]:
    """Estimate known request text with a UTF-8 byte/4 proxy.

    The estimate includes the prompt, accumulated input/history, tool schemas, and
    output schema. Provider protocol overhead and the provider tokenizer are not
    available here, so this is a preflight estimate rather than an exact token count.
    """

    tools = [
        {
            "name": getattr(tool, "name", None),
            "description": getattr(tool, "description", None),
            "params_json_schema": getattr(tool, "params_json_schema", None),
        }
        for tool in agent.tools
    ]
    output_type = agent.output_type
    output_schema = None
    if output_type is not None:
        try:
            output_schema = TypeAdapter(output_type).json_schema()
        except Exception:
            output_schema = repr(output_type)
    material = {
        "system_prompt": system_prompt,
        "input": _jsonable(input_items),
        "tools": tools,
        "output_schema": output_schema,
    }
    byte_count = len(json.dumps(
        material, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8"))
    return max(1, math.ceil(byte_count / 4)), byte_count


@dataclass
class UsageLedger:
    """Per-run model request estimates, provider usage, and cost state."""

    budget: Any
    requests_started: int = 0
    requests_completed: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    usage_complete: bool = True
    rate_estimated_cost_usd: Decimal | None = None
    estimates: list[dict[str, object]] = field(default_factory=list)
    usage_records: list[dict[str, object]] = field(default_factory=list)
    gate_events: list[dict[str, object]] = field(default_factory=list)

    @property
    def prices_configured(self) -> bool:
        return (
            self.budget.input_cost_per_million is not None
            and self.budget.output_cost_per_million is not None
        )

    def _cost(self, input_tokens: int, output_tokens: int) -> Decimal | None:
        if not self.prices_configured:
            return None
        return (
            Decimal(input_tokens) * self.budget.input_cost_per_million
            + Decimal(output_tokens) * self.budget.output_cost_per_million
        ) / _MILLION

    def before_model_call(self, *, system_prompt: str | None, input_items: list, agent) -> None:
        sequence = self.requests_started + 1
        if self.requests_completed and not self.usage_complete:
            event = {
                "sequence": sequence,
                "stage": "before_model_call",
                "status": "blocked",
                "reason": "prior_usage_unknown",
            }
            self.gate_events.append(event)
            raise InferenceError(
                "scenario inference usage is unavailable for the next-call budget gate",
                "budget_exhausted",
            )

        estimated_input, known_bytes = estimate_known_request_tokens(
            system_prompt=system_prompt, input_items=input_items, agent=agent
        )
        projected_total = self.total_tokens + estimated_input + self.budget.max_output_tokens
        estimated_incremental_cost = self._cost(
            estimated_input, self.budget.max_output_tokens
        )
        projected_cost = (
            None
            if estimated_incremental_cost is None
            else (self.rate_estimated_cost_usd or Decimal(0)) + estimated_incremental_cost
        )
        estimate = {
            "sequence": sequence,
            "method": "known_request_utf8_bytes_divided_by_4",
            "known_request_bytes": known_bytes,
            "estimated_input_tokens": estimated_input,
            "reserved_output_tokens": self.budget.max_output_tokens,
            "projected_total_tokens": projected_total,
            "projected_cost_usd": None if projected_cost is None else str(projected_cost),
            "limitations": (
                "Provider protocol overhead and exact tokenizer behavior are unavailable; "
                "the estimate is not a strict token or cost upper bound."
            ),
        }
        self.estimates.append(estimate)

        reason = None
        if projected_total > self.budget.max_total_tokens:
            reason = "projected_token_limit"
        elif (
            self.budget.max_cost_usd is not None
            and projected_cost is not None
            and projected_cost > self.budget.max_cost_usd
        ):
            reason = "projected_cost_limit"
        if reason is not None:
            self.gate_events.append({
                "sequence": sequence,
                "stage": "before_model_call",
                "status": "blocked",
                "reason": reason,
            })
            raise InferenceError(
                "scenario inference model-call budget exhausted", "budget_exhausted"
            )
        self.requests_started += 1
        self.gate_events.append({
            "sequence": sequence,
            "stage": "before_model_call",
            "status": "allowed",
            "reason": None,
        })

    def after_model_call(self, response) -> None:
        usage: Usage = response.usage
        known = bool(
            response.raw_usage is not None
            or usage.input_tokens
            or usage.output_tokens
            or usage.total_tokens
        )
        total = usage.total_tokens or (usage.input_tokens + usage.output_tokens)
        reported_requests = usage.requests or 1
        self.requests_completed += 1
        self.input_tokens += usage.input_tokens
        self.output_tokens += usage.output_tokens
        self.total_tokens += total
        if not known:
            self.usage_complete = False
        incremental_cost = self._cost(usage.input_tokens, usage.output_tokens) if known else None
        if incremental_cost is not None:
            self.rate_estimated_cost_usd = (
                self.rate_estimated_cost_usd or Decimal(0)
            ) + incremental_cost
        elif not self.prices_configured or not known:
            self.rate_estimated_cost_usd = None
        self.usage_records.append({
            "sequence": len(self.usage_records) + 1,
            "known": known,
            "reported_requests": reported_requests,
            "input_tokens": usage.input_tokens if known else None,
            "output_tokens": usage.output_tokens if known else None,
            "total_tokens": total if known else None,
            "cost_usd": None if incremental_cost is None else str(incremental_cost),
            "raw_usage_available": response.raw_usage is not None,
        })

        reason = None
        if known and self.total_tokens > self.budget.max_total_tokens:
            reason = "actual_token_limit"
        elif (
            self.budget.max_cost_usd is not None
            and self.rate_estimated_cost_usd is not None
            and self.rate_estimated_cost_usd > self.budget.max_cost_usd
        ):
            reason = "actual_cost_limit"
        if reason is not None:
            self.gate_events.append({
                "sequence": self.requests_started,
                "stage": "after_model_call",
                "status": "exhausted",
                "reason": reason,
            })
            raise InferenceError(
                "scenario inference model-call budget exhausted", "budget_exhausted"
            )

    def to_dict(self) -> dict[str, object]:
        accounting_complete = (
            self.usage_complete and self.requests_started == self.requests_completed
        )
        return {
            "requests_started": self.requests_started,
            "requests_completed": self.requests_completed,
            "input_tokens": self.input_tokens if accounting_complete else None,
            "output_tokens": self.output_tokens if accounting_complete else None,
            "total_tokens": self.total_tokens if accounting_complete else None,
            "usage_complete": accounting_complete,
            "known_usage_subtotal": {
                "input_tokens": self.input_tokens,
                "output_tokens": self.output_tokens,
                "total_tokens": self.total_tokens,
            },
            "rate_estimated_cost_usd": (
                None if self.rate_estimated_cost_usd is None
                else str(self.rate_estimated_cost_usd)
            ),
            "pricing": {
                "input_cost_per_million": (
                    None if self.budget.input_cost_per_million is None
                    else str(self.budget.input_cost_per_million)
                ),
                "output_cost_per_million": (
                    None if self.budget.output_cost_per_million is None
                    else str(self.budget.output_cost_per_million)
                ),
                "basis": (
                    "host_supplied_per_million_token_prices"
                    if self.prices_configured else "unknown"
                ),
            },
            "estimates": self.estimates,
            "usage_records": self.usage_records,
            "gate_events": self.gate_events,
            "limitations": [
                "Preflight token estimates use known serialized request bytes divided by four.",
                "Provider tokenizer and protocol overhead can differ, so cost is not a strict hard cap.",
                "A locally cancelled request may still incur provider charges not present in usage.",
            ],
        }


class BudgetHooks(RunHooks):
    """Capture responses and enforce the next-call global budget gate."""

    def __init__(self, ledger: UsageLedger, responses: list | None = None):
        self.ledger = ledger
        self.responses = responses

    async def on_llm_start(self, context, agent, system_prompt, input_items):
        self.ledger.before_model_call(
            system_prompt=system_prompt, input_items=input_items, agent=agent
        )

    async def on_llm_end(self, context, agent, response):
        if self.responses is not None:
            self.responses.append({
                "output": [item.model_dump(mode="json") for item in response.output],
                "usage": TypeAdapter(Usage).dump_python(response.usage, mode="json"),
                "raw_usage": _jsonable(response.raw_usage),
                "response_id": response.response_id,
                "request_id": response.request_id,
                "resolved_model_version": None,
            })
        self.ledger.after_model_call(response)


__all__ = ["BudgetHooks", "UsageLedger", "estimate_known_request_tokens"]
