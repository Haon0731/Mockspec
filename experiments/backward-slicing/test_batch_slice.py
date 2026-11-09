import asyncio
from types import SimpleNamespace

import pytest

import batch_slice


def test_per_criterion_reach_does_not_borrow_other_criterion_evidence():
    graph = {"nodes": [{"id": "a", "source_id": "test"}, {"id": "b", "source_id": "test"},
                       {"id": "p", "source_id": "product", "contains_unknown_call": True}],
             "edges": [{"from": "p", "to": "a", "kind": "call_return"}], "seeds": ["a", "b"]}
    result = batch_slice.evidence_summary(graph)
    assert result["criteria"][0]["product_source_candidates"] == ["product"]
    assert result["criteria"][1]["product_source_candidates"] == []
    assert result["criteria"][1]["unknown_call_nodes"] == []
    assert result["semantic_validation"] == "not_evaluated"


def test_backend_timeout_keeps_criteria_source_identity_and_failure_stage(monkeypatch, tmp_path):
    case = SimpleNamespace(masked_test=SimpleNamespace(text="def test_x():\n    assert result\n"),
                           language="python", target_selector="test.py::test_x")
    monkeypatch.setattr(batch_slice, "select_sources", lambda *args, **kwargs: {
        "sources": [{"source_id": "test", "logical_path": "test.py", "text": case.masked_test.text}],
        "truncated": False, "opaque_methods": [],
    })

    async def timeout(*args):
        raise asyncio.TimeoutError()

    monkeypatch.setattr(batch_slice, "backend", timeout)
    result = asyncio.run(batch_slice.run_case(case, work=str(tmp_path), java_command=None,
                                              seconds=1, max_files=2, max_bytes=1000))
    assert result["status"] == "timeout"
    assert result["stage"] == "backward_graph"
    assert result["target_extraction"]["criteria"]
    assert result["sources"][0]["sha256"]
    assert "graph" not in result


def test_bad_inventory_or_budget_rejected_before_creating_output(tmp_path):
    output = tmp_path / "run"
    inventory = {"input_policy": "frozen-agent", "cases": [
        {"case_id": "x", "dataset": "a"}, {"case_id": "x", "dataset": "a"}]}
    with pytest.raises(ValueError, match="duplicate"):
        asyncio.run(batch_slice.run(inventory, output))
    assert not output.exists()
    with pytest.raises(ValueError, match="positive"):
        asyncio.run(batch_slice.run(inventory, output, seconds=float("nan")))
    assert not output.exists()


def test_summary_preserves_failed_unknown_and_unselected_denominators():
    rows = [{"identity": {"language": "python"}, "status": status,
             "failure_categories": categories} for status, categories in (
        ("failed", ["resource_limit"]), ("unknown", ["no_visible_criterion"]),
        ("not_selected", []), ("partial", ["unresolved_calls", "semantic_analysis_gap"]))]
    result = batch_slice.summarize(rows)
    assert result["counts"]["total"] == 4
    assert result["counts"]["with_product_candidate"] == 0
    assert result["counts"]["status"] == {"failed": 1, "unknown": 1, "not_selected": 1, "partial": 1}
