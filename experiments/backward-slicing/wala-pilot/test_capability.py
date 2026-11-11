import importlib.util
import copy
import json
from pathlib import Path
import sys


HERE = Path(__file__).resolve().parent


def load(name):
    spec = importlib.util.spec_from_file_location(name, HERE / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


build_jib_skeleton = load("build_jib_skeleton")
run_capability = load("run_capability")
summarize_capability = load("summarize_capability")
summarize_repair = load("summarize_repair")


def test_structure_only_jib_skeleton_contains_no_mock_answers(tmp_path):
    source = tmp_path / "JarFiles.java"
    source.write_text("package com.google.cloud.tools.jib.cli.jar; public class JarFiles {}\n")
    # A compile failure is acceptable here: the test inspects generated structural input.
    output = tmp_path / "output"
    result = build_jib_skeleton.build(source, "", output)
    assert result["status"] == "compile_failed"
    generated = "\n".join(path.read_text() for path in (output / "source").rglob("*.java"))
    assert "when(" not in generated
    assert "thenReturn" not in generated
    assert "mock(" not in generated
    assert "diagnostic_override" in " ".join(result["assumptions"])
    assert all(item["origin"] in {"reviewed_visible_source", "visible_signature_structure_only"}
               for item in result["mappings"])


def test_wala_backend_runs_real_state_and_alias_summaries(tmp_path):
    classpath = Path("/tmp/wala-cp.txt")
    assert classpath.is_file(), "generate with: mvn dependency:build-classpath -Dmdep.outputFile=/tmp/wala-cp.txt"
    output = tmp_path / "capability"
    probes = tuple(
        probe for probe in run_capability.PROBES
        if probe[0] in {"unknown_write", "unknown_alias"}
    )
    manifest = run_capability.run(classpath, output, probes)
    assert manifest["status"] == "analysed"
    state = json.loads((output / "unknown_write.json").read_text())
    alias = json.loads((output / "unknown_alias.json").read_text())
    assert summarize_capability._assess_backend_state(state, "mutate")["passed"] is True
    assert summarize_capability._assess_backend_alias(alias, "alias")["passed"] is True


def test_summary_keeps_failed_opaque_effect_gate_blocked():
    evidence = HERE.parents[2] / "out/mockbench-study/issue23-wala-evidence-20261110"
    assert evidence.is_dir(), f"missing checked-in issue-23 evidence: {evidence}"
    result = summarize_capability.summarize(evidence)
    assert result["visible_mechanisms"]["status"] == "passed"
    assert result["opaque_call_effects"]["status"] == "blocked"
    assert result["real_jib_input"]["wala_analysis"] == "resource_limit"
    assert result["real_jib_input"]["automatic_entry_recovery"] == "not_evaluated"
    assert result["gate_passed"] is False
    assert result["status"] == "blocked"


def _effect_result(kind, statements, edges, targets=("sample.Summary.mutate",)):
    return {
        "slice_statements": statements,
        "slice_edges": edges,
        "call_bindings": [{
            "caller": statements[0]["method"],
            "declared_target": "sample.UnknownDependency.mutate(Lsample/Box;)V",
            "status": "resolved_candidates" if targets else "unresolved",
            "possible_targets": list(targets),
        }],
        "opaque_effects": [{
            "kind": kind,
            "target": "sample.UnknownDependency.mutate",
            "provenance": "wala_method_summary",
        }],
    }


def test_effect_gate_accepts_multihop_state_path_not_only_direct_heap_edge():
    result = _effect_result("state", [
        {"method": "sample.Client.observe", "instruction": "invoke mutate"},
        {"method": "sample.Summary.mutate", "instruction": "putfield value"},
        {"method": "sample.Client.observe", "instruction": "getfield value"},
    ], [
        {"from": 0, "to": 1, "labels": ["PARAMETER_DEP"]},
        {"from": 1, "to": 2, "labels": ["HEAP_DATA_DEP"]},
    ])
    assessed = summarize_capability._assess_effect(result, "mutate", "state")
    assert assessed["passed"] is True
    assert assessed["witness_path"] == [0, 1, 2]


def test_effect_gate_distinguishes_alias_completion_exception_and_fresh_negative():
    alias = _effect_result("alias", [
        {"method": "sample.Client.aliasObserve", "instruction": "invoke alias"},
        {"kind": "NORMAL_RET_CALLEE", "method": "sample.Summary.alias", "instruction": "return v2"},
        {"method": "sample.Client.aliasObserve", "instruction": "putfield value"},
        {"method": "sample.Client.aliasObserve", "instruction": "getfield value"},
    ], [
        {"from": 0, "to": 1, "labels": ["PARAMETER_DEP"]},
        {"from": 1, "to": 2, "labels": ["DATA_DEP"]},
        {"from": 2, "to": 3, "labels": ["HEAP_DATA_DEP"]},
    ], targets=("sample.Summary.alias",))
    alias["opaque_effects"][0]["target"] = "sample.UnknownDependency.alias"
    alias["call_bindings"][0]["declared_target"] = "sample.UnknownDependency.alias(Lsample/Box;)Lsample/Box;"
    assert summarize_capability._assess_effect(alias, "alias", "alias")["passed"] is True

    completion = _effect_result("normal_completion", [
        {"method": "sample.Client.complete", "instruction": "invoke mutate"},
        {"kind": "NORMAL_RET_CALLER", "method": "sample.Client.complete", "instruction": "invoke mutate"},
        {"method": "sample.Client.complete", "instruction": "invoke observe"},
    ], [
        {"from": 0, "to": 1, "labels": ["CONTROL_DEP"]},
        {"from": 1, "to": 2, "labels": ["CONTROL_DEP"]},
    ])
    assessed = summarize_capability._assess_effect(completion, "mutate", "normal_completion")
    assert assessed["passed"] is True
    assert assessed["field_heap_edge_required"] is False

    exceptional = _effect_result("write_before_throw", [
        {"method": "sample.Client.catchObserve", "instruction": "invoke mutate"},
        {"method": "sample.Summary.mutate", "instruction": "putfield value"},
        {"method": "sample.Summary.mutate", "instruction": "throw"},
        {"method": "sample.Client.catchObserve", "instruction": "get caught exception"},
        {"method": "sample.Client.catchObserve", "instruction": "getfield value"},
    ], [
        {"from": 0, "to": 1, "labels": ["PARAMETER_DEP"]},
        {"from": 1, "to": 2, "labels": ["CONTROL_DEP"]},
        {"from": 2, "to": 3, "labels": ["EXCEPTION_DEP"]},
        {"from": 3, "to": 4, "labels": ["CONTROL_DEP"]},
        {"from": 1, "to": 4, "labels": ["HEAP_DATA_DEP"]},
    ])
    assert summarize_capability._assess_effect(
        exceptional, "mutate", "write_before_throw")["passed"] is True

    fresh = _effect_result("fresh_negative", [
        {"method": "sample.Client.freshObserve", "instruction": "invoke mutate"},
        {"method": "sample.Summary.mutate", "instruction": "putfield value"},
        {"method": "sample.Client.freshObserve", "instruction": "getfield value"},
    ], [{"from": 0, "to": 1, "labels": ["PARAMETER_DEP"]}])
    fresh["region_evidence"] = {
        "argument_region": "allocation@fresh",
        "observed_region": "entry-parameter@observed",
        "points_to_disjoint": True,
        "unknown_region_overflow": False,
    }
    assessed = summarize_capability._assess_effect(fresh, "mutate", "fresh_negative")
    assert assessed["passed"] is True
    assert assessed["shared_region_path"] is False


def test_gate_requires_positive_analysis_completion_and_tracks_entry_origin():
    decision = summarize_capability._analysis_gate({
        "timed_out": False,
        "oom": False,
        "analysis_completed": False,
        "entry_origin": "reviewed_structure_skeleton",
    })
    assert decision["passed"] is False
    assert decision["automatic_entry_recovery"] is False


def test_effect_gate_rejects_control_only_disconnected_and_unproven_negative():
    state = _effect_result("state", [
        {"method": "sample.Client.observe", "instruction": "invoke mutate"},
        {"method": "sample.Summary.mutate", "instruction": "putfield value"},
        {"method": "sample.Client.observe", "instruction": "getfield value"},
    ], [
        {"from": 0, "to": 1, "labels": ["CONTROL_DEP"]},
        {"from": 1, "to": 2, "labels": ["CONTROL_DEP"]},
    ])
    assert summarize_capability._assess_effect(state, "mutate", "state")["passed"] is False

    exceptional = _effect_result("write_before_throw", [
        {"method": "sample.Client.catchObserve", "instruction": "invoke mutate"},
        {"method": "sample.Summary.mutate", "instruction": "putfield value"},
        {"method": "sample.Summary.mutate", "instruction": "throw"},
        {"method": "sample.Client.catchObserve", "instruction": "get caught exception"},
        {"method": "sample.Client.catchObserve", "instruction": "getfield value"},
    ], [
        {"from": 1, "to": 2, "labels": ["CONTROL_DEP"]},
        {"from": 2, "to": 3, "labels": ["EXCEPTION_DEP"]},
        {"from": 1, "to": 4, "labels": ["HEAP_DATA_DEP"]},
    ])
    assert summarize_capability._assess_effect(
        exceptional, "mutate", "write_before_throw")["passed"] is False

    fresh = _effect_result("fresh_negative", [
        {"method": "sample.Client.freshObserve", "instruction": "invoke mutate"},
    ], [])
    assert summarize_capability._assess_effect(
        fresh, "mutate", "fresh_negative")["passed"] is False


def test_repair_report_preserves_historical_failure_and_does_not_upgrade_binding():
    mechanisms = HERE.parents[2] / "out/mockbench-study/issue23-repair-20261111-mechanisms-r5"
    historical = HERE.parents[2] / "out/mockbench-study/issue23-wala-evidence-20261110"
    report = summarize_repair.summarize(mechanisms, historical)
    assert report["schema_version"] == "mockspec.wala-issue23-repair/v2"
    assert report["mechanism_run"]["all_opaque_calls_bound_to_summary_target"] is True
    assert all(not row["passed"] for row in report["mechanism_run"]["relations"].values())
    assert report["jib_scale_diagnostic"]["rerun"] == "not_rerun"
    assert report["gate_passed"] is False


def test_repair_overall_gate_stays_blocked_when_mechanisms_pass_but_jib_not_run(monkeypatch):
    mechanisms = HERE.parents[2] / "out/mockbench-study/issue23-repair-20261111-mechanisms-r5"
    historical = HERE.parents[2] / "out/mockbench-study/issue23-wala-evidence-20261110"

    def passed_effect(result, target, relation):
        return {
            "passed": True,
            "call_binding": "resolved_candidates",
            "possible_target_count": 1,
            "witness_path": [0, 1],
            "field_heap_edge_required": relation in {"state", "write_before_throw"},
            "shared_region_path": False,
            "summary_effect_kinds": [relation],
            "region_evidence": None,
        }

    monkeypatch.setattr(summarize_repair, "_assess_effect", passed_effect)
    report = summarize_repair.summarize(mechanisms, historical)
    assert report["mechanism_gate_passed"] is True
    assert report["gate_components"]["positive_real_input_analysis"] is False
    assert report["gate_components"]["accepted_unknown_effect_model"] is False
    assert report["gate_passed"] is False
    assert report["status"] == "blocked"


def test_real_backend_evidence_requires_summary_ir_and_pointer_region_receipts():
    root = HERE.parents[2] / "out/mockbench-study"
    evidence = root / "issue23-propagation-20261111-final-regression-r3"
    state = json.loads((evidence / "unknown_write.json").read_text())
    alias = json.loads((evidence / "unknown_alias.json").read_text())
    assert summarize_capability._assess_backend_state(state, "mutate")["passed"] is True
    assert summarize_capability._assess_backend_alias(alias, "alias")["passed"] is True


def test_backend_witness_rejects_native_empty_ir_wrong_region_and_broken_alias():
    root = HERE.parents[2] / "out/mockbench-study"
    native = json.loads((
        root / "issue23-propagation-20261111-config-a/unknown_write.json"
    ).read_text())
    assert summarize_capability._assess_backend_state(native, "mutate")["passed"] is False

    evidence = root / "issue23-propagation-20261111-final-regression-r3"
    state = json.loads((evidence / "unknown_write.json").read_text())
    wrong_region = copy.deepcopy(state)
    caller_return = next(
        row for row in wrong_region["slice_statements"]
        if row["kind"] == "HEAP_RET_CALLER" and "mutate" in row["statement_text"]
    )
    caller_return["heap_location"] = "different-region"
    assert summarize_capability._assess_backend_state(
        wrong_region, "mutate")["passed"] is False

    alias = json.loads((evidence / "unknown_alias.json").read_text())
    disjoint = copy.deepcopy(alias)
    returned = next(
        row for row in disjoint["entry_local_points_to"] if row["value_number"] == 5
    )
    returned["regions"] = ["fresh-return-region"]
    assert summarize_capability._assess_backend_alias(disjoint, "alias")["passed"] is False

    broken_heap = copy.deepcopy(alias)
    broken_heap["slice_edges"] = [
        edge for edge in broken_heap["slice_edges"]
        if not (
            "HEAP_DATA_DEP" in edge["labels"]
            and "putfield" in (
                broken_heap["slice_statements"][edge["from"]].get("instruction") or ""
            )
            and "getfield" in (
                broken_heap["slice_statements"][edge["to"]].get("instruction") or ""
            )
        )
    ]
    assert summarize_capability._assess_backend_alias(
        broken_heap, "alias")["passed"] is False

    duplicate_call = copy.deepcopy(alias)
    duplicated = next(
        copy.deepcopy(row) for row in duplicate_call["slice_statements"]
        if row["kind"] == "NORMAL"
        and row["method"].startswith("sample.MechanismProbe.unknownAliasObservation")
        and "invokeinterface" in (row.get("instruction") or "")
    )
    duplicated["instruction"] = duplicated["instruction"].replace("@2", "@99")
    duplicated["statement_text"] = duplicated["statement_text"].replace("@2", "@99")
    duplicate_call["slice_statements"].append(duplicated)
    assert summarize_capability._assess_backend_alias(
        duplicate_call, "alias")["passed"] is False

    wrong_put_receiver = copy.deepcopy(alias)
    put = next(
        row for row in wrong_put_receiver["slice_statements"]
        if row["method"].startswith("sample.MechanismProbe.unknownAliasObservation")
        and "putfield" in (row.get("instruction") or "")
    )
    put["instruction"] = put["instruction"].replace("putfield 5.", "putfield 99.")
    put_effect = next(
        row for row in wrong_put_receiver["entry_instruction_effects"]
        if row["instruction_index"] == put["instruction_index"]
    )
    put_effect["uses"][0] = 99
    assert summarize_capability._assess_backend_alias(
        wrong_put_receiver, "alias")["passed"] is False

    wrong_get_receiver = copy.deepcopy(alias)
    observed_get = next(
        row for row in wrong_get_receiver["slice_statements"]
        if row["method"].startswith("sample.MechanismProbe.unknownAliasObservation")
        and "getfield" in (row.get("instruction") or "")
    )
    get_effect = next(
        row for row in wrong_get_receiver["entry_instruction_effects"]
        if row["instruction_index"] == observed_get["instruction_index"]
    )
    get_effect["uses"][0] = 99
    assert summarize_capability._assess_backend_alias(
        wrong_get_receiver, "alias")["passed"] is False

    helper_only = copy.deepcopy(alias)
    for binding in helper_only["call_bindings"]:
        binding["declared_target"] = binding["declared_target"].replace(
            ".alias(", ".aliasHelper("
        )
    for row in helper_only["slice_statements"]:
        if row["method"].startswith("sample.MechanismProbe.unknownAliasObservation"):
            row["instruction"] = (row.get("instruction") or "").replace(
                ", alias(", ", aliasHelper("
            )
            row["statement_text"] = row["statement_text"].replace(
                ", alias(", ", aliasHelper("
            )
    assert summarize_capability._assess_backend_alias(
        helper_only, "alias")["passed"] is False


def test_propagation_report_consumes_real_witnesses_and_keeps_missing_gates_blocked():
    study_root = HERE.parents[2] / "out/mockbench-study"
    report = summarize_repair.summarize_propagation(study_root)
    assert report["verified_relations"][
        "same_call_same_region_state_propagation"
    ]["passed"] is True
    assert report["verified_relations"][
        "return_alias_with_caller_state_observation"
    ]["passed"] is True
    assert report["still_blocked"]["normal_completion"].startswith("not_evaluated")
    assert report["still_blocked"]["write_before_throw"].startswith("not_evaluated")
    assert report["still_blocked"]["fresh_no_alias"]
    assert report["gate_passed"] is False
    assert len(report["attempt_ledger"]) == len(summarize_repair.PROPAGATION_ATTEMPTS)


def test_historical_issue23_gate_rejects_changed_backend_and_preserves_unknown_boundaries():
    study_root = HERE.parents[2] / "out/mockbench-study"
    report = summarize_capability.summarize_issue23_expanded_effects(
        study_root / "issue23-capability-closure-20261111-r4",
        study_root / "issue23-jib-ci-original-20261111-r7/summary.json",
    )
    # Issue 32 adds a generic mode to the probe. The immutable issue-23 receipt
    # remains accepted for its recorded implementation, never for new source.
    historical = json.loads((study_root / "issue23-capability-closure-20261111-r4/report.json").read_text())
    assert historical["gate_passed"] is True
    assert historical["source_identity_matches"] is True
    assert report["gate_passed"] is False
    assert report["source_identity_matches"] is False
    assert report["preregistration_matches"] is True
    assert all(
        row["passed"] and row["region_origin_proved"]
        for row in report["expanded_unknown_effects"]["relations"].values()
    )
    assert all(report["expanded_unknown_effects"]["negative_checks"].values())
    assert all(
        report["prior_value_control_state_mechanisms"]["current_negative_checks"].values()
    )
    unknown = set(report["expanded_unknown_effects"]["scope"]["unknown"])
    assert "heap paths deeper than one reference field" in unknown
    assert "arrays and collection contents" in unknown
    assert "library and runtime globals" in unknown
    assert report["real_reviewed_input"]["semantic_status"] == "partial_diagnostic"
    assert report["real_reviewed_input"]["automatic_entry_recovery"] == "not_evaluated_here"
