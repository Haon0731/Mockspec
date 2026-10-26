from __future__ import annotations

import asyncio
import json

import pytest

from mock_spec.source_analysis import SourceAnalysis, normalize_analysis_arguments
from mock_spec.schemas import UnifiedTask
from mock_spec.validation import SourceStore
from test_repository_search_integration import _repository
from test_schemas import valid_task_data


@pytest.mark.parametrize(
    "arguments",
    [
        {},
        {"source_id": ""},
        {"source_id": "mut:0", "symbol": ""},
        {"source_id": "mut:0", "unknown": True},
    ],
)
def test_analysis_argument_protocol_rejects_ambiguous_values(arguments) -> None:
    with pytest.raises(ValueError):
        normalize_analysis_arguments(arguments)


def test_discovered_file_analysis_is_internal_and_symbol_exact(tmp_path) -> None:
    task, repository, _ = _repository(tmp_path)
    store = SourceStore(task, repository=repository)
    analysis = SourceAnalysis(task, source_store=store)
    try:
        search = store.search(path_pattern="src/new_context.py")
        source_id = search["matches"][0]["source_id"]
        assert store.observations() == ()

        result = asyncio.run(
            analysis.inspect_dependency_usage(source_id, symbol="dependency")
        )

        assert result["status"] == "ok"
        assert result["query"] == {"symbol": "dependency"}
        calls = [fact for fact in result["facts"] if fact["kind"] == "call"]
        assert {fact["callee"] for fact in calls} == {"dependency"}
        assert all(fact["match_precision"] == "unresolved" for fact in result["facts"])
        assert all("quote" not in fact for fact in result["facts"])
        assert store.observations() == ()
    finally:
        repository.close()


def test_symbol_text_fallback_is_explicitly_low_precision(tmp_path) -> None:
    task, repository, _ = _repository(tmp_path)
    store = SourceStore(task, repository=repository)
    analysis = SourceAnalysis(task, source_store=store)
    try:
        search = store.search(path_pattern="src/new_context.py")
        source_id = search["matches"][0]["source_id"]
        result = asyncio.run(
            analysis.inspect_program_structure(source_id, symbol="SEARCH_CONTRACT")
        )

        assert result["status"] == "ok"
        assert result["facts"] == [{
            "kind": "text_match",
            "source_id": source_id,
            "start_line": 1,
            "end_line": 1,
            "symbol": "SEARCH_CONTRACT",
            "match_precision": "text_match",
        }]
        assert store.observations() == ()
    finally:
        repository.close()


def test_same_name_substring_is_not_a_syntax_match(tmp_path) -> None:
    task, repository, added = _repository(tmp_path)
    added.write_text(
        "def sample():\n"
        "    dependency_extra()\n",
        encoding="utf-8",
    )
    store = SourceStore(task, repository=repository)
    analysis = SourceAnalysis(task, source_store=store)
    try:
        search = store.search(path_pattern="src/new_context.py")
        source_id = search["matches"][0]["source_id"]
        result = asyncio.run(
            analysis.inspect_dependency_usage(source_id, symbol="dependency")
        )

        assert result["status"] == "unresolved"
        assert result["facts"] == []
    finally:
        repository.close()


def test_bounded_error_keeps_real_classification_and_usage() -> None:
    analysis = SourceAnalysis(
        UnifiedTask.from_dict(valid_task_data()), max_output_chars=512
    )
    result = analysis.bounded({
        "status": "error",
        "backend": "backend-" + "x" * 1000,
        "source_id": "source-" + "x" * 1000,
        "error_code": "backend_unavailable",
        "failure_kind": "backend_unavailable",
        "usage": {"bytes": 31, "elapsed_seconds": 0.25},
        "facts": [],
        "diagnostics": ["x" * 1000],
        "limitations": ["x" * 1000],
    })

    assert result["error_code"] == "backend_unavailable"
    assert result["failure_kind"] == "backend_unavailable"
    assert result["usage"] == {"bytes": 31, "elapsed_seconds": 0.25}
    assert len(json.dumps(result, ensure_ascii=False)) <= 512


def test_bounded_long_symbol_omits_query_but_keeps_usage() -> None:
    analysis = SourceAnalysis(
        UnifiedTask.from_dict(valid_task_data()), max_output_chars=512
    )
    result = analysis.bounded({
        "status": "ok",
        "backend": "cpython_ast",
        "source_id": "mut:0",
        "query": {"symbol": "x" * 2000},
        "usage": {"bytes": 47, "elapsed_seconds": 0.5},
        "facts": [{"kind": "call", "callee": "x" * 2000}],
        "diagnostics": [],
        "limitations": [],
    })

    assert result["status"] == "incomplete"
    assert result["error_code"] == "output_limit"
    assert result["usage"] == {"bytes": 47, "elapsed_seconds": 0.5}
    assert "query" not in result
    assert len(json.dumps(result, ensure_ascii=False)) <= 512


def test_receiver_name_is_not_reported_as_a_resolved_target() -> None:
    receiver = {
        "kind": "call", "name": "dependency", "target": "client",
        "match_precision": "resolved_target",
    }
    named_expression = {
        "kind": "named_expression", "target": "client",
        "match_precision": "unresolved",
    }

    assert SourceAnalysis._matches_symbol(receiver, "client") is False
    assert SourceAnalysis._matches_symbol(named_expression, "client") is True
    assert named_expression["match_precision"] == "syntax_match"
