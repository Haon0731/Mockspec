import asyncio
import importlib.util
import json
from pathlib import Path

import pytest

from mock_spec.schemas import UnifiedTask
from mock_spec.validation import InputValidationError
from test_schemas import valid_task_data


MODULE_PATH = (
    Path(__file__).resolve().parents[1] / "experiments" / "mockbench-study" / "batch.py"
)
SPEC = importlib.util.spec_from_file_location("mockbench_study_batch", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
batch = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(batch)


def _case(
    case_id: str, *, admission_status: str = "reviewed_candidate",
    language: str = "python", dataset: str = "python-benchmark-new",
) -> dict:
    return {
        "language": language,
        "dataset": dataset,
        "case_id": case_id,
        "admission_status": admission_status,
        "snapshot_root": f"/frozen/{dataset}/{case_id}",
    }


def _inventory(*cases: dict) -> dict:
    return {"schema_version": batch.INVENTORY_SCHEMA, "cases": list(cases)}


def _task(case_id: str) -> UnifiedTask:
    data = valid_task_data()
    data["case_id"] = case_id
    return UnifiedTask.from_dict(data)


class SuccessfulAnalysis:
    def __init__(self, task):
        self.task = task

    async def inspect_program_structure(self, source_id):
        return {
            "status": "ok",
            "facts": [{"kind": "function", "source_id": source_id}],
            "limitations": ["syntax only"],
        }

    async def inspect_dependency_usage(self, source_id):
        return {
            "status": "unresolved",
            "facts": [],
            "diagnostics": ["no supported fact"],
            "limitations": ["syntax local"],
        }


def test_unadmitted_case_is_retained_without_loading(tmp_path, monkeypatch):
    def forbidden_loader(*args, **kwargs):
        raise AssertionError("unadmitted cases must not reach an adapter")

    monkeypatch.setattr(batch.python, "load_task", forbidden_loader)
    summary = batch.run_batch(
        _inventory(_case("pending", admission_status="pending_review")), tmp_path / "out"
    )

    result = json.loads((tmp_path / "out" / "results.json").read_text())["results"][0]
    assert result["run_status"] == "not_admitted"
    assert result["failure_stage"] == "admission"
    assert summary["counts"] == {
        "total": 1, "review_candidates": 0, "selected": 0,
        "attempted": 0, "admitted": 0, "analysis_attempted": 0,
        "observed": 0, "partial": 0, "failed": 0, "blocked": 1,
        "by_status": {"not_admitted": 1},
    }


def test_adapter_revalidates_catalog_identity_and_records_rejection(tmp_path, monkeypatch):
    def rejecting_loader(snapshot_root, *, case_id):
        assert snapshot_root == Path("/frozen/python-benchmark-new/rejected")
        assert case_id == "rejected"
        raise InputValidationError("case has no unique approved input review")

    monkeypatch.setattr(batch.python, "load_task", rejecting_loader)
    summary = batch.run_batch(_inventory(_case("rejected")), tmp_path / "out")

    result = json.loads((tmp_path / "out" / "results.json").read_text())["results"][0]
    assert result["run_status"] == "input_rejected"
    assert result["failure_stage"] == "admission"
    assert result["failure_category"] == "input_rejected"
    assert result["admission_verified"] is False
    assert summary["counts"]["failed"] == 1
    assert summary["counts"]["admitted"] == 0


def test_unexpected_adapter_failure_is_runtime_error(tmp_path, monkeypatch):
    def broken_loader(snapshot_root, *, case_id):
        raise RuntimeError("adapter bug")

    monkeypatch.setattr(batch.python, "load_task", broken_loader)
    batch.run_batch(_inventory(_case("broken-adapter")), tmp_path / "out")

    result = json.loads((tmp_path / "out" / "results.json").read_text())["results"][0]
    assert result["run_status"] == "unexpected_error"
    assert result["failure_stage"] == "runtime"
    assert result["failure_category"] == "unexpected_error"


def test_failure_does_not_stop_batch_and_limit_keeps_denominator(tmp_path, monkeypatch):
    monkeypatch.setattr(batch.python, "load_task", lambda root, *, case_id: _task(case_id))

    class ConditionalAnalysis(SuccessfulAnalysis):
        async def inspect_program_structure(self, source_id):
            if self.task.case_id == "broken":
                return {
                    "status": "error", "error_code": "backend_unavailable",
                    "failure_kind": "backend_unavailable", "facts": [],
                    "diagnostics": ["pinned parser missing"], "limitations": [],
                }
            return await super().inspect_program_structure(source_id)

    monkeypatch.setattr(batch, "SourceAnalysis", ConditionalAnalysis)
    summary = batch.run_batch(
        _inventory(_case("broken"), _case("good"), _case("outside-limit")),
        tmp_path / "out", limit=2,
    )

    document = json.loads((tmp_path / "out" / "results.json").read_text())
    results = document["results"]
    assert [item["run_status"] for item in results] == [
        "analysis_failed", "observed", "not_selected"
    ]
    assert results[0]["failure_stage"] == "analysis"
    assert results[0]["failure_category"] == "backend_unavailable"
    assert results[1]["observations"][0]["tools"]["program_structure"]["facts"]
    assert all(
        value["status"] == "not_evaluated"
        for value in results[1]["semantic_evaluation"].values()
    )
    assert summary["counts"]["total"] == 3
    assert summary["counts"]["review_candidates"] == 3
    assert summary["counts"]["attempted"] == 2
    assert summary["counts"]["admitted"] == 2
    assert summary["counts"]["analysis_attempted"] == 2
    assert summary["counts"]["observed"] == 1
    assert summary["counts"]["failed"] == 1
    assert summary["counts"]["blocked"] == 1
    assert len(list((tmp_path / "out").glob("case-*.json"))) == 3
    assert summary["by_failure_stage"] == {"analysis": 1, "selection": 1}
    assert summary["by_failure_category"] == {
        "backend_unavailable": 1, "batch_limit": 1,
    }


def test_case_timeout_is_isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(batch.python, "load_task", lambda root, *, case_id: _task(case_id))

    class SlowAnalysis(SuccessfulAnalysis):
        async def inspect_dependency_usage(self, source_id):
            await asyncio.sleep(0.1)
            return await super().inspect_dependency_usage(source_id)

    monkeypatch.setattr(batch, "SourceAnalysis", SlowAnalysis)
    summary = batch.run_batch(
        _inventory(_case("slow")), tmp_path / "out", timeout_seconds=0.01
    )

    result = json.loads((tmp_path / "out" / "results.json").read_text())["results"][0]
    assert result["run_status"] == "timeout"
    assert result["failure_stage"] == "runtime"
    assert result["failure_category"] == "timeout"
    assert result["admission_verified"] is True
    assert result["analysis_attempted"] is True
    assert result["observations"][0]["tools"]["program_structure"]["status"] == "ok"
    assert "cannot preempt" in result["limitations"][0]
    assert summary["counts"]["failed"] == 1


def test_incomplete_analysis_is_partial_not_observed_or_failed(tmp_path, monkeypatch):
    monkeypatch.setattr(batch.python, "load_task", lambda root, *, case_id: _task(case_id))

    class IncompleteAnalysis(SuccessfulAnalysis):
        async def inspect_program_structure(self, source_id):
            return {
                "status": "incomplete", "facts": [], "error_code": "output_limit",
                "diagnostics": ["truncated"], "limitations": ["partial output"],
            }

    monkeypatch.setattr(batch, "SourceAnalysis", IncompleteAnalysis)
    summary = batch.run_batch(_inventory(_case("partial")), tmp_path / "out")

    result = json.loads((tmp_path / "out" / "results.json").read_text())["results"][0]
    assert result["run_status"] == "analysis_incomplete"
    assert summary["counts"]["partial"] == 1
    assert summary["counts"]["observed"] == 0
    assert summary["counts"]["failed"] == 0


def test_parse_failure_uses_reported_backend_failure_kind(tmp_path, monkeypatch):
    monkeypatch.setattr(batch.python, "load_task", lambda root, *, case_id: _task(case_id))

    class ParseFailureAnalysis(SuccessfulAnalysis):
        async def inspect_program_structure(self, source_id):
            return {
                "status": "error", "error_code": "analysis_error",
                "failure_kind": "parse_error", "facts": [],
                "diagnostics": ["source could not be parsed"], "limitations": [],
            }

    monkeypatch.setattr(batch, "SourceAnalysis", ParseFailureAnalysis)
    summary = batch.run_batch(_inventory(_case("parse-failure")), tmp_path / "out")

    result = json.loads((tmp_path / "out" / "results.json").read_text())["results"][0]
    assert result["failure_category"] == "parse_error"
    assert summary["by_failure_category"] == {"parse_error": 1}


@pytest.mark.parametrize(
    "change,match",
    [
        ({"dataset": "forged"}, "dataset differs"),
        ({"snapshot_id": "forged"}, "identity differs"),
    ],
)
def test_inventory_cannot_spoof_snapshot_identity(tmp_path, monkeypatch, change, match):
    def forbidden_loader(*args, **kwargs):
        raise AssertionError("identity mismatch must fail before adapter loading")

    monkeypatch.setattr(batch.python, "load_task", forbidden_loader)
    case = _case("snapshot")
    case.update(change)
    batch.run_batch(_inventory(case), tmp_path / "out")

    result = json.loads((tmp_path / "out" / "results.json").read_text())["results"][0]
    assert result["run_status"] == "input_rejected"
    assert match in result["failure"]["message"]


def test_adapter_task_identity_must_match_inventory(tmp_path, monkeypatch):
    monkeypatch.setattr(
        batch.python, "load_task", lambda root, *, case_id: _task("different-case")
    )
    batch.run_batch(_inventory(_case("expected-case")), tmp_path / "out")

    result = json.loads((tmp_path / "out" / "results.json").read_text())["results"][0]
    assert result["run_status"] == "input_rejected"
    assert "task identity differs" in result["failure"]["message"]


def test_inventory_revision_must_match_installed_review(tmp_path, monkeypatch):
    def forbidden_loader(*args, **kwargs):
        raise AssertionError("revision mismatch must fail before adapter loading")

    monkeypatch.setattr(batch.java, "load_task", forbidden_loader)
    case = {
        "language": "java",
        "dataset": "java-bechmark-strong",
        "case_id": (
            "com.google.cloud.tools.jib.cli.jar.JarFilesTest#"
            "testToJibContainerBuilder_explodedStandard_basicInfo@69ae66df"
        ),
        "admission_status": "reviewed_candidate",
        "snapshot_id": "5b93995c794af0152854a01ddda2fe77948b5ab828b05282b00728f7729f64d4",
        "snapshot_root": (
            "/frozen/java-bechmark-strong/"
            "5b93995c794af0152854a01ddda2fe77948b5ab828b05282b00728f7729f64d4"
        ),
        "inventory_revision": "0" * 64,
    }
    batch.run_batch(_inventory(case), tmp_path / "out")

    result = json.loads((tmp_path / "out" / "results.json").read_text())["results"][0]
    assert result["run_status"] == "input_rejected"
    assert "installed input review" in result["failure"]["message"]


def test_run_inputs_and_implementation_manifest_are_persisted(tmp_path, monkeypatch):
    monkeypatch.setattr(batch.python, "load_task", lambda root, *, case_id: _task(case_id))
    monkeypatch.setattr(batch, "SourceAnalysis", SuccessfulAnalysis)
    inventory = _inventory(_case("manifest"))
    batch.run_batch(inventory, tmp_path / "out", timeout_seconds=3.5, limit=1)

    assert json.loads((tmp_path / "out" / "inventory.json").read_text()) == inventory
    manifest = json.loads((tmp_path / "out" / "run-manifest.json").read_text())
    assert manifest["analysis_profile"] == "syntax_baseline"
    assert manifest["budget"] == {"per_case_deadline_seconds": 3.5, "case_limit": 1}
    assert len(manifest["implementation"]["sha256"]) == 64
    assert len(manifest["inventory_sha256"]) == 64


def test_existing_evidence_directory_is_not_overwritten(tmp_path):
    output = tmp_path / "existing"
    output.mkdir()
    marker = output / "evidence.json"
    marker.write_text("original", encoding="utf-8")

    with pytest.raises(FileExistsError):
        batch.run_batch(_inventory(), output)

    assert marker.read_text(encoding="utf-8") == "original"


@pytest.mark.parametrize("timeout", [0, -1, float("inf"), float("nan"), True])
def test_timeout_budget_must_be_positive_and_finite(tmp_path, timeout):
    with pytest.raises(ValueError, match="positive finite"):
        batch.run_batch(_inventory(), tmp_path / "out", timeout_seconds=timeout)


@pytest.mark.parametrize("limit", [0, -1, 1.5, True])
def test_case_limit_must_be_positive_integer(tmp_path, limit):
    with pytest.raises(ValueError, match="positive integer"):
        batch.run_batch(_inventory(), tmp_path / "out", limit=limit)


def test_duplicate_language_and_case_id_is_rejected_even_across_datasets(tmp_path):
    with pytest.raises(ValueError, match="unique"):
        batch.run_batch(
            _inventory(_case("same", dataset="one"), _case("same", dataset="two")),
            tmp_path / "out",
        )
