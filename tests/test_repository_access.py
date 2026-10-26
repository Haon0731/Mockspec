from __future__ import annotations

import asyncio
from copy import deepcopy
import hashlib
from pathlib import Path

import pytest

from mock_spec.adapters._frozen import _load_approved, _load_approved_with_repository
from mock_spec.scenario import validate_contract
from mock_spec.schemas import UnifiedTask
from mock_spec.source_analysis import SourceAnalysis
from mock_spec.validation import InputValidationError, SourceStore
from test_adapters import _build_snapshot


def _extend_mut(root: Path, review: dict) -> tuple[str, str]:
    item = next(source for source in review["sources"] if source["role"] == "mut")
    payload = b"# outside initial fragment\ndef target():\n    return dependency()\n"
    path = root / "tree" / item["path"]
    path.write_bytes(payload)
    item["sha256"] = hashlib.sha256(payload).hexdigest()
    item["start_line"] = 2
    item["end_line"] = 3
    return item["logical_path"], item["path"]


def _contract(task, source_id: str, start: int, end: int, quote: str) -> dict:
    claim = {
        "statement": "Observed repository text supports this scenario.",
        "status": "required", "scenario_ids": ["observed"],
        "condition": "The reviewed source remains frozen.",
        "evidence_ids": ["repository-evidence"],
    }
    return {
        "schema_version": "mock-spec.scenario/v0.1",
        "case_id": task.case_id, "language": task.language,
        "test_objectives": [claim],
        "scenario_hypotheses": [{
            "id": "observed", "description": "Repository evidence was observed.",
            "condition": "The cited lines were returned by a tool.",
            "status": "candidate", "evidence_ids": ["repository-evidence"],
        }],
        "dependency_constraints": [], "expected_interactions": [],
        "verification_candidates": [],
        "evidence": [{
            "id": "repository-evidence", "source_id": source_id,
            "start_line": start, "end_line": end, "quote": quote,
            "rationale": "Exact returned source lines.",
        }],
        "uncertainty": [],
    }


def test_repository_read_is_host_only_and_recorded_after_return(tmp_path: Path) -> None:
    root, review = _build_snapshot(tmp_path, "python")
    logical_path, _ = _extend_mut(root, review)
    task, repository = _load_approved_with_repository(root, review)
    try:
        store = SourceStore(task, repository=repository)
        returned = store.read("mut", 1, 1)
        assert returned.text == "# outside initial fragment"
        assert returned.source_id.startswith("repository:")
        assert store.observations() == ()
        assert review["snapshot_id"] not in str(task.to_dict())

        store.record_observation(returned, response_id="query:1")
        observation = store.observations()[0]
        assert observation["snapshot_id"] == review["snapshot_id"]
        assert observation["logical_path"] == logical_path
        assert observation["response_id"] == "query:1"
        assert observation["sha256"] == hashlib.sha256(returned.text.encode()).hexdigest()
    finally:
        repository.close()


def test_adjacent_observations_are_citeable_but_gaps_and_wrong_quotes_fail(
    tmp_path: Path,
) -> None:
    root, review = _build_snapshot(tmp_path, "python")
    logical_path, _ = _extend_mut(root, review)
    task, repository = _load_approved_with_repository(root, review)
    try:
        store = SourceStore(task, repository=repository)
        first = store.read(logical_path, 1, 1)
        second = store.read(first.source_id, 2, 2)
        store.record_observation(first, response_id="query:1")
        store.record_observation(second, response_id="query:2")
        validate_contract(
            _contract(task, first.source_id, 1, 2,
                      "# outside initial fragment\ndef target():"),
            task, source_store=store,
        )

        gap_store = SourceStore(task, repository=repository)
        third = gap_store.read(logical_path, 3, 3)
        gap_store.record_observation(first)
        gap_store.record_observation(third)
        with pytest.raises(ValueError, match="unobserved"):
            validate_contract(
                _contract(task, first.source_id, 1, 3,
                          "# outside initial fragment\ndef target():\n    return dependency()"),
                task, source_store=gap_store,
            )
        with pytest.raises(ValueError, match="quote does not match"):
            validate_contract(
                _contract(task, first.source_id, 1, 1, "fabricated"),
                task, source_store=store,
            )
    finally:
        repository.close()


@pytest.mark.parametrize("locator", ["/etc/passwd", "../outside.py", "src/../outside.py"])
def test_repository_rejects_unbound_paths(tmp_path: Path, locator: str) -> None:
    root, review = _build_snapshot(tmp_path, "python")
    task, repository = _load_approved_with_repository(root, review)
    try:
        with pytest.raises(InputValidationError, match="outside the reviewed"):
            SourceStore(task, repository=repository).read(locator, 1, 1)
    finally:
        repository.close()


def test_replacement_after_binding_is_rejected_before_external_read(tmp_path: Path) -> None:
    root, review = _build_snapshot(tmp_path, "python")
    logical_path, relative = _extend_mut(root, review)
    task, repository = _load_approved_with_repository(root, review)
    source_path = root / "tree" / relative
    sentinel = tmp_path / "external-sentinel.py"
    sentinel.write_text("secret external content\n", encoding="utf-8")
    source_path.unlink()
    source_path.symlink_to(sentinel)
    try:
        with pytest.raises(InputValidationError, match="symlink"):
            SourceStore(task, repository=repository).read(logical_path, 1, 1)
    finally:
        repository.close()


def test_content_change_between_pages_fails_closed(tmp_path: Path) -> None:
    root, review = _build_snapshot(tmp_path, "python")
    logical_path, relative = _extend_mut(root, review)
    task, repository = _load_approved_with_repository(root, review)
    try:
        store = SourceStore(task, repository=repository)
        first = store.read(logical_path, 1, 1)
        store.record_observation(first, response_id="query:1")
        (root / "tree" / relative).write_text("changed\n", encoding="utf-8")
        with pytest.raises(InputValidationError, match="identity changed"):
            store.read(first.source_id, 2, 2)
    finally:
        repository.close()


def test_control_material_must_be_regular_file(tmp_path: Path) -> None:
    root, review = _build_snapshot(tmp_path, "python")
    control = root / review["controls"]["runtime_lock"]["path"]
    control.unlink()
    control.mkdir()
    with pytest.raises(InputValidationError, match="not a regular file"):
        _load_approved(root, review)


def test_symlink_in_snapshot_root_ancestor_is_rejected(tmp_path: Path) -> None:
    real_base = tmp_path / "real-base"
    real_base.mkdir()
    root, review = _build_snapshot(real_base, "python")
    linked_base = tmp_path / "linked-base"
    linked_base.symlink_to(real_base, target_is_directory=True)
    with pytest.raises(InputValidationError, match="non-symlink directory"):
        _load_approved(linked_base / root.relative_to(real_base), review)


def test_repository_binding_and_store_reject_other_task(tmp_path: Path) -> None:
    root, review = _build_snapshot(tmp_path, "python")
    task, repository = _load_approved_with_repository(root, review)
    changed = deepcopy(task.to_dict())
    changed["case_id"] = "different"
    try:
        with pytest.raises(InputValidationError, match="binding task identity"):
            SourceStore(UnifiedTask.from_dict(changed), repository=repository)
    finally:
        repository.close()


def test_blank_observed_line_keeps_its_range_but_is_not_analyzable(tmp_path: Path) -> None:
    root, review = _build_snapshot(tmp_path, "python")
    item = next(source for source in review["sources"] if source["role"] == "mut")
    payload = b"\ndef target():\n    return dependency()\n"
    (root / "tree" / item["path"]).write_bytes(payload)
    item["sha256"] = hashlib.sha256(payload).hexdigest()
    item["start_line"], item["end_line"] = 2, 3
    task, repository = _load_approved_with_repository(root, review)
    try:
        store = SourceStore(task, repository=repository)
        blank = store.read(item["logical_path"], 1, 1)
        store.record_observation(blank, response_id="query:blank")

        assert store.quote(blank.source_id, 1, 1) == ""
        assert store.observations()[0]["start_line"] == 1
        with pytest.raises(InputValidationError, match="no analyzable text"):
            store.get(blank.source_id)
        with pytest.raises(ValueError):
            validate_contract(
                _contract(task, blank.source_id, 1, 1, ""),
                task, source_store=store,
            )
    finally:
        repository.close()


def test_fabricated_repository_observation_is_rejected(tmp_path: Path) -> None:
    root, review = _build_snapshot(tmp_path, "python")
    logical_path, _ = _extend_mut(root, review)
    task, repository = _load_approved_with_repository(root, review)
    try:
        store = SourceStore(task, repository=repository)
        source = store.read(logical_path, 1, 1)
        fabricated = type(source)(
            source_id=source.source_id,
            logical_path=source.logical_path,
            text="fabricated",
            symbol=source.symbol,
            start_line=source.start_line,
            end_line=source.end_line,
            resolution_status=source.resolution_status,
            target_precision=source.target_precision,
        )
        with pytest.raises(InputValidationError, match="content or identity changed"):
            store.record_observation(fabricated)
    finally:
        repository.close()


def test_observed_range_ending_in_blank_line_remains_analyzable(tmp_path: Path) -> None:
    root, review = _build_snapshot(tmp_path, "python")
    item = next(source for source in review["sources"] if source["role"] == "mut")
    payload = b"# outside\ndef target():\n    return dependency()\n\n"
    (root / "tree" / item["path"]).write_bytes(payload)
    item["sha256"] = hashlib.sha256(payload).hexdigest()
    item["start_line"], item["end_line"] = 2, 3
    task, repository = _load_approved_with_repository(root, review)
    try:
        store = SourceStore(task, repository=repository)
        source = store.read(item["logical_path"], 2, 4)
        store.record_observation(source, response_id="query:trailing-blank")

        reconstructed = store.get(source.source_id)
        assert reconstructed.start_line == 2
        assert reconstructed.end_line == 4
        assert reconstructed.text == "def target():\n    return dependency()\n\n"
        result = asyncio.run(SourceAnalysis(task, source_store=store)._analyze(source.source_id))
        assert result["status"] == "ok"
    finally:
        repository.close()


def test_observing_initial_source_does_not_duplicate_store_ids(tmp_path: Path) -> None:
    root, review = _build_snapshot(tmp_path, "python")
    task = _load_approved(root, review)
    store = SourceStore(task)
    source = store.read("mut", task.mut_source.start_line, task.mut_source.start_line)

    store.record_observation(source)

    assert store.ids().count("mut") == 1
