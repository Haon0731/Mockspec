"""Persist real-tool SDK integration evidence; never a live or autonomous-model test."""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import hashlib
from importlib.metadata import version
import json
from pathlib import Path
from time import perf_counter
from unittest.mock import patch

from agents.models.interface import Model, ModelResponse
from agents.usage import Usage
from openai.types.responses import ResponseFunctionToolCall, ResponseOutputMessage, ResponseOutputText

from mock_spec import inference, inference_v02
from mock_spec.adapters import java, python
from mock_spec.budget import UsageLedger
from mock_spec.execution_metadata import collect_execution_metadata
from mock_spec.scenario import validate_contract


class ScriptedModel(Model):
    """Fixed host-side test responses, with no provider client or networking."""

    def __init__(self, contract: dict, requests: list[tuple[str, dict]]):
        self.contract = contract
        self.requests = requests
        self.inputs = []

    async def get_response(self, *args, **kwargs):
        index = len(self.inputs)
        self.inputs.append(kwargs["input"])
        if index < len(self.requests):
            name, arguments = self.requests[index]
            output = [ResponseFunctionToolCall(
                type="function_call", name=name, call_id=f"offline-{index}",
                arguments=json.dumps(arguments),
            )]
        else:
            output = [ResponseOutputMessage(
                type="message", id="offline-final", role="assistant", status="completed",
                content=[ResponseOutputText(type="output_text", annotations=[],
                                           text=json.dumps(self.contract))],
            )]
        # Explicit synthetic usage exercises the budget interface, NOT measured tokens.
        return ModelResponse(output=output, response_id=None,
                             usage=Usage(requests=1, input_tokens=1, output_tokens=1, total_tokens=2))

    async def stream_response(self, *args, **kwargs):
        raise AssertionError("offline validation does not stream")
        yield


async def run_one(task, contract_data: dict, version_id: str) -> dict:
    selected = inference if version_id == "v0.1" else inference_v02
    src = task.mut_source
    requests = [] if version_id == "v0.1" else [
        ("inspect_source", {"source_id": src.source_id, "start_line": src.start_line,
                            "end_line": src.end_line}),
        ("inspect_program_structure", {"source_id": src.source_id}),
        ("inspect_dependency_usage", {"source_id": src.source_id}),
    ]
    model = ScriptedModel(contract_data, requests)
    original_builder = selected.build_agent

    def build(*args, **kwargs):
        agent = original_builder(*args, **kwargs)
        agent.model = model
        return agent

    trace, responses = [], []
    budget = inference_v02.InferenceBudget()
    ledger = UsageLedger(budget)
    kwargs = {} if version_id == "v0.1" else {
        "budget": budget, "tool_trace": trace, "usage_ledger": ledger,
    }
    started = perf_counter()
    with patch.object(selected, "build_agent", build), patch.dict(
        "os.environ", {"OPENAI_API_KEY": "offline-placeholder-never-a-provider-key"}
    ):
        contract = await selected.infer_scenario(
            task, model="scripted-offline", responses=responses, **kwargs,
        )
    payload = json.dumps(task.to_dict(), ensure_ascii=False, separators=(",", ":"))
    sent = model.inputs[0]
    if isinstance(sent, list):
        sent = sent[0]["content"]
    assert sent == payload
    assert contract == validate_contract(contract_data, task)
    assert len(trace) == len(requests)
    for event in trace:
        assert event["status"] in {"ok", "incomplete"}, event
        if event["tool"] != "inspect_source":
            assert event["result"]["facts"], event
    return {
        "mode": "offline_scripted_sdk_with_real_tools",
        "autonomous_tool_choice_verified": False,
        "model_quality_comparison_possible": False,
        "contract_origin": "preexisting_host_synthetic_fixture_not_model_inference",
        "agent_version": version_id, "model": "scripted-offline",
        "case_id": task.case_id, "language": task.language,
        "prompt_version": selected.PROMPT_VERSION,
        "prompt_sha256": hashlib.sha256(selected._PROMPT_PATH.read_bytes()).hexdigest(),
        "task_sha256": hashlib.sha256(payload.encode()).hexdigest(),
        "input_equal_to_baseline": True,
        "duration_seconds": perf_counter() - started,
        "scripted_model_calls": len(model.inputs),
        "provider_requests": 0, "provider_token_usage": None,
        "provider_cost_usd": "0", "cost_basis": "no_provider_client_or_request",
        "usage_warning": "raw_responses usage is scripted placeholder data, not measured tokens",
        "budget_test_ledger": ledger.to_dict() if version_id == "v0.2" else None,
        "tool_trace": trace, "raw_responses": responses,
        "validation": "schema_and_evidence_locations_passed",
        "semantic_correctness_verified": False,
        "contract": contract.model_dump(),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--language", choices=("java", "python"), required=True)
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--case-id", required=True)
    parser.add_argument("--synthetic-contract", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    # Reserve before loading/running: do not replace prior validation evidence.
    args.output_dir.mkdir(parents=True, exist_ok=False)
    loader = java.load_task if args.language == "java" else python.load_task
    task = loader(args.snapshot, case_id=args.case_id)
    data = json.loads(args.synthetic_contract.read_text())
    validate_contract(data, task)
    artifacts = []
    for version_id in ("v0.1", "v0.2"):
        artifact = asyncio.run(run_one(task, data, version_id))
        artifacts.append(artifact)
        with (args.output_dir / f"{version_id}.json").open("x") as stream:
            json.dump(artifact, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write("\n")
    metadata = collect_execution_metadata()
    summary = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "mode": "offline_validation_only", "case_id": task.case_id,
        "snapshot_id": args.snapshot.name,
        "packages": {p: version(p) for p in ("openai-agents", "openai", "pydantic")},
        "code_commit": metadata.code_commit,
        "code_dirty": metadata.code_dirty,
        "code_sha256": metadata.code_files_sha256,
        "runtime_identity": metadata.runtime_identity,
        "same_initial_payload": artifacts[0]["task_sha256"] == artifacts[1]["task_sha256"],
        "same_synthetic_contract": artifacts[0]["contract"] == artifacts[1]["contract"],
        "real_tool_calls": len(artifacts[1]["tool_trace"]),
        "autonomous_tool_choice_verified": False,
        "quality_effect": "unmeasured_without_live_inference",
    }
    with (args.output_dir / "summary.json").open("x") as stream:
        json.dump(summary, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()
