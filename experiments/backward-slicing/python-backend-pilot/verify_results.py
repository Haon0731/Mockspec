"""Verify the pre-registered Issue #24 mechanism and Flask relations."""
from __future__ import annotations

import argparse
import ast
import csv
import json
from pathlib import Path


def load_rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def contains(rows: list[dict[str, str]], **expected: str) -> bool:
    return any(all(row.get(key) == value for key, value in expected.items()) for row in rows)


def load_optional_rows(path: Path) -> list[dict[str, str]] | None:
    return load_rows(path) if path.is_file() else None


def evaluate_state_boundary_evidence(results: Path) -> dict:
    rows = load_optional_rows(results / "probe-state-boundary-flow.csv")
    if rows is None:
        return {
            "schema": "mock-spec.issue27-state-boundary-evidence/v1",
            "status": "unknown",
            "checks": {},
        }

    required = {
        "relation", "caseKind", "scopeKind", "functionName", "pathId", "fieldName",
        "regionKey", "callLine", "regionLine", "observedLine",
    }

    def path(row: dict[str, str], *, relation: str, function: str, scope: str,
             field: str) -> bool:
        if not required <= row.keys() or not all(row.get(key) for key in required):
            return False
        if not all(row[key].isdigit() for key in ("callLine", "regionLine", "observedLine")):
            return False
        return (
            row["relation"] == relation
            and row["caseKind"] == "positive"
            and row["scopeKind"] == scope
            and row["functionName"] == function
            and row["fieldName"] == field
            and row["pathId"] == f"{function}:{row['callLine']}:{row['observedLine']}"
            and int(row["callLine"]) <= int(row["observedLine"])
        )

    def unknown_path(row: dict[str, str], *, relation: str, function: str,
                     scope: str, field: str) -> bool:
        return (
            required <= row.keys()
            and all(row.get(key) for key in required)
            and all(row[key].isdigit() for key in ("callLine", "regionLine", "observedLine"))
            and row["relation"] == relation
            and row["caseKind"] == "unknown"
            and row["scopeKind"] == scope
            and row["functionName"] == function
            and row["fieldName"] == field
            and row["pathId"] == f"{function}:{row['callLine']}:{row['observedLine']}"
            and int(row["callLine"]) <= int(row["regionLine"])
            and int(row["regionLine"]) <= int(row["observedLine"])
        )

    fixture_functions = {
        node.name for node in ast.parse(
            (Path(__file__).resolve().parent / "mechanism_probe.py").read_text(encoding="utf-8")
        ).body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    required_negative_fixtures = {
        "receiver_fresh_other", "receiver_effect_overwritten",
        "local_object_is_not_global", "global_effect_overwritten",
        "escaped_fresh_other",
        "escaped_effect_overwritten", "visible_pure_then_observe",
    }
    required_unknown_fixtures = {
        "receiver_other_object", "escaped_other_object", "global_rebind_boundary",
    }

    checks = {
        "receiver_effect_reaches_receiver_read": any(path(
            row, relation="unknown_receiver_write_to_read",
            function="receiver_may_write", scope="receiver", field="configured",
        ) for row in rows),
        "receiver_loop_prewrite_not_false_kill": any(path(
            row, relation="unknown_receiver_write_to_read",
            function="receiver_loop_prewrite_is_not_a_kill", scope="receiver",
            field="configured",
        ) for row in rows),
        "global_effect_reaches_global_read": any(path(
            row, relation="unknown_global_write_to_read",
            function="global_may_write", scope="module_global", field="active",
        ) for row in rows),
        "escaped_argument_effect_reaches_same_argument_read": any(path(
            row, relation="unknown_escaped_argument_write_to_read",
            function="escaped_argument_may_write", scope="escaped_argument", field="sealed",
        ) for row in rows),
        "receiver_fresh_object_excluded": not any(
            row.get("functionName") == "receiver_fresh_other" for row in rows
        ),
        "receiver_parameter_alias_recorded_unknown": any(unknown_path(
            row, relation="parameter_alias_boundary_unknown",
            function="receiver_other_object", scope="parameter_alias", field="configured",
        )
            for row in rows
        ),
        "receiver_overwrite_kills_unknown_effect": not any(
            row.get("functionName") == "receiver_effect_overwritten" for row in rows
        ),
        "local_object_not_promoted_to_global": not any(
            row.get("functionName") == "local_object_is_not_global" for row in rows
        ),
        "global_overwrite_kills_unknown_effect": not any(
            row.get("functionName") == "global_effect_overwritten" for row in rows
        ),
        "global_rebind_recorded_as_unknown": any(unknown_path(
            row, relation="global_rebind_boundary_unknown",
            function="global_rebind_boundary", scope="module_global_rebind", field="active",
        )
            for row in rows
        ),
        "global_rebind_not_falsely_linked": not any(
            row.get("relation") == "unknown_global_write_to_read"
            and row.get("functionName") == "global_rebind_boundary"
            for row in rows
        ),
        "escaped_fresh_object_excluded": not any(
            row.get("functionName") == "escaped_fresh_other" for row in rows
        ),
        "escaped_parameter_alias_recorded_unknown": any(unknown_path(
            row, relation="parameter_alias_boundary_unknown",
            function="escaped_other_object", scope="parameter_alias", field="sealed",
        )
            for row in rows
        ),
        "escaped_overwrite_kills_unknown_effect": not any(
            row.get("functionName") == "escaped_effect_overwritten" for row in rows
        ),
        "visible_pure_call_has_no_unknown_effect": not any(
            row.get("functionName") == "visible_pure_then_observe" for row in rows
        ),
        "negative_fixtures_present": required_negative_fixtures <= fixture_functions,
        "unknown_fixtures_present": required_unknown_fixtures <= fixture_functions,
    }
    return {
        "schema": "mock-spec.issue27-state-boundary-evidence/v1",
        "status": "passed" if all(checks.values()) else "blocked",
        "checks": checks,
        "semantic_scope": (
            "bounded exact receiver, resolved module-global binding, and explicit retain-then-use escape paths"
        ),
        "limitations": [
            "The module-global relation is a conservative unknown-call boundary, not a concrete write.",
            "A module-global object rebind is emitted as an unresolved region boundary, not an exclusion.",
            "Distinct parameter local sources are emitted as unresolved possible aliases.",
            "The escaped relation requires an explicit earlier opaque argument-passing call on the same dependency receiver.",
            "Dynamic attributes and arbitrary transitive heap reachability remain unknown.",
        ],
    }


def evaluate_model_evidence(results: Path) -> dict:
    """Evaluate model propagation only from backend-produced path relations.

    The older effects query is intentionally not consumed here: its rows are
    candidate classifications and cannot establish a propagated path.
    """
    effects = load_optional_rows(results / "probe-unknown-effect-flow.csv")
    exceptional = load_optional_rows(results / "probe-unknown-exception-state.csv")
    if effects is None or exceptional is None:
        return {
            "schema": "mock-spec.issue24-model-evidence/v2",
            "status": "unknown",
            "checks": {
                "unknown_effect_model_propagation_verified": False,
                "exception_path_state_propagation_verified": False,
            },
        }

    def backend_path(row: dict[str, str], *, relation: str, case_kind: str,
                     function: str, exit_kind: str) -> bool:
        required = (
            "pathId", "fieldName", "regionKey", "callLine", "middleLine", "observedLine"
        )
        if not all(row.get(key) for key in required):
            return False
        if not all(row.get(key, "").isdigit() for key in ("callLine", "middleLine", "observedLine")):
            return False
        return (
            row.get("relation") == relation
            and row.get("caseKind") == case_kind
            and row.get("functionName") == function
            and row.get("fieldName") == "ready"
            and row.get("exitKind") == exit_kind
            and row.get("pathId") == f"{function}:{row['callLine']}:{row['observedLine']}"
            and int(row["callLine"]) <= int(row["middleLine"]) <= int(row["observedLine"])
        )

    def matching(rows: list[dict[str, str]], **expected: str) -> list[dict[str, str]]:
        return [row for row in rows if backend_path(row, **expected)]

    may_write = matching(
        effects, relation="unknown_may_write_to_read", case_kind="positive",
        function="unknown_may_write", exit_kind="state",
    )
    alias = matching(
        effects, relation="return_alias_write_to_argument_read", case_kind="positive",
        function="returned_alias_write", exit_kind="state",
    )
    fresh_argument = matching(
        effects, relation="trusted_fresh_argument_region_disjoint", case_kind="negative",
        function="fresh_argument_does_not_reach", exit_kind="excluded",
    )
    fresh_alias = matching(
        effects, relation="trusted_fresh_return_alias_channel_disjoint", case_kind="negative",
        function="fresh_return_alias_does_not_reach", exit_kind="excluded",
    )
    killed = matching(
        effects, relation="unknown_state_killed_by_definite_write", case_kind="kill",
        function="definite_state_kill", exit_kind="state",
    )
    normal = matching(
        effects, relation="execution_effect_after_state_kill", case_kind="normal",
        function="definite_state_kill", exit_kind="normal",
    )
    raised = matching(
        effects, relation="execution_effect_after_state_kill", case_kind="exception",
        function="definite_state_kill", exit_kind="exception",
    )
    conditional = matching(
        effects, relation="conditional_write_not_a_kill", case_kind="negative",
        function="conditional_write_is_not_a_kill", exit_kind="state",
    )
    other_field = matching(
        effects, relation="return_alias_other_field_excluded", case_kind="negative",
        function="returned_alias_other_field", exit_kind="state",
    )
    conditional_may_write = matching(
        effects, relation="unknown_may_write_to_read", case_kind="positive",
        function="conditional_write_is_not_a_kill", exit_kind="state",
    )
    conditional_pair = any(
        c["pathId"] == m["pathId"] and c["callLine"] == m["callLine"]
        and c["regionKey"] == m["regionKey"] and c["fieldName"] == m["fieldName"]
        for c in conditional for m in conditional_may_write
    )
    conditional_then_kill = matching(
        effects, relation="unknown_state_killed_by_definite_write", case_kind="kill",
        function="conditional_then_definite_kill", exit_kind="state",
    )
    invalid_live_after_final_kill = matching(
        effects, relation="unknown_may_write_to_read", case_kind="positive",
        function="conditional_then_definite_kill", exit_kind="state",
    )
    loop_prewrite = matching(
        effects, relation="unknown_may_write_to_read", case_kind="positive",
        function="direct_argument_loop_prewrite_is_not_a_kill", exit_kind="state",
    )
    execution_pair = any(
        n["pathId"] == x["pathId"] and n["regionKey"] == x["regionKey"] == k["regionKey"]
        and n["callLine"] == x["callLine"] == k["callLine"]
        for k in killed for n in normal for x in raised
    )
    effect_checks = all((may_write, alias, fresh_argument, fresh_alias, killed,
                         conditional, conditional_then_kill, other_field, loop_prewrite)) and \
        conditional_pair and execution_pair and not invalid_live_after_final_kill

    handler = matching(
        exceptional, relation="write_effect_exception_path_to_read", case_kind="handler",
        function="caught_and_finally", exit_kind="exception",
    )
    final = matching(
        exceptional, relation="write_effect_exception_path_to_read", case_kind="finally",
        function="caught_and_finally", exit_kind="exception",
    )
    exception_checks = any(
        h["callLine"] == f["callLine"] and h["regionKey"] == f["regionKey"]
        for h in handler for f in final
    )
    checks = {
        "unknown_effect_model_propagation_verified": effect_checks,
        "exception_path_state_propagation_verified": exception_checks,
        "direct_argument_loop_prewrite_not_false_kill": bool(loop_prewrite),
    }
    return {
        "schema": "mock-spec.issue24-model-evidence/v2",
        "status": "passed" if all(checks.values()) else "blocked",
        "checks": checks,
        "supported_scope": [
            "direct positional argument regions",
            "one returned alias followed by a same-field write",
            "same-field dominating kills",
            "exceptional CFG paths to a handler or finally read in the same try statement",
            "fresh direct-argument and returned-alias channels for the trusted pass-only Box probe constructor",
        ],
        "unsupported_scope": [
            "arbitrary transitive heap regions",
            "factory calls not proven to allocate a fresh object",
            "unknown or dynamic attribute names",
            "global or escaped aliases not rooted at the selected argument region",
            "receiver-side effects remain unknown even when the selected fresh argument channel is disjoint",
            "freshness for classes with bases, metaclasses, __new__, or __init__",
            "properties, __setattr__, container elements, and regions with multiple local sources",
        ],
    }


def evaluate(results: Path) -> dict:
    local = load_rows(results / "probe-local-flow.csv")
    global_flow = load_rows(results / "probe-global-flow.csv")
    control = load_rows(results / "probe-control-flow.csv")
    effects = load_rows(results / "probe-effects.csv")
    exceptions = load_rows(results / "probe-exceptions.csv")
    reachability = load_rows(results / "probe-reachability.csv")
    flask = load_rows(results / "flask-flask-abort.csv")
    flask_exceptions = load_rows(results / "flask-exceptions.csv")

    model_evidence = evaluate_model_evidence(results)
    state_boundary_evidence = evaluate_state_boundary_evidence(results)
    checks = {
        "overwrite_removes_old_value_flow": (
            contains(local, check="constant_overwrite_reaches_observe")
            and not contains(local, check="overwritten_unknown_return_reaches_observe")
        ),
        "matched_actual_formal_return_flow": contains(
            global_flow, check="actual_formal_return_observe"
        ),
        "unknown_call_normal_and_exception_edges": (
            contains(control, callName="fill", edgeKind="normal")
            and contains(control, callName="fill", edgeKind="exception")
        ),
        "early_return_and_raise_do_not_reach_later_observe": (
            contains(reachability, relation="terminal_source", functionName="early_exit")
            and contains(reachability, relation="target_after_terminal", functionName="early_exit")
            and contains(reachability, relation="terminal_source", functionName="explicit_raise")
            and not contains(reachability, relation="reachable_after_terminal")
        ),
        "catch_and_finally_structure": (
            contains(control, callName="fill", edgeKind="exception",
                     col4="ControlFlowNode for ExceptStmt")
            and contains(control, callName="fill", edgeKind="exception",
                         col4="ControlFlowNode for observe")
        ),
        "visible_alias_write_reaches_read": contains(
            effects,
            relation="visible_write_to_read",
            modality="backend_may",
            functionName="visible_write",
        ),
        "proven_fresh_write_excluded": not contains(
            effects, relation="visible_write_to_read", functionName="proven_fresh_write"
        ),
        "direct_argument_loop_prewrite_not_false_kill": model_evidence["checks"].get(
            "direct_argument_loop_prewrite_not_false_kill"
        ) is True,
        "candidate_unknown_return_alias_and_downstream_use_rows": (
            contains(
                effects,
                relation="unknown_return_may_alias_argument",
                modality="conservative_unknown",
                functionName="unknown_write",
            )
            and contains(
                effects,
                relation="unknown_return_value_reaches_return_use",
                modality="backend_may",
                functionName="unknown_write",
            )
        ),
        "candidate_unknown_may_write_observed_state_row": contains(
            effects,
            relation="unknown_call_may_write_observed_object",
            modality="conservative_unknown",
            functionName="unknown_write",
        ),
        "unknown_may_raise": (
            contains(
                effects,
                relation="unknown_call_may_raise",
                modality="backend_may",
                functionName="unknown_write",
            )
            and contains(exceptions, relation="raises_unknown_type", label="fill")
        ),
        "candidate_unknown_write_before_raise_row": contains(
            effects,
            relation="unknown_call_may_write_before_raise",
            modality="conservative_unknown",
            functionName="caught_and_finally",
        ),
        "flask_test_call_resolves_to_mut": contains(
            flask, relation="test_call_resolves_to_helper_abort"
        ),
        "flask_aborter_normal_path_reaches_fallback": contains(
            flask, relation="aborter_normal_path_reaches_fallback"
        ),
        "flask_aborter_exception_blocks_normal_continuation": (
            contains(flask_exceptions, relation="raises_unknown_type", label="aborter")
            and contains(flask_exceptions, relation="exceptional_exit", label="aborter")
        ),
        **model_evidence["checks"],
    }
    return {
        "schema": "mock-spec.issue24-verdict/v2",
        "status": "passed" if all(checks.values()) else "blocked",
        "gate_passed": all(checks.values()),
        "checks": checks,
        "model_evidence_status": model_evidence["status"],
        "model_evidence": model_evidence,
        "state_boundary_evidence": state_boundary_evidence,
        "limitations": [
            "CodeQL supplies CFG, value flow, attribute nodes, and unknown-exception evidence.",
            "The original effects query remains candidate evidence and is not accepted by the propagation gate.",
            "The bounded model covers direct argument regions, one returned-alias field write, definite same-field kills, and exceptional handler/finally paths.",
            "Arbitrary transitive heap reachability remains outside this pilot and must stay unknown.",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    verdict = evaluate(args.results)
    rendered = json.dumps(verdict, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0 if verdict["status"] != "blocked" else 1


if __name__ == "__main__":
    raise SystemExit(main())
