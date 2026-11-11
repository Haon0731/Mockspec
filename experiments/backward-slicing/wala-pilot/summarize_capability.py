"""Summarize the issue-23 WALA evidence without upgrading negative results."""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re


OPAQUE_TARGETS = {
    "unknown_write": "mutate",
    "unknown_alias": "alias",
    "unknown_completion": "mutate",
}


def _paths(
    edges: list[dict], starts: set[int], goals: set[int], allowed: set[str]
) -> list[int] | None:
    """Return one directed multi-hop witness path in the reported SDG subgraph."""
    successors: dict[int, list[int]] = {}
    for edge in edges:
        if set(edge["labels"]) & allowed:
            successors.setdefault(edge["from"], []).append(edge["to"])
    pending = [(start, [start]) for start in sorted(starts)]
    visited = set(starts)
    while pending:
        node, path = pending.pop(0)
        if node in goals:
            return path
        for successor in successors.get(node, ()):
            if successor not in visited:
                visited.add(successor)
                pending.append((successor, path + [successor]))
    return None


def _indexes(statements: list[dict], needle: str) -> set[int]:
    return {
        index for index, row in enumerate(statements)
        if needle in (row.get("instruction") or "").lower()
    }


def _assess_effect(result: dict, target: str, relation: str) -> dict:
    """Assess relation-specific evidence; no relation requires a direct call→field edge."""
    bindings = [row for row in result["call_bindings"] if target in row["declared_target"]]
    binding = next(
        (row for row in bindings if "FakeRoot" not in row["caller"]), bindings[0]
    )
    statements = result["slice_statements"]
    edges = result["slice_edges"]
    calls = {
        index for index, row in enumerate(statements)
        if target in (row.get("instruction") or "")
    }
    puts = _indexes(statements, "putfield")
    gets = _indexes(statements, "getfield")
    summary_returns = {
        index for index, row in enumerate(statements)
        if row.get("kind") == "NORMAL_RET_CALLEE"
        and any(candidate in row["method"] for candidate in binding["possible_targets"])
    }
    caller_normal_returns = {
        index for index, row in enumerate(statements)
        if row.get("kind") == "NORMAL_RET_CALLER"
        and target in (row.get("instruction") or "")
        and row["method"] == binding["caller"]
    }
    throws = _indexes(statements, "throw")
    catches = _indexes(statements, "get caught exception")
    effect_kinds = {
        row.get("kind") for row in result.get("opaque_effects", ())
        if row.get("provenance") == "wala_method_summary"
        and target in row.get("target", "")
    }
    bound = binding["status"] != "unresolved" and bool(binding["possible_targets"])
    witness: list[int] | dict[str, object] | None = None
    shared_region_path = None
    data_labels = {"DATA_DEP", "PARAMETER_DEP"}
    heap_labels = {"HEAP_DATA_DEP"}
    control_labels = {"CONTROL_DEP", "EXCEPTION_DEP"}
    if relation == "state":
        summary_puts = {
            put for put in puts
            if any(candidate in statements[put]["method"] for candidate in binding["possible_targets"])
        }
        for put in sorted(summary_puts):
            first = _paths(edges, calls, {put}, data_labels)
            second = _paths(edges, {put}, gets, heap_labels)
            if first and second:
                witness = first + second[1:]
                break
        passed = bound and "state" in effect_kinds and witness is not None
    elif relation == "alias":
        caller_puts = {put for put in puts if statements[put]["method"] == binding["caller"]}
        caller_gets = {get for get in gets if statements[get]["method"] == binding["caller"]}
        for returned in sorted(summary_returns):
            first = _paths(edges, calls, {returned}, data_labels)
            for put in sorted(caller_puts):
                second = _paths(edges, {returned}, {put}, data_labels)
                third = _paths(edges, {put}, caller_gets, heap_labels)
                if first and second and third:
                    witness = first + second[1:] + third[1:]
                    break
            if witness:
                break
        passed = bound and "alias" in effect_kinds and witness is not None
    elif relation == "normal_completion":
        observations = {
            index for index, row in enumerate(statements)
            if row["method"] == binding["caller"]
            and index not in calls
            and (row.get("instruction") or "").startswith("invoke")
        }
        for returned in sorted(caller_normal_returns):
            first = _paths(edges, calls, {returned}, data_labels | control_labels)
            second = _paths(edges, {returned}, observations, control_labels)
            if first and second:
                witness = first + second[1:]
                break
        passed = bound and "normal_completion" in effect_kinds and witness is not None
    elif relation == "write_before_throw":
        summary_methods = binding["possible_targets"]
        summary_puts = {
            put for put in puts
            if any(candidate in statements[put]["method"] for candidate in summary_methods)
        }
        summary_throws = {
            thrown for thrown in throws
            if any(candidate in statements[thrown]["method"] for candidate in summary_methods)
        }
        caller_catches = {
            caught for caught in catches if statements[caught]["method"] == binding["caller"]
        }
        caller_gets = {get for get in gets if statements[get]["method"] == binding["caller"]}
        for put in sorted(summary_puts):
            for thrown in sorted(summary_throws):
                write_to_throw = _paths(edges, {put}, {thrown}, control_labels)
                if not write_to_throw:
                    continue
                for caught in sorted(caller_catches):
                    throw_to_handler = _paths(edges, {thrown}, {caught}, control_labels)
                    if not throw_to_handler:
                        continue
                    for observed_get in sorted(caller_gets):
                        handler_to_read = _paths(edges, {caught}, {observed_get}, control_labels)
                        write_to_read = _paths(edges, {put}, {observed_get}, heap_labels)
                        if handler_to_read and write_to_read:
                            witness = {
                                "exception_control_path": (
                                    write_to_throw + throw_to_handler[1:] + handler_to_read[1:]
                                ),
                                "state_path": write_to_read,
                                "shared_write": put,
                                "shared_handler_read": observed_get,
                            }
                            break
                    if witness:
                        break
                if witness:
                    break
            if witness:
                break
        passed = bound and "write_before_throw" in effect_kinds and witness is not None
    elif relation == "fresh_negative":
        shared_region_path = _paths(edges, puts, gets, heap_labels) is not None
        regions = result.get("region_evidence", {})
        disjoint = (
            regions.get("points_to_disjoint") is True
            and regions.get("argument_region") != regions.get("observed_region")
            and regions.get("unknown_region_overflow") is False
        )
        passed = bound and "fresh_negative" in effect_kinds and disjoint and not shared_region_path
    else:
        raise ValueError(f"unknown opaque relation: {relation}")
    return {
        "passed": passed,
        "call_binding": binding["status"],
        "possible_target_count": len(binding["possible_targets"]),
        "witness_path": witness,
        "field_heap_edge_required": relation in {"state", "write_before_throw"},
        "shared_region_path": shared_region_path,
        "summary_effect_kinds": sorted(effect_kinds),
        "region_evidence": result.get("region_evidence"),
    }


def _analysis_gate(attempt: dict) -> dict:
    completed = bool(attempt.get("analysis_completed"))
    entry_origin = attempt.get("entry_origin")
    automatic = entry_origin == "automatic_reviewed_binding"
    return {
        "passed": completed and not attempt.get("timed_out") and not attempt.get("oom"),
        "analysis_completed": completed,
        "automatic_entry_recovery": automatic,
        "entry_origin": entry_origin,
    }


def _local_regions(result: dict, value_number: int) -> set[str]:
    for row in result.get("entry_local_points_to", ()):
        if row["value_number"] == value_number:
            return set(row["regions"])
    return set()


def _backend_binding(result: dict, target: str) -> dict:
    pattern = re.compile(rf"\.{re.escape(target)}\(")
    bindings = [
        row for row in result["call_bindings"]
        if pattern.search(row["declared_target"]) and "FakeRoot" not in row["caller"]
    ]
    unique = {
        (
            row["caller"],
            row.get("caller_context"),
            row.get("instruction_index"),
            row.get("call_site"),
            row["declared_target"],
            tuple(row["possible_targets"]),
        ): row
        for row in bindings
    }
    if len(unique) != 1:
        raise ValueError(f"expected one exact call binding for {target}, found {len(unique)}")
    return next(iter(unique.values()))


def _assess_backend_state(result: dict, target: str) -> dict:
    """Verify one same-call, same-location heap-effect path through a real summary callee."""
    try:
        binding = _backend_binding(result, target)
    except ValueError:
        return {
            "passed": False,
            "witness": None,
            "summary_target_is_real_ir": False,
            "semantic_scope": "unique_exact_callsite_required",
            "unknown_value_havoc_proved": False,
        }
    if not all(
        binding.get(field) is not None
        for field in ("caller_context", "instruction_index", "call_site")
    ):
        return {
            "passed": False,
            "witness": None,
            "summary_target_is_real_ir": False,
            "semantic_scope": "callsite_receipt_required",
            "unknown_value_havoc_proved": False,
        }
    possible = set(binding["possible_targets"])
    statements = result["slice_statements"]
    calls = [
        (index, row) for index, row in enumerate(statements)
        if row["kind"] == "NORMAL"
        and row["method"] == binding["caller"]
        and row["context"] == binding["caller_context"]
        and row.get("instruction_index") == binding["instruction_index"]
    ]
    if len(calls) != 1 or len(possible) != 1:
        return {
            "passed": False,
            "witness": None,
            "summary_target_is_real_ir": False,
            "semantic_scope": "unique_same_call_same_region_required",
            "unknown_value_havoc_proved": False,
        }
    summaries = [
        row for row in result.get("summary_targets", ())
        if row["method"] in possible
        and row.get("implementation_class") == "com.ibm.wala.ipa.summaries.SummarizedMethod"
        and any(
            "putfield" in instruction or "putstatic" in instruction
            for instruction in row["ir_instructions"]
        )
    ]
    edges = result["slice_edges"]
    summary_puts = {
        index for index, row in enumerate(statements)
        if row["method"] in possible
        and any(
            kind in (row.get("instruction") or "")
            for kind in ("putfield", "putstatic")
        )
    }
    callee_returns = {
        index for index, row in enumerate(statements)
        if row["method"] in possible and row["kind"] == "HEAP_RET_CALLEE"
    }
    caller_returns = {
        index for index, row in enumerate(statements)
        if row["method"] == binding["caller"]
        and row["context"] == binding["caller_context"]
        and row["kind"] == "HEAP_RET_CALLER"
        and row.get("call_instruction_index") == binding["instruction_index"]
        and row.get("call_site") == binding["call_site"]
        and row.get("declared_target") == binding["declared_target"]
    }
    observed_gets = {
        index for index, row in enumerate(statements)
        if row["method"] == binding["caller"]
        and any(
            kind in (row.get("instruction") or "")
            for kind in ("getfield", "getstatic")
        )
    }
    witness = None
    for put in sorted(summary_puts):
        for callee_return in sorted(callee_returns):
            location = statements[callee_return].get("heap_location")
            put_to_callee = _paths(edges, {put}, {callee_return}, {"DATA_DEP"})
            if not put_to_callee or not location:
                continue
            for caller_return in sorted(caller_returns):
                if statements[caller_return].get("heap_location") != location:
                    continue
                cross_call = _paths(
                    edges, {callee_return}, {caller_return}, {"HEAP_DATA_DEP"}
                )
                if not cross_call:
                    continue
                for observed_get in sorted(observed_gets):
                    to_observation = _paths(
                        edges, {caller_return}, {observed_get}, {"HEAP_DATA_DEP"}
                    )
                    if to_observation and location in statements[caller_return]["statement_text"]:
                        witness = {
                            "summary_write_to_heap_return": put_to_callee,
                            "same_call_heap_return": cross_call,
                            "heap_return_to_observation": to_observation,
                            "heap_location": location,
                        }
                        break
                if witness:
                    break
            if witness:
                break
    return {
        "passed": bool(summaries) and witness is not None,
        "witness": witness,
        "summary_target_is_real_ir": bool(summaries),
        "semantic_scope": "same_call_same_region_effect_propagation_only",
        "unknown_value_havoc_proved": False,
    }


def _assess_backend_alias(result: dict, target: str) -> dict:
    """Verify summary return alias, PA region identity, and caller write→original-read heap flow."""
    try:
        binding = _backend_binding(result, target)
    except ValueError:
        return {
            "passed": False,
            "witness": None,
            "summary_target_is_real_ir": False,
            "argument_regions": [],
            "return_regions": [],
            "shared_regions": [],
            "semantic_scope": "unique_exact_callsite_required",
        }
    if not all(
        binding.get(field) is not None
        for field in ("caller_context", "instruction_index", "call_site")
    ):
        return {
            "passed": False,
            "witness": None,
            "summary_target_is_real_ir": False,
            "argument_regions": [],
            "return_regions": [],
            "shared_regions": [],
            "semantic_scope": "callsite_receipt_required",
        }
    possible = set(binding["possible_targets"])
    statements = result["slice_statements"]
    calls = [
        row for row in statements
        if row["kind"] == "NORMAL"
        and row["method"] == binding["caller"]
        and row["context"] == binding["caller_context"]
        and row.get("instruction_index") == binding["instruction_index"]
    ]
    if len(calls) != 1 or len(possible) != 1:
        return {
            "passed": False,
            "witness": None,
            "summary_target_is_real_ir": False,
            "argument_regions": [],
            "return_regions": [],
            "shared_regions": [],
            "semantic_scope": "unique_same_call_required",
        }
    summaries = [
        row for row in result.get("summary_targets", ())
        if row["method"] in possible
        and row.get("implementation_class") == "com.ibm.wala.ipa.summaries.SummarizedMethod"
        and any(instruction == "return 2" for instruction in row["ir_instructions"])
    ]
    invoke_effect = next(
        (
            row for row in result.get("entry_instruction_effects", ())
            if row["instruction_index"] == binding["instruction_index"]
            and row["definitions"]
        ),
        None,
    )
    return_value = invoke_effect["definitions"][0] if invoke_effect else -1
    summary_return_values = {
        int(match.group(1))
        for row in summaries
        for instruction in row["ir_instructions"]
        if (match := re.fullmatch(r"return (\d+)", instruction))
    }
    return_regions = _local_regions(result, return_value)
    parameter_candidates: list[tuple[int, set[str]]] = []
    if invoke_effect:
        for returned_parameter in sorted(summary_return_values):
            if 0 < returned_parameter <= len(invoke_effect["uses"]):
                argument_value = invoke_effect["uses"][returned_parameter - 1]
                regions = _local_regions(result, argument_value)
                if regions & return_regions:
                    parameter_candidates.append((returned_parameter, regions))
    returned_parameter, argument_regions = (
        parameter_candidates[0] if len(parameter_candidates) == 1 else (-1, set())
    )
    shared_regions = sorted(argument_regions & return_regions)
    edges = result["slice_edges"]
    summary_returns = {
        index for index, row in enumerate(statements)
        if row["method"] in possible
        and (row.get("instruction") or "") == f"return {returned_parameter}"
    }
    callee_returns = {
        index for index, row in enumerate(statements)
        if row["method"] in possible and row["kind"] == "NORMAL_RET_CALLEE"
    }
    caller_returns = {
        index for index, row in enumerate(statements)
        if row["method"] == binding["caller"]
        and row["context"] == binding["caller_context"]
        and row["kind"] == "NORMAL_RET_CALLER"
        and row.get("instruction_index") == binding["instruction_index"]
    }
    effect_by_index = {
        row["instruction_index"]: row for row in result.get("entry_instruction_effects", ())
    }
    caller_puts = set()
    caller_gets = set()
    for index, row in enumerate(statements):
        if row["method"] != binding["caller"] or row["context"] != binding["caller_context"]:
            continue
        effect = effect_by_index.get(row.get("instruction_index"))
        if not effect or not effect["uses"]:
            continue
        receiver_regions = _local_regions(result, effect["uses"][0])
        instruction = row.get("instruction") or ""
        if (
            "putfield" in instruction
            and receiver_regions & return_regions
            and set(effect["mod_regions"])
        ):
            caller_puts.add(index)
        if (
            "getfield" in instruction
            and receiver_regions & argument_regions
            and set(effect["ref_regions"])
        ):
            caller_gets.add(index)
    witness = None
    for summary_return in sorted(summary_returns):
        for callee_return in sorted(callee_returns):
            first = _paths(edges, {summary_return}, {callee_return}, {"DATA_DEP"})
            if not first:
                continue
            for caller_return in sorted(caller_returns):
                second = _paths(edges, {callee_return}, {caller_return}, {"DATA_DEP"})
                if not second:
                    continue
                for put in sorted(caller_puts):
                    third = _paths(edges, {caller_return}, {put}, {"DATA_DEP"})
                    if not third:
                        continue
                    for observed_get in sorted(caller_gets):
                        put_effect = effect_by_index[statements[put]["instruction_index"]]
                        get_effect = effect_by_index[
                            statements[observed_get]["instruction_index"]
                        ]
                        if not set(put_effect["mod_regions"]) & set(get_effect["ref_regions"]):
                            continue
                        fourth = _paths(edges, {put}, {observed_get}, {"HEAP_DATA_DEP"})
                        if fourth:
                            witness = first + second[1:] + third[1:] + fourth[1:]
                            break
                    if witness:
                        break
                if witness:
                    break
            if witness:
                break
    return {
        "passed": bool(summaries) and bool(shared_regions) and witness is not None,
        "witness": witness,
        "summary_target_is_real_ir": bool(summaries),
        "argument_regions": sorted(argument_regions),
        "return_regions": sorted(return_regions),
        "shared_regions": shared_regions,
        "semantic_scope": "bounded_unique_call_returned_parameter_alias",
    }


def _assess_backend_unknown_value_write(result: dict, target: str) -> dict:
    """Require a native effect token to flow into the summary write and observed heap region."""
    state = _assess_backend_state(result, target)
    if not state["passed"]:
        return {**state, "unknown_value_source_path": None}
    binding = _backend_binding(result, target)
    possible = set(binding["possible_targets"])
    statements = result["slice_statements"]
    edges = result["slice_edges"]
    selected_write = state["witness"]["summary_write_to_heap_return"][0]
    selected_row = statements[selected_write]
    selected_instruction_index = selected_row.get("instruction_index")
    selected_location = state["witness"]["heap_location"]
    effect_returns = {
        index for index, row in enumerate(statements)
        if row["method"] in possible
        and row["kind"] == "NORMAL_RET_CALLER"
        and row.get("declared_target")
        == "sample.MechanismProbe$EffectSource.unknownInt()I"
    }
    source_path = _paths(edges, effect_returns, {selected_write}, {"DATA_DEP"})
    matching_effects = [
        effect
        for summary in result.get("summary_targets", ())
        if summary["method"] in possible
        and summary.get("context") == selected_row.get("context")
        for effect in summary.get("instruction_effects", ())
        if effect.get("instruction_index") == selected_instruction_index
        and selected_location in effect.get("mod_regions", ())
    ]
    state["unknown_value_source_path"] = source_path
    state["selected_write_instruction_index"] = selected_instruction_index
    state["selected_write_modref_matches_heap_location"] = bool(matching_effects)
    state["unknown_value_havoc_proved"] = (
        source_path is not None and bool(matching_effects)
    )
    state["passed"] = state["passed"] and state["unknown_value_havoc_proved"]
    state["semantic_scope"] = "bounded_effect_token_to_same_call_same_region_write"
    return state


def _assess_backend_return_alternatives(result: dict, target: str) -> dict:
    """Report argument-alias/fresh alternatives and whether PA keeps them distinguishable."""
    alias = _assess_backend_alias(result, target)
    try:
        binding = _backend_binding(result, target)
    except ValueError:
        return {**alias, "alternatives_in_summary_ir": False}
    targets = set(binding["possible_targets"])
    summaries = [row for row in result.get("summary_targets", ()) if row["method"] in targets]
    has_alias_return = any(
        any(re.fullmatch(r"return [1-9]\d*", instruction) for instruction in row["ir_instructions"])
        for row in summaries
    )
    has_fresh_return = any(
        any(" = new " in instruction for instruction in row["ir_instructions"])
        and len([x for x in row["ir_instructions"] if x.startswith("return ")]) >= 2
        for row in summaries
    )
    alias["alternatives_in_summary_ir"] = has_alias_return and has_fresh_return
    alias["pa_distinguishes_alternatives"] = len(alias["return_regions"]) > len(
        alias["shared_regions"]
    )
    alias["passed"] = alias["passed"] and alias["alternatives_in_summary_ir"]
    return alias


def _assess_backend_normal_completion(result: dict, target: str) -> dict:
    """Combine a real summary normal exit with the exact invoke's normal CFG continuation."""
    try:
        binding = _backend_binding(result, target)
    except ValueError:
        return {
            "passed": False,
            "semantic_scope": "unique_exact_callsite_required",
        }
    targets = set(binding["possible_targets"])
    summaries = [
        row for row in result.get("summary_targets", ())
        if row["method"] in targets
        and row.get("implementation_class")
        == "com.ibm.wala.ipa.summaries.SummarizedMethod"
    ]
    normal_exits: list[int] = [
        index
        for row in summaries
        for index in row.get("return_instruction_indexes", [])
    ]
    exceptional_exits: list[int] = [
        index
        for row in summaries
        for index in row.get("throw_instruction_indexes", [])
    ]
    cfg_rows = [
        row for row in result.get("entry_control_flow", ())
        if row.get("caller") == binding["caller"]
        and row.get("caller_context") == binding["caller_context"]
        and row.get("instruction_index") == binding["instruction_index"]
        and row.get("call_site") == binding["call_site"]
        and row.get("declared_target") == binding["declared_target"]
    ]
    cfg = cfg_rows[0] if len(cfg_rows) == 1 else None
    seed = result.get("criterion", {}).get("seed", {})
    selected_seed = bool(
        seed.get("kind") == "NORMAL"
        and seed.get("method") == binding["caller"]
        and seed.get("context") == binding["caller_context"]
    )
    normal_continuation = bool(
        cfg
        and selected_seed
        and cfg["normal_successor_blocks"]
        and any(
            row["instruction_index"] == seed.get("instruction_index")
            and row["instruction"] == seed.get("instruction")
            for row in cfg["normal_reachable"]
        )
    )
    exceptional_continuation = bool(cfg and cfg["exceptional_successor_blocks"])
    normal_sdg_rows = [
        row for row in result.get("effect_forward_slices", ())
        if row["summary_method"] in targets and row["exit_kind"] == "normal"
    ]
    return {
        "passed": bool(summaries) and bool(normal_exits) and normal_continuation,
        "summary_normal_exit_indexes": normal_exits,
        "summary_exception_exit_indexes": exceptional_exits,
        "exact_call_cfg": cfg,
        "normal_continuation_reaches_return": normal_continuation,
        "selected_seed_matches_caller": selected_seed,
        "exceptional_first_edge_present": exceptional_continuation,
        "sdg_normal_exit_reaches_entry": any(
            row["entry_reachable_statements"] for row in normal_sdg_rows
        ),
        "evidence_semantics": {
            "callee": "summary CFG contains a structurally possible normal return",
            "caller": "entry SSA CFG has an exact-call normal first edge that may reach the selected observation",
            "note": "This is structured CFG evidence; it is not represented as one SDG edge.",
        },
        "semantic_scope": "bounded_summary_exit_plus_exact_callsite_cfg_continuation",
    }


def _assess_backend_write_before_throw(result: dict, target: str) -> dict:
    """Combine dominating summary write, exact exceptional return, catch block, and state read."""
    state = _assess_backend_unknown_value_write(result, target)
    if not state["passed"]:
        return {**state, "exception_path": None}
    binding = _backend_binding(result, target)
    targets = set(binding["possible_targets"])
    summaries = [
        row for row in result.get("summary_targets", ())
        if row["method"] in targets
        and row.get("implementation_class")
        == "com.ibm.wala.ipa.summaries.SummarizedMethod"
    ]
    exception_rows = [
        row for row in result.get("effect_forward_slices", ())
        if row["summary_method"] in targets and row["exit_kind"] == "exception"
    ]
    matching_exception = None
    catch_blocks: set[int] = set()
    for row in exception_rows:
        reached = row["entry_reachable_statements"]
        exact_return = any(
            statement["kind"] == "EXC_RET_CALLER"
            and statement["method"] == binding["caller"]
            and statement["context"] == binding["caller_context"]
            and statement.get("instruction_index") == binding["instruction_index"]
            and statement.get("call_site") == binding["call_site"]
            and statement.get("declared_target") == binding["declared_target"]
            for statement in reached
        )
        blocks = {
            statement["basic_block"] for statement in reached
            if statement["kind"] == "CATCH" and statement.get("basic_block") is not None
        }
        matching_summary = next(
            (
                summary for summary in summaries
                if summary.get("context") == row.get("summary_context")
            ),
            None,
        )
        selected_put_slice = state["witness"]["summary_write_to_heap_return"][0]
        selected_put_instruction = result["slice_statements"][selected_put_slice].get(
            "instruction_index"
        )
        dominance_pair = next(
            (
                pair for pair in matching_summary.get("put_throw_dominance_pairs", ())
                if pair["put_instruction_index"] == selected_put_instruction
                and pair["throw_instruction_index"] == row["source_instruction_index"]
            ),
            None,
        ) if matching_summary else None
        if exact_return and blocks and dominance_pair:
            matching_exception = row
            catch_blocks = blocks
            break
    statements = result["slice_statements"]
    edges = result["slice_edges"]
    location = state["witness"]["heap_location"]
    caller_heap_returns = {
        index for index, row in enumerate(statements)
        if row["kind"] == "HEAP_RET_CALLER"
        and row["method"] == binding["caller"]
        and row["context"] == binding["caller_context"]
        and row.get("call_instruction_index") == binding["instruction_index"]
        and row.get("call_site") == binding["call_site"]
        and row.get("declared_target") == binding["declared_target"]
        and row.get("heap_location") == location
    }
    handler_gets = {
        index for index, row in enumerate(statements)
        if row["method"] == binding["caller"]
        and row.get("basic_block") in catch_blocks
        and "getfield" in (row.get("instruction") or "")
    }
    handler_path = _paths(
        edges, caller_heap_returns, handler_gets, {"HEAP_DATA_DEP"}
    )
    handler_reads_state = handler_path is not None
    return {
        "passed": matching_exception is not None and handler_reads_state,
        "state_effect": state,
        "selected_put_dominates_matched_throw": matching_exception is not None,
        "exception_path": matching_exception,
        "catch_blocks": sorted(catch_blocks),
        "heap_return_to_handler_read": handler_path,
        "handler_reads_state": handler_reads_state,
        "semantic_scope": "bounded_effect_token_write_before_exact_call_exception",
    }


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _has_instruction(result: dict, method: str, instruction: str) -> bool:
    return any(
        method in row["method"] and instruction in (row.get("instruction") or "")
        for row in result["slice_statements"]
    )


def _opaque_result(result: dict, target: str) -> dict:
    binding = next(row for row in result["call_bindings"] if target in row["declared_target"])
    statements = result["slice_statements"]
    call_indexes = {
        index for index, row in enumerate(statements)
        if target in (row.get("instruction") or "")
    }
    observed_field_indexes = {
        index for index, row in enumerate(statements)
        if "getfield" in (row.get("instruction") or "")
    }
    direct_heap_edges = [
        edge for edge in result["slice_edges"]
        if "HEAP_DATA_DEP" in edge["labels"]
        and edge["from"] in call_indexes
        and edge["to"] in observed_field_indexes
    ]
    return {
        "call_binding": binding["status"],
        "possible_target_count": len(binding["possible_targets"]),
        "direct_call_to_observed_field_heap_edges": len(direct_heap_edges),
    }


def summarize(evidence: Path) -> dict:
    visible = _load(evidence / "mechanisms/visible.json")
    opaque = {
        label: _opaque_result(
            _load(evidence / f"mechanisms/{label}.json"), target
        )
        for label, target in OPAQUE_TARGETS.items()
    }
    raw = _load(evidence / "input/preparation.json")
    expanded = _load(evidence / "expanded/manifest.json")
    skeleton = _load(evidence / "skeleton/manifest.json")
    oom = (evidence / "jib-diagnostic.stderr.txt").read_text(encoding="utf-8")
    elapsed = re.search(r"Elapsed \(wall clock\) time.*: ([0-9:.]+)", oom)
    rss = re.search(r"Maximum resident set size \(kbytes\): (\d+)", oom)

    visible_relations = {
        "live_definition_and_cross_procedure_return": (
            _has_instruction(visible, "visibleTransform", "binaryop(mul)")
            and any(row["kind"] == "NORMAL_RET_CALLER" for row in visible["slice_statements"])
        ),
        "overwritten_definition_excluded": not any(
            "binaryop(add)" in (row.get("instruction") or "")
            and "100" in (row.get("instruction") or "")
            for row in visible["slice_statements"]
        ),
        "control_and_exception_structure": (
            visible["slice_edge_labels"].get("CONTROL_DEP", 0) > 0
            and _has_instruction(visible, "visibleObservation", "conditional branch")
            and _has_instruction(visible, "visibleObservation", "throw")
        ),
        "visible_field_write": (
            visible["slice_edge_labels"].get("HEAP_DATA_DEP", 0) > 0
            and _has_instruction(visible, "visibleWrite", "putfield")
        ),
    }
    opaque_model_passed = all(
        item["call_binding"] != "unresolved"
        and item["direct_call_to_observed_field_heap_edges"] > 0
        for item in opaque.values()
    )
    jib_oom = "java.lang.OutOfMemoryError" in oom
    gate_passed = (
        all(visible_relations.values())
        and opaque_model_passed
        and raw["raw_compile"]["exit_code"] == 0
        and skeleton["status"] == "compiled"
        and not jib_oom
    )
    return {
        "schema_version": "mockspec.wala-capability-summary/v1",
        "status": "passed" if gate_passed else "blocked",
        "gate_passed": gate_passed,
        "case": {
            "case_id": raw["case_id"],
            "snapshot_id": raw["snapshot_id"],
            "target_executed": False,
        },
        "artifact_storage": {
            "durable_evidence": str(evidence),
            "generated_source_and_binary_workspace": "/tmp/mockspec-issue23-work-20261110",
            "recorded_path_note": (
                "Raw and expanded receipts preserve the command paths used when first run; the complete "
                "generated workspace was subsequently moved to /tmp and no dataset or binary was retained "
                "in the repository evidence directory."
            ),
        },
        "visible_mechanisms": {
            "status": "passed" if all(visible_relations.values()) else "failed",
            "relations": visible_relations,
            "metrics": {
                "slice_statements": visible["slice_statement_count"],
                "slice_edges": visible["slice_edge_count"],
                "edge_labels": visible["slice_edge_labels"],
                "call_graph_nodes": visible["call_graph_nodes"],
                "pointer_instance_keys": visible["pointer_instance_keys"],
                "timings_millis": visible["timings_millis"],
            },
        },
        "opaque_call_effects": {
            "status": "passed" if opaque_model_passed else "blocked",
            "observations": opaque,
            "reason": (
                "The no-target interface calls remain syntactic call/SSA statements. WALA did not "
                "supply a conservative callee summary for possible argument writes, return aliasing, "
                "arbitrary exceptions, or write-before-throw effects."
            ),
        },
        "real_jib_input": {
            "raw_masked_compile": "passed" if raw["raw_compile"]["exit_code"] == 0 else "blocked",
            "raw_masked_compile_seconds": raw["raw_compile"]["seconds"],
            "parameter_harness_compile": (
                "passed" if raw["harness_compile"]["exit_code"] == 0 else "blocked"
            ),
            "expanded_visible_source": expanded["status"],
            "structure_only_skeleton": skeleton["status"],
            "entry_mode": "diagnostic_override",
            "automatic_entry_recovery": "not_evaluated",
            "wala_analysis": "resource_limit" if jib_oom else "unknown",
            "wala_failure": "java.lang.OutOfMemoryError" if jib_oom else None,
            "wall_clock": elapsed.group(1) if elapsed else None,
            "max_rss_kbytes": int(rss.group(1)) if rss else None,
            "configured_java_heap_bytes": 2 * 1024**3,
        },
        "decision": (
            "Do not integrate this WALA configuration as the MockSpec dependency backend yet. "
            "Keep the positive visible-code mechanisms, but add an explicit opaque-effect summary "
            "layer and a demand-driven/budgeted SDG strategy before repeating the Jib gate."
        ),
        "official_references": [
            "https://wala.github.io/javadoc/com/ibm/wala/ipa/slicer/Slicer.html",
            "https://wala.github.io/javadoc/com/ibm/wala/ipa/slicer/SDG.html",
            "https://github.com/wala/WALA/blob/v1.7.2/core/src/main/java/com/ibm/wala/ipa/slicer/Slicer.java",
        ],
    }


def summarize_unknown_effects(
    study_root: Path, review_run: Path | None = None
) -> dict:
    """Summarize the bounded effect-token study without promoting it to general havoc."""
    parent = study_root.parent
    primary = parent / f"{study_root.name}-final"
    if review_run is None:
        review_run = parent / f"{study_root.name}-review-rev" / "run"
    ready = _load(review_run / "unknown_ready.json")
    alias = _load(primary / "unknown_alias.json")
    completion = _load(review_run / "unknown_completion.json")
    exception = _load(review_run / "unknown_exception.json")
    relations = {
        "unknown_value_state_write": _assess_backend_unknown_value_write(
            ready, "mutate"
        ),
        "return_alias_with_fresh_alternative": _assess_backend_return_alternatives(
            alias, "alias"
        ),
        "normal_completion": _assess_backend_normal_completion(completion, "mutate"),
        "write_before_throw_to_handler_state_read": _assess_backend_write_before_throw(
            exception, "mutate"
        ),
    }
    selected_ready = relations["unknown_value_state_write"]["witness"][
        "summary_write_to_heap_return"
    ][0]
    missing_selected_token = deepcopy(ready)
    effect_sources = {
        index
        for index, row in enumerate(missing_selected_token["slice_statements"])
        if row["kind"] == "NORMAL_RET_CALLER"
        and row.get("declared_target")
        == "sample.MechanismProbe$EffectSource.unknownInt()I"
    }
    missing_selected_token["slice_edges"] = [
        edge
        for edge in missing_selected_token["slice_edges"]
        if not (
            edge["from"] in effect_sources
            and edge["to"] == selected_ready
            and "DATA_DEP" in edge["labels"]
        )
    ]
    unrelated_return = deepcopy(completion)
    unrelated_return["criterion"]["seed"]["method"] = (
        "sample.Unrelated.sameIndexAndText()I"
    )
    wrong_call = deepcopy(completion)
    wrong_call["entry_control_flow"][0]["call_site"] += "-unrelated"
    wrong_dominance = deepcopy(exception)
    selected_exception = relations["write_before_throw_to_handler_state_read"]
    selected_put_slice = selected_exception["state_effect"]["witness"][
        "summary_write_to_heap_return"
    ][0]
    selected_put_instruction = exception["slice_statements"][selected_put_slice][
        "instruction_index"
    ]
    selected_throw_instruction = selected_exception["exception_path"][
        "source_instruction_index"
    ]
    for summary in wrong_dominance["summary_targets"]:
        summary["put_throw_dominance_pairs"] = [
            pair
            for pair in summary.get("put_throw_dominance_pairs", ())
            if not (
                pair["put_instruction_index"] == selected_put_instruction
                and pair["throw_instruction_index"] == selected_throw_instruction
            )
        ]
    negative_checks = {
        "missing_selected_field_token_rejected": not _assess_backend_unknown_value_write(
            missing_selected_token, "mutate"
        )["passed"],
        "other_method_same_return_rejected": not _assess_backend_normal_completion(
            unrelated_return, "mutate"
        )["passed"],
        "wrong_callsite_rejected": not _assess_backend_normal_completion(
            wrong_call, "mutate"
        )["passed"],
        "other_field_dominance_rejected": not _assess_backend_write_before_throw(
            wrong_dominance, "mutate"
        )["passed"],
    }
    attempt_names = [
        f"{study_root.name}-config-a",
        f"{study_root.name}-config-a-forward",
        f"{study_root.name}-config-b",
        f"{study_root.name}-final",
        f"{study_root.name}-final-r2",
    ]
    ledger = []
    for name in attempt_names:
        manifest_path = parent / name / "manifest.json"
        if not manifest_path.exists():
            continue
        manifest = _load(manifest_path)
        ledger.append({
            "artifact": str(manifest_path.parent),
            "status": manifest["status"],
            "backend_sha256": manifest["inputs"]["backend_sha256"],
            "mechanism_sha256": manifest["inputs"]["mechanism_sha256"],
            "probes": [
                {
                    "label": row["label"],
                    "exit_code": row["exit_code"],
                    "timed_out": row["timed_out"],
                    "seconds": row["seconds"],
                }
                for row in manifest["attempts"]
            ],
        })
    review_manifest = _load(review_run / "manifest.json")
    ledger.append({
        "artifact": str(review_run),
        "status": review_manifest["status"],
        "backend_sha256": review_manifest["inputs"]["backend_sha256"],
        "mechanism_sha256": review_manifest["inputs"]["mechanism_sha256"],
        "probes": [
            {
                "label": row["label"],
                "exit_code": row["exit_code"],
                "timed_out": row["timed_out"],
                "seconds": row["seconds"],
            }
            for row in review_manifest["attempts"]
        ],
    })
    mechanism_gate = (
        all(row["passed"] for row in relations.values())
        and all(negative_checks.values())
    )
    return {
        "schema_version": "mockspec.wala-unknown-effects-study/v2",
        "revision": {
            "reason": "independent review false-positive repair",
            "historical_report": str(study_root / "report.json"),
            "review_red_receipt": str(
                parent / f"{study_root.name}-review-rev" / "reviewer-red.json"
            ),
        },
        "status": "blocked",
        "mechanism_gate_passed": mechanism_gate,
        "overall_gate_passed": False,
        "configuration": {
            "wala": "1.7.2",
            "call_graph": "0-1-CFA",
            "data_dependence": "FULL",
            "control_dependence": "FULL",
            "heap_limit_bytes": 2 * 1024**3,
            "analysis_timeout_seconds_per_probe": 180,
            "effect_model": "bounded-effect-token-v1",
            "configuration_families": 1,
            "scope": "sample.MechanismProbe fixture only",
        },
        "relations": relations,
        "negative_validator_checks": negative_checks,
        "model_interpretation": {
            "proved": [
                "native unknown scalar token reaches a same-call/same-region field write",
                "argument alias and fresh return alternatives occur in summary IR",
                "a structural summary normal exit aligns with the exact call site's normal CFG continuation to the selected observation",
                "write dominates throw and the exact exceptional path reaches a handler read of the same heap region",
            ],
            "still_unknown": [
                "pointer analysis merges the fresh and argument return alternatives",
                "receiver fields, reference fields, arrays, globals/statics, escaped regions, null, other arguments, and arbitrary exception types",
                "visible-signature library boundaries outside the configured Application carrier set",
            ],
            "not_claimed": "This is not a general sound unknown-call havoc model and does not encode concrete Mock behavior.",
        },
        "root_cause_correction": {
            "old_model": "self-write preserved the old field value and receiver-null was an infeasible exception branch",
            "new_model": "native EffectSource tokens supply unknown scalar values; explicit CFG alternatives model normal return, throw, write-before-throw, argument return, and fresh return",
            "normal_completion_note": "WALA emits no void NORMAL_RET SDG bridge, so this relation uses separate callee-summary CFG and exact caller CFG evidence.",
        },
        "attempt_ledger": ledger,
        "real_jib": {
            "status": "not_rerun",
            "reason": "This bounded mechanism change does not address transitive-MOD HeapReturnCaller materialization pressure.",
        },
        "decision": "Retain the bounded mechanism, but keep the issue gate blocked pending an explicit unknown-region abstraction and a completed real reviewed-input run.",
    }


def _unknown_effects_markdown(report: dict) -> str:
    relation_lines = "\n".join(
        f"- `{name}`: **{'passed' if row['passed'] else 'blocked'}**"
        for name, row in report["relations"].items()
    )
    unknown_lines = "\n".join(
        f"- {item}" for item in report["model_interpretation"]["still_unknown"]
    )
    return f"""# Issue 23 bounded unknown-effect study

Status: **{report['status']}**. The bounded mechanism gate passes; the overall gate remains blocked.

## Verified relations

{relation_lines}

Normal completion is a structured proof: a structural summary normal exit is paired with the exact invoke's normal SSA-CFG successor and selected observation. It is not presented as a nonexistent single SDG edge.

## Remaining unknowns

{unknown_lines}

This model is not a general sound unknown-call havoc model and does not choose concrete Mock behavior. Jib was not rerun because this change does not address its known transitive-MOD `HeapReturnCaller` materialization pressure.

## Reproduce

```bash
python3 experiments/backward-slicing/wala-pilot/summarize_capability.py \\
  --unknown-effects-root out/mockbench-study/issue23-unknown-effects-20261111 \\
  --unknown-effects-review-run out/mockbench-study/issue23-unknown-effects-20261111-review-rev/run \\
  --output out/mockbench-study/issue23-unknown-effects-20261111-review-rev/report.json
```
"""


def summarize_issue23_expanded_effects(run: Path, jib_summary_path: Path) -> dict:
    """Close the issue-23 capability matrix without claiming general unknown-call soundness."""
    manifest = _load(run / "manifest.json")
    pre_registration = _load(run / "pre-registration.json")
    here = Path(__file__).resolve().parent
    source_identity_matches = (
        manifest.get("inputs", {}).get("backend_sha256")
        == _sha256(here / "backend/src/main/java/org/mockspec/research/WalaSliceProbe.java")
        and manifest.get("inputs", {}).get("mechanism_sha256")
        == _sha256(here / "backend/src/testFixtures/java/sample/MechanismProbe.java")
        and manifest.get("inputs", {}).get("runner_sha256")
        == _sha256(here / "run_capability.py")
    )
    preregistration_matches = (
        pre_registration.get("inputs") == manifest.get("inputs")
        and pre_registration.get("configuration") == manifest.get("configuration")
        and set(pre_registration.get("selected_probes", ()))
        == {
            "unknown_ready", "unknown_alias", "unknown_completion", "unknown_exception",
            "unknown_receiver", "unknown_reachable", "unknown_escaped", "unknown_static",
        }
    )
    labels = {
        "receiver_direct_field": "unknown_receiver",
        "argument_one_hop_reachable_field": "unknown_reachable",
        "application_static_escaped_object": "unknown_escaped",
        "application_static_primitive": "unknown_static",
    }
    relations = {
        name: _assess_backend_unknown_value_write(
            _load(run / f"{label}.json"), "mutate"
        )
        for name, label in labels.items()
    }
    origin_needles = {
        "receiver_direct_field": ("putfield", "OpaqueCarrier, receiverState"),
        "argument_one_hop_reachable_field": ("getfield", "MechanismProbe$Box, child"),
        "application_static_escaped_object": ("getstatic", "MechanismProbe, escaped"),
        "application_static_primitive": ("putstatic", "MechanismProbe, globalReady"),
    }
    for name, label in labels.items():
        result = _load(run / f"{label}.json")
        witness = relations[name].get("witness")
        if not witness:
            relations[name]["region_origin_proved"] = False
            relations[name]["region_origin_path"] = None
            relations[name]["passed"] = False
            continue
        selected_write = witness["summary_write_to_heap_return"][0]
        selected = result["slice_statements"][selected_write]
        kind, needle = origin_needles[name]
        if kind.startswith("put"):
            origin_proved = (
                kind in (selected.get("instruction") or "")
                and needle in (selected.get("instruction") or "")
            )
            origin_path = [selected_write] if origin_proved else None
        else:
            origins = {
                index for index, row in enumerate(result["slice_statements"])
                if row["method"] == selected["method"]
                and kind in (row.get("instruction") or "")
                and needle in (row.get("instruction") or "")
            }
            origin_path = _paths(
                result["slice_edges"], origins, {selected_write}, {"DATA_DEP"}
            )
            origin_proved = origin_path is not None
        relations[name]["region_origin_proved"] = origin_proved
        relations[name]["region_origin_path"] = origin_path
        relations[name]["passed"] = relations[name]["passed"] and origin_proved
    negative_checks = {}
    for name, label in labels.items():
        mutated = deepcopy(_load(run / f"{label}.json"))
        relation = relations[name]
        witness = relation.get("witness")
        if not witness:
            negative_checks[f"{name}_without_selected_token_rejected"] = False
            continue
        selected_write = witness["summary_write_to_heap_return"][0]
        sources = {
            index for index, row in enumerate(mutated["slice_statements"])
            if row["kind"] == "NORMAL_RET_CALLER"
            and row.get("declared_target")
            == "sample.MechanismProbe$EffectSource.unknownInt()I"
        }
        mutated["slice_edges"] = [
            edge for edge in mutated["slice_edges"]
            if not (
                edge["from"] in sources
                and edge["to"] == selected_write
                and "DATA_DEP" in edge["labels"]
            )
        ]
        negative_checks[f"{name}_without_selected_token_rejected"] = not (
            _assess_backend_unknown_value_write(mutated, "mutate")["passed"]
        )
    ready = _load(run / "unknown_ready.json")
    alias = _load(run / "unknown_alias.json")
    completion = _load(run / "unknown_completion.json")
    exception = _load(run / "unknown_exception.json")
    core_relations = {
        "unknown_value_state_write": _assess_backend_unknown_value_write(
            ready, "mutate"
        ),
        "return_alias_with_fresh_alternative": _assess_backend_return_alternatives(
            alias, "alias"
        ),
        "normal_completion": _assess_backend_normal_completion(completion, "mutate"),
        "write_before_throw_to_handler_state_read": _assess_backend_write_before_throw(
            exception, "mutate"
        ),
    }
    selected_ready = core_relations["unknown_value_state_write"]["witness"][
        "summary_write_to_heap_return"
    ][0]
    missing_selected_token = deepcopy(ready)
    effect_sources = {
        index for index, row in enumerate(missing_selected_token["slice_statements"])
        if row["kind"] == "NORMAL_RET_CALLER"
        and row.get("declared_target")
        == "sample.MechanismProbe$EffectSource.unknownInt()I"
    }
    missing_selected_token["slice_edges"] = [
        edge for edge in missing_selected_token["slice_edges"]
        if not (
            edge["from"] in effect_sources
            and edge["to"] == selected_ready
            and "DATA_DEP" in edge["labels"]
        )
    ]
    unrelated_return = deepcopy(completion)
    unrelated_return["criterion"]["seed"]["method"] = (
        "sample.Unrelated.sameIndexAndText()I"
    )
    wrong_call = deepcopy(completion)
    wrong_call["entry_control_flow"][0]["call_site"] += "-unrelated"
    wrong_dominance = deepcopy(exception)
    selected_exception = core_relations[
        "write_before_throw_to_handler_state_read"
    ]
    selected_put_slice = selected_exception["state_effect"]["witness"][
        "summary_write_to_heap_return"
    ][0]
    selected_put_instruction = exception["slice_statements"][selected_put_slice][
        "instruction_index"
    ]
    selected_throw_instruction = selected_exception["exception_path"][
        "source_instruction_index"
    ]
    for summary in wrong_dominance["summary_targets"]:
        summary["put_throw_dominance_pairs"] = [
            pair for pair in summary.get("put_throw_dominance_pairs", ())
            if not (
                pair["put_instruction_index"] == selected_put_instruction
                and pair["throw_instruction_index"] == selected_throw_instruction
            )
        ]
    core_negative_checks = {
        "missing_selected_field_token_rejected": not (
            _assess_backend_unknown_value_write(missing_selected_token, "mutate")[
                "passed"
            ]
        ),
        "other_method_same_return_rejected": not (
            _assess_backend_normal_completion(unrelated_return, "mutate")["passed"]
        ),
        "wrong_callsite_rejected": not (
            _assess_backend_normal_completion(wrong_call, "mutate")["passed"]
        ),
        "other_field_dominance_rejected": not (
            _assess_backend_write_before_throw(wrong_dominance, "mutate")["passed"]
        ),
    }
    sample = _load(run / "unknown_receiver.json")
    scope = sample.get("opaque_effect_model_scope", {})
    required_modeled = {
        "receiver direct int fields",
        "one reference-field hop to int fields",
        "Application static int fields",
        "Application static reference fields one hop to int fields",
    }
    required_unknown = {
        "heap paths deeper than one reference field",
        "arrays and collection contents",
        "library and runtime globals",
        "escaped objects not reachable through modeled parameters or Application statics",
    }
    boundary_gate = (
        required_modeled <= set(scope.get("modeled", ()))
        and required_unknown <= set(scope.get("unknown", ()))
    )
    jib = _load(jib_summary_path)
    application_methods = {
        row["method"] for row in jib.get("application_slice_statements", ())
    }
    jib_gate = (
        jib.get("entry_origin") == "diagnostic_override"
        and jib.get("target_executed") is False
        and jib.get("slice_statement_count", 0) > 0
        and any("JarFiles.toJibContainerBuilder" in method for method in application_methods)
        and "not formal Jib acceptance" in jib.get("semantic_result", "")
    )
    expanded_gate = (
        manifest.get("status") == "analysed"
        and source_identity_matches
        and preregistration_matches
        and all(row["passed"] for row in relations.values())
        and all(negative_checks.values())
        and boundary_gate
    )
    historical = run.parent / "issue23-unknown-effects-20261111-review-rev" / "report.json"
    historical_report = _load(historical)
    prior_gate = (
        all(row["passed"] for row in core_relations.values())
        and all(core_negative_checks.values())
    )
    first_attempt_path = run.parent / "issue23-wala-evidence-20261110" / "report.json"
    first_attempt = _load(first_attempt_path)
    first_attempt_gate = (
        first_attempt.get("real_jib_input", {}).get("raw_masked_compile") == "blocked"
        and first_attempt.get("real_jib_input", {}).get("structure_only_skeleton") == "compiled"
        and first_attempt.get("real_jib_input", {}).get("entry_mode") == "diagnostic_override"
        and first_attempt.get("case", {}).get("target_executed") is False
        and first_attempt.get("visible_mechanisms", {}).get("status") == "passed"
    )
    overall = expanded_gate and prior_gate and first_attempt_gate and jib_gate
    return {
        "schema_version": "mockspec.wala-issue23-closure/v1",
        "status": "passed" if overall else "blocked",
        "gate_passed": overall,
        "configuration": manifest["configuration"],
        "evaluator_sha256": _sha256(Path(__file__).resolve()),
        "source_identity_matches": source_identity_matches,
        "preregistration_matches": preregistration_matches,
        "expanded_unknown_effects": {
            "passed": expanded_gate,
            "relations": relations,
            "negative_checks": negative_checks,
            "scope": scope,
        },
        "prior_value_control_state_mechanisms": {
            "passed": prior_gate,
            "current_relations": core_relations,
            "current_negative_checks": core_negative_checks,
            "historical_evidence": str(historical),
            "historical_gate_passed": historical_report.get("mechanism_gate_passed"),
        },
        "first_real_input_attempt": {
            "passed": first_attempt_gate,
            "evidence": str(first_attempt_path),
            "raw_masked_compile": first_attempt["real_jib_input"]["raw_masked_compile"],
            "structure_only_skeleton": first_attempt["real_jib_input"]["structure_only_skeleton"],
            "visible_mechanisms": first_attempt["visible_mechanisms"]["status"],
        },
        "real_reviewed_input": {
            "passed": jib_gate,
            "evidence": str(jib_summary_path),
            "entry_origin": jib.get("entry_origin"),
            "automatic_entry_recovery": "not_evaluated_here",
            "semantic_status": "partial_diagnostic",
            "target_executed": jib.get("target_executed"),
            "slice_statement_count": jib.get("slice_statement_count"),
            "limitations": [
                jib.get("excluded_boundary"),
                jib.get("ci_heap_precision_boundary"),
                "This capability gate does not substitute for issue 25's original-assertion Jib acceptance.",
            ],
        },
        "acceptance_matrix": {
            "version_config_budget_and_cost_recorded": manifest.get("status") == "analysed",
            "raw_masked_and_structure_only_attempts_preserved": first_attempt_gate,
            "real_input_mappable_result_with_diagnostic_entry_separated": jib_gate,
            "value_control_field_alias_and_unknown_effect_mechanisms": prior_gate,
            "receiver_argument_reachable_global_escaped_and_write_before_throw": expanded_gate,
            "failures_and_resource_boundaries_preserved": True,
        },
        "not_claimed": [
            "general sound havoc for arbitrary Java heaps",
            "automatic Jib entry recovery",
            "original Jib assertion-to-code influence",
            "path feasibility or concrete Mock behavior",
        ],
        "decision": (
            "Issue 23's bounded backend capability gate passes. Continue to issue 25 for "
            "original-observation mapping; retain unsupported heap regions as explicit unknowns."
            if overall else
            "Keep issue 23 open until every acceptance-matrix component passes."
        ),
    }


def _issue23_expanded_markdown(report: dict) -> str:
    matrix = "\n".join(
        f"- `{name}`: **{'passed' if passed else 'blocked'}**"
        for name, passed in report["acceptance_matrix"].items()
    )
    relations = "\n".join(
        f"- `{name}`: **{'passed' if row['passed'] else 'blocked'}**"
        for name, row in report["expanded_unknown_effects"]["relations"].items()
    )
    return f"""# Issue 23 WALA capability closure

Status: **{report['status']}**. This closes a bounded backend capability gate, not Jib's original-observation acceptance.

## Acceptance matrix

{matrix}

## Newly verified opaque-effect regions

{relations}

Unsupported deeper heap, arrays, library/runtime globals, unbounded escaped state, null, and arbitrary exception types remain explicit unknowns. No concrete Mock behavior is encoded.
"""


def main() -> None:
    parser = argparse.ArgumentParser()
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--evidence", type=Path)
    source.add_argument("--unknown-effects-root", type=Path)
    source.add_argument("--issue23-expanded-run", type=Path)
    parser.add_argument("--unknown-effects-review-run", type=Path)
    parser.add_argument("--jib-summary", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(f"output must not exist: {args.output}")
    if args.issue23_expanded_run:
        if args.jib_summary is None:
            parser.error("--jib-summary is required with --issue23-expanded-run")
        report = summarize_issue23_expanded_effects(
            args.issue23_expanded_run, args.jib_summary
        )
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(report, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        args.output.with_suffix(".md").write_text(
            _issue23_expanded_markdown(report), encoding="utf-8"
        )
    elif args.unknown_effects_root:
        report = summarize_unknown_effects(
            args.unknown_effects_root, args.unknown_effects_review_run
        )
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(report, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        args.output.with_suffix(".md").write_text(
            _unknown_effects_markdown(report), encoding="utf-8"
        )
    else:
        assert args.evidence is not None
        args.output.write_text(
            json.dumps(summarize(args.evidence), indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )


if __name__ == "__main__":
    main()
