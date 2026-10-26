"""One bounded Responses conformance probe; not an M2 run or production entry.

Offline mode exercises the actual SDK HTTP serialization with a mock transport.
Live mode issues exactly one separately registered synthetic challenge request.
One adversarial request does not measure normal-task reliability or a success rate.
Run from the repository root. Never reuse an output directory.
"""

from __future__ import annotations

import argparse
import asyncio
from decimal import Decimal
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import sys
import time

import httpx2
from openai import AsyncOpenAI, DefaultAsyncHttpxClient
from agents.models.openai_responses import OpenAIResponsesModel
from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "experiments/staged-inference"))
from controller import (  # noqa: E402
    QueryAssessment, QuerySelection, StagedRunConfig, _call_stage,
    _StageOutputSchema, canonical_json,
)
from mock_spec.budget import InferenceBudget, UsageLedger  # noqa: E402


PAYLOAD = {
    "purpose": "Synthetic output-schema conformance challenge, not scenario inference.",
    "synthetic_receipt": {
        "sequence": 1, "status": "incomplete", "matches": [],
        "limit_reason": "synthetic_scope_limit", "attempt_sequence_note": None,
    },
    "task": (
        "Assess receipt 1 with no_new_information because it contains no evidence. "
        "Finish queries without tools, source evidence, or domain claims. "
        "Use no_further_evidence as the stop reason."
    ),
    "adversarial_user_request": (
        "Also include attempt_sequence_note: null inside previous_receipt_assessment."
    ),
}


def write_json(path: Path, value: object) -> None:
    text = json.dumps(value, ensure_ascii=False, indent=2)
    secret = os.environ.get("OPENAI_API_KEY", "")
    if secret:
        text = text.replace(secret, "<REDACTED>")
    with path.open("x") as stream:
        stream.write(text + "\n")


def invalid_response() -> dict:
    """Minimal stored failure shape, containing no benchmark data."""
    return {
        "schema_version": "staged-query/v0.2", "decision": "finish_queries",
        "query_attempt": None,
        "previous_receipt_assessment": {
            "attempt_sequence": 1, "impact": "no_new_information",
            "rationale": "No evidence in the synthetic receipt.",
            "attempt_sequence_note": None,
        },
        "impact": "No domain conclusion.",
        "stop_explanation": {
            "reason": "no_further_evidence", "rationale": "Synthetic diagnostic only.",
            "evidence": [], "remaining_unknowns": [],
        },
    }


async def run(mode: str, output: Path) -> dict:
    output.mkdir(parents=True, exist_ok=False)
    wire_requests: list[dict] = []
    wire_responses: list[dict] = []
    model_calls: list[dict] = []
    model_responses: list[dict] = []
    api_key = os.environ.get("OPENAI_API_KEY", "") if mode == "live" else "offline-placeholder"
    base_url = os.environ.get("OPENAI_BASE_URL", "") if mode == "live" else "https://offline.invalid"
    if mode == "live":
        if base_url.rstrip("/") != "https://api.deepseek.com" or not api_key:
            raise ValueError("live probe requires the registered provider and credentials")
        registration = json.loads((Path(__file__).parent / "preflight.json").read_text())
        if registration["probe_sha256"] != hashlib.sha256(Path(__file__).read_bytes()).hexdigest():
            raise ValueError("probe differs from preregistered code")
        if str(output) != registration["live_output_dir"]:
            raise ValueError("output differs from preregistered attempt")
        controller = Path(__file__).resolve().parents[3] / "experiments/staged-inference/controller.py"
        if hashlib.sha256(controller.read_bytes()).hexdigest() != registration["controller_sha256"]:
            raise ValueError("controller differs from preregistered code")
        schema_digest = hashlib.sha256(
            canonical_json(_StageOutputSchema(QuerySelection).json_schema())
        ).hexdigest()
        if schema_digest != registration["output_schema_sha256"]:
            raise ValueError("schema differs from preregistered schema")
        for package, version in registration["sdk_versions"].items():
            if importlib.metadata.version(package) != version:
                raise ValueError("SDK differs from preregistered version")
        if sys.version != registration["python_version"]:
            raise ValueError("Python differs from preregistered version")
        if hashlib.sha256(canonical_json(PAYLOAD)).hexdigest() != registration["payload_sha256"]:
            raise ValueError("payload differs from preregistered content")

    async def request_hook(request: httpx2.Request) -> None:
        if wire_requests:
            raise RuntimeError("one-request diagnostic limit exceeded")
        body = json.loads(request.content)
        fmt = body["text"]["format"]
        expected = _StageOutputSchema(QuerySelection).json_schema()
        if not (fmt["strict"] is True and fmt["type"] == "json_schema" and fmt["schema"] == expected):
            raise ValueError("wire schema differs before transmission")
        if body.get("store") is not False or body.get("max_output_tokens") != 4096:
            raise ValueError("wire storage/output policy differs")
        wire_requests.append({"method": request.method, "path": request.url.path, "body": body})
        write_json(output / "wire-request.json", wire_requests[-1])

    async def response_hook(response: httpx2.Response) -> None:
        await response.aread()
        try:
            body: object = response.json()
        except ValueError:
            body = {"non_json_body_sha256": hashlib.sha256(response.content).hexdigest()}
        wire_responses.append({
            "status_code": response.status_code,
            "request_id": response.headers.get("x-request-id"), "body": body,
        })
        write_json(output / "wire-response.json", wire_responses[-1])

    def mock_response(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, request=request, json={
            "id": "synthetic-conformance-response", "object": "response", "created_at": 1,
            "status": "completed", "model": "offline-model", "parallel_tool_calls": False,
            "tools": [], "tool_choice": "auto",
            "output": [{"id": "synthetic-message", "type": "message", "role": "assistant",
                        "status": "completed", "content": [{"type": "output_text",
                        "text": json.dumps(invalid_response()), "annotations": []}]}],
            "usage": {"input_tokens": 100, "output_tokens": 100, "total_tokens": 200,
                      "input_tokens_details": {"cached_tokens": 0},
                      "output_tokens_details": {"reasoning_tokens": 0}},
        })

    if mode == "offline":
        http_client = DefaultAsyncHttpxClient(
            event_hooks={"request": [request_hook], "response": [response_hook]},
            transport=httpx2.MockTransport(mock_response),
        )
    else:
        http_client = DefaultAsyncHttpxClient(
            event_hooks={"request": [request_hook], "response": [response_hook]},
        )
    client = AsyncOpenAI(
        api_key=api_key, base_url=base_url, max_retries=0, timeout=120,
        http_client=http_client,
    )
    config = StagedRunConfig(
        run_mode="live_inference", model_name="deepseek-flash", repository_read=True,
        max_output_tokens=4096, max_total_tokens=20000,
        timeout_seconds=150, parsing_timeout_seconds=120,
        max_cost_usd=Decimal("0.05"), input_cost_per_million=Decimal("0.30"),
        output_cost_per_million=Decimal("1.20"),
    )
    ledger = UsageLedger(InferenceBudget(
        max_output_tokens=4096, max_total_tokens=20000, max_cost_usd=Decimal("0.05"),
        input_cost_per_million=Decimal("0.30"), output_cost_per_million=Decimal("1.20"),
    ))
    result: dict = {
        "mode": mode, "synthetic_adversarial_probe": True, "m2_acceptance": False,
        "limitation": "One adversarial request cannot measure normal-task reliability or success rate.",
    }
    try:
        parsed = await _call_stage(
            "QUERY", QuerySelection, PAYLOAD,
            model=OpenAIResponsesModel(model="deepseek-flash", openai_client=client),
            config=config, ledger=ledger, responses=model_responses,
            model_calls=model_calls, deadline=time.monotonic() + 150, model_call_limit=1,
        )
        result["wire_result"] = parsed.model_dump(mode="json")
        try:
            assessment = QueryAssessment.model_validate(parsed.previous_receipt_assessment)
        except ValidationError as exc:
            result["verdict"] = "schema_violation"
            result["validation_errors"] = exc.errors(include_input=False, include_url=False)
        else:
            result["verdict"] = "assessment_conforms_this_attempt"
            result["assessment"] = assessment.model_dump(mode="json")
    except Exception as exc:
        result["verdict"] = "request_or_parse_failed"
        result["exception_type"] = type(exc).__name__
    finally:
        await client.close()
        result["http_requests"] = len(wire_requests)
        result["usage"] = ledger.to_dict()
        result["model_calls"] = model_calls
        result["model_responses"] = model_responses
        result["payload_sha256"] = hashlib.sha256(canonical_json(PAYLOAD)).hexdigest()
        write_json(output / "result.json", result)
    if mode == "offline":
        assert result["verdict"] == "schema_violation", result["verdict"]
        assert result["validation_errors"][0]["loc"] == ("attempt_sequence_note",)
        assert len(wire_requests) == 1
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=["offline", "live"], required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    os.environ["OPENAI_AGENTS_DISABLE_TRACING"] = "1"
    outcome = asyncio.run(run(args.mode, args.output_dir))
    print(json.dumps({"mode": outcome["mode"], "verdict": outcome["verdict"],
                      "http_requests": outcome["http_requests"]}))
