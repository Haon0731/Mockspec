"""Create the versioned issue-23 repair report from bounded mechanism evidence."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from summarize_capability import (
    _analysis_gate,
    _assess_backend_alias,
    _assess_backend_state,
    _assess_effect,
)


RELATIONS = {
    "state_may_write": ("unknown_write", "mutate", "state"),
    "return_alias": ("unknown_alias", "alias", "alias"),
    "normal_completion": ("unknown_completion", "mutate", "normal_completion"),
    "write_before_throw": ("unknown_exception", "mutate", "write_before_throw"),
    "fresh_no_alias": ("unknown_fresh", "mutate", "fresh_negative"),
}

PROPAGATION_ATTEMPTS = (
    ("config_a", "evidence_export_only", "issue23-propagation-20261111-config-a"),
    ("config_b_initial", "selector_before_builder", "issue23-propagation-20261111-config-b"),
    ("config_b_exact_r2", "accepts_bypass_empty_summary", "issue23-propagation-20261111-config-b-r2"),
    ("config_b_diag", "configured_ssa_receipt", "issue23-propagation-20261111-config-b-diag"),
    ("config_b_diag2", "selected_method_class_receipt", "issue23-propagation-20261111-config-b-diag2"),
    ("config_b_final", "selector_after_builder_with_bypass", "issue23-propagation-20261111-config-b-final"),
    ("config_b_final2", "selector_after_builder_without_bypass", "issue23-propagation-20261111-config-b-final2"),
    ("final_regression", "current_code_state_and_alias", "issue23-propagation-20261111-final-regression"),
    (
        "final_regression_r2",
        "exact_callsite_and_receiver_region_validation",
        "issue23-propagation-20261111-final-regression-r2",
    ),
    (
        "final_regression_r3",
        "heap_callsite_identity_and_receiver_region_validation",
        "issue23-propagation-20261111-final-regression-r3",
    ),
)


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def summarize(mechanisms: Path, historical: Path) -> dict:
    manifest = _load(mechanisms / "manifest.json")
    relations = {}
    metrics = {}
    for label, (artifact, target, relation) in RELATIONS.items():
        result = _load(mechanisms / f"{artifact}.json")
        assessment = _assess_effect(result, target, relation)
        relations[label] = assessment
        metrics[artifact] = {
            "call_graph_nodes": result["call_graph_nodes"],
            "application_call_graph_nodes": result.get("application_call_graph_nodes"),
            "call_graph_contexts": result.get("call_graph_contexts"),
            "pointer_instance_keys": result["pointer_instance_keys"],
            "lazy_sdg_nodes_after_slice": result.get("lazy_sdg_nodes_after_slice"),
            "slice_statements": result["slice_statement_count"],
            "slice_edges": result["slice_edge_count"],
            "timings_millis": result["timings_millis"],
        }
    all_bound = all(item["possible_target_count"] > 0 for item in relations.values())
    all_relations = all(item["passed"] for item in relations.values())
    jib_attempt = _analysis_gate({
        "timed_out": False,
        "oom": False,
        "analysis_completed": False,
        "entry_origin": "not_run",
    })
    model_acceptance = False
    overall_gate = all_relations and jib_attempt["passed"] and model_acceptance
    historical_report = _load(historical / "report.json")
    return {
        "schema_version": "mockspec.wala-issue23-repair/v2",
        "status": "passed" if overall_gate else "blocked",
        "gate_passed": overall_gate,
        "mechanism_gate_passed": all_relations,
        "gate_components": {
            "mechanism_relations": all_relations,
            "positive_real_input_analysis": jib_attempt["passed"],
            "accepted_unknown_effect_model": model_acceptance,
        },
        "model_scope": {
            "status": "historical_v2_superseded",
            "implemented_in": (
                "The v2 run attempted MethodSummary selection before builder creation. Later evidence "
                "showed that the selected callee remained native empty IR."
            ),
            "not_mock_behavior": True,
            "covered": [
                "visible argument field mod/ref probe",
                "one same-type argument return-alias probe",
                "normal/unchecked-exception summary CFG probe",
            ],
            "not_covered": [
                "fresh/null/receiver/other-argument/global return alternatives",
                "unknown-region overflow when a field or implementation is absent",
                "general nondeterministic exception sources and feasibility",
                "proof that distinct allocation sites remain disjoint through heap summaries",
            ],
        },
        "mechanism_run": {
            "path": str(mechanisms),
            "manifest_status": manifest["status"],
            "all_opaque_calls_bound_to_summary_target": all_bound,
            "relations": relations,
            "metrics": metrics,
        },
        "historical_comparison": {
            "path": str(historical),
            "old_gate_passed": historical_report["gate_passed"],
            "old_opaque_status": historical_report["opaque_call_effects"]["status"],
            "change": (
                "Opaque calls now have one WALA summary target in the call graph, but the strict "
                "relation gate remains blocked because the SDG evidence does not yet prove the "
                "required state, alias, completion, exception, and disjoint-region relations."
            ),
        },
        "jib_scale_diagnostic": {
            "rerun": "not_rerun",
            "reason": (
                "The bounded mechanism model did not recover any strict relation, so another real-Jib "
                "FULL/FULL run would repeat the known 2 GiB PDG population failure without a scale fix."
            ),
            "known_failure": "OutOfMemoryError in PDG.populate/addParamPassingStatements",
            "known_configuration": "WALA 1.7.2, 0-1-CFA, FULL/FULL, -Xmx2g, 180s",
            "positive_completion_required": True,
            "no_oom_is_not_completion": True,
            "instrumentation_added": [
                "call graph/application node/context/pointer counts before SDG",
                "explicit lazy SDG initialization marker",
                "backward-slice start/completion and lazy SDG node count",
            ],
            "pdg_note": (
                "WALA SDG creates PDGs lazily; enumerating every PDG to count it would force the same "
                "population cost. Stage markers therefore report the last completed stage instead."
            ),
            "analysis_gate": jib_attempt,
        },
        "entry_and_input": {
            "raw_or_reviewed_structure_skeleton_allowed": True,
            "reviewed_structure_skeleton_counts_as_automatic_entry": False,
            "automatic_entry_recovery": "not_evaluated_in_this_mechanism_run",
            "issue21_binding_evidence_consumed": False,
            "reason": "No new Jib run was justified after the strict mechanism gate remained blocked.",
        },
        "decision": (
            "Keep issue 23 blocked. This historical v2 report established target reachability but did "
            "not establish summary SSA participation. Use the v3 propagation report for the correction "
            "and current strict relation evidence."
        ),
    }


def summarize_propagation(study_root: Path) -> dict:
    """Summarize the selector/propagation follow-up while preserving every attempt."""
    ledger = []
    for attempt_id, purpose, directory in PROPAGATION_ATTEMPTS:
        root = study_root / directory
        manifest = _load(root / "manifest.json")
        attempts = manifest["attempts"]
        ledger.append({
            "attempt_id": attempt_id,
            "purpose": purpose,
            "path": str(root),
            "status": manifest["status"],
            "backend_sha256": manifest["inputs"]["backend_sha256"],
            "probes": [row["label"] for row in attempts],
            "probe_results": {
                row["label"]: {
                    "exit_code": row["exit_code"],
                    "timed_out": row["timed_out"],
                    "seconds": row["seconds"],
                }
                for row in attempts
            },
            "total_probe_seconds": sum(row["seconds"] for row in attempts),
        })
    canonical = study_root / "issue23-propagation-20261111-final-regression-r3"
    state = _assess_backend_state(_load(canonical / "unknown_write.json"), "mutate")
    alias = _assess_backend_alias(_load(canonical / "unknown_alias.json"), "alias")
    config_a_alias = _load(
        study_root / "issue23-propagation-20261111-config-a/unknown_alias.json"
    )
    config_a_fresh = _load(
        study_root / "issue23-propagation-20261111-config-a/unknown_fresh.json"
    )
    native_target = config_a_alias["summary_targets"][0]
    diagnostic_target = _load(
        study_root / "issue23-propagation-20261111-config-b-diag2/unknown_alias.json"
    )["summary_targets"][0]
    fresh_regions = {
        row["value_number"]: row["regions"]
        for row in config_a_fresh["entry_local_points_to"]
    }
    return {
        "schema_version": "mockspec.wala-issue23-repair/v3-propagation",
        "status": "blocked",
        "gate_passed": False,
        "canonical_evidence": str(canonical),
        "correction_to_v2": (
            "The r5 call target was a native ShrikeCTMethod with empty IR and empty ModRef. "
            "Target reachability did not mean MethodSummary SSA participated in CG/PA/SDG. "
            "Builder creation overwrote the selector installed too early."
        ),
        "verified_relations": {
            "same_call_same_region_state_propagation": state,
            "return_alias_with_caller_state_observation": alias,
        },
        "hypotheses": {
            "summary_ssa_missing": {
                "verdict": "confirmed_for_r5_and_config_a",
                "selected_implementation": diagnostic_target["implementation_class"],
                "selected_ir_instruction_count": len(native_target["ir_instructions"]),
                "selected_mod_region_count": len(native_target["mod_regions"]),
            },
            "pointer_alias_missing": {
                "verdict": "rejected_for_return_alias_after_selector_fix",
                "argument_regions": alias["argument_regions"],
                "return_regions": alias["return_regions"],
                "shared_regions": alias["shared_regions"],
                "fresh_negative_verdict": "blocked",
                "fresh_entry_regions": fresh_regions.get(2),
                "fresh_allocation_regions": fresh_regions.get(4),
            },
            "gate_missed_wala_representation": {
                "verdict": "confirmed_only_after_real_summary_ir",
                "state": "put -> HEAP_RET_CALLEE -> same-call HEAP_RET_CALLER -> get",
                "alias": (
                    "summary return -> NORMAL_RET_CALLEE/CALLER -> caller put; PA region identity "
                    "and put -> original-argument get HEAP_DATA_DEP"
                ),
            },
        },
        "still_blocked": {
            "general_unknown_may_write": (
                "The bounded summary self-writes a field value. This proves state-effect plumbing, "
                "not arbitrary-value havoc."
            ),
            "normal_completion": "not_evaluated_by_current_two-probe_regression",
            "write_before_throw": "not_evaluated_by_current_two-probe_regression",
            "fresh_no_alias": "merged points-to regions; disjointness not proved",
            "receiver_global_escaped_null_other_returns": "outside the bounded model",
            "real_jib": "not rerun; no scale improvement for the known PDG population OOM",
        },
        "configuration_budget": {
            "configuration_families": 2,
            "config_a": "evidence-only baseline; semantics unchanged",
            "config_b": (
                "selector timing/exact-summary repair family; revisions changed the analysis path "
                "and are all retained below"
            ),
            "attempt_count": len(ledger),
            "cost_scope": "total_probe_seconds excludes compilation, packaging, and input adaptation",
            "wala": "1.7.2",
            "java_heap_bytes": 2 * 1024**3,
            "timeout_seconds": 180,
        },
        "attempt_ledger": ledger,
        "decision": (
            "Keep issue 23 blocked. Real summary SSA now participates and two bounded propagation "
            "mechanisms have strict witnesses, but the general unknown-effect and real-Jib gates remain incomplete."
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mechanisms", type=Path)
    parser.add_argument("--historical", type=Path)
    parser.add_argument("--propagation-study-root", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(f"output must not exist: {args.output}")
    if args.propagation_study_root:
        report = summarize_propagation(args.propagation_study_root)
    elif args.mechanisms and args.historical:
        report = summarize(args.mechanisms, args.historical)
    else:
        parser.error(
            "provide --propagation-study-root or both --mechanisms and --historical"
        )
    args.output.write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
