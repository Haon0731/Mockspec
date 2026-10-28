"""Mechanical checks for the consumer-facing staged dependency spec."""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys

import pytest


STAGED_ROOT = Path(__file__).parents[1] / "experiments" / "staged-inference"
sys.path.insert(0, str(STAGED_ROOT))

from specification import (  # noqa: E402
    build_dependency_spec,
    validate_semantics,
)
from mock_spec.scenario import ScenarioContract  # noqa: E402
from mock_spec.schemas import UnifiedTask  # noqa: E402
from test_scenario import contract_data  # noqa: E402
from test_schemas import valid_task_data  # noqa: E402


def _contract(*, two_branches: bool = False) -> ScenarioContract:
    raw = contract_data()
    if two_branches:
        raw["scenario_hypotheses"].append({
            "id": "alternative",
            "description": "A separately supported dependency path also returns 2.",
            "condition": "The alternative path is active.",
            "status": "candidate",
            "evidence_ids": ["call", "expectation"],
        })
        for collection in (
            raw["test_objectives"], raw["dependency_constraints"],
            raw["expected_interactions"],
        ):
            collection[0]["scenario_ids"].append("alternative")
    return ScenarioContract.model_validate(raw)


def _semantics(*, two_branches: bool = False) -> dict:
    branches = [{
        "scenario_id": "normal",
        "disposition": "allowed",
        "rationale": "The visible path and result requirement jointly permit this branch.",
        "evidence_ids": ["call", "expectation"],
        "premise_ids": ["mut-call", "test-result"],
        "unknown_ids": [],
    }]
    if two_branches:
        branches.append({
            "scenario_id": "alternative",
            "disposition": "allowed",
            "rationale": "Independent visible evidence also permits the complete alternative.",
            "evidence_ids": ["call", "expectation"],
            "premise_ids": ["mut-call", "test-result"],
            "unknown_ids": [],
        })
    return {
        "schema_version": "staged-spec-semantics/v0.1",
        "delivery_status": "ready",
        "fixed_inputs": [{
            "statement": "The visible test calls target with the fixed value 1.",
            "goal_indexes": [0],
            "evidence_ids": ["expectation"],
        }],
        "replacement_boundary": [{
            "id": "dependency-call",
            "target": "dependency(1)",
            "replaceable_behavior": "The response of this dependency call may be controlled.",
            "preserved_behavior": "target and its direct return remain real code.",
            "status": "confirmed",
            "evidence_ids": ["call"],
            "premise_ids": ["mut-call"],
            "unknown_ids": [],
        }],
        "preserved_real_logic": [{
            "statement": "The target's direct return of the dependency result remains real.",
            "evidence_ids": ["call"],
            "premise_ids": ["mut-call"],
        }],
        "premises": [
            {
                "id": "mut-call",
                "kind": "implementation_fact",
                "statement": "The frozen MUT directly returns dependency(value).",
                "evidence_ids": ["call"],
                "assumption_impact": None,
            },
            {
                "id": "test-result",
                "kind": "test_requirement",
                "statement": "The visible test requires the result to equal 2.",
                "evidence_ids": ["expectation"],
                "assumption_impact": None,
            },
        ],
        "constraint_basis": [{
            "constraint_index": 0,
            "premise_ids": ["mut-call", "test-result"],
        }],
        "branches": branches,
        "goal_coverage": [{
            "goal_index": 0,
            "disposition": "constrained",
            "constraint_indexes": [0],
            "unknown_ids": [],
            "rationale": "The dependency result constraint supplies the visible result.",
            "evidence_ids": ["call", "expectation"],
            "premise_ids": ["mut-call", "test-result"],
        }],
        "unknowns": [{
            "id": "unrelated-implementation-detail",
            "question": "What internal implementation does dependency use?",
            "impact": "It is outside this call-response specification.",
            "disposition": "out_of_scope",
            "goal_indexes": [],
            "constraint_indexes": [],
            "scenario_ids": [],
            "formal_uncertainty_indexes": [0],
            "rationale": "The response relation is specified without constraining internals.",
        }],
        "allowed_variations": [{
            "id": "internal-state",
            "statement": "Internal implementation may vary while the required response is preserved.",
            "scenario_ids": ["normal"] + (["alternative"] if two_branches else []),
            "constraint_indexes": [0],
            "evidence_ids": ["call", "expectation"],
            "premise_ids": ["mut-call", "test-result"],
        }],
        "conflicts": [],
    }


def _task_hash(task: UnifiedTask) -> str:
    return hashlib.sha256(json.dumps(
        task.to_dict(), ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")).hexdigest()


def test_two_evidence_backed_branches_are_allowed_without_ambiguity() -> None:
    contract = _contract(two_branches=True)

    result = validate_semantics(_semantics(two_branches=True), contract)

    assert result.delivery_status == "ready"
    assert [item.disposition for item in result.branches] == ["allowed", "allowed"]


def test_unresolved_branch_is_partial_and_not_an_allowed_choice() -> None:
    contract = _contract(two_branches=True)
    raw = _semantics(two_branches=True)
    raw["delivery_status"] = "partial"
    raw["unknowns"].append({
        "id": "branch-legality",
        "question": "Does the task actually permit the alternative path?",
        "impact": "The generator cannot select the alternative until this is resolved.",
        "disposition": "blocking",
        "goal_indexes": [0],
        "constraint_indexes": [0],
        "scenario_ids": ["alternative"],
        "formal_uncertainty_indexes": [],
        "rationale": "Equal final results alone do not establish equivalent scenario meaning.",
    })
    raw["branches"][1].update(
        disposition="unresolved", evidence_ids=[], premise_ids=[],
        unknown_ids=["branch-legality"],
    )
    raw["goal_coverage"][0].update(disposition="blocked", unknown_ids=["branch-legality"])

    result = validate_semantics(raw, contract)

    assert result.branches[1].disposition == "unresolved"
    with pytest.raises(ValueError, match="ready specification"):
        raw["delivery_status"] = "ready"
        validate_semantics(raw, contract)


def test_allowed_branch_without_evidence_is_rejected() -> None:
    raw = _semantics()
    raw["branches"][0]["evidence_ids"] = []

    with pytest.raises(ValueError, match="allowed branch requires evidence"):
        validate_semantics(raw, _contract())


@pytest.mark.parametrize("disposition", ["nonblocking", "out_of_scope"])
def test_unresolved_branch_requires_scenario_mapped_blocking_unknown(
    disposition: str,
) -> None:
    contract = _contract(two_branches=True)
    raw = _semantics(two_branches=True)
    raw["delivery_status"] = "partial"
    raw["unknowns"].append({
        "id": "branch-gap",
        "question": "Is the alternative branch permitted?",
        "impact": "The alternative cannot yet be selected.",
        "disposition": disposition,
        "goal_indexes": [0],
        "constraint_indexes": [0],
        "scenario_ids": ["alternative"],
        "formal_uncertainty_indexes": [],
        "rationale": "This deliberately uses the wrong uncertainty disposition.",
    })
    raw["branches"][1].update(
        disposition="unresolved", evidence_ids=[], premise_ids=[],
        unknown_ids=["branch-gap"],
    )

    with pytest.raises(ValueError, match="blocking unknown mapped to that scenario"):
        validate_semantics(raw, contract)


def test_unresolved_branch_unknown_must_name_the_affected_scenario() -> None:
    contract = _contract(two_branches=True)
    raw = _semantics(two_branches=True)
    raw["delivery_status"] = "partial"
    raw["unknowns"].append({
        "id": "branch-gap",
        "question": "Is the alternative branch permitted?",
        "impact": "The alternative cannot yet be selected.",
        "disposition": "blocking",
        "goal_indexes": [0],
        "constraint_indexes": [0],
        "scenario_ids": ["normal"],
        "formal_uncertainty_indexes": [],
        "rationale": "The mapping intentionally points at the wrong branch.",
    })
    raw["branches"][1].update(
        disposition="unresolved", evidence_ids=[], premise_ids=[],
        unknown_ids=["branch-gap"],
    )

    with pytest.raises(ValueError, match="blocking unknown mapped to that scenario"):
        validate_semantics(raw, contract)


def test_fixed_inputs_cover_every_goal_with_valid_nonrepeated_indexes() -> None:
    contract_raw = contract_data()
    second_goal = deepcopy(contract_raw["test_objectives"][0])
    second_goal["statement"] = "A second visible objective is fixed independently."
    contract_raw["test_objectives"].append(second_goal)
    contract = ScenarioContract.model_validate(contract_raw)
    raw = _semantics()
    second_coverage = deepcopy(raw["goal_coverage"][0])
    second_coverage["goal_index"] = 1
    raw["goal_coverage"].append(second_coverage)

    with pytest.raises(ValueError, match="every test objective requires a fixed-input mapping"):
        validate_semantics(raw, contract)

    raw["fixed_inputs"][0]["goal_indexes"] = [0, 2]
    with pytest.raises(ValueError, match="fixed-input goal index"):
        validate_semantics(raw, contract)

    raw["fixed_inputs"][0]["goal_indexes"] = [0, 0, 1]
    with pytest.raises(ValueError, match="cannot repeat a goal mapping"):
        validate_semantics(raw, contract)


def test_constrained_goal_covers_each_allowed_branch_with_applicable_constraint() -> None:
    contract_raw = _contract(two_branches=True).model_dump(mode="json")
    contract_raw["dependency_constraints"][0]["scenario_ids"] = ["normal"]
    alternative_constraint = deepcopy(contract_raw["dependency_constraints"][0])
    alternative_constraint["statement"] = "The alternative dependency path returns 2."
    alternative_constraint["scenario_ids"] = ["alternative"]
    contract_raw["dependency_constraints"].append(alternative_constraint)
    contract = ScenarioContract.model_validate(contract_raw)
    raw = _semantics(two_branches=True)
    raw["constraint_basis"].append({
        "constraint_index": 1,
        "premise_ids": ["mut-call", "test-result"],
    })
    raw["allowed_variations"][0]["constraint_indexes"].append(1)

    with pytest.raises(ValueError, match="every allowed branch"):
        validate_semantics(raw, contract)


def test_real_code_only_goal_requires_no_synthetic_dependency_obligation() -> None:
    contract_raw = contract_data()
    contract_raw["dependency_constraints"] = []
    contract_raw["expected_interactions"] = []
    contract = ScenarioContract.model_validate(contract_raw)
    raw = _semantics()
    raw["replacement_boundary"] = []
    raw["constraint_basis"] = []
    raw["goal_coverage"][0].update(
        disposition="real_code", constraint_indexes=[], unknown_ids=[],
        rationale="The fixed input and preserved real computation determine this objective.",
    )
    raw["allowed_variations"] = []

    result = validate_semantics(raw, contract)

    assert result.delivery_status == "ready"
    assert result.replacement_boundary == []
    assert result.goal_coverage[0].disposition == "real_code"
    assert result.constraint_basis == []


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda raw: raw["constraint_basis"].clear(), "every dependency constraint"),
        (lambda raw: raw["goal_coverage"].clear(), "every test objective"),
        (lambda raw: raw["allowed_variations"][0].update(
            evidence_ids=["missing"], premise_ids=[]
        ), "unknown evidence"),
        (lambda raw: raw["constraint_basis"][0].update(
            premise_ids=["missing"]
        ), "unknown premise"),
    ],
)
def test_dangling_or_missing_semantic_mappings_are_rejected(mutation, message: str) -> None:
    raw = _semantics()
    mutation(raw)

    with pytest.raises(ValueError, match=message):
        validate_semantics(raw, _contract())


def test_assumption_is_delivered_with_its_impact_and_constraint_mapping() -> None:
    raw = _semantics()
    raw["premises"].append({
        "id": "regression-assumption",
        "kind": "assumption",
        "statement": "The current frozen path is treated as the regression reference.",
        "evidence_ids": [],
        "assumption_impact": "A defect in the MUT may be inherited by this partial specification.",
    })
    raw["constraint_basis"][0]["premise_ids"].append("regression-assumption")

    result = validate_semantics(raw, _contract())

    assert result.premises[-1].assumption_impact is not None
    assert "regression-assumption" in result.constraint_basis[0].premise_ids


def test_unmapped_assumption_and_impactless_assumption_are_rejected() -> None:
    raw = _semantics()
    raw["premises"].append({
        "id": "orphan-assumption",
        "kind": "assumption",
        "statement": "An unverified behavior might hold.",
        "evidence_ids": [],
        "assumption_impact": "It could change the dependency constraint.",
    })
    with pytest.raises(ValueError, match="every premise must support"):
        validate_semantics(raw, _contract())

    raw["premises"][-1]["assumption_impact"] = None
    with pytest.raises(ValueError, match="assumption requires impact"):
        validate_semantics(raw, _contract())


def test_conflict_must_be_explicit_and_cannot_be_delivered_ready() -> None:
    raw = _semantics()
    raw["delivery_status"] = "conflict"
    raw["conflicts"] = [{
        "id": "dependency-conflict",
        "statement": "The dependency semantics conflict with the required response.",
        "impact": "No reasonable dependency behavior can currently satisfy the objective.",
        "evidence_ids": ["call", "expectation"],
        "premise_ids": ["mut-call", "test-result"],
    }]

    assert validate_semantics(raw, _contract()).delivery_status == "conflict"
    raw["delivery_status"] = "ready"
    with pytest.raises(ValueError, match="ready specification"):
        validate_semantics(raw, _contract())


def test_build_dependency_spec_binds_complete_task_contract_and_stop() -> None:
    task = UnifiedTask.from_dict(valid_task_data())
    contract = _contract()
    input_hash = _task_hash(task)

    result = build_dependency_spec(
        semantics=_semantics(), contract=contract, task=task,
        run_id="run-1", input_hash=input_hash,
        original_goal_locations=[{
            "source_id": "test:0", "start_line": 5, "end_line": 5,
        }],
        exploration={
            "reason": "evidence_sufficient",
            "rationale": "All current goal-to-constraint mappings have an explicit basis.",
            "evidence_ids": ["call", "expectation"],
            "unknown_ids": [],
        },
    )

    assert result.schema_version == "mock-spec.dependency/v0.1"
    assert result.task == task.to_dict()
    assert result.contract == contract
    assert result.regression_reference.status == "reference_only"
    assert "not asserted" in result.regression_reference.statement


def test_build_dependency_spec_rejects_identity_hash_and_stop_drift() -> None:
    task = UnifiedTask.from_dict(valid_task_data())
    contract = _contract()
    base = {
        "semantics": _semantics(), "contract": contract, "task": task,
        "run_id": "run-1", "input_hash": _task_hash(task),
        "original_goal_locations": [{
            "source_id": "test:0", "start_line": 5, "end_line": 5,
        }],
        "exploration": {
            "reason": "evidence_sufficient", "rationale": "Evidence is sufficient.",
            "evidence_ids": ["call"], "unknown_ids": [],
        },
    }

    invalid_hash = deepcopy(base)
    invalid_hash["input_hash"] = "0" * 64
    with pytest.raises(ValueError, match="input hash mismatch"):
        build_dependency_spec(**invalid_hash)

    invalid_stop = deepcopy(base)
    invalid_stop["exploration"]["evidence_ids"] = ["missing"]
    with pytest.raises(ValueError, match="unknown evidence"):
        build_dependency_spec(**invalid_stop)


def test_validators_are_mechanical_and_reject_no_semantic_counterexample() -> None:
    """Passing establishes closed mappings, not entailment or global weakest constraints."""
    result = validate_semantics(_semantics(), _contract())

    assert result.constraint_basis[0].constraint_index == 0
