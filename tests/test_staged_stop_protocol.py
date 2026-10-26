"""Repository entry validates explicit stops without claiming semantic proof."""
from copy import deepcopy
import json

import pytest

from test_staged_repository_exploration import _repository_run
from scripted import FixedScriptModel
from agents.usage import Usage


@pytest.mark.parametrize("reason", ["evidence_sufficient", "unknowns_nonblocking", "no_further_evidence"])
def test_agent_stop_preserves_evidence_and_unknown_impact(tmp_path, reason):
    def change(outputs):
        stop = outputs[8]["stop_explanation"]
        stop["reason"] = reason
        stop["remaining_unknowns"] = [] if reason == "evidence_sufficient" else [{
            "question": "Other dependency inputs are not explored.",
            "impact": "They do not affect this test input or candidate selection.",
            "blocking": False,
        }]
    result = _repository_run(tmp_path, transform=change)
    assert result["contract"] is not None, result["record"]["error"]
    record = result["record"]
    assert record["exploration_stop"]["reason"] == reason
    assert record["termination"]["initiator"] == "agent"
    assert record["termination"]["evidence_sufficiency_claimed"] == (reason == "evidence_sufficient")


@pytest.mark.parametrize("defect", ["absent", "bad_quote", "unknown_source", "blocking"])
def test_invalid_stop_cannot_publish_contract(tmp_path, defect):
    def change(outputs):
        stop = outputs[8]["stop_explanation"]
        if defect == "absent":
            outputs[8]["stop_explanation"] = None
        elif defect == "bad_quote":
            stop["evidence"][0]["quote"] = "not observed"
        elif defect == "unknown_source":
            stop["evidence"][0]["source_id"] = "not observed"
        else:
            stop["remaining_unknowns"] = [{
                "question": "Which result is required?", "impact": "Changes selection.",
                "blocking": True,
            }]
    result = _repository_run(tmp_path, transform=change)
    assert result["contract"] is None
    assert result["record"]["stop_reason"] == "output_invalid"
    assert len(result["record"]["source_observations"]) == 7
    assert result["record"]["termination"]["initiator"] == "controller"
    assert not (tmp_path / "run" / "scenario_contract.json").exists()


def test_stop_can_cite_observed_range_with_unread_gap_elsewhere(tmp_path):
    def change(outputs):
        # Earlier evidence covers 1..6; a gap before 9 must not invalidate a quote of 1.
        outputs[7]["query_attempt"]["arguments_json"] = json.dumps({
            "source_id": "src/example.py", "start_line": 9, "end_line": 9,
        })
        dynamic = deepcopy(outputs[9]["contract"]["evidence"][-1])
        dynamic.pop("id")
        dynamic.pop("rationale")
        outputs[8]["stop_explanation"]["evidence"] = [dynamic]
    result = _repository_run(tmp_path, transform=change)
    assert result["contract"] is not None, result["record"]["error"]


def test_shared_budget_after_construct_never_publishes_diagnostic_draft(tmp_path):
    class Exhausted(FixedScriptModel):
        async def get_response(self, *args, **kwargs):
            response = await super().get_response(*args, **kwargs)
            if self.calls == 10:
                response.usage = Usage(requests=1, input_tokens=60000,
                                       output_tokens=100, total_tokens=60100)
            return response
    result = _repository_run(tmp_path, model_class=Exhausted)
    assert result["contract"] is None
    assert result["record"]["stop_reason"] == "budget_exhausted"
    assert result["record"]["termination"]["initiator"] == "controller"
    assert not result["record"]["termination"]["evidence_sufficiency_claimed"]
    assert len(result["record"]["source_observations"]) == 7
    assert not (tmp_path / "run" / "scenario_contract.json").exists()


def test_more_than_six_effective_queries_mix_all_three_tools(tmp_path):
    def change(outputs):
        for index, tool in [(2, "inspect_program_structure"),
                            (3, "inspect_dependency_usage")]:
            outputs[index]["query_attempt"]["tool"] = tool
            outputs[index]["query_attempt"]["arguments_json"] = json.dumps({
                "source_id": "src/example.py", "symbol": "target" if index == 2 else "dependency",
            })
    result = _repository_run(tmp_path, transform=change)
    assert result["contract"] is not None, result["record"]["error"]
    log = result["record"]["query_log"]
    assert len(log) == 7
    assert {item["tool"] for item in log} == {
        "inspect_source", "inspect_program_structure", "inspect_dependency_usage",
    }
    assert all(item["status"] == "ok" for item in log)
    assert result["manifest"]["controller_limits"]["model_calls"] is None
