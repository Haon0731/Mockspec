"""Delivery repair preserves a complete static semantic snapshot (AC-08)."""
from __future__ import annotations

import asyncio
from copy import deepcopy
from pathlib import Path
import sys
import time

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "experiments" / "staged-inference"))
from controller import (  # noqa: E402
    FormatRepairAllowance, StagedInferenceError, StagedRunConfig,
    _repair_query_output, execute_staged_run,
)
from stage_protocol import materialize_contract  # noqa: E402
from mock_spec.budget import InferenceBudget, UsageLedger  # noqa: E402
from scripted import FixedScriptModel, scripted_outputs, synthetic_task  # noqa: E402
from mock_spec.schemas import UnifiedTask  # noqa: E402
from mock_spec.validation import SourceStore  # noqa: E402


def run_repair(tmp_path, *, damage, repair=None, max_calls=10):
    outputs = [deepcopy(item) for item in scripted_outputs()]
    expected = deepcopy(outputs[-1])
    damage(outputs[-1])
    outputs.append(deepcopy(expected) if repair is None else repair(deepcopy(expected)))
    model = FixedScriptModel(outputs)
    result = asyncio.run(execute_staged_run(
        synthetic_task(), output_dir=tmp_path / "run", model=model,
        config=StagedRunConfig(max_model_calls=max_calls),
    ))
    expected_contract = materialize_contract(
        expected["contract"], SourceStore(UnifiedTask.from_dict(synthetic_task()))
    )
    return result, model, expected_contract


@pytest.mark.parametrize("damage", [
    lambda value: value.pop("schema_version"),
    lambda value: value["contract"].update(schema_version="wrong-envelope"),
    lambda value: value["contract"]["evidence"][0].update(start_line="11", end_line="11"),
])
def test_repair_preserves_contract_selection_and_evidence(tmp_path, damage):
    result, model, expected = run_repair(tmp_path, damage=damage)
    assert result["record"]["result_status"] == "SELECTED"
    assert model.calls == 5
    assert result["manifest"]["usage"]["requests_started"] == 5
    assert result["contract"] == expected
    log = result["record"]["format_repair_log"]
    assert len(log) == 1 and log[0]["status"] == "completed"
    assert log[0]["semantic_snapshot"] == log[0]["after"]
    assert result["record"]["revision_log"] == []


@pytest.mark.parametrize("damage", [
    lambda value: value.pop("contract"),
    lambda value: value["contract"]["evidence"][0].update(quote="invented"),
    lambda value: value["contract"]["evidence"][0].update(start_line="10"),
    lambda value: value["contract"]["evidence"][0].update(source_id="other:0"),
    lambda value: value["contract"]["scenario_hypotheses"][0].update(condition="new condition"),
])
def test_no_repair_without_unchanged_complete_snapshot(tmp_path, damage):
    result, model, _ = run_repair(tmp_path, damage=damage)
    assert result["record"]["result_status"] == "ERROR"
    assert result["record"]["stop_reason"] == "output_invalid"
    assert model.calls == 4
    assert not (tmp_path / "run" / "scenario_contract.json").exists()


def test_unknown_delivery_locator_is_a_citation_failure_not_a_format_repair(tmp_path):
    result, model, _ = run_repair(
        tmp_path,
        damage=lambda value: value["contract"]["evidence"][0].update(
            source_id="unknown:0"
        ),
    )

    assert model.calls == 4
    assert result["record"]["format_repair_log"] == []
    stage = result["record"]["stages"][-1]
    assert stage["stage"] == "DELIVER"
    assert stage["result"] is None
    assert stage["citation_error"] == {
        "field_path": "contract.evidence.0",
        "source_id": "unknown:0",
        "start_line": 11,
        "end_line": 11,
        "error": "unknown source_id",
    }


@pytest.mark.parametrize("field", ["test_objectives", "scenario_hypotheses",
                                    "dependency_constraints", "expected_interactions",
                                    "verification_candidates", "evidence"])
def test_repair_cannot_change_any_contract_semantics(tmp_path, field):
    def mutate(value):
        if field == "verification_candidates":
            claim = deepcopy(value["contract"]["test_objectives"][0])
            claim["status"] = "candidate"
            value["contract"][field].append(claim)
        else:
            item = value["contract"][field][0]
            key = "rationale" if field == "evidence" else (
                "description" if field == "scenario_hypotheses" else "statement"
            )
            item[key] = "changed semantics"
        return value
    result, model, _ = run_repair(
        tmp_path, damage=lambda value: value.pop("schema_version"), repair=mutate,
    )
    assert model.calls == 5
    assert result["record"]["result_status"] == "ERROR"
    assert result["record"]["stop_reason"] == "output_invalid"
    assert result["record"]["format_repair_log"][0]["after"] is not None
    assert not (tmp_path / "run" / "scenario_contract.json").exists()


def test_still_invalid_repair_is_not_retried(tmp_path):
    def invalid(value):
        value.pop("schema_version")
        return value
    result, model, _ = run_repair(
        tmp_path, damage=lambda value: value.pop("schema_version"), repair=invalid,
    )
    assert model.calls == 5
    assert result["record"]["result_status"] == "ERROR"
    assert result["record"]["error_stage"] == "FORMAT_REPAIR"
    assert len(result["record"]["format_repair_log"]) == 1


def test_shared_budget_does_not_publish_known_invalid_delivery(tmp_path):
    result, model, _ = run_repair(
        tmp_path, damage=lambda value: value.pop("schema_version"), max_calls=4,
    )
    assert model.calls == 4
    assert result["record"]["result_status"] == "INCONCLUSIVE"
    assert result["record"]["stop_reason"] == "budget_exhausted"
    assert not (tmp_path / "run" / "scenario_contract.json").exists()


def test_malformed_json_cannot_trigger_repair(tmp_path):
    from scripted import _response

    class BrokenJsonModel(FixedScriptModel):
        async def get_response(self, *args, **kwargs):
            response = await super().get_response(*args, **kwargs)
            if self.calls == 4:
                response = _response({})
                response.output[0].content[0].text = '{"contract":'
            return response

    model = BrokenJsonModel()
    result = asyncio.run(execute_staged_run(
        synthetic_task(), output_dir=tmp_path / "run", model=model, config=StagedRunConfig(),
    ))
    assert model.calls == 4
    assert result["record"]["result_status"] == "ERROR"
    assert result["record"]["format_repair_log"] == []
    assert result["record"]["query_log"] == []


def test_semantic_revision_and_format_repair_have_separate_logs(tmp_path):
    from scripted import task_for_replay_case

    outputs = [deepcopy(item) for item in scripted_outputs("legal_variation", variant="C")]
    repaired = deepcopy(outputs[-1])
    outputs[-1].pop("schema_version")
    outputs.append(repaired)
    model = FixedScriptModel(outputs)
    result = asyncio.run(execute_staged_run(
        task_for_replay_case("legal_variation"), output_dir=tmp_path / "run", model=model,
        config=StagedRunConfig(variant="C"),
    ))
    record = result["record"]
    assert record["result_status"] == "SELECTED"
    assert model.calls == result["manifest"]["usage"]["requests_started"] == 7
    assert len(record["revision_log"]) == len(record["format_repair_log"]) == 1
    final_construct = record["revision_log"][0]["after"]
    assert record["allowed_variations"] == final_construct["allowed_variations"]
    assert record["critical_behaviors"] == final_construct["critical_behaviors"]
    assert result["contract"].model_dump(mode="json") == final_construct["contract"]
    assert record["query_log"] == []


@pytest.mark.parametrize("invalid", [True, False])
def test_actual_token_exhaustion_cannot_hide_invalid_final_response(tmp_path, invalid):
    from agents.usage import Usage

    class ExcessUsageModel(FixedScriptModel):
        async def get_response(self, *args, **kwargs):
            response = await super().get_response(*args, **kwargs)
            if self.calls == 4:
                response.usage = Usage(
                    requests=1, input_tokens=100, output_tokens=100000, total_tokens=100100
                )
            return response

    outputs = [deepcopy(item) for item in scripted_outputs()]
    if invalid:
        outputs[-1].pop("schema_version")
    model = ExcessUsageModel(outputs)
    result = asyncio.run(execute_staged_run(
        synthetic_task(), output_dir=tmp_path / "run", model=model, config=StagedRunConfig(),
    ))
    assert model.calls == 4
    if invalid:
        assert result["record"]["result_status"] == "ERROR"
        assert result["record"]["stop_reason"] == "output_invalid"
        assert result["contract"] is None
        assert not (tmp_path / "run" / "scenario_contract.json").exists()
    else:
        assert result["record"]["result_status"] == "INCONCLUSIVE"
        assert result["record"]["stop_reason"] == "budget_exhausted"
        assert result["contract"] is None


def _query_repair_outputs(*, request_count=1):
    from scripted import query_script

    outputs = [deepcopy(item) for item in query_script(request_count)]
    repaired = deepcopy(outputs[2])
    outputs[2]["previous_receipt_assessment"]["format_note"] = None
    outputs.insert(3, repaired)
    return outputs


def test_query_null_extra_repair_preserves_finish_semantics(tmp_path):
    model = FixedScriptModel(_query_repair_outputs())
    result = asyncio.run(execute_staged_run(
        synthetic_task(), output_dir=tmp_path / "run", model=model,
        config=StagedRunConfig(),
    ))
    assert result["record"]["result_status"] == "SELECTED"
    assert model.calls == result["manifest"]["usage"]["requests_started"] == 6
    log = result["record"]["format_repair_log"][0]
    assert log["outcome"] == "completed"
    assert log["original_attempt_sequence"] is None
    assert log["repaired_attempt_sequence"] is None
    assert log["repair_call_sequence"] == 4
    assert log["comparison_snapshot"] == log["raw_after"]


def test_query_request_repair_keeps_original_error_and_dispatches_new_attempt(tmp_path):
    model = FixedScriptModel(_query_repair_outputs(request_count=2))
    result = asyncio.run(execute_staged_run(
        synthetic_task(), output_dir=tmp_path / "run", model=model,
        config=StagedRunConfig(),
    ))
    receipts = result["record"]["query_log"]
    assert result["record"]["result_status"] == "SELECTED"
    assert [item["sequence"] for item in receipts] == [1, 2, 3]
    assert receipts[1]["status"] == "protocol_error"
    assert receipts[2]["status"] == "ok"
    log = result["record"]["format_repair_log"][0]
    assert (log["original_attempt_sequence"], log["repaired_attempt_sequence"]) == (2, 3)


@pytest.mark.parametrize("mutation", [
    lambda value: value.pop("stop_explanation"),
    lambda value: value.update(impact="changed semantic impact"),
])
def test_query_repair_requires_complete_raw_and_exact_semantics(tmp_path, mutation):
    outputs = _query_repair_outputs()
    mutation(outputs[3])
    model = FixedScriptModel(outputs)
    result = asyncio.run(execute_staged_run(
        synthetic_task(), output_dir=tmp_path / "run", model=model,
        config=StagedRunConfig(),
    ))
    assert result["record"]["result_status"] == "ERROR"
    assert model.calls == 4
    entry = result["record"]["format_repair_log"][0]
    assert entry["outcome"] in {
        "rejected_semantic_change", "rejected_incomplete_raw_response",
        "rejected_invalid_repair",
    }
    assert entry["raw_after"] is not None


@pytest.mark.parametrize("arguments", [
    '{"source_id":"unknown:0","start_line":1,"end_line":2}',
    '{"source_id":"mut:0","start_line":1,"end_line":999}',
])
def test_query_repair_rejects_invalid_source_identity_or_initial_range(
    tmp_path, arguments,
):
    outputs = _query_repair_outputs(request_count=2)
    outputs[2]["query_attempt"]["arguments_json"] = arguments
    model = FixedScriptModel(outputs)
    result = asyncio.run(execute_staged_run(
        synthetic_task(), output_dir=tmp_path / "run", model=model,
        config=StagedRunConfig(),
    ))
    assert result["record"]["result_status"] == "ERROR"
    assert model.calls == 3
    assert result["record"]["query_log"][1]["status"] == "protocol_error"
    assert result["record"]["format_repair_log"][0]["outcome"] == "ineligible"


def test_query_and_delivery_share_one_irreversible_repair_allowance(tmp_path):
    outputs = _query_repair_outputs()
    expected_delivery = deepcopy(outputs[-1])
    outputs[-1].pop("schema_version")
    outputs.append(expected_delivery)
    model = FixedScriptModel(outputs)
    result = asyncio.run(execute_staged_run(
        synthetic_task(), output_dir=tmp_path / "run", model=model,
        config=StagedRunConfig(),
    ))
    assert result["record"]["result_status"] == "ERROR"
    assert model.calls == 6
    logs = result["record"]["format_repair_log"]
    assert logs[0]["outcome"] == "completed"
    assert logs[1]["status"] == "allowance_exhausted"


@pytest.mark.parametrize("field", [
    "decision", "candidate", "tool", "arguments_json", "assessment_impact",
    "assessment_rationale", "outer_impact",
])
def test_query_repair_rejects_each_changed_request_field(tmp_path, field):
    outputs = _query_repair_outputs(request_count=2)
    repaired = outputs[3]
    if field == "decision":
        repaired["decision"] = "finish_queries"
    elif field == "candidate":
        repaired["query_attempt"]["scenario_id"] = "changed"
    elif field == "tool":
        repaired["query_attempt"]["tool"] = "inspect_dependency_usage"
    elif field == "arguments_json":
        repaired["query_attempt"]["arguments_json"] += " "
    elif field == "assessment_impact":
        repaired["previous_receipt_assessment"]["impact"] = "supports"
    elif field == "assessment_rationale":
        repaired["previous_receipt_assessment"]["rationale"] = "changed"
    else:
        repaired["impact"] = "changed"
    result = asyncio.run(execute_staged_run(
        synthetic_task(), output_dir=tmp_path / "run", model=FixedScriptModel(outputs),
        config=StagedRunConfig(),
    ))
    assert result["record"]["result_status"] == "ERROR"
    assert result["record"]["format_repair_log"][0]["outcome"] == (
        "rejected_semantic_change"
    )


@pytest.mark.parametrize("field", ["stop_evidence", "stop_unknowns"])
def test_query_repair_rejects_changed_stop_fields(tmp_path, field):
    outputs = _query_repair_outputs()
    if field == "stop_evidence":
        outputs[3]["stop_explanation"]["evidence"][0]["end_line"] = 4
    else:
        outputs[3]["stop_explanation"]["remaining_unknowns"][0]["impact"] = "changed"
    result = asyncio.run(execute_staged_run(
        synthetic_task(), output_dir=tmp_path / "run", model=FixedScriptModel(outputs),
        config=StagedRunConfig(),
    ))
    assert result["record"]["result_status"] == "ERROR"
    assert result["record"]["format_repair_log"][0]["outcome"] == (
        "rejected_semantic_change"
    )


def test_query_repair_rejects_pending_receipt_content_change():
    from scripted import query_script

    outputs = query_script(2)
    raw = deepcopy(outputs[2])
    repaired = deepcopy(raw)
    raw["previous_receipt_assessment"]["format_note"] = None
    pending = {
        "sequence": 1, "response_id": "query:1", "status": "ok",
        "result": {"status": "ok"}, "assessment": None,
        "assessment_status": "incomplete",
    }

    class MutatingModel(FixedScriptModel):
        async def get_response(self, *args, **kwargs):
            response = await super().get_response(*args, **kwargs)
            pending["result"] = {"status": "changed"}
            return response

    task = UnifiedTask.from_dict(synthetic_task())
    logs = []
    with pytest.raises(StagedInferenceError):
        asyncio.run(_repair_query_output(
            raw, original_call_sequence=2, original_attempt_sequence=2,
            pending_receipt=pending, run_id="run", case_id=task.case_id,
            candidate_ids={"dependency_returns_two"}, task=task,
            source_store=SourceStore(task), model=MutatingModel([repaired]),
            config=StagedRunConfig(), ledger=UsageLedger(InferenceBudget()),
            responses=[], model_calls=[], stages=[], repair_log=logs,
            allowance=FormatRepairAllowance(), deadline=time.monotonic() + 10,
            model_call_limit=10,
        ))
    assert logs[0]["outcome"] == "rejected_post_validation"
