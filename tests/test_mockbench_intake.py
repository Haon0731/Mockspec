"""Full-cohort input checks do not require program analysis or manual MUT labels."""
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from mock_spec.validation import InputValidationError

PATH = Path(__file__).resolve().parents[1] / "experiments/mockbench-study/batch.py"
SPEC = importlib.util.spec_from_file_location("study_intake", PATH)
batch = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(batch)


def inventory():
    return {
        "schema_version": batch.INVENTORY_SCHEMA, "input_policy": "frozen-agent",
        "registry_root": "/explicit-host-registry", "cases": [
            {"language": "python", "dataset": "python-benchmark-new", "case_id": case,
             "snapshot_id": case, "inventory_revision": "revision", "admission_status": "frozen_candidate"}
            for case in ("first", "second", "third")
        ],
    }


class Case:
    language = "python"
    target_selector = "test_target"
    source_paths = ("test.py", "src.py")
    masked_test = SimpleNamespace(logical_path="test.py", end_line=2, text="def test_target():\n    pass\n")

    def __init__(self, case_id):
        self.snapshot_id = case_id
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.closed = True


def test_all_cases_load_and_close_without_analysis_or_mut(tmp_path, monkeypatch):
    from mock_spec.adapters import frozen_dataset
    loaded = []

    class Dataset:
        def __init__(self, *args, **kwargs):
            pass

        def load_case(self, case_id):
            case = Case(case_id)
            loaded.append(case)
            return case

    monkeypatch.setattr(frozen_dataset, "FrozenDataset", Dataset)
    summary = batch.check_inputs(inventory(), tmp_path / "run")
    assert summary["counts"] == {"total": 3, "loaded": 3, "failed": 0}
    assert all(case.closed for case in loaded)
    rows = json.loads((tmp_path / "run/results.json").read_text())["results"]
    assert all(r["mut_selection"] == "not_requested" for r in rows)
    assert all("text" not in r["masked_test"] for r in rows)
    with pytest.raises(FileExistsError):
        batch.check_inputs(inventory(), tmp_path / "run")


def test_rejected_input_and_identity_mismatch_do_not_stop_batch(tmp_path, monkeypatch):
    from mock_spec.adapters import frozen_dataset

    class Dataset:
        def __init__(self, *args, **kwargs):
            pass

        def load_case(self, case_id):
            if case_id == "first":
                raise InputValidationError("mask changed")
            return Case("different-snapshot" if case_id == "second" else case_id)

    monkeypatch.setattr(frozen_dataset, "FrozenDataset", Dataset)
    summary = batch.check_inputs(inventory(), tmp_path / "run")
    assert summary["counts"] == {"total": 3, "loaded": 1, "failed": 2}
    assert len(list((tmp_path / "run").glob("case-*.json"))) == 3


def test_frozen_inventory_cannot_silently_use_legacy_syntax_batch(tmp_path):
    with pytest.raises(ValueError, match="check-inputs"):
        batch.run_batch(inventory(), tmp_path / "run")
    assert not (tmp_path / "run").exists()
