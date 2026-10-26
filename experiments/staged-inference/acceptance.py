"""Build and verify the fixed offline acceptance evidence for M1 staged inference."""

from __future__ import annotations

import argparse
import asyncio
from contextlib import nullcontext
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any
from unittest.mock import patch

from controller import StagedRunConfig, execute_staged_run
from mock_spec.source_analysis import SourceAnalysis
from scripted import FixedScriptModel, query_script, scripted_outputs, synthetic_task


REPORT_VERSION = "m1-staged-inference-acceptance/v0.2"
MANIFEST_VERSION = "scenario-inference-manifest/v0.1"
RECORD_VERSION = "scenario-inference-record/v0.2"
CONTRACT_VERSION = "mock-spec.scenario/v0.1"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} does not contain a JSON object")
    return value


def _model_payload(value: object) -> dict[str, Any]:
    if isinstance(value, list):
        if len(value) != 1 or not isinstance(value[0], dict):
            raise ValueError("model input has an unexpected message envelope")
        value = value[0].get("content")
    if not isinstance(value, str):
        raise ValueError("model input content is not serialized JSON")
    payload = json.loads(value)
    if not isinstance(payload, dict):
        raise ValueError("model input JSON is not an object")
    return payload


def _write_json_exclusive(path: Path, value: object) -> None:
    payload = json.dumps(value, ensure_ascii=False, indent=2) + "\n"
    with path.open("x", encoding="utf-8") as handle:
        handle.write(payload)


def _cases() -> list[dict[str, object]]:
    cases: list[dict[str, object]] = []
    for variant in ("B", "C"):
        for replay_case, status, stop_reason, exit_code in (
            ("selected", "SELECTED", "completed", 0),
            ("ambiguous", "AMBIGUOUS", "completed", 0),
            ("inconclusive", "INCONCLUSIVE", "evidence_exhausted", 0),
            ("error", "ERROR", "output_invalid", 1),
        ):
            cases.append({
                "name": f"{variant.lower()}-{replay_case}-q0",
                "variant": variant,
                "replay_case": replay_case,
                "query_count": 0,
                "expected_status": status,
                "expected_stop_reason": stop_reason,
                "expected_exit_code": exit_code,
            })
        for query_count, status, stop_reason in (
            (6, "SELECTED", "completed"),
            (7, "INCONCLUSIVE", "budget_exhausted"),
        ):
            cases.append({
                "name": f"{variant.lower()}-selected-q{query_count}",
                "variant": variant,
                "replay_case": "selected",
                "query_count": query_count,
                "expected_status": status,
                "expected_stop_reason": stop_reason,
                "expected_exit_code": 0,
            })
    return cases


def _run_boundary_cases(output_dir: Path) -> list[dict[str, object]]:
    cases: list[tuple[dict[str, object], FixedScriptModel, StagedRunConfig]] = []

    tampered = deepcopy(scripted_outputs())
    tampered[0]["original_goals"][0]["quote"] = "invented replacement assertion"
    cases.append(({
        "name": "boundary-original-assertion-tamper",
        "variant": "B", "replay_case": "assertion_tamper", "query_count": 0,
        "expected_status": "ERROR", "expected_stop_reason": "output_invalid",
    }, FixedScriptModel(tampered), StagedRunConfig()))

    parser_outputs = query_script(1)
    parser_outputs[1]["query_attempt"]["tool"] = "inspect_program_structure"
    parser_outputs[1]["query_attempt"]["arguments_json"] = json.dumps({"source_id": "mut:0"})
    cases.append(({
        "name": "boundary-parser-backend-unavailable",
        "variant": "B", "replay_case": "parser_backend_unavailable", "query_count": 1,
        "expected_status": "SELECTED", "expected_stop_reason": "completed",
    }, FixedScriptModel(parser_outputs), StagedRunConfig()))

    results: list[dict[str, object]] = []
    async def unavailable_parser(
        self: SourceAnalysis, source_id: str
    ) -> dict[str, object]:
        del self, source_id
        return {
            "status": "error", "error_code": "backend_unavailable", "facts": [],
            "diagnostics": ["The configured program parser is unavailable."],
            "limitations": ["No structural facts were established."],
        }

    for case, model, config in cases:
        parser_patch = (
            patch.object(
                SourceAnalysis, "inspect_program_structure", unavailable_parser
            )
            if case["replay_case"] == "parser_backend_unavailable"
            else nullcontext()
        )
        with parser_patch:
            run_dir = output_dir / str(case["name"])
            asyncio.run(execute_staged_run(
                synthetic_task(), output_dir=run_dir, model=model, config=config,
                requested_case_id="synthetic_python_delegation",
            ))
        result = _verify_run(case, run_dir)
        if case["replay_case"] == "assertion_tamper":
            record = _read_json(run_dir / "inference_record.json")
            if record["error_stage"] != "EXTRACT":
                raise ValueError("assertion tamper was not rejected at EXTRACT")
        else:
            record = _read_json(run_dir / "inference_record.json")
            receipt = record["query_log"][0]
            if receipt["result"].get("error_code") != "backend_unavailable":
                raise ValueError("parser unavailability was not preserved in the query receipt")
            forwarded = _model_payload(model.inputs[2])
            forwarded_receipt = forwarded["query_receipts"][0]
            if forwarded_receipt["result"] != receipt["result"]:
                raise ValueError("parser unavailability did not reach the following model input")
            result["boundary_evidence"] = {
                "receipt_status": receipt["status"],
                "error_code": receipt["result"]["error_code"],
                "forwarded_to_stage": forwarded["stage"],
                "later_result_status": record["result_status"],
            }
        results.append(result)
    return results


def _run_cross_stage_deadline_case(output_dir: Path) -> dict[str, object]:
    import controller

    class VirtualClock:
        value = 100.0

        def monotonic(self) -> float:
            return self.value

    clock = VirtualClock()

    class AdvancingModel(FixedScriptModel):
        async def get_response(self, *args: object, **kwargs: object):
            response = await super().get_response(*args, **kwargs)
            clock.value += 0.04
            return response

    case: dict[str, object] = {
        "name": "boundary-cross-stage-deadline",
        "variant": "B", "replay_case": "cross_stage_deadline", "query_count": 0,
        "expected_status": "INCONCLUSIVE", "expected_stop_reason": "run_timeout",
    }
    original_monotonic = controller.time.monotonic
    try:
        controller.time.monotonic = clock.monotonic
        run_dir = output_dir / str(case["name"])
        asyncio.run(execute_staged_run(
            synthetic_task(), output_dir=run_dir,
            model=AdvancingModel(scripted_outputs()),
            config=StagedRunConfig(timeout_seconds=0.1),
            requested_case_id="synthetic_python_delegation",
        ))
    finally:
        controller.time.monotonic = original_monotonic
    result = _verify_run(case, run_dir)
    record = _read_json(run_dir / "inference_record.json")
    manifest = _read_json(run_dir / "run_manifest.json")
    if len(record["stages"]) < 2 or manifest["error_stage"] != "CONSTRUCT":
        raise ValueError("deadline evidence did not cross completed stage boundaries")
    if manifest["usage"]["requests_started"] != 3:
        raise ValueError("deadline evidence unexpectedly began a finalization request")
    return result


def _verify_run(case: dict[str, object], run_dir: Path) -> dict[str, object]:
    manifest_path = run_dir / "run_manifest.json"
    record_path = run_dir / "inference_record.json"
    manifest = _read_json(manifest_path)
    record = _read_json(record_path)
    if manifest["schema_version"] != MANIFEST_VERSION:
        raise ValueError(f"{case['name']}: unexpected manifest version")
    if record["schema_version"] != RECORD_VERSION:
        raise ValueError(f"{case['name']}: unexpected record version")
    if manifest["run_mode"] != "synthetic_replay" or manifest["adapter_reviewed"] is not False:
        raise ValueError(f"{case['name']}: offline provenance is not explicit")

    identity_fields = (
        "run_id", "requested_case_id", "case_id", "language", "input_hash",
        "result_status", "stop_reason", "error_stage",
    )
    for field in identity_fields:
        if manifest[field] != record[field]:
            raise ValueError(f"{case['name']}: {field} differs between artifacts")
    if manifest["variant"] != case["variant"]:
        raise ValueError(f"{case['name']}: variant differs from the requested replay")
    if manifest["result_status"] != case["expected_status"]:
        raise ValueError(f"{case['name']}: unexpected result status")
    if manifest["stop_reason"] != case["expected_stop_reason"]:
        raise ValueError(f"{case['name']}: unexpected stop reason")
    if manifest["versions"] != {
        "policy": "staged-scenario-inference/v0.2",
        "prompts": "staged-scenario-inference-prompts/v0.2",
        "record": RECORD_VERSION,
        "contract": CONTRACT_VERSION,
        "citation_mode": "source_locator_host_materialized_quote/v1",
    }:
        raise ValueError(f"{case['name']}: version set is incomplete or changed")

    expected_artifacts = {
        "input_snapshot.json", "inference_record.json", "scenario_contract.json",
    }
    if set(manifest["artifact_sha256"]) != expected_artifacts:
        raise ValueError(f"{case['name']}: artifact inventory is incomplete or unexpected")
    artifact_hashes: dict[str, str | None] = {}
    for name, expected_hash in manifest["artifact_sha256"].items():
        artifact_path = run_dir / name
        actual_hash = _sha256(artifact_path) if artifact_path.is_file() else None
        if actual_hash != expected_hash:
            raise ValueError(f"{case['name']}: hash mismatch for {name}")
        artifact_hashes[name] = actual_hash
    input_hash = artifact_hashes.get("input_snapshot.json")
    if input_hash is None or input_hash != manifest["input_hash"]:
        raise ValueError(f"{case['name']}: input snapshot identity is inconsistent")
    snapshot = _read_json(run_dir / "input_snapshot.json")
    if snapshot.get("case_id") != manifest["case_id"]:
        raise ValueError(f"{case['name']}: snapshot case identity differs")
    if snapshot.get("language") != manifest["language"]:
        raise ValueError(f"{case['name']}: snapshot language differs")

    contract_path = run_dir / "scenario_contract.json"
    if contract_path.is_file():
        contract = _read_json(contract_path)
        if contract["schema_version"] != CONTRACT_VERSION:
            raise ValueError(f"{case['name']}: unexpected contract version")
        if contract["case_id"] != manifest["case_id"]:
            raise ValueError(f"{case['name']}: contract case identity differs")
    if (manifest["result_status"] in ("SELECTED", "AMBIGUOUS")) != contract_path.is_file():
        raise ValueError(f"{case['name']}: final-contract publication is inconsistent")

    query_accounting = manifest["query_accounting"]
    expected_queries = case["query_count"]
    if not isinstance(expected_queries, int):
        raise ValueError(f"{case['name']}: query count is not an integer")
    if query_accounting["observed_attempts"] != expected_queries:
        raise ValueError(f"{case['name']}: observed query attempts differ")
    if query_accounting["budgeted_attempts"] != min(expected_queries, 6):
        raise ValueError(f"{case['name']}: budgeted query attempts differ")

    return {
        "name": case["name"],
        "variant": manifest["variant"],
        "replay_case": case["replay_case"],
        "query_count": expected_queries,
        "run_id": manifest["run_id"],
        "requested_case_id": manifest["requested_case_id"],
        "case_id": manifest["case_id"],
        "language": manifest["language"],
        "input_hash": manifest["input_hash"],
        "versions": manifest["versions"],
        "result_status": manifest["result_status"],
        "stop_reason": manifest["stop_reason"],
        "error_stage": manifest["error_stage"],
        "artifact_sha256": artifact_hashes,
        "run_manifest_sha256": _sha256(manifest_path),
    }


def build_acceptance(output_dir: Path) -> dict[str, object]:
    output_dir.mkdir(parents=True, exist_ok=False)
    entry = Path(__file__).with_name("run.py")
    project_root = Path(__file__).parents[2]
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(project_root / "src")
    results: list[dict[str, object]] = []
    for case in _cases():
        run_dir = output_dir / str(case["name"])
        command = [
            sys.executable, str(entry), "--mode", "synthetic_replay",
            "--variant", str(case["variant"]),
            "--replay-case", str(case["replay_case"]),
            "--query-count", str(case["query_count"]),
            "--max-model-calls", "12", "--output-dir", str(run_dir),
        ]
        completed = subprocess.run(
            command, cwd=project_root, env=environment, text=True,
            capture_output=True, check=False,
        )
        if completed.returncode != case["expected_exit_code"]:
            raise RuntimeError(
                f"{case['name']}: exit {completed.returncode}; stderr={completed.stderr.strip()}"
            )
        results.append(_verify_run(case, run_dir))

    results.extend(_run_boundary_cases(output_dir))
    results.append(_run_cross_stage_deadline_case(output_dir))

    report: dict[str, object] = {
        "schema_version": REPORT_VERSION,
        "engineering_acceptance": "passed",
        "run_count": len(results),
        "coverage": {
            "variants": ["B", "C"],
            "result_states": ["SELECTED", "AMBIGUOUS", "INCONCLUSIVE", "ERROR"],
            "query_attempt_counts": [0, 6, 7],
            "boundary_cases": [
                "original_assertion_tamper", "parser_backend_unavailable",
                "cross_stage_total_deadline",
            ],
        },
        "runs": results,
        "limitations": [
            "All runs use synthetic tasks and the fixed offline SDK model.",
            "The evidence does not establish reviewed Adapter admission, target-test execution, or inference quality.",
            "The fixed model does not demonstrate autonomous tool selection.",
            "Exact input locators and host-materialized quotes are protected, but this acceptance cannot prove the semantic faithfulness of an arbitrary free-text goal paraphrase.",
        ],
    }
    _write_json_exclusive(output_dir / "acceptance_report.json", report)
    return report


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        report = build_acceptance(args.output_dir)
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"M1 acceptance failed: {exc}", file=sys.stderr)
        return 1
    print(f"{report['engineering_acceptance']}: {report['run_count']} replay runs")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
