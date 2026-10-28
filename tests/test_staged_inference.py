"""Entry-point acceptance tests for the M1 variant-B staged replay."""

from __future__ import annotations

import asyncio
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


STAGED_ROOT = Path(__file__).parents[1] / "experiments" / "staged-inference"
sys.path.insert(0, str(STAGED_ROOT))

from controller import StagedRunConfig, canonical_json, execute_staged_run  # noqa: E402
from scripted import FixedScriptModel, scripted_outputs, synthetic_task  # noqa: E402


def _run(tmp_path: Path, outputs: list[dict] | None = None):
    model = FixedScriptModel(outputs)
    output_dir = tmp_path / "run"
    result = asyncio.run(execute_staged_run(
        synthetic_task(),
        output_dir=output_dir,
        model=model,
        config=StagedRunConfig(),
        requested_case_id="synthetic_python_delegation",
    ))
    return result, model, output_dir


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _serialized_model_input(value: object) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, default=str)


def _model_payload(value: object) -> dict:
    if isinstance(value, list):
        assert len(value) == 1
        value = value[0]["content"]
    assert isinstance(value, str)
    return json.loads(value)


def test_stage_inputs_expose_exact_source_line_coordinates(tmp_path) -> None:
    task = synthetic_task()
    # Masked input can contain significant whitespace and non-one-based snippets.
    supporting = {
        **task["mut_source"], "source_id": "context",
        "start_line": 101, "end_line": 103,
        "text": "   \nimport example\n\n",
    }
    task["supporting_sources"].append(supporting)
    task["sources"].append({
        "source_id": "context", "logical_path": supporting["logical_path"],
        "role": "supporting",
    })
    model = FixedScriptModel()
    result = asyncio.run(execute_staged_run(
        task, output_dir=tmp_path / "run", model=model,
        config=StagedRunConfig(), requested_case_id=task["case_id"],
    ))
    assert result["manifest"]["result_status"] == "SELECTED"
    for raw in model.inputs:
        payload = _model_payload(raw)
        projected_task = deepcopy(task)
        for source in (
            projected_task["masked_test"], projected_task["mut_source"],
            *projected_task["supporting_sources"],
        ):
            source.pop("text")
        assert payload["visible_task"] == projected_task
        assert payload["source_line_index"]["context"] == [
            {"line": 101, "text": "   "},
            {"line": 102, "text": "import example"},
            {"line": 103, "text": ""},
        ]
        for source in [task["masked_test"], task["mut_source"], *task["supporting_sources"]]:
            indexed = payload["source_line_index"][source["source_id"]]
            assert [row["line"] for row in indexed] == list(
                range(source["start_line"], source["end_line"] + 1)
            )
            assert [row["text"] for row in indexed] == source["text"].splitlines()


def test_legacy_extract_quote_is_rejected_without_conversion(tmp_path):
    outputs = scripted_outputs()
    outputs[0]["original_goals"] = [{
        "source_id": "test:0", "start_line": 5, "end_line": 5,
        "quote": "    assert target(1) == 2",
    }]
    result, model, output_dir = _run(tmp_path, outputs)
    assert result["manifest"]["error_stage"] == "EXTRACT"
    assert result["record"]["stop_reason"] == "output_invalid"
    assert result["record"]["stages"] == []
    assert model.calls == 1
    assert not (output_dir / "scenario_contract.json").exists()


def test_extract_locator_failure_retains_wire_selection(tmp_path):
    outputs = scripted_outputs()
    outputs[0]["original_goals"] = [{
        "source_id": "mut:0", "start_line": 9, "end_line": 9,
    }]
    result, model, output_dir = _run(tmp_path, outputs)
    assert result["manifest"]["error_stage"] == "EXTRACT"
    stage = result["record"]["stages"][0]
    assert stage["wire_result"]["original_goals"] == outputs[0]["original_goals"]
    assert stage["wire_result"]["result_status"] == "CONTINUE"
    assert stage["result"] is None
    assert stage["model_call_sequence"] == 1
    assert stage["citation_error"] == {
        "field_path": "original_goals.0",
        "source_id": "mut:0",
        "start_line": 9,
        "end_line": 9,
        "error": "evidence range outside allowed source",
    }
    assert result["record"]["error"]["message"] == (
        "evidence range outside allowed source"
    )
    assert model.calls == 1
    assert not (output_dir / "scenario_contract.json").exists()


def test_variant_b_replay_preserves_identity_stages_and_artifact_hashes(tmp_path) -> None:
    result, model, output_dir = _run(tmp_path)
    task = synthetic_task()

    snapshot = output_dir / "input_snapshot.json"
    record_path = output_dir / "inference_record.json"
    contract_path = output_dir / "scenario_contract.json"
    spec_path = output_dir / "dependency_spec.json"
    manifest_path = output_dir / "run_manifest.json"
    record = _read(record_path)
    contract = _read(contract_path)
    manifest = _read(manifest_path)

    assert snapshot.read_bytes() == canonical_json(task)
    input_hash = hashlib.sha256(canonical_json(task)).hexdigest()
    assert manifest["input_hash"] == record["input_hash"] == input_hash
    assert manifest["run_id"] == record["run_id"]
    assert manifest["case_id"] == record["case_id"] == contract["case_id"]
    assert manifest["language"] == record["language"] == contract["language"]
    assert manifest["result_status"] == record["result_status"] == "SELECTED"
    assert record["selected_scenario_id"] == "dependency_returns_two"
    assert record["selected_scenario_id"] in {
        item["id"] for item in contract["scenario_hypotheses"]
    }
    assert [item["stage"] for item in record["stages"]] == [
        "EXTRACT", "QUERY", "CONSTRUCT", "DELIVER"
    ]
    assert record["schema_version"] == "scenario-inference-record/v0.4"
    assert record["citation_mode"] == "source_locator_host_materialized_quote/v1"
    assert manifest["versions"]["citation_mode"] == record["citation_mode"]
    assert [item["model_call_sequence"] for item in record["stages"]] == [1, 2, 3, 4]
    for stage in record["stages"]:
        assert stage["citation_mode"] == record["citation_mode"]
        assert '"quote"' not in json.dumps(stage["wire_result"])
        assert '"quote"' in json.dumps(stage["result"])
    assert record["query_log"] == []
    assert 1 <= len(record["critical_behaviors"]) <= 3
    assert record["unresolved"] == []
    assert record["revision_log"] == []
    assert record["degeneracy_contrast"] == {
        "status": "disabled_by_variant",
        "execution_status": "not_executed",
        "support": None, "assumption_reason": None, "semantic_role": None,
        "alternative": None,
        "bypass_behavior": None,
        "evidence_ids": [],
        "distinguishing_observation": None,
        "revision_requested": False,
        "revision_reason": None,
    }

    assert manifest["run_mode"] == "synthetic_replay"
    assert manifest["variant"] == "B"
    assert manifest["stop_reason"] == "completed"
    assert manifest["error_stage"] is None
    assert manifest["artifact_sha256"] == {
        "input_snapshot.json": _sha256(snapshot),
        "inference_record.json": _sha256(record_path),
        "scenario_contract.json": _sha256(contract_path),
        "dependency_spec.json": _sha256(spec_path),
    }
    assert "run_manifest.json" not in manifest["artifact_sha256"]
    assert result["record"] == record
    assert result["manifest"]["run_id"] == manifest["run_id"]

    assert model.calls == 4
    assert manifest["usage"]["requests_started"] == 4
    assert manifest["usage"]["requests_completed"] == 4
    assert len(manifest["usage"]["gate_events"]) == 4
    assert all(event["status"] == "allowed"
               for event in manifest["usage"]["gate_events"])

    assert [_model_payload(item)["stage"] for item in model.inputs] == [
        "EXTRACT", "QUERY", "CONSTRUCT", "DELIVER"
    ]
    for model_input in model.inputs:
        payload = _model_payload(model_input)
        projected_task = deepcopy(task)
        for source in (
            projected_task["masked_test"], projected_task["mut_source"],
            *projected_task["supporting_sources"],
        ):
            source.pop("text")
        assert payload["visible_task"] == projected_task
        serialized = _serialized_model_input(model_input)
        assert "reviewed_inputs" not in serialized
        assert "physical_path" not in serialized
        assert "evaluation" not in serialized
        assert str(tmp_path) not in serialized


def test_existing_output_directory_is_rejected_without_another_model_call(tmp_path) -> None:
    _, model, output_dir = _run(tmp_path)
    calls = model.calls

    with pytest.raises(FileExistsError, match="already exists"):
        asyncio.run(execute_staged_run(
            synthetic_task(), output_dir=output_dir, model=model,
            config=StagedRunConfig(),
        ))

    assert model.calls == calls


def test_cli_runs_replay_and_preserves_artifacts_when_repeat_is_rejected(tmp_path) -> None:
    output_dir = tmp_path / "cli-run"
    command = [
        sys.executable,
        str(STAGED_ROOT / "run.py"),
        "--mode", "synthetic_replay",
        "--variant", "B",
        "--output-dir", str(output_dir),
    ]
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(Path(__file__).parents[1] / "src")

    first = subprocess.run(
        command, cwd=Path(__file__).parents[1], env=environment,
        text=True, capture_output=True, check=False,
    )
    assert first.returncode == 0, first.stderr
    original_bytes = {
        path.name: path.read_bytes()
        for path in output_dir.iterdir()
        if path.is_file()
    }

    repeated = subprocess.run(
        command, cwd=Path(__file__).parents[1], env=environment,
        text=True, capture_output=True, check=False,
    )

    assert repeated.returncode != 0
    assert "already exists" in repeated.stderr
    assert {
        path.name: path.read_bytes()
        for path in output_dir.iterdir()
        if path.is_file()
    } == original_bytes


def _invalid_outputs(kind: str) -> tuple[list[dict], str, int]:
    outputs = deepcopy(scripted_outputs())
    if kind == "unknown_source":
        outputs[0]["original_goals"][0]["source_id"] = "unknown:0"
        return outputs, "EXTRACT", 1
    if kind == "bad_quote":
        outputs[0]["original_goals"][0]["quote"] = "invented oracle"
        return outputs, "EXTRACT", 1
    if kind == "out_of_range":
        outputs[0]["original_goals"][0]["start_line"] = 6
        outputs[0]["original_goals"][0]["end_line"] = 6
        return outputs, "EXTRACT", 1

    construct = outputs[2]
    if kind == "case_id":
        construct["contract"]["case_id"] = "forged_case"
    elif kind == "language":
        construct["contract"]["language"] = "java"
    elif kind == "unknown_evidence_id":
        construct["contract"]["test_objectives"][0]["evidence_ids"] = ["missing"]
    elif kind == "critical_behavior_unknown_evidence":
        construct["critical_behaviors"][0]["evidence_ids"] = ["missing"]
    elif kind == "selected_not_candidate":
        construct["selected_scenario_id"] = "missing_candidate"
    elif kind == "selected_hypothesis_unknown":
        construct["contract"]["scenario_hypotheses"][0]["status"] = "unknown"
    elif kind == "three_candidates":
        for suffix in ("two", "three"):
            candidate = deepcopy(construct["contract"]["scenario_hypotheses"][0])
            candidate["id"] = f"dependency_returns_{suffix}"
            candidate["description"] = f"A distinct candidate named {suffix}."
            construct["contract"]["scenario_hypotheses"].append(candidate)
    elif kind == "zero_critical_behaviors":
        construct["critical_behaviors"] = []
        # The self-contained spec must still map every formal dependency constraint.
        construct["spec"]["constraint_basis"] = []
    elif kind == "four_critical_behaviors":
        construct["critical_behaviors"] = construct["critical_behaviors"] * 4
    elif kind == "blocking_unresolved":
        construct["unresolved"] = ["The selected dependency binding is unresolved."]
    elif kind == "deliver_changes_contract":
        # scripted_outputs intentionally shares the contract object between stages;
        # sever that alias so this mutation exercises DELIVER alone.
        outputs[3]["contract"] = deepcopy(outputs[3]["contract"])
        outputs[3]["contract"]["evidence"][0]["rationale"] = (
            "DELIVER attempted to replace the constructed rationale."
        )
        return outputs, "DELIVER", 4
    else:  # pragma: no cover - protects the test table itself
        raise AssertionError(kind)
    return outputs, "CONSTRUCT", 3


@pytest.mark.parametrize("kind", [
    "unknown_source",
    "bad_quote",
    "out_of_range",
    "case_id",
    "language",
    "unknown_evidence_id",
    "critical_behavior_unknown_evidence",
    "selected_not_candidate",
    "selected_hypothesis_unknown",
    "three_candidates",
    "zero_critical_behaviors",
    "four_critical_behaviors",
    "blocking_unresolved",
    "deliver_changes_contract",
])
def test_invalid_model_claims_leave_error_evidence_without_contract(
    tmp_path, kind: str
) -> None:
    outputs, expected_stage, expected_calls = _invalid_outputs(kind)
    result, model, output_dir = _run(tmp_path, outputs)
    record = _read(output_dir / "inference_record.json")
    manifest = _read(output_dir / "run_manifest.json")

    assert model.calls == expected_calls
    assert result["contract"] is None
    assert record["result_status"] == manifest["result_status"] == "ERROR"
    assert record["selected_scenario_id"] is None
    assert record["error"] is not None
    assert manifest["stop_reason"] == "output_invalid"
    assert manifest["error_stage"] == expected_stage
    assert manifest["artifact_sha256"]["inference_record.json"] == _sha256(
        output_dir / "inference_record.json"
    )
    assert manifest["artifact_sha256"]["scenario_contract.json"] is None
    assert not (output_dir / "scenario_contract.json").exists()


def test_missing_configuration_leaves_evidence_without_model_use(tmp_path) -> None:
    model = FixedScriptModel()
    output_dir = tmp_path / "missing-config"

    result = asyncio.run(execute_staged_run(
        synthetic_task(), output_dir=output_dir, model=model, config=None,
    ))

    assert result["contract"] is None
    assert result["record"]["error"]["type"] == "StagedInferenceError"
    assert result["manifest"]["result_status"] == "ERROR"
    assert result["manifest"]["stop_reason"] == "input_invalid"
    assert result["manifest"]["error_stage"] == "CONFIG"
    assert result["manifest"]["configuration"] is None
    assert result["manifest"]["budget"] is None
    assert result["manifest"]["usage"] is None
    assert (output_dir / "inference_record.json").is_file()
    assert (output_dir / "run_manifest.json").is_file()
    assert not (output_dir / "scenario_contract.json").exists()
    assert model.calls == 0


def test_invalid_configuration_object_is_rejected_before_model_use() -> None:
    model = FixedScriptModel()

    with pytest.raises(ValueError, match="model_name is required"):
        StagedRunConfig(model_name=" ")

    assert model.calls == 0


def test_model_call_budget_is_shared_across_stages(tmp_path) -> None:
    model = FixedScriptModel()
    output_dir = tmp_path / "budgeted-run"

    result = asyncio.run(execute_staged_run(
        synthetic_task(), output_dir=output_dir, model=model,
        config=StagedRunConfig(max_model_calls=3),
    ))

    assert model.calls == 3
    assert result["contract"] is None
    assert result["manifest"]["result_status"] == "INCONCLUSIVE"
    assert result["manifest"]["stop_reason"] == "budget_exhausted"
    assert result["manifest"]["usage"]["requests_started"] == 3
    assert not (output_dir / "scenario_contract.json").exists()


def test_total_token_gate_blocks_first_request_and_leaves_evidence(tmp_path) -> None:
    model = FixedScriptModel()
    output_dir = tmp_path / "token-budget-run"

    result = asyncio.run(execute_staged_run(
        synthetic_task(), output_dir=output_dir, model=model,
        config=StagedRunConfig(max_output_tokens=100, max_total_tokens=100),
    ))

    usage = result["manifest"]["usage"]
    assert model.calls == 0
    assert result["contract"] is None
    assert result["manifest"]["result_status"] == "INCONCLUSIVE"
    assert result["manifest"]["stop_reason"] == "budget_exhausted"
    assert result["manifest"]["error_stage"] == "EXTRACT"
    assert usage["requests_started"] == usage["requests_completed"] == 0
    assert usage["gate_events"] == [{
        "sequence": 1,
        "stage": "before_model_call",
        "status": "blocked",
        "reason": "projected_token_limit",
    }]
    assert not (output_dir / "scenario_contract.json").exists()


def _assert_artifacts(output_dir: Path) -> tuple[dict, dict]:
    record = _read(output_dir / "inference_record.json")
    manifest = _read(output_dir / "run_manifest.json")
    for field in ("run_id", "case_id", "language", "input_hash", "result_status",
                  "stop_reason", "error_stage", "requested_case_id"):
        assert record[field] == manifest[field]
    for name, expected in manifest["artifact_sha256"].items():
        path = output_dir / name
        assert expected == (_sha256(path) if path.exists() else None)
    return record, manifest


@pytest.mark.parametrize("case,status,has_contract", [
    ("selected", "SELECTED", True),
    ("ambiguous", "AMBIGUOUS", True),
    ("inconclusive", "INCONCLUSIVE", False),
    ("error", "ERROR", False),
])
def test_four_results_through_cli(tmp_path, case, status, has_contract) -> None:
    output_dir = tmp_path / case
    process = subprocess.run([
        sys.executable, str(STAGED_ROOT / "run.py"),
        "--mode", "synthetic_replay", "--variant", "B",
        "--replay-case", case, "--output-dir", str(output_dir),
    ], env={**os.environ, "PYTHONPATH": str(STAGED_ROOT.parents[1] / "src")},
        capture_output=True, text=True, check=False)
    assert process.returncode == (1 if status == "ERROR" else 0), process.stderr
    record, manifest = _assert_artifacts(output_dir)
    assert record["result_status"] == status
    assert (output_dir / "scenario_contract.json").exists() == has_contract
    if status != "SELECTED":
        assert record["selected_scenario_id"] is None
    assert [stage["stage"] for stage in record["stages"]] == [
        "EXTRACT", "QUERY", "CONSTRUCT", "DELIVER"
    ]
    if status == "AMBIGUOUS":
        contract = _read(output_dir / "scenario_contract.json")
        assert len(contract["scenario_hypotheses"]) == 2
        assert all(item["status"] == "candidate" for item in contract["scenario_hypotheses"])
        assert record["selection_rationale"]
    if status == "INCONCLUSIVE":
        assert record["unresolved"]
        assert manifest["stop_reason"] == "evidence_exhausted"
    if status == "ERROR":
        assert record["error"]
        assert manifest["error_stage"] == "DELIVER"


@pytest.mark.parametrize("contents", ["{", "[]", '{"case_id":"unreviewed"}'])
def test_cli_rejected_input_has_registered_attempt(tmp_path, contents) -> None:
    source = tmp_path / "input.json"
    source.write_text(contents)
    output_dir = tmp_path / "rejected"
    process = subprocess.run([
        sys.executable, str(STAGED_ROOT / "run.py"),
        "--mode", "synthetic_replay", "--variant", "B",
        "--task-json", str(source), "--requested-case-id", "requested-unreviewed",
        "--output-dir", str(output_dir),
    ], env={**os.environ, "PYTHONPATH": str(STAGED_ROOT.parents[1] / "src")},
        capture_output=True, text=True, check=False)
    assert process.returncode == 1, process.stderr
    record, manifest = _assert_artifacts(output_dir)
    assert record["run_id"]
    assert record["requested_case_id"] == "requested-unreviewed"
    assert record["case_id"] is record["input_hash"] is None
    assert record["error"]
    assert manifest["stop_reason"] == "input_invalid"
    assert manifest["artifact_sha256"]["input_snapshot.json"] is None


@pytest.mark.parametrize("kind", [
    "ambiguous_selected", "ambiguous_one", "ambiguous_unknown", "ambiguous_no_contract",
    "inconclusive_selected", "inconclusive_no_reason", "deliver_status",
    "host_run_id", "host_usage", "host_stop_reason",
])
def test_result_invariants_reject_model_forgery(tmp_path, kind) -> None:
    case = "inconclusive" if kind.startswith("inconclusive") else "ambiguous"
    outputs = deepcopy(scripted_outputs(case))
    construct = outputs[2]
    if kind in ("ambiguous_selected", "inconclusive_selected"):
        construct["selected_scenario_id"] = "dependency_returns_two"
    elif kind == "ambiguous_one":
        construct["contract"]["scenario_hypotheses"] = construct["contract"]["scenario_hypotheses"][:1]
    elif kind == "ambiguous_unknown":
        construct["contract"]["scenario_hypotheses"][1]["status"] = "unknown"
    elif kind == "ambiguous_no_contract":
        construct["contract"] = None
    elif kind == "inconclusive_no_reason":
        construct["unresolved"] = []
    elif kind == "deliver_status":
        outputs[3]["result_status"] = "INCONCLUSIVE"
    elif kind == "host_run_id":
        construct["run_id"] = "forged"
    elif kind == "host_usage":
        construct["usage"] = {"requests_started": 0}
    elif kind == "host_stop_reason":
        construct["stop_reason"] = "completed"
    result, _, output_dir = _run(tmp_path, outputs)
    assert result["contract"] is None
    record, manifest = _assert_artifacts(output_dir)
    assert record["result_status"] == "ERROR"
    assert record["selected_scenario_id"] is None
    assert manifest["stop_reason"] == "output_invalid"
    assert not (output_dir / "scenario_contract.json").exists()


def test_inconclusive_can_retain_valid_diagnostic_contract(tmp_path) -> None:
    outputs = deepcopy(scripted_outputs("ambiguous"))
    for output in outputs[2:]:
        output["result_status"] = "INCONCLUSIVE"
        output["selected_scenario_id"] = None
    result, _, output_dir = _run(tmp_path, outputs)
    record, manifest = _assert_artifacts(output_dir)
    assert record["result_status"] == "INCONCLUSIVE"
    assert result["contract"] is not None
    assert manifest["artifact_sha256"]["scenario_contract.json"]


@pytest.mark.parametrize("filename", [
    "input_snapshot.json", "inference_record.json", "scenario_contract.json", "run_manifest.json",
])
def test_artifact_write_failure_is_visible_and_manifest_never_claims_complete(
    tmp_path, monkeypatch, capsys, filename
) -> None:
    import controller
    import run as entry

    original = controller._write_json_exclusive
    def fail_write(path, value, **kwargs):
        if path.name == filename:
            raise OSError("injected storage failure")
        return original(path, value, **kwargs)
    monkeypatch.setattr(controller, "_write_json_exclusive", fail_write)
    output_dir = tmp_path / "write-failure"
    code = entry.main([
        "--mode", "synthetic_replay", "--variant", "B", "--output-dir", str(output_dir),
    ])
    assert code != 0
    assert capsys.readouterr().err
    if (output_dir / "run_manifest.json").exists():
        record, manifest = _assert_artifacts(output_dir)
        assert record["result_status"] == "ERROR"
        assert manifest["stop_reason"] == "artifact_error"


def test_output_directory_creation_failure_is_non_success(tmp_path, capsys) -> None:
    import run as entry

    parent = tmp_path / "file"
    parent.write_text("preserve")
    assert entry.main([
        "--mode", "synthetic_replay", "--variant", "B",
        "--output-dir", str(parent / "impossible"),
    ]) == 2
    assert capsys.readouterr().err
    assert parent.read_text() == "preserve"


def test_manifest_is_last_publication(tmp_path, monkeypatch) -> None:
    import controller

    original = controller._write_json_exclusive
    written = []
    def observe(path, value, **kwargs):
        original(path, value, **kwargs)
        written.append(path.name)
    monkeypatch.setattr(controller, "_write_json_exclusive", observe)
    _run(tmp_path)
    assert written[-1] == "run_manifest.json"


def test_model_transport_failure_is_error_not_semantic_unknown(tmp_path) -> None:
    class FailingModel(FixedScriptModel):
        async def get_response(self, *args, **kwargs):
            raise ConnectionError("injected transport failure")

    output_dir = tmp_path / "model-failure"
    result = asyncio.run(execute_staged_run(
        synthetic_task(), output_dir=output_dir, model=FailingModel(),
        config=StagedRunConfig(),
    ))
    record, manifest = _assert_artifacts(output_dir)
    assert result["contract"] is None
    assert record["result_status"] == "ERROR"
    assert manifest["stop_reason"] == "model_error"
    assert manifest["error_stage"] == "EXTRACT"
    assert manifest["usage"]["requests_started"] == 1
    assert manifest["usage"]["requests_completed"] == 0


@pytest.mark.parametrize("case", ["ambiguous", "inconclusive"])
def test_budget_exhaustion_keeps_draft_without_publishing_contract(tmp_path, case) -> None:
    output_dir = tmp_path / case
    result = asyncio.run(execute_staged_run(
        synthetic_task(), output_dir=output_dir,
        model=FixedScriptModel(scripted_outputs(case)),
        config=StagedRunConfig(max_model_calls=3),
    ))
    record, manifest = _assert_artifacts(output_dir)
    assert record["result_status"] == "INCONCLUSIVE"
    assert record["selected_scenario_id"] is None
    assert manifest["stop_reason"] == "budget_exhausted"
    assert result["contract"] is None
    assert any(stage["stage"] == "CONSTRUCT" for stage in record["stages"])


def test_missing_input_file_is_registered_before_read(tmp_path) -> None:
    import run as entry

    output_dir = tmp_path / "missing-input"
    assert entry.main([
        "--mode", "synthetic_replay", "--variant", "B",
        "--task-json", str(tmp_path / "absent.json"),
        "--requested-case-id", "requested", "--output-dir", str(output_dir),
    ]) == 1
    record, manifest = _assert_artifacts(output_dir)
    assert record["case_id"] is None
    assert record["requested_case_id"] == "requested"
    assert manifest["stop_reason"] == "input_invalid"


def test_selected_allows_explicit_nonblocking_unknowns(tmp_path) -> None:
    outputs = deepcopy(scripted_outputs())
    outputs[2]["nonblocking_unknowns"] = [
        "Unobserved dependency cache internals are unknown and do not affect the visible result."
    ]
    _, _, output_dir = _run(tmp_path, outputs)
    record, _ = _assert_artifacts(output_dir)
    assert record["result_status"] == "SELECTED"
    assert record["nonblocking_unknowns"] == outputs[2]["nonblocking_unknowns"]
    assert record["unresolved"] == []


def test_extract_inconclusive_marker_does_not_shrink_later_investigation(tmp_path) -> None:
    outputs = deepcopy(scripted_outputs("inconclusive"))
    outputs[0].update({
        "result_status": "INCONCLUSIVE", "candidates": [],
    })
    result, model, output_dir = _run(tmp_path, outputs)
    record, manifest = _assert_artifacts(output_dir)
    assert model.calls == 4
    assert result["contract"] is None
    assert record["result_status"] == "INCONCLUSIVE"
    assert record["unresolved"] == outputs[0]["unresolved"]
    assert [stage["stage"] for stage in record["stages"]] == [
        "EXTRACT", "QUERY", "CONSTRUCT", "DELIVER",
    ]
    assert manifest["stop_reason"] == "evidence_exhausted"
    assert manifest["artifact_sha256"]["scenario_contract.json"] is None


def test_input_loader_rejection_retains_request_and_never_calls_model(tmp_path) -> None:
    output_dir = tmp_path / "review-rejection"
    model = FixedScriptModel()
    def reject():
        assert output_dir.is_dir()
        raise ValueError("selected snapshot is not approved")
    result = asyncio.run(execute_staged_run(
        output_dir=output_dir, model=model, config=StagedRunConfig(),
        requested_case_id="requested_case", task_loader=reject,
    ))
    record, manifest = _assert_artifacts(output_dir)
    assert model.calls == 0
    assert result["contract"] is None
    assert record["case_id"] is record["input_hash"] is None
    assert record["requested_case_id"] == "requested_case"
    assert "not approved" in record["error"]["message"]
    assert manifest["stop_reason"] == "input_invalid"


@pytest.mark.parametrize("count,seen,status", [(0, 0, "SELECTED"), (6, 6, "SELECTED"), (7, 7, "INCONCLUSIVE")])
def test_query_limits_through_cli(tmp_path, count, seen, status):
    output_dir = tmp_path / f"queries-{count}"
    process = subprocess.run([
        sys.executable, str(STAGED_ROOT / "run.py"), "--mode", "synthetic_replay",
        "--variant", "B", "--query-count", str(count), "--output-dir", str(output_dir),
    ], env={**os.environ, "PYTHONPATH": str(STAGED_ROOT.parents[1] / "src")},
        capture_output=True, text=True, check=False)
    assert process.returncode == 0, process.stderr
    record, manifest = _assert_artifacts(output_dir)
    assert record["result_status"] == status
    assert record["query_accounting"]["observed_attempts"] == seen
    assert record["query_accounting"]["budgeted_attempts"] == min(seen, 6)
    assert manifest["configuration"]["max_query_attempts"] == 6
    assert manifest["budget"]["max_tool_calls"] == 6
    if count:
        assert {r["tool"] for r in record["query_log"][:6]} == {
            "inspect_source", "inspect_program_structure", "inspect_dependency_usage",
        }
        assert all(r["assessment"] is not None for r in record["query_log"][:6])
    if count == 7:
        assert record["query_log"][-1]["status"] == "budget_exhausted"
        assert record["query_log"][-1]["assessment"] is None
        assert manifest["stop_reason"] == "budget_exhausted"
        assert manifest["usage"]["requests_started"] == 8
    else:
        assert manifest["usage"]["requests_started"] == 4 + count


def _query_run(tmp_path, outputs, *, model=None, config=None):
    model = FixedScriptModel(outputs) if model is None else model
    output_dir = tmp_path / "query-run"
    result = asyncio.run(execute_staged_run(
        synthetic_task(), output_dir=output_dir, model=model,
        config=StagedRunConfig() if config is None else config,
    ))
    _assert_artifacts(output_dir)
    return result, model


@pytest.mark.parametrize("kind", [
    "json", "missing_argument", "wrong_type", "unknown_source", "range", "path",
    "unknown_tool", "missing_question", "missing_candidate", "unknown_candidate",
    "wrong_envelope", "null_envelope", "extra_path", "duplicate_argument",
])
def test_invalid_query_attempt_is_counted_and_receipt_reaches_model(tmp_path, kind):
    from scripted import query_script
    outputs = query_script(1)
    request = outputs[1]["query_attempt"]
    arguments = {"source_id": "mut:0", "start_line": 10, "end_line": 11}
    if kind == "json":
        request["arguments_json"] = "{"
    elif kind == "missing_argument":
        del arguments["end_line"]
    elif kind == "wrong_type":
        arguments["start_line"] = True
    elif kind == "unknown_source":
        arguments["source_id"] = "unknown"
    elif kind == "range":
        arguments["start_line"] = 1
    elif kind == "path":
        arguments["source_id"] = "/etc/passwd"
    elif kind == "unknown_tool":
        request["tool"] = "inspect_source_renamed"
    elif kind == "missing_question":
        del request["question"]
    elif kind == "missing_candidate":
        del request["scenario_id"]
    elif kind == "unknown_candidate":
        request["scenario_id"] = "invented"
    elif kind == "wrong_envelope":
        outputs[1]["query_attempt"] = 42
    elif kind == "null_envelope":
        outputs[1]["query_attempt"] = None
    elif kind == "extra_path":
        arguments["path"] = "/etc/passwd"
    elif kind == "duplicate_argument":
        request["arguments_json"] = '{"source_id":"other","source_id":"mut:0","start_line":10,"end_line":11}'
    if kind not in {"json", "duplicate_argument"}:
        request["arguments_json"] = json.dumps(arguments)
    result, model = _query_run(tmp_path, outputs)
    record = result["record"]
    if kind in {"missing_question", "missing_candidate", "wrong_envelope"}:
        assert record["result_status"] == "ERROR"
        assert record["stop_reason"] == "output_invalid"
        assert model.calls == 2
        assert record["query_accounting"]["observed_attempts"] == 1
        assert record["query_log"][0]["status"] == "protocol_error"
        assert record["format_repair_log"][0]["outcome"] == "ineligible"
        return
    assert record["result_status"] == "SELECTED", record.get("error")
    assert record["query_accounting"]["budgeted_attempts"] == 1
    receipt = record["query_log"][0]
    assert receipt["result"]["status"] == "error"
    assert receipt["assessment"]["impact"] == "no_new_information"
    received = _model_payload(model.inputs[2])["query_receipts"]
    assert received[0]["result"] == receipt["result"]
    assert _model_payload(model.inputs[3])["query_receipts"][0]["assessment"] == receipt["assessment"]


def test_failed_retries_and_renamed_tools_share_six_attempts(tmp_path, monkeypatch):
    from scripted import query_script
    from mock_spec.source_analysis import SourceAnalysis
    calls = []
    async def fail(self, **kwargs):
        calls.append(kwargs)
        raise RuntimeError("injected backend failure")
    monkeypatch.setattr(SourceAnalysis, "inspect_source", fail)
    outputs = query_script(7)
    for item in outputs[1:8]:
        item["query_attempt"]["tool"] = "inspect_source"
        item["query_attempt"]["arguments_json"] = json.dumps({
            "source_id": "mut:0", "start_line": 10, "end_line": 11,
        })
    outputs[3]["query_attempt"]["tool"] = "renamed"
    result, model = _query_run(tmp_path, outputs)
    assert len(calls) == 5
    assert len(result["record"]["query_log"]) == 7
    assert result["record"]["query_accounting"]["budgeted_attempts"] == 6
    assert model.calls == 8


def test_unknown_model_usage_blocks_next_request_and_query(tmp_path):
    from agents.usage import Usage
    from scripted import query_script
    class UnknownUsage(FixedScriptModel):
        async def get_response(self, *args, **kwargs):
            response = await super().get_response(*args, **kwargs)
            if self.calls == 2:
                response.usage = Usage()
            return response
    model = UnknownUsage(query_script(1))
    result, _ = _query_run(tmp_path, [], model=model)
    usage = result["manifest"]["usage"]
    assert model.calls == 2
    assert not usage["usage_complete"]
    assert usage["total_tokens"] is None
    assert usage["rate_estimated_cost_usd"] is None
    assert result["manifest"]["stop_reason"] == "budget_exhausted"
    receipt = result["record"]["query_log"][0]
    assert receipt["result"]["status"] == "error"
    assert receipt["assessment_status"] == "incomplete"


def test_query_budget_stop_keeps_unfinished_assessment(tmp_path):
    from scripted import query_script
    result, model = _query_run(tmp_path, query_script(1), config=StagedRunConfig(max_model_calls=2))
    assert model.calls == 2
    assert result["manifest"]["stop_reason"] == "budget_exhausted"
    assert result["record"]["query_log"][0]["assessment"] is None
    assert result["record"]["result_status"] == "INCONCLUSIVE"


def test_model_timeout_has_incomplete_usage_and_no_finalization_request(tmp_path):
    class SlowModel(FixedScriptModel):
        cleaned = False
        async def get_response(self, *args, **kwargs):
            try:
                await asyncio.sleep(60)
            finally:
                self.cleaned = True
    model = SlowModel()
    result, _ = _query_run(tmp_path, [], model=model, config=StagedRunConfig(timeout_seconds=0.05))
    assert model.cleaned
    assert result["manifest"]["stop_reason"] == "run_timeout"
    assert result["manifest"]["usage"]["requests_started"] == 1
    assert result["manifest"]["usage"]["requests_completed"] == 0
    assert result["manifest"]["usage"]["total_tokens"] is None


def test_tool_run_deadline_cancels_and_preserves_receipt(tmp_path, monkeypatch):
    from scripted import query_script
    from mock_spec.source_analysis import SourceAnalysis
    cleaned = []
    async def slow(self, **kwargs):
        try:
            await asyncio.sleep(60)
        finally:
            cleaned.append(True)
    monkeypatch.setattr(SourceAnalysis, "inspect_source", slow)
    result, model = _query_run(tmp_path, query_script(1), config=StagedRunConfig(timeout_seconds=0.2))
    assert cleaned == [True]
    assert model.calls == 2
    assert result["manifest"]["stop_reason"] == "run_timeout"
    assert result["record"]["query_log"][0]["status"] == "timeout"
    assert result["record"]["query_log"][0]["assessment"] is None


@pytest.mark.parametrize("impact", ["supports", "weakens", "no_new_information"])
def test_model_assessments_are_preserved(tmp_path, impact):
    from scripted import query_script
    outputs = query_script(1)
    outputs[2]["previous_receipt_assessment"]["impact"] = impact
    result, _ = _query_run(tmp_path, outputs)
    receipt = result["record"]["query_log"][0]
    assert receipt["assessment"]["impact"] == impact
    assert receipt["assessment_status"] == "complete"
    assert receipt["result"]["text"] == synthetic_task()["mut_source"]["text"]


@pytest.mark.parametrize("stage_index", [0, 3, 4])
def test_query_action_outside_query_stage_is_rejected(tmp_path, stage_index, monkeypatch):
    from scripted import query_script
    from mock_spec.source_analysis import SourceAnalysis
    calls = []
    original = SourceAnalysis.inspect_source
    async def observe(self, **kwargs):
        calls.append(kwargs)
        return await original(self, **kwargs)
    monkeypatch.setattr(SourceAnalysis, "inspect_source", observe)
    outputs = query_script(1)
    outputs[stage_index]["query_attempt"] = deepcopy(outputs[1]["query_attempt"])
    result, _ = _query_run(tmp_path, outputs)
    assert result["manifest"]["stop_reason"] == "output_invalid"
    assert result["record"]["result_status"] == "ERROR"
    assert len(calls) == (0 if stage_index == 0 else 1)


def test_missing_receipt_assessment_is_explicitly_incomplete(tmp_path):
    from scripted import query_script
    outputs = query_script(1)
    outputs[2]["previous_receipt_assessment"] = None
    result, model = _query_run(tmp_path, outputs)
    assert model.calls == 3
    assert result["manifest"]["stop_reason"] == "output_invalid"
    receipt = result["record"]["query_log"][0]
    assert receipt["assessment"] is None
    assert receipt["assessment_status"] == "incomplete"


def test_configured_result_size_reaches_analysis_and_manifest(tmp_path):
    from scripted import query_script
    task = synthetic_task()
    task["mut_source"]["text"] = "def target(value):\n    return dependency(value)" + " " * 1000
    outputs = query_script(1)
    # Stop after the receipt to avoid requiring an unrelated new contract fixture.
    result = asyncio.run(execute_staged_run(
        task, output_dir=tmp_path / "bounded", model=FixedScriptModel(outputs),
        config=StagedRunConfig(max_tool_output_chars=512, max_model_calls=2),
    ))
    receipt = result["record"]["query_log"][0]["result"]
    assert receipt["error_code"] == "output_limit"
    assert len(json.dumps(receipt, ensure_ascii=False)) <= 512
    assert result["manifest"]["configuration"]["max_tool_output_chars"] == 512
    assert result["manifest"]["usage"]["estimates"][0]["limitations"]


def test_research_cannot_raise_tool_limit_and_production_default_is_eight():
    from mock_spec.budget import InferenceBudget
    with pytest.raises(ValueError, match="six"):
        StagedRunConfig(max_query_attempts=7)
    assert InferenceBudget().max_tool_calls == 8


@pytest.mark.parametrize("assessment", [None, "invalid", {"attempt_sequence": "one"}])
def test_next_attempt_counted_before_invalid_assessment_rejection(tmp_path, monkeypatch, assessment):
    from scripted import query_script
    from mock_spec.source_analysis import SourceAnalysis
    calls = []
    original = SourceAnalysis.inspect_source
    async def observe(self, **kwargs):
        calls.append(kwargs)
        return await original(self, **kwargs)
    monkeypatch.setattr(SourceAnalysis, "inspect_source", observe)
    outputs = query_script(2)
    outputs[2]["previous_receipt_assessment"] = assessment
    outputs[2]["query_attempt"] = deepcopy(outputs[1]["query_attempt"])
    result, model = _query_run(tmp_path, outputs)
    assert model.calls == 3
    assert len(calls) == 1
    assert result["record"]["result_status"] == "ERROR"
    assert result["manifest"]["stop_reason"] == "output_invalid"
    assert result["record"]["query_accounting"]["observed_attempts"] == 2
    assert result["record"]["query_accounting"]["budgeted_attempts"] == 2
    receipts = result["record"]["query_log"]
    assert receipts[0]["assessment_status"] == "incomplete"
    assert receipts[1]["result"]["status"] == "error"


def test_exact_total_token_limit_stops_tool_execution(tmp_path, monkeypatch):
    from agents.usage import Usage
    from scripted import query_script
    from mock_spec.source_analysis import SourceAnalysis
    calls = []
    async def forbidden(self, **kwargs):
        calls.append(kwargs)
        raise AssertionError("tool must not execute after token exhaustion")
    monkeypatch.setattr(SourceAnalysis, "inspect_source", forbidden)
    class Exhausted(FixedScriptModel):
        async def get_response(self, *args, **kwargs):
            response = await super().get_response(*args, **kwargs)
            if self.calls == 2:
                response.usage = Usage(requests=1, input_tokens=59700, output_tokens=100, total_tokens=59800)
            return response
    model = Exhausted(query_script(1))
    result, _ = _query_run(tmp_path, [], model=model)
    assert model.calls == 2
    assert calls == []
    assert result["manifest"]["stop_reason"] == "budget_exhausted"
    assert result["manifest"]["usage"]["total_tokens"] == 60000
    assert result["record"]["query_accounting"]["observed_attempts"] == 1


def test_finish_with_query_attempt_is_logged_but_never_executed(tmp_path, monkeypatch):
    from scripted import query_script
    from mock_spec.source_analysis import SourceAnalysis
    calls = []
    async def forbidden(self, **kwargs):
        calls.append(kwargs)
        raise AssertionError("contradictory finish must not execute")
    monkeypatch.setattr(SourceAnalysis, "inspect_source", forbidden)
    outputs = query_script(1)
    outputs[1]["decision"] = "finish_queries"
    result, _ = _query_run(tmp_path, outputs)
    assert calls == []
    assert result["manifest"]["stop_reason"] == "output_invalid"
    assert result["record"]["query_accounting"]["observed_attempts"] == 1
    assert result["record"]["query_log"][0]["status"] == "protocol_error"
