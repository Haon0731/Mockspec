"""Strict source-linked contract gate for the reviewed Jib replay."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


HERE = Path(__file__).resolve().parent
REGISTRY = Path("/home/haon/projects/Stub-Tester/.omx/developer-realistic-registry-max")
pytestmark = pytest.mark.real_data
SPEC = importlib.util.spec_from_file_location(
    "jib_slice_contract_subject", HERE / "project_jib_slice_contract.py",
)
subject = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(subject)


def test_jib_contract_retains_all_observations_and_one_reviewed_relation():
    contract = subject.build_contract(REGISTRY)
    assert len(contract.observations) == 19
    assert [row.observation_id for row in contract.observations] == [
        f"criterion:{index}" for index in range(19)
    ]
    assert len(contract.relations) == 1
    relation = contract.relations[0]
    assert relation.kind == "state"
    assert relation.status == "conditional"
    assert relation.observation_ids == ["criterion:0"]
    assert len(contract.unknowns) == 19
    assert contract.coverage.status == "partial"
    assert len(contract.exclusions) == 2
    assert contract.cost.target_executions == 0
    assert contract.semantic_claim == "clues_only_not_semantic_proof"
    context = contract.contexts[0]
    assert context.input_facts["origin"] == "reviewed_replay"
    assert context.input_facts["fresh_generic_backend"] is False
