import json
from copy import deepcopy
from pathlib import Path

import pytest

from mock_spec.schemas import UnifiedTask
from mock_spec.scenario import ScenarioContract, validate_contract
from test_schemas import valid_task_data


def contract_data():
    claim = {
        "statement": "The visible test expects target(1) to equal 2.",
        "status": "required",
        "scenario_ids": ["normal"],
        "condition": "The visible test calls target(1).",
        "evidence_ids": ["expectation"],
    }
    return {
        "schema_version": "mock-spec.scenario/v0.1",
        "case_id": "example_1", "language": "python",
        "test_objectives": [claim],
        "scenario_hypotheses": [{
            "id": "normal", "description": "Dependency returns 2.",
            "condition": "target(1) completes normally.",
            "status": "candidate", "evidence_ids": ["call", "expectation"],
        }],
        "dependency_constraints": [{
            **claim, "statement": "dependency(1) returns 2.",
            "condition": "The direct delegation completes normally.",
            "evidence_ids": ["call", "expectation"],
        }],
        "expected_interactions": [{
            **claim, "statement": "dependency is called with 1.",
            "evidence_ids": ["call", "expectation"], "assertion_policy": "not_implied",
        }],
        "verification_candidates": [],
        "evidence": [
            {"id": "expectation", "source_id": "test:0", "start_line": 5,
             "end_line": 5, "quote": "    assert target(1) == 2",
             "rationale": "Visible result assertion."},
            {"id": "call", "source_id": "mut:0", "start_line": 11,
             "end_line": 11, "quote": "    return dependency(value)",
             "rationale": "Direct return of the dependency result."},
        ],
        "uncertainty": [{
            "statement": "No implementation of dependency is visible.",
            "status": "unknown", "scenario_ids": [], "condition": None,
            "evidence_ids": [],
        }],
    }


def test_conditional_contract_and_unknown_validate():
    contract = validate_contract(contract_data(), UnifiedTask.from_dict(valid_task_data()))
    assert contract.dependency_constraints[0].status == "required"
    assert contract.uncertainty[0].status == "unknown"
    assert contract.expected_interactions[0].assertion_policy == "not_implied"


def test_documented_json_schema_matches_contract_model() -> None:
    documented = json.loads(
        (Path(__file__).parents[1] / "docs/scenario-contract.schema.json").read_text(
            encoding="utf-8"
        )
    )

    assert documented == ScenarioContract.model_json_schema()


@pytest.mark.parametrize("mutation", [
    lambda d: d["evidence"][0].update(source_id="private"),
    lambda d: d["evidence"][0].update(start_line=1, end_line=1),
    lambda d: d["evidence"][0].update(end_line=6),
    lambda d: d["evidence"][0].update(start_line=True),
    lambda d: d["evidence"][0].update(quote="invented assertion"),
    lambda d: d["dependency_constraints"][0].update(evidence_ids=[]),
    lambda d: d["dependency_constraints"][0].update(condition=None),
    lambda d: d["dependency_constraints"][0].update(condition=" "),
    lambda d: d["dependency_constraints"][0].update(scenario_ids=[]),
    lambda d: d["dependency_constraints"][0].update(scenario_ids=["invented"]),
    lambda d: d["dependency_constraints"][0].update(evidence_ids=["missing"]),
    lambda d: d["evidence"].append(deepcopy(d["evidence"][0])),
    lambda d: d.update(case_id="other"),
    lambda d: d.update(language="java"),
    lambda d: d.update(reference_mock="forbidden"),
    lambda d: d["expected_interactions"][0].update(assertion_policy="required"),
    lambda d: d["verification_candidates"].append(deepcopy(d["test_objectives"][0])),
])
def test_invalid_contract_rejected(mutation):
    data = contract_data()
    mutation(data)
    with pytest.raises(ValueError):
        validate_contract(data, UnifiedTask.from_dict(valid_task_data()))
