"""Capture non-streaming provider responses before the Agents SDK parses them."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import os
import re
from types import SimpleNamespace
from typing import Any

from agents.usage import Usage

from mock_spec.inference import InferenceError


_SECRET_PATTERNS = (
    (re.compile(r"(?i)\bBearer\s+[^\s,;]+"), "Bearer <REDACTED>"),
    (re.compile(r"\bsk-[A-Za-z0-9_-]+"), "<REDACTED>"),
    (
        re.compile(
            r"(?i)\b(api[_-]?key|authorization|access[_-]?token|secret|password)\b"
            r"[\"']?\s*[:=]\s*[\"']?[^\s,;\"'}]+"
        ),
        None,
    ),
)
_SECRET_KEYS = {
    "authorization", "api_key", "api-key", "access_token", "secret", "password",
}


class ProviderCaptureError(ValueError):
    """The provider replied, but its auditable response could not be retained."""


def _redact(value: object) -> object:
    if isinstance(value, dict):
        return {
            str(key): (
                "<REDACTED>" if str(key).lower() in _SECRET_KEYS else _redact(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact(item) for item in value]
    if isinstance(value, str):
        text = value
        api_key = os.environ.get("OPENAI_API_KEY", "")
        if api_key:
            text = text.replace(api_key, "<REDACTED>")
        for pattern, replacement in _SECRET_PATTERNS:
            text = pattern.sub(
                replacement
                if replacement is not None
                else lambda match: f"{match.group(1)}=<REDACTED>",
                text,
            )
        return text
    return value


def _usage_from_body(body: object) -> tuple[Usage, dict[str, object]] | None:
    if not isinstance(body, dict) or not isinstance(body.get("usage"), dict):
        return None
    raw = body["usage"]
    values: dict[str, int] = {}
    for key in ("input_tokens", "output_tokens", "total_tokens"):
        value = raw.get(key)
        if type(value) is not int or value < 0:
            return None
        values[key] = value
    return Usage(requests=1, input_tokens=values["input_tokens"],
                 output_tokens=values["output_tokens"], total_tokens=values["total_tokens"]), dict(raw)


def _measurement(value: object, *, available: bool) -> dict[str, object]:
    if not available:
        return {"presence": "unavailable", "value": None}
    if type(value) is int and value >= 0:
        return {"presence": "reported", "value": value}
    return {"presence": "not_reported", "value": None}


def _usage_measurements(captures: list[dict[str, object]]) -> dict[str, dict[str, object]]:
    unavailable = {
        name: _measurement(None, available=False)
        for name in ("input_tokens", "output_tokens", "total_tokens", "reasoning_tokens")
    }
    if len(captures) != 1 or captures[0].get("body_truncated") is True:
        return unavailable
    body = captures[0].get("body")
    if not isinstance(body, dict):
        return unavailable
    usage = body.get("usage")
    if not isinstance(usage, dict):
        return {
            name: _measurement(None, available=True)
            for name in unavailable
        }
    output_details = usage.get("output_tokens_details")
    reasoning = (
        output_details.get("reasoning_tokens")
        if isinstance(output_details, dict) else None
    )
    return {
        "input_tokens": _measurement(usage.get("input_tokens"), available=True),
        "output_tokens": _measurement(usage.get("output_tokens"), available=True),
        "total_tokens": _measurement(usage.get("total_tokens"), available=True),
        "reasoning_tokens": _measurement(reasoning, available=True),
    }


@dataclass
class ResponseCapture:
    """Associate one HTTP response with the currently executing staged model call.

    Staged inference issues model calls sequentially and disables provider retries.
    ``begin_call`` therefore establishes the only valid owner for subsequent
    response-hook events.  Request headers and request bodies are never retained.
    """

    max_body_bytes: int = 2_000_000
    _active_sequence: int | None = None
    _captures: dict[int, list[dict[str, object]]] = field(default_factory=dict)

    def begin_call(self, sequence: int) -> None:
        if type(sequence) is not int or sequence < 1:
            raise ValueError("provider capture sequence must be a positive integer")
        if self._active_sequence is not None:
            raise RuntimeError("provider capture already has an active model call")
        self._active_sequence = sequence
        self._captures.setdefault(sequence, [])

    async def on_response(self, response: Any) -> None:
        sequence = self._active_sequence
        if sequence is None:
            raise RuntimeError("provider response arrived without an active model call")
        content = await response.aread()
        truncated = len(content) > self.max_body_bytes
        retained = content[: self.max_body_bytes]
        try:
            decoded: object = json.loads(retained)
            encoding = "json"
        except (UnicodeDecodeError, json.JSONDecodeError):
            decoded = retained.decode("utf-8", errors="replace")
            encoding = "text"
        body = _redact(decoded)
        response_id = body.get("id") if isinstance(body, dict) else None
        request_id = response.headers.get("x-request-id")
        self._captures[sequence].append({
            "call_sequence": sequence,
            "http_status": response.status_code,
            "request_id": request_id,
            "response_id": response_id if isinstance(response_id, str) else None,
            "body_encoding": encoding,
            "body_truncated": truncated,
            "body": body,
        })

    def finish_call(
        self,
        event: dict[str, object],
        ledger: Any,
        sdk_responses_for_this_call: list[dict],
    ) -> None:
        sequence = event.get("sequence")
        if type(sequence) is not int or sequence != self._active_sequence:
            raise RuntimeError("provider capture call association mismatch")
        captures = self._captures.pop(sequence, [])
        self._active_sequence = None
        event["provider_responses"] = captures
        event["provider_response_capture"] = (
            "captured" if captures else "unavailable"
        )
        event["provider_usage"] = _usage_measurements(captures)

        if any(item.get("body_truncated") is True for item in captures):
            event["provider_response_capture"] = "capture_failed"
            event["status"] = "failed"
            event["exception"] = {
                "classification": "provider_capture",
                "type": "ProviderCaptureError",
                "message": "provider response exceeded the capture limit",
                "status_code": None,
                "request_id": None,
                "error_code": "provider_body_truncated",
                "cause_types": [],
            }
            if event.get("model_request_started") is not False and not sdk_responses_for_this_call:
                try:
                    ledger.after_model_call(SimpleNamespace(
                        usage=Usage(requests=1), raw_usage=None,
                    ))
                except InferenceError as exc:
                    if exc.category != "budget_exhausted":
                        raise
                    event["provider_usage_gate"] = {
                        "type": type(exc).__name__, "category": exc.category,
                    }
            raise ProviderCaptureError("provider response capture was truncated")

        if event.get("model_request_started") is False:
            event["provider_usage_recovery"] = "not_applicable_request_not_started"
            return

        # Successful SDK responses are already accounted by BudgetHooks.  A
        # failed/incomplete response never reaches that hook, so close exactly
        # that one request here using provider usage when available.
        if sdk_responses_for_this_call:
            event["provider_usage_recovery"] = "not_needed_sdk_accounted"
            return
        usage = None
        raw_usage = None
        if len(captures) == 1:
            parsed = _usage_from_body(captures[0]["body"])
            if parsed is not None:
                usage, raw_usage = parsed
        if usage is None:
            usage = Usage(requests=1)
            event["provider_usage_recovery"] = "unknown"
        else:
            event["provider_usage_recovery"] = "recovered_from_http_response"
        try:
            ledger.after_model_call(SimpleNamespace(usage=usage, raw_usage=raw_usage))
        except InferenceError as exc:
            # UsageLedger updates its counters before raising an actual-budget
            # exhaustion. Preserve that fact while allowing the original model
            # failure to remain the stage's primary exception.
            if exc.category != "budget_exhausted":
                raise
            event["provider_usage_gate"] = {
                "type": type(exc).__name__,
                "category": exc.category,
            }


__all__ = ["ProviderCaptureError", "ResponseCapture"]
