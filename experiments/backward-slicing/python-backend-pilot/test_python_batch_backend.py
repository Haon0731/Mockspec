from __future__ import annotations

import asyncio
import json
import importlib.util
import os
from pathlib import Path
from types import SimpleNamespace

_spec = importlib.util.spec_from_file_location(
    "python_batch_backend_test_subject", Path(__file__).with_name("batch_semantic_backend.py"))
assert _spec is not None and _spec.loader is not None
backend = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(backend)


def admitted_entry(case_id: str) -> dict:
    spec = backend.SUPPORTED[case_id]
    return {
        "language": "python", "dataset": spec["dataset"], "case_id": case_id,
        "inventory_revision": backend.INVENTORY_REVISION, "snapshot_id": spec["snapshot_id"],
        "snapshot_root": f"/frozen/{spec['snapshot_id']}",
        "admission_status": "frozen_candidate", "admission_verified": False,
        "reviewed_snapshot_id": None,
        "input_context": {
            "target_selector": spec["target_selector"],
            "masking_contract": spec["masking_contract"],
            "masked_test_sha256": spec["masked_test_sha256"],
            "sources": [{"logical_path": path, "sha256": sha}
                        for path, sha in spec["sources"].items()],
        },
    }


def case_result(case_id: str) -> dict:
    spec = backend.SUPPORTED[case_id]
    return {
        "schema_version": "mock-spec.backward-analysis-case/v1",
        "identity": {
            "case_id": case_id, "dataset": spec["dataset"], "language": "python",
            "snapshot_id": spec["snapshot_id"], "masking_contract": spec["masking_contract"],
            "masked_test_sha256": spec["masked_test_sha256"],
            "target_selector": spec["target_selector"],
        },
        "source_scope": [{"logical_path": path, "sha256": sha, "source_id": f"source:{index}"}
                         for index, (path, sha) in enumerate(spec["sources"].items())],
        "observations": [{
            "observation_id": observation_id,
            "location": {"source_id": "test", "start_line": line, "end_line": line},
        } for observation_id, line in spec["observations"]],
        "semantic_validation": {"status": "verified_incomplete"},
        "completion": {"status": "partial"},
    }


def fake_driver(monkeypatch, *, mutate_source: bool = False):
    monkeypatch.setattr(backend, "validate_result_contract", lambda _: None)
    monkeypatch.setattr(backend, "_validate_support_hashes", lambda _: None)

    calls = []

    def invoke(command, **kwargs):
        calls.append({"command": command, "kwargs": kwargs})
        target = Path(command[command.index("--output") + 1])
        target.mkdir(parents=True)
        if "run_demand_analysis.py" in command[1]:
            case = case_result("flask_308")
            if mutate_source:
                case["source_scope"][0]["sha256"] = "0" * 64
            (target / "result.json").write_text(json.dumps({"status": "passed", "cost": {"total_seconds": 1}}))
            (target / "observation-study.json").write_text(json.dumps({"case_result": case}))
            (target / "demand-mechanism-summary.json").write_text("{}")
        else:
            case = case_result("langflow_189")
            (target / "study.json").write_text(json.dumps({
                "case_result": case, "cost": {"total_seconds": 1}
            }))
            (target / "receipt.json").write_text(json.dumps({
                "status": "verified_incomplete", "target_executed": False
            }))
            (target / "input-manifest.json").write_text("{}")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(backend.subprocess, "run", invoke)
    return calls


def executable(tmp_path: Path) -> Path:
    path = tmp_path / "codeql"
    path.write_text("stub")
    return path


def test_unknown_case_is_explicitly_unsupported_without_starting_driver(tmp_path, monkeypatch):
    monkeypatch.setattr(backend.subprocess, "run", lambda *_args, **_kwargs: (_ for _ in ()).throw(
        AssertionError("unsupported input must not start a driver")
    ))
    result = backend.run({
        "case_id": "other", "language": "python", "dataset": "python-benchmark-new"
    }, tmp_path, tmp_path / "out", executable(tmp_path))

    assert result["status"] == "unsupported"
    assert result["result_contract"] is None
    assert "not label this input as CodeQL-analysed" in result["limitations"][2]
    assert json.loads((tmp_path / "out/batch-result.json").read_text())["status"] == "unsupported"


def test_supported_case_requires_admitted_input_context(tmp_path):
    entry = admitted_entry("flask_308")
    del entry["input_context"]["masked_test_sha256"]

    result = backend.run(entry, tmp_path, tmp_path / "out", executable(tmp_path))

    assert result["status"] == "unsupported"
    assert "masked_test_sha256" in result["limitations"][0]


def test_flask_rerun_returns_only_original_three_observations(tmp_path, monkeypatch):
    fake_driver(monkeypatch)
    result = backend.run(
        admitted_entry("flask_308"), tmp_path, tmp_path / "out", executable(tmp_path)
    )

    assert result["status"] == "complete"
    assert [row["observation_id"] for row in result["result_contract"]["observations"]] == [
        "criterion:0", "criterion:1", "criterion:2"
    ]
    assert result["provenance"]["mechanism_probes_counted_as_dataset_cases"] is False
    assert result["terminal"] == {
        "execution": "complete", "semantic_validation": "verified_incomplete",
        "completion": "partial",
    }


def test_langflow_rerun_returns_validated_case_contract(tmp_path, monkeypatch):
    calls = fake_driver(monkeypatch)
    result = backend.run(
        admitted_entry("langflow_189"), tmp_path, tmp_path / "out", executable(tmp_path)
    )

    assert result["status"] == "complete"
    assert result["backend"] == "langflow_origin_flow"
    assert result["source"]["adapter_validation"] == "exact_paths_and_sha256"
    assert result["provenance"]["target_executed"] is False
    assert len(result["result_contract"]["observations"]) == 2
    assert calls[0]["kwargs"]["env"] == {
        "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8", "PATH": os.defpath,
    }
    assert "HOME" not in calls[0]["kwargs"]["env"]
    assert result["provenance"]["driver_environment"]["python_executable"]
    assert result["provenance"]["driver_environment"]["policy"] \
        == "fixed locale plus os.defpath; no inherited environment or credentials"


def test_changed_adapter_source_fails_closed_and_preserves_failure(tmp_path, monkeypatch):
    fake_driver(monkeypatch, mutate_source=True)
    result = backend.run(
        admitted_entry("flask_308"), tmp_path, tmp_path / "out", executable(tmp_path)
    )

    assert result["status"] == "failed"
    assert result["error"]["type"] == "ValueError"
    assert "source scope" in result["error"]["message"]
    assert (tmp_path / "out/driver/observation-study.json").is_file()


def test_async_interface_uses_same_strict_runner(tmp_path, monkeypatch):
    fake_driver(monkeypatch)
    result = asyncio.run(backend.run_async(
        admitted_entry("langflow_189"), tmp_path, tmp_path / "out", executable(tmp_path)
    ))

    assert result["status"] == "complete"
    assert json.loads((tmp_path / "out/batch-result.json").read_text())["backend"] \
        == "langflow_origin_flow"
