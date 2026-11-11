import asyncio
import copy
from contextlib import contextmanager
import json
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
    assert set(result["timings_seconds"]) == {"criterion_extraction", "source_selection"}
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


def test_parallel_cases_keep_inventory_order_and_isolate_failure(monkeypatch, tmp_path):
    inventory = {"input_policy": "frozen-agent", "registry_root": str(tmp_path), "cases": [
        {"case_id": name, "dataset": "sample", "inventory_revision": "r1",
         "snapshot_id": "snapshot", "language": "python"} for name in ("first", "bad", "last")
    ]}
    active = 0
    peak = 0

    class Dataset:
        def __init__(self, *args, **kwargs):
            pass

        @contextmanager
        def load_case(self, case_id):
            yield SimpleNamespace(case_id=case_id, snapshot_id="snapshot", language="python")

    async def analyse(case, **kwargs):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        try:
            await asyncio.sleep(0)
            if case.case_id == "bad":
                raise ValueError("synthetic isolated failure")
            return {"status": "partial", "failure_categories": []}
        finally:
            active -= 1

    monkeypatch.setattr(batch_slice, "FrozenDataset", Dataset)
    monkeypatch.setattr(batch_slice, "run_case", analyse)
    monkeypatch.setattr(batch_slice, "_backend_paths", lambda: None)
    output = tmp_path / "parallel"
    result = asyncio.run(batch_slice.run(inventory, output, workers=2))
    rows = json.loads((output / "results.json").read_text())["cases"]
    assert [row["identity"]["case_id"] for row in rows] == ["first", "bad", "last"]
    assert [row["status"] for row in rows] == ["partial", "failed", "partial"]
    assert result["counts"]["total"] == 3
    assert peak == 2
    assert all(row["result_contract"]["identity"]["case_id"] == row["identity"]["case_id"] for row in rows)
    assert json.loads((output / "run-manifest.json").read_text())["configuration"]["workers"] == 2


def test_invalid_worker_count_is_rejected_before_output(tmp_path):
    with pytest.raises(ValueError, match="workers"):
        asyncio.run(batch_slice.run({"input_policy": "frozen-agent"}, tmp_path / "bad", workers=0))
    assert not (tmp_path / "bad").exists()


def candidate_row():
    identity = {"case_id": "a", "dataset": "frozen", "snapshot_id": "s", "language": "python"}
    result = {"identity": identity, "status": "partial", "stage": "complete",
              "failure_categories": ["semantic_analysis_gap"], "elapsed_seconds": 1,
              "input_context": {"masked_test_sha256": "a" * 64, "target_selector": "test.py::test_a",
                                "masking_contract": "slots"}}
    batch_slice.attach_result_contract(result, identity)
    return result


def test_unsupported_keeps_candidate_diagnostics_without_promoting_them():
    result = candidate_row()
    original = copy.deepcopy(result)
    batch_slice.merge_semantic_result(result, {"status": "unsupported", "reason": "no compiled entry adapter"}, result["identity"])
    assert result["status"] == "unsupported"
    assert result["candidate_diagnostics"]["result_contract"] == original["result_contract"]
    assert result["candidate_diagnostics"]["elapsed_seconds"] == 1
    contract = result["result_contract"]
    assert contract["relations"] == []
    assert contract["semantic_validation"]["status"] == "not_evaluated"
    assert contract["analysis_boundaries"]["unanalysed_regions"][-1]["reason"] == "no compiled entry adapter"


@pytest.mark.parametrize("field", ["case_id", "snapshot_id", "masked_test_sha256", "target_selector", "masking_contract"])
def test_semantic_contract_cannot_replace_input_identity(field):
    result = candidate_row()
    contract = copy.deepcopy(result["result_contract"])
    contract["identity"][field] = "b" * 64 if field == "masked_test_sha256" else "other"
    with pytest.raises(ValueError, match="mismatch"):
        batch_slice.merge_semantic_result(result, {"status": "partial", "result_contract": contract}, result["identity"])


def test_semantic_contract_survives_integration_without_candidate_reconstruction():
    result = candidate_row()
    contract = copy.deepcopy(result["result_contract"])
    contract["entry_point"]["mut_binding"]["status"] = "unique"
    contract["completion"]["failure_categories"] = ["known_unknown_effect"]
    batch_slice.merge_semantic_result(result, {"status": "partial", "result_contract": contract}, result["identity"])
    assert result["result_contract"] == contract
    assert result["failure_categories"] == ["known_unknown_effect"]
    assert result["candidate_diagnostics"]["failure_categories"] == ["semantic_analysis_gap"]


def test_formal_run_rejects_case_filter_before_creating_output(tmp_path):
    inventory = {"input_policy": "frozen-agent", "cases": [{"dataset": "d", "case_id": "a"}]}
    with pytest.raises(ValueError, match="no case filter"):
        asyncio.run(batch_slice.run(inventory, tmp_path / "formal", case_ids=["a"], semantic_gate=tmp_path / "gate"))
    assert not (tmp_path / "formal").exists()


@pytest.mark.parametrize("tool", [None, "directory", "nonexecutable"])
def test_formal_run_rejects_unusable_codeql_before_creating_output(tmp_path, tool):
    codeql = None
    if tool == "directory":
        codeql = tmp_path
    elif tool == "nonexecutable":
        codeql = tmp_path / "codeql"
        codeql.write_text("not executable")
    inventory = {"input_policy": "frozen-agent", "cases": []}
    with pytest.raises(ValueError, match="executable CodeQL file"):
        asyncio.run(batch_slice.run(inventory, tmp_path / "formal", semantic_gate=tmp_path / "gate",
                                   baseline=tmp_path / "baseline", codeql=codeql))
    assert not (tmp_path / "formal").exists()


def test_semantic_rejection_isolated_and_later_case_still_recorded(monkeypatch, tmp_path):
    inventory = {"input_policy": "frozen-agent", "registry_root": str(tmp_path), "cases": [
        {"case_id": name, "dataset": "frozen", "inventory_revision": "r1",
         "snapshot_id": "s", "language": "python"} for name in ("bad", "last")]}

    class Dataset:
        def __init__(self, *args, **kwargs):
            pass

        @contextmanager
        def load_case(self, case_id):
            yield SimpleNamespace(case_id=case_id, snapshot_id="s", language="python")

    async def analyse(case, **kwargs):
        return {"status": "partial", "failure_categories": ["semantic_analysis_gap"]}

    async def semantic(entry, result, **kwargs):
        if entry["case_id"] == "bad":
            contract = copy.deepcopy(result["result_contract"])
            contract["identity"]["snapshot_id"] = "wrong"
            return {"status": "partial", "result_contract": contract}
        return {"status": "unsupported", "reason": "not in adapter domain"}

    ledger = [{**entry, "masked_test_sha256": None, "target_selector": None} for entry in inventory["cases"]]
    monkeypatch.setattr(batch_slice, "verify_formal_inputs", lambda *args: {"baseline_input_identity": ledger})
    monkeypatch.setattr(batch_slice, "FrozenDataset", Dataset)
    monkeypatch.setattr(batch_slice, "run_case", analyse)
    monkeypatch.setattr(batch_slice, "run_semantic_backend", semantic)
    monkeypatch.setattr(batch_slice, "_backend_paths", lambda: None)
    output = tmp_path / "formal"
    codeql = tmp_path / "codeql"
    codeql.write_text("fake executable; semantic dispatch is replaced in this isolation test")
    codeql.chmod(0o700)
    summary = asyncio.run(batch_slice.run(inventory, output, workers=2,
                                         semantic_gate=tmp_path / "gate", baseline=tmp_path / "baseline",
                                         codeql=codeql))
    rows = json.loads((output / "results.json").read_text())["cases"]
    assert [row["status"] for row in rows] == ["failed", "unsupported"]
    assert all(row["result_contract"]["identity"]["snapshot_id"] == "s" for row in rows)
    assert summary["semantic_backend_status"] == {"failed": 1, "unsupported": 1}
    assert (output / "semantic" / "1-rejected-result.json").exists()
