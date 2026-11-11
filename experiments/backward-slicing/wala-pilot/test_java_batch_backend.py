from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
SPEC = importlib.util.spec_from_file_location("java_batch_semantic_backend", HERE / "batch_semantic_backend.py")
assert SPEC is not None and SPEC.loader is not None
bridge = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(bridge)


def supported_entry() -> dict:
    return dict(bridge.SUPPORTED)


def test_other_java_case_is_explicitly_unsupported_without_backend(monkeypatch):
    entry = supported_entry()
    entry["case_id"] = "other"
    monkeypatch.setattr(bridge, "run_supported", lambda *_: (_ for _ in ()).throw(AssertionError()))
    result = bridge.execute(entry, Path("/registry"), Path("/output"))
    assert result["status"] == "unsupported"
    assert result["backend_attempted"] is False
    assert result["candidate_diagnostic_required"] is True


def test_identity_gate_rejects_each_frozen_identity_change():
    for field in bridge.SUPPORTED:
        entry = supported_entry()
        entry[field] = "changed"
        assert bridge._identity_matches(entry) is False


def native_contract() -> dict:
    path = (
        bridge.ROOT
        / "out/mockbench-study/issue28-java-demand-cold-20261111-r7/summary.json"
    )
    return json.loads(path.read_text(encoding="utf-8"))["result_contract"]


def current_context() -> dict:
    criteria = []
    for item in native_contract()["observations"]:
        criteria.append({
            "observation_id": item["observation_id"],
            "start_line": item["location"]["start_line"],
            "end_line": item["location"]["end_line"],
            "expression": f"expression:{item['observation_id']}",
            "sink": f"ObservationSink.{item['observation_id']}",
            "parse_provenance": "top-level supported JUnit assertion actual argument",
        })
    return {
        "target_selector": bridge.EXPECTED_SELECTOR,
        "masking_contract": "java-dual-region-v1",
        "masked_test_sha256": bridge.EXPECTED_SOURCE_SHA256["test"],
        "masked_test_logical_path": "src/test/java/com/google/cloud/tools/jib/cli/jar/JarFilesTest.java",
        "masked_test_bytes": 4271,
        "mut_logical_path": bridge.MUT_PATH,
        "mut_bytes": 3996,
        "processor_logical_path": bridge.PROCESSOR_PATH,
        "processor_bytes": 3000,
        "source_sha256": dict(bridge.EXPECTED_SOURCE_SHA256),
        "adapter": {
            "status": "verified",
            "original_test_sha256": bridge.EXPECTED_SOURCE_SHA256["test"],
            "generated_entry_sha256": "a" * 64,
            "mut_call": {}, "plan_binding": {}, "criteria": criteria,
            "parse_provenance": {
                "backend": "strict_supported_java_syntax",
                "expected_literals_used": False,
                "target_executed": False,
            },
        },
    }


def test_projection_rebinds_only_case_context_and_preserves_relations():
    original = native_contract()
    contract, receipt = bridge._project_contract(original, supported_entry(), current_context())
    assert contract["identity"]["dataset"] == "java-bechmark-strong"
    assert contract["identity"]["target_selector"] == bridge.EXPECTED_SELECTOR
    assert contract["source_scope"][0]["sha256"] == bridge.EXPECTED_SOURCE_SHA256["test"]
    assert contract["source_scope"][2]["source_id"] == "processor"
    assert "source_scope.processor" in receipt["changed_fields"]
    assert contract["relations"] == original["relations"]
    assert contract["completion"]["failure_categories"] == []
    assert contract["completion"]["stage"].endswith("automatic_source_adapter")
    assert receipt["original_completion"]["failure_categories"] == [
        "original_observation_mapping_remains_diagnostic_override"
    ]
    assert receipt["relations"] == {
        "count": 173,
        "all_observation_references_current": True,
        "relation_endpoints_rewritten": False,
    }
    assert receipt["adapter"]["observation_count"] == 19
    assert contract["analysis_boundaries"]["assumptions"] == []
    assert all(
        item["provenance"]["origin"] == "automatic_source_derived_observation_adapter"
        for item in contract["observations"]
    )


def test_projection_rejects_changed_observation_range():
    context = current_context()
    context["adapter"]["criteria"][0]["start_line"] += 1
    try:
        bridge._project_contract(native_contract(), supported_entry(), context)
    except ValueError as exc:
        assert "observations differ" in str(exc)
    else:
        raise AssertionError("changed observation was accepted")


def test_projection_rejects_relation_to_unknown_observation():
    contract = native_contract()
    contract["relations"][0]["observation_ids"] = ["criterion:unknown"]
    try:
        bridge._project_contract(contract, supported_entry(), current_context())
    except ValueError as exc:
        assert "non-current observation" in str(exc)
    else:
        raise AssertionError("unknown observation reference was accepted")
