"""Strict repository-mode mappings keep research judgments auditable."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import sys

import pytest


STAGED_ROOT = Path(__file__).parents[1] / "experiments" / "staged-inference"
sys.path.insert(0, str(STAGED_ROOT))

from controller import (  # noqa: E402
    ConstructResult,
    ContrastResult,
    _validate_constructed,
    _validate_contrast,
)
from scripted import scripted_outputs, synthetic_task  # noqa: E402
from mock_spec.schemas import UnifiedTask  # noqa: E402
from mock_spec.validation import SourceStore  # noqa: E402


def _selected() -> tuple[ConstructResult, UnifiedTask, SourceStore]:
    raw = scripted_outputs("selected")[2]
    task = UnifiedTask.from_dict(synthetic_task())
    return ConstructResult.model_validate(raw), task, SourceStore(task)


def test_strict_construct_accepts_evidence_assumption_and_formal_claim_mappings() -> None:
    constructed, task, store = _selected()

    contract = _validate_constructed(
        constructed, task, store, strict_mapping=True
    )

    assert contract is not None
    assert constructed.critical_behaviors[0].semantic_role == "dependency_contract"
    assert {item.target for item in constructed.claim_basis} >= {
        "selection_rationale", "allowed_variation", "formal_claim"
    }


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda value: value["critical_behaviors"][0].update(
            formal_claim_index=99
        ), "formal claim index"),
        (lambda value: value["claim_basis"][2].update(
            support="assumption", assumption_reason=None, evidence_ids=[]
        ), "assumption requires"),
        (lambda value: value.__setitem__(
            "claim_basis",
            [item for item in value["claim_basis"] if item["target"] != "allowed_variation"],
        ), "lack claim-basis mappings"),
    ],
)
def test_strict_construct_rejects_unreviewable_mapping(mutation, message: str) -> None:
    raw = deepcopy(scripted_outputs("selected")[2])
    mutation(raw)
    task = UnifiedTask.from_dict(synthetic_task())

    with pytest.raises(ValueError, match=message):
        _validate_constructed(
            ConstructResult.model_validate(raw), task, SourceStore(task),
            strict_mapping=True,
        )


def test_strict_contrast_requires_evidence_or_explicit_assumption() -> None:
    constructed, task, store = _selected()
    contract = _validate_constructed(constructed, task, store, strict_mapping=True)
    assert contract is not None
    raw = {
        "schema_version": "staged-contrast/v0.1",
        "status": "none_identified",
        "execution_status": "not_executed",
        "alternative": None,
        "bypass_behavior": None,
        "evidence_ids": [],
        "distinguishing_observation": None,
        "revision_requested": False,
        "revision_reason": None,
        "support": "assumption",
        "assumption_reason": "No evidence-backed static bypass was identified.",
        "semantic_role": "dependency_contract",
    }
    _validate_contrast(
        ContrastResult.model_validate(raw), contract, strict_mapping=True
    )

    raw["assumption_reason"] = None
    with pytest.raises(ValueError, match="explicit reason"):
        _validate_contrast(
            ContrastResult.model_validate(raw), contract, strict_mapping=True
        )


def test_strict_construct_maps_nonblocking_unknowns() -> None:
    raw = deepcopy(scripted_outputs("selected")[2])
    raw["nonblocking_unknowns"] = ["Concrete dependency state remains unknown."]
    task = UnifiedTask.from_dict(synthetic_task())
    with pytest.raises(ValueError, match="lack claim-basis mappings"):
        _validate_constructed(
            ConstructResult.model_validate(raw), task, SourceStore(task),
            strict_mapping=True,
        )

    raw["claim_basis"].append({
        "claim": "concrete dependency state",
        "basis": "scenario_inference",
        "target": "nonblocking_unknown",
        "target_index": 0,
        "semantic_role": "dependency_contract",
        "evidence_ids": [],
        "support": "assumption",
        "assumption_reason": "The implementation remains hidden and does not affect selection.",
    })
    assert _validate_constructed(
        ConstructResult.model_validate(raw), task, SourceStore(task),
        strict_mapping=True,
    ) is not None


def test_critical_behavior_keeps_legacy_evidence_rule_but_allows_strict_assumption() -> None:
    raw = deepcopy(scripted_outputs("selected")[2])
    raw["critical_behaviors"][0]["evidence_ids"] = []
    task = UnifiedTask.from_dict(synthetic_task())
    with pytest.raises(ValueError, match="requires evidence"):
        _validate_constructed(
            ConstructResult.model_validate(raw), task, SourceStore(task)
        )

    raw["critical_behaviors"][0].update({
        "support": "assumption",
        "assumption_reason": "This behavior is retained as an explicit hypothesis.",
    })
    assert _validate_constructed(
        ConstructResult.model_validate(raw), task, SourceStore(task),
        strict_mapping=True,
    ) is not None
