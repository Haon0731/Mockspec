"""Stable model references keep repository-mode judgments auditable."""

from __future__ import annotations

from copy import deepcopy
import hashlib
import os
from pathlib import Path
import sys

import pytest


STAGED_ROOT = Path(__file__).parents[1] / "experiments" / "staged-inference"
sys.path.insert(0, str(STAGED_ROOT))

from stage_protocol import (  # noqa: E402
    ConstructResult,
    ConstructSelection,
    ContrastResult,
    ClosedQuestionUpdateSelection,
    InvestigationRequest,
    materialize_construct,
    project_question_updates,
    validate_constructed,
    validate_contrast,
)
from specification import validate_semantics  # noqa: E402
from investigation import InvestigationLedger  # noqa: E402
from scripted import scripted_outputs, synthetic_task  # noqa: E402
from mock_spec.schemas import UnifiedTask  # noqa: E402
from mock_spec.validation import SourceStore  # noqa: E402
from mock_spec.source_repository import ReviewedRepository  # noqa: E402


def _context() -> tuple[UnifiedTask, SourceStore]:
    task = UnifiedTask.from_dict(synthetic_task())
    return task, SourceStore(task)


def _construct(raw: dict, task: UnifiedTask, store: SourceStore) -> ConstructResult:
    return materialize_construct(ConstructSelection.model_validate(raw), store, task)


def _selected() -> tuple[ConstructResult, UnifiedTask, SourceStore]:
    task, store = _context()
    return _construct(scripted_outputs("selected")[2], task, store), task, store


def test_stable_refs_are_projected_to_consumer_indexes_and_host_identity() -> None:
    raw = scripted_outputs("selected")[2]
    assert "case_id" not in raw["contract"]
    assert "language" not in raw["contract"]
    constructed, task, store = _selected()

    contract = validate_constructed(constructed, task, store, strict_mapping=True)

    assert contract is not None
    assert (contract.case_id, contract.language) == (task.case_id, task.language)
    assert constructed.spec is not None
    assert constructed.spec.fixed_inputs[0].goal_indexes == [0]
    assert constructed.spec.constraint_basis[0].constraint_index == 0
    assert constructed.spec.goal_coverage[0].constraint_indexes == [0]
    assert constructed.spec.unknowns[0].formal_uncertainty_indexes == [0]
    assert constructed.spec.allowed_variations[0].constraint_indexes == [0]


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (
            lambda value: value["contract"]["test_objectives"].append(
                deepcopy(value["contract"]["test_objectives"][0])
            ),
            "duplicate goal stable ref",
        ),
        (
            lambda value: value["spec"]["constraint_basis"][0].update(
                constraint_ref="missing-constraint"
            ),
            "unknown stable ref",
        ),
        (
            lambda value: value["spec"]["goal_coverage"][0].update(
                constraint_refs=["constraint-1", "constraint-1"]
            ),
            "duplicate stable refs",
        ),
    ],
)
def test_stable_ref_projection_rejects_duplicate_or_unknown_refs(
    mutation, message: str,
) -> None:
    raw = deepcopy(scripted_outputs("selected")[2])
    mutation(raw)
    task, store = _context()

    with pytest.raises(ValueError, match=message):
        _construct(raw, task, store)


def test_host_projection_does_not_invent_a_missing_semantic_relation() -> None:
    raw = deepcopy(scripted_outputs("selected")[2])
    raw["spec"]["constraint_basis"] = []
    task, store = _context()
    constructed = _construct(raw, task, store)

    contract = validate_constructed(constructed, task, store, strict_mapping=True)
    assert contract is not None and constructed.spec is not None
    with pytest.raises(ValueError, match="every dependency constraint requires"):
        validate_semantics(constructed.spec, contract)


def test_spec_unknowns_and_variations_are_the_single_model_source() -> None:
    raw = deepcopy(scripted_outputs("selected")[2])
    raw["spec"]["unknowns"][0].update(
        question="Concrete dependency state remains unknown.",
        impact="It does not affect the selected observable branch.",
    )
    task, store = _context()
    constructed = _construct(raw, task, store)

    assert constructed.unresolved == []
    assert constructed.nonblocking_unknowns == [
        "Concrete dependency state remains unknown."
    ]
    assert constructed.allowed_variations == [
        raw["spec"]["allowed_variations"][0]["statement"]
    ]
    assert validate_constructed(
        constructed, task, store, strict_mapping=True
    ) is not None


def test_fallback_unknowns_cannot_silently_compete_with_a_complete_spec() -> None:
    raw = deepcopy(scripted_outputs("selected")[2])
    raw["fallback_unknowns"] = [{"question": "Competing unknown", "blocking": False}]
    task, store = _context()

    with pytest.raises(ValueError, match="only allowed when spec is absent"):
        _construct(raw, task, store)


def test_blocking_unknown_cannot_omit_affected_goal_refs() -> None:
    raw = deepcopy(scripted_outputs("ambiguous")[2])
    raw["spec"]["unknowns"][0]["goal_refs"] = []
    task, store = _context()
    constructed = _construct(raw, task, store)
    assert constructed.contract is not None and constructed.spec is not None

    with pytest.raises(ValueError, match="blocked coverage must match"):
        validate_semantics(constructed.spec, constructed.contract)


def test_host_projects_all_receipts_needed_for_combined_dynamic_evidence(
    tmp_path: Path,
) -> None:
    task, _ = _context()
    root = tmp_path / "snapshot"
    (root / "tree").mkdir(parents=True)
    source = b"first line\nsecond line\n"
    (root / "tree" / "dynamic.py").write_bytes(source)
    root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    try:
        repository = ReviewedRepository(
            root_fd,
            task=task,
            snapshot_id="stable-ref-receipts",
            discovery_root="tree",
            files_by_logical_path={
                "dynamic.py": ("tree/dynamic.py", hashlib.sha256(source).hexdigest())
            },
        )
    finally:
        os.close(root_fd)
    try:
        store = SourceStore(task, repository=repository)
        source_id = repository.source_id_for("dynamic.py")
        store.record_observation(store.read(source_id, 1, 1), response_id="query:1")
        store.record_observation(store.read(source_id, 2, 2), response_id="query:2")
        attempts = [
            {"sequence": sequence, "status": "ok", "result": {},
             "response_id": f"query:{sequence}"}
            for sequence in (1, 2)
        ]
        ledger = InvestigationLedger(1, repository_available=True)
        ledger.register([
            InvestigationRequest(
                question="What do both dynamic lines establish?",
                goal_indexes=[0],
                impact="Both lines are required to close the original goal question.",
            )
        ], origin="EXTRACT")
        selection = ClosedQuestionUpdateSelection(
            question_id="q1", question=None, goal_indexes=None, impact=None,
            state="resolved", rationale="The two observed lines jointly answer it.",
            evidence=[{"source_id": source_id, "start_line": 1, "end_line": 2}],
        )

        projected = project_question_updates(
            [selection], existing_questions=ledger.snapshot(),
            attempts=attempts, store=store,
        )
        assert projected[0].attempted_sequences == [1, 2]
        ledger.apply_updates(projected, store=store, attempts=attempts, task=task)
        assert ledger.snapshot()[0]["state"] == "resolved"

        incomplete = project_question_updates(
            [selection], existing_questions=[{
                **ledger.snapshot()[0], "state": "open", "attempted_sequences": [],
            }], attempts=attempts[:1], store=store,
        )
        assert incomplete[0].attempted_sequences == [1]
        incomplete_ledger = InvestigationLedger(1, repository_available=True)
        incomplete_ledger.register([
            InvestigationRequest(
                question="What do both dynamic lines establish?",
                goal_indexes=[0],
                impact="Both lines are required to close the original goal question.",
            )
        ], origin="EXTRACT")
        with pytest.raises(ValueError, match="associated with its observed query receipt"):
            incomplete_ledger.apply_updates(
                incomplete, store=store, attempts=attempts[:1], task=task,
            )
    finally:
        repository.close()


def test_strict_contrast_requires_evidence_or_explicit_assumption() -> None:
    constructed, task, store = _selected()
    contract = validate_constructed(constructed, task, store, strict_mapping=True)
    assert contract is not None
    raw = {
        "schema_version": "staged-contrast/v0.3",
        "status": "none_identified",
        "alternative": None,
        "bypass_behavior": None,
        "evidence_ids": [],
        "distinguishing_observation": None,
        "revision_requested": False,
        "revision_reason": None,
        "support": "assumption",
        "assumption_reason": "No evidence-backed static bypass was identified.",
        "semantic_role": "dependency_contract",
        "weakness_review": "No evidence-backed weaker alternative was identified.",
        "overconstraint_review": "Unrelated implementation details remain variable.",
        "observation_limits": "This static review does not execute a counterexample.",
        "investigation_requests": [],
    }
    validate_contrast(ContrastResult.model_validate(raw), contract, strict_mapping=True)

    raw["assumption_reason"] = None
    with pytest.raises(ValueError, match="explicit reason"):
        validate_contrast(
            ContrastResult.model_validate(raw), contract, strict_mapping=True
        )
