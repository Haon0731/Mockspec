"""Repository-backed staged inference closes read, observe, and cite."""

from __future__ import annotations

import asyncio
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import sys

import pytest


STAGED_ROOT = Path(__file__).parents[1] / "experiments" / "staged-inference"
sys.path.insert(0, str(STAGED_ROOT))

from controller import (  # noqa: E402
    QuerySelection, StagedRunConfig, _validate_repairable_query, execute_staged_run,
)
from scripted import FixedScriptModel, query_script, synthetic_task  # noqa: E402
from mock_spec.schemas import UnifiedTask  # noqa: E402
from mock_spec.source_repository import ReviewedRepository  # noqa: E402
from mock_spec.validation import SourceStore  # noqa: E402


def test_repair_qualification_allows_future_read_after_disjoint_search_matches(
    tmp_path: Path,
) -> None:
    task = UnifiedTask.from_dict(synthetic_task())
    tree = tmp_path / "snapshot" / "tree" / "src"
    tree.mkdir(parents=True)
    payload = b"head\nTOKEN first\ngap one\ngap two\nTOKEN second\ntail\n"
    (tree / "example.py").write_bytes(payload)
    root_fd = os.open(tmp_path / "snapshot", os.O_RDONLY | os.O_DIRECTORY)
    try:
        repository = ReviewedRepository(
            root_fd, task=task, snapshot_id="synthetic-reviewed-snapshot",
            discovery_root="tree",
            files_by_logical_path={
                "src/example.py": (
                    "tree/src/example.py", hashlib.sha256(payload).hexdigest()
                )
            },
        )
    finally:
        os.close(root_fd)
    try:
        store = SourceStore(task, repository=repository)
        search = store.search(text="TOKEN", max_output_chars=16000)
        source_id = repository.source_id_for("src/example.py")
        assert [match["start_line"] for match in search["matches"]] == [2, 5]
        before = store.observations()
        wire = QuerySelection.model_validate({
            "schema_version": "staged-query/v0.4",
            "decision": "request_query",
            "query_attempt": {
                "question": "What do the intervening lines establish?",
                "scenario_id": "dependency_returns_two",
                "tool": "inspect_source",
                "arguments_json": json.dumps({
                    "operation": "read", "source_id": source_id,
                    "start_line": 3, "end_line": 4,
                }),
            },
            "previous_receipt_assessment": None,
            "impact": "The future bounded read may resolve the question.",
            "stop_explanation": None,
            "question_updates": [],
            "candidate_updates": [],
        })
        query = _validate_repairable_query(
            wire, pending_receipt=None,
            candidate_ids={"dependency_returns_two"},
            source_store=store, task=task, require_explicit_stop=True,
        )
        assert query.query_attempt is not None
        assert store.observations() == before
    finally:
        repository.close()


def _repository_run(tmp_path: Path, *, bad_quote: bool = False, transform=None,
                    config=None, model_class=FixedScriptModel,
                    invalidate_before_materialization: str | None = None) -> dict:
    task = UnifiedTask.from_dict(synthetic_task())
    tree = tmp_path / "snapshot" / "tree" / "src"
    tree.mkdir(parents=True)
    lines = [
        "DYNAMIC_CONTRACT = 'dependency result is 2'",
        *[f"# context {line}" for line in range(2, 10)],
        "def target(value):",
        "    return dependency(value)",
    ]
    payload = ("\n".join(lines) + "\n").encode()
    source_path = tree / "example.py"
    source_path.write_bytes(payload)
    root_fd = os.open(tmp_path / "snapshot", os.O_RDONLY | os.O_DIRECTORY)
    try:
        repository = ReviewedRepository(
            root_fd,
            task=task,
            snapshot_id="synthetic-reviewed-snapshot",
            files_by_logical_path={
                "src/example.py": (
                    "tree/src/example.py", hashlib.sha256(payload).hexdigest()
                )
            },
        )
    finally:
        os.close(root_fd)

    outputs = query_script(7)
    for line_number, output in enumerate(outputs[1:8], start=1):
        output["query_attempt"]["tool"] = "inspect_source"
        output["query_attempt"]["arguments_json"] = json.dumps({
            "source_id": "src/example.py", "start_line": line_number, "end_line": line_number,
        })
    source_id = repository.source_id_for("src/example.py")
    dynamic = {
        "id": "dynamic-contract",
        "source_id": source_id,
        "start_line": 1,
        "end_line": 1,
        "rationale": "The repository observation states the dependency result contract.",
    }
    if bad_quote:
        dynamic["quote"] = "wrong"
    construct = outputs[9]
    deliver = outputs[10]
    construct["contract"] = deepcopy(construct["contract"])
    construct["contract"]["evidence"].append(dynamic)
    construct["contract"]["dependency_constraints"][0]["evidence_ids"].append(
        "dynamic-contract"
    )
    deliver["contract"] = deepcopy(construct["contract"])

    if transform is not None:
        transform(outputs)

    model = model_class(outputs)
    if invalidate_before_materialization is not None:
        action = invalidate_before_materialization

        class InvalidatingModel(FixedScriptModel):
            async def get_response(self, *args, **kwargs):
                response = await super().get_response(*args, **kwargs)
                if self.calls == 1:
                    if action == "closed":
                        repository.close()
                    else:
                        repository._invalidate("test invalidation")
                return response

        model = InvalidatingModel(outputs)
    return asyncio.run(execute_staged_run(
        output_dir=tmp_path / "run",
        model=model,
        config=config or StagedRunConfig(),
        task_loader=lambda: (task, repository),
        requested_case_id=task.case_id,
    ))


def _analysis_repository_run(tmp_path: Path) -> dict:
    """Replay discovery, whole-file syntax analysis, an exact read, then citation."""
    task = UnifiedTask.from_dict(synthetic_task())
    tree = tmp_path / "snapshot" / "tree" / "src"
    tree.mkdir(parents=True)
    lines = [
        "def dependency_extra(value):",
        "    return value + 100",
        "",
        "def dependency(value):",
        "    return value + 1",
        "",
        "def target(value):",
        "    return dependency(value)",
    ]
    payload = ("\n".join(lines) + "\n").encode()
    (tree / "example.py").write_bytes(payload)
    root_fd = os.open(tmp_path / "snapshot", os.O_RDONLY | os.O_DIRECTORY)
    try:
        repository = ReviewedRepository(
            root_fd,
            task=task,
            snapshot_id="synthetic-reviewed-snapshot",
            discovery_root="tree",
            files_by_logical_path={
                "src/example.py": (
                    "tree/src/example.py", hashlib.sha256(payload).hexdigest()
                )
            },
        )
    finally:
        os.close(root_fd)

    source_id = repository.source_id_for("src/example.py")
    outputs = query_script(4)
    attempts = [output["query_attempt"] for output in outputs[1:5]]
    requests = [
        ("inspect_source", {"operation": "search", "path_pattern": "src/*.py"}),
        ("inspect_program_structure", {"source_id": source_id, "symbol": "target"}),
        ("inspect_dependency_usage", {"source_id": source_id, "symbol": "dependency"}),
        ("inspect_source", {
            "source_id": source_id, "start_line": 8, "end_line": 8,
        }),
    ]
    for attempt, (tool, arguments) in zip(attempts, requests):
        attempt["tool"] = tool
        attempt["arguments_json"] = json.dumps(arguments)

    dynamic = {
        "id": "dynamic-call",
        "source_id": source_id,
        "start_line": 8,
        "end_line": 8,
        "rationale": "The repository observation shows the exact dependency call.",
    }
    construct = outputs[6]
    deliver = outputs[7]
    construct["contract"] = deepcopy(construct["contract"])
    construct["contract"]["evidence"].append(dynamic)
    construct["contract"]["dependency_constraints"][0]["evidence_ids"].append(
        "dynamic-call"
    )
    deliver["contract"] = deepcopy(construct["contract"])

    return asyncio.run(execute_staged_run(
        output_dir=tmp_path / "analysis-run",
        model=FixedScriptModel(outputs),
        config=StagedRunConfig(),
        task_loader=lambda: (task, repository),
        requested_case_id=task.case_id,
    ))


def test_repository_mode_reads_more_than_six_times_and_strictly_cites_observation(
    tmp_path: Path,
) -> None:
    result = _repository_run(tmp_path)

    assert result["manifest"]["result_status"] == "SELECTED", result["record"]["error"]
    assert result["manifest"]["controller_limits"] == {
        "model_calls": None,
        "query_attempts": None,
        "termination": "shared_deadline_token_cost_and_repetition_protection",
    }
    assert result["record"]["query_accounting"] == {
        "observed_attempts": 7, "budgeted_attempts": 7, "limit": None,
    }
    assert len(result["record"]["model_calls"]) == 11
    observations = result["record"]["source_observations"]
    assert len(observations) == 7
    assert [item["response_id"] for item in observations] == [
        f"query:{index}" for index in range(1, 8)
    ]
    assert [item["start_line"] for item in observations] == list(range(1, 8))


def test_repository_observation_quote_mismatch_is_rejected(tmp_path: Path) -> None:
    result = _repository_run(tmp_path, bad_quote=True)

    assert result["contract"] is None
    assert result["manifest"]["result_status"] == "ERROR"
    assert result["manifest"]["error_stage"] == "CONSTRUCT"
    assert result["manifest"]["stop_reason"] == "output_invalid"
    assert len(result["record"]["source_observations"]) == 7


@pytest.mark.parametrize("action", ["closed", "invalidated"])
def test_repository_binding_is_revalidated_before_materializing_citations(
    tmp_path: Path, action: str,
) -> None:
    result = _repository_run(
        tmp_path, invalidate_before_materialization=action
    )

    assert result["contract"] is None
    assert result["manifest"]["result_status"] == "ERROR"
    assert result["manifest"]["error_stage"] == "EXTRACT"
    assert len(result["record"]["stages"]) == 1
    stage = result["record"]["stages"][0]
    assert stage["stage"] == "EXTRACT"
    assert stage["citation_mode"] == "source_locator_host_materialized_quote/v1"
    assert stage["model_call_sequence"] == 1
    assert stage["wire_result"]["schema_version"] == "staged-extract/v0.2"
    assert stage["result"] is None
    assert "quote" not in json.dumps(stage["wire_result"])
    assert str(tmp_path) not in json.dumps(result["record"]["error"])


def test_controller_replays_discover_analyze_read_and_cite_without_implicit_observation(
    tmp_path: Path,
) -> None:
    result = _analysis_repository_run(tmp_path)

    assert result["manifest"]["result_status"] == "SELECTED", result["record"]["error"]
    log = result["record"]["query_log"]
    assert [item["tool"] for item in log] == [
        "inspect_source",
        "inspect_program_structure",
        "inspect_dependency_usage",
        "inspect_source",
    ]
    assert [item["status"] for item in log] == ["ok", "ok", "ok", "ok"]

    structure = log[1]["result"]
    assert structure["query"]["symbol"] == "target"
    assert structure["facts"]
    assert all(fact["match_precision"] in {"syntax_match", "resolved_target"}
               for fact in structure["facts"])

    usage = log[2]["result"]
    assert usage["query"]["symbol"] == "dependency"
    assert usage["facts"]
    assert all(fact["match_precision"] in {"syntax_match", "resolved_target", "unresolved"}
               for fact in usage["facts"])
    assert not any(fact.get("callee") == "dependency_extra" for fact in usage["facts"])

    # Path discovery and internal full-file parser input do not authorize citation.
    observations = result["record"]["source_observations"]
    assert [(item["start_line"], item["end_line"], item["response_id"])
            for item in observations] == [(8, 8, "query:4")]
    assert observations[0]["text"] == "    return dependency(value)"
