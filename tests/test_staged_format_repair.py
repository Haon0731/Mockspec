"""Delivery repair preserves a complete static semantic snapshot (AC-08)."""
from __future__ import annotations

import asyncio
from copy import deepcopy
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "experiments" / "staged-inference"))
from controller import StagedRunConfig, execute_staged_run  # noqa: E402
from scripted import FixedScriptModel, scripted_outputs, synthetic_task  # noqa: E402


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
    return result, model, expected


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
    assert result["contract"].model_dump(mode="json") == expected["contract"]
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
