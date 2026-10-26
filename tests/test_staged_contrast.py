"""Acceptance tests for variant C static contrast and bounded semantic revision."""

from __future__ import annotations

import asyncio
from pathlib import Path
import sys


STAGED_ROOT = Path(__file__).parents[1] / "experiments" / "staged-inference"
sys.path.insert(0, str(STAGED_ROOT))

from controller import StagedRunConfig, execute_staged_run  # noqa: E402
from scripted import (  # noqa: E402
    FixedScriptModel,
    scripted_outputs,
    task_for_replay_case,
)


def _run(tmp_path: Path, case: str, variant: str = "C", **config_overrides):
    task = task_for_replay_case(case)
    outputs = scripted_outputs(case, variant=variant)
    result = asyncio.run(execute_staged_run(
        task,
        output_dir=tmp_path / f"{case}-{variant}",
        model=FixedScriptModel(outputs),
        config=StagedRunConfig(variant=variant, **config_overrides),
        requested_case_id=task["case_id"],
    ))
    return result


def test_b_and_c_share_input_and_only_c_runs_static_contrast(tmp_path) -> None:
    b = _run(tmp_path, "cache_miss", "B")
    c = _run(tmp_path, "cache_miss", "C")

    assert b["record"]["input_hash"] == c["record"]["input_hash"]
    assert set(b["record"]["degeneracy_contrast"]) == set(c["record"]["degeneracy_contrast"])
    assert b["contract"].model_dump() == c["contract"].model_dump()
    assert b["record"]["degeneracy_contrast"]["status"] == "disabled_by_variant"
    assert c["record"]["degeneracy_contrast"] == {
        "status": "proposed",
        "execution_status": "not_executed",
        "alternative": "Mock cache.get('k') to return the asserted record directly.",
        "bypass_behavior": "The correct return bypasses backend.fetch and cache.set.",
        "evidence_ids": ["hit_branch", "assertion", "miss_requirement"],
        "distinguishing_observation": (
            "Observe backend.fetch('k') and cache.set('k', fetched_value)."
        ),
        "revision_requested": False,
        "revision_reason": None,
        "support": "evidence",
        "assumption_reason": None,
        "semantic_role": "dependency_contract",
    }
    assert [stage["stage"] for stage in b["record"]["stages"]] == [
        "EXTRACT", "QUERY", "CONSTRUCT", "DELIVER",
    ]
    assert [stage["stage"] for stage in c["record"]["stages"]] == [
        "EXTRACT", "QUERY", "CONSTRUCT", "CONTRAST", "DELIVER",
    ]


def test_cache_miss_contrast_keeps_original_goal_and_marks_checks_not_implied(tmp_path) -> None:
    result = _run(tmp_path, "cache_miss")
    contract = result["contract"].model_dump(mode="json")

    assert result["record"]["result_status"] == "SELECTED"
    assert [item["statement"] for item in contract["test_objectives"]] == [
        "The visible test expects load('k', ...) to return the record.",
        "On a cache miss, the target fetches and refills the cache.",
    ]
    assert contract["verification_candidates"] == []
    assert all(
        interaction["assertion_policy"] == "not_implied"
        for interaction in contract["expected_interactions"]
    )
    assert any("same key" in item["statement"] for item in contract["dependency_constraints"])
    assert any("return is ignored" in item["statement"]
               for item in contract["dependency_constraints"])
    assert all("normally" in item["statement"]
               for item in contract["dependency_constraints"])


def test_removing_explicit_cache_requirement_preserves_ambiguity(tmp_path) -> None:
    result = _run(tmp_path, "cache_unspecified")
    contract = result["contract"].model_dump(mode="json")

    assert result["record"]["result_status"] == "AMBIGUOUS"
    assert result["record"]["selected_scenario_id"] is None
    assert {item["id"] for item in contract["scenario_hypotheses"]} == {
        "cache_miss_refill", "cached_hit",
    }
    assert "implementation branches" in result["record"]["selection_rationale"]
    assert result["record"]["degeneracy_contrast"]["status"] == "proposed"
    assert result["record"]["revision_log"] == []


def test_one_revision_preserves_snapshots_and_legal_variations(tmp_path) -> None:
    result = _run(tmp_path, "legal_variation")
    record = result["record"]
    contract = result["contract"].model_dump(mode="json")

    assert record["result_status"] == "SELECTED"
    assert [stage["stage"] for stage in record["stages"]] == [
        "EXTRACT", "QUERY", "CONSTRUCT", "CONTRAST", "REVISE", "DELIVER",
    ]
    assert len(record["revision_log"]) == 1
    revision = record["revision_log"][0]
    assert set(revision["modified_objects"]) == {
        "contract.verification_candidates", "allowed_variations",
    }
    assert revision["before"]["contract"]["test_objectives"] == (
        revision["after"]["contract"]["test_objectives"]
    )
    assert len(contract["scenario_hypotheses"]) <= 2
    assert contract["verification_candidates"][0]["status"] == "candidate"
    allowed = " ".join(record["allowed_variations"])
    assert "construction form" in allowed
    assert "unrelated mock fields" in allowed


def test_second_semantic_revision_request_is_rejected_without_contract(tmp_path) -> None:
    result = _run(tmp_path, "second_revision")

    assert result["record"]["result_status"] == "ERROR"
    assert result["record"]["stop_reason"] == "output_invalid"
    assert result["record"]["error_stage"] == "REVISE"
    assert result["contract"] is None
    assert result["record"]["revision_log"]
    assert [stage["stage"] for stage in result["record"]["stages"]][-1] == "REVISE"


def test_revision_budget_exhaustion_does_not_publish_pre_revision_contract(tmp_path) -> None:
    result = _run(tmp_path, "legal_variation", max_model_calls=4)

    assert result["record"]["result_status"] == "INCONCLUSIVE"
    assert result["record"]["stop_reason"] == "budget_exhausted"
    assert result["record"]["error_stage"] == "REVISE"
    assert result["record"]["degeneracy_contrast"]["revision_requested"] is True
    assert result["contract"] is None
    assert [stage["stage"] for stage in result["record"]["stages"]][-1] == "CONTRAST"


def test_budget_before_contrast_records_unavailable_without_extra_request(tmp_path) -> None:
    result = _run(tmp_path, "cache_miss", max_model_calls=3)

    assert result["record"]["result_status"] == "INCONCLUSIVE"
    assert result["contract"] is None
    assert result["record"]["stop_reason"] == "budget_exhausted"
    assert result["record"]["error_stage"] == "CONTRAST"
    assert result["record"]["degeneracy_contrast"]["status"] == "unavailable"
    assert result["record"]["degeneracy_contrast"]["execution_status"] == "not_executed"
    assert result["manifest"]["usage"]["requests_started"] == 3


def test_completed_contrast_can_report_no_supported_alternative(tmp_path) -> None:
    result = _run(tmp_path, "selected")

    assert result["record"]["result_status"] == "SELECTED"
    assert result["record"]["degeneracy_contrast"]["status"] == "none_identified"
    assert result["record"]["degeneracy_contrast"]["execution_status"] == "not_executed"



def test_legal_alternative_can_replace_candidate_without_changing_original_goal(tmp_path):
    from copy import deepcopy

    outputs = [deepcopy(item) for item in scripted_outputs("selected", variant="C")]
    before = outputs[2]
    after = deepcopy(before)
    contract = after["contract"]
    contract["scenario_hypotheses"][0].update(
        id="equivalent_return", description="The dependency returns 2.0, equal to the asserted 2."
    )
    for field in ("test_objectives", "dependency_constraints", "expected_interactions"):
        for claim in contract[field]:
            claim["scenario_ids"] = ["equivalent_return"]
    contract["dependency_constraints"][0]["statement"] = "dependency(1) returns 2.0."
    after["selected_scenario_id"] = "equivalent_return"
    after["selection_rationale"] = "The ordinary equality permits this alternative construction."
    outputs[3] = {
        "schema_version": "staged-contrast/v0.1", "status": "proposed",
        "execution_status": "not_executed", "alternative": "Return 2.0 rather than integer 2.",
        "bypass_behavior": "This challenges the draft's integer construction, not the original equality.",
        "evidence_ids": ["assertion"],
        "distinguishing_observation": "A type check would distinguish them, but is not an original goal.",
        "revision_requested": True,
        "revision_reason": "The proposed alternative is actually legal under ordinary equality.",
    }
    outputs.insert(4, {
        "schema_version": "staged-revise/v0.1", "revised_construct": after,
        "modified_objects": ["contract", "selected_scenario_id", "selection_rationale"],
        "evidence_reason": "The same original equality accepts 2.0; rebind the unchanged goal.",
        "evidence_ids": ["assertion"], "request_another_revision": False,
    })
    outputs[-1].update(selected_scenario_id="equivalent_return", contract=contract)
    result = asyncio.run(execute_staged_run(
        task_for_replay_case("selected"), output_dir=tmp_path / "replacement",
        model=FixedScriptModel(outputs), config=StagedRunConfig(variant="C"),
    ))
    assert result["record"]["result_status"] == "SELECTED"
    assert result["record"]["selected_scenario_id"] == "equivalent_return"
    assert len(result["contract"].scenario_hypotheses) == 1
    assert result["contract"].test_objectives[0].statement == (
        before["contract"]["test_objectives"][0]["statement"]
    )
    assert result["record"]["revision_log"][0]["before"]["contract"] == before["contract"]
    assert result["record"]["query_log"] == []
