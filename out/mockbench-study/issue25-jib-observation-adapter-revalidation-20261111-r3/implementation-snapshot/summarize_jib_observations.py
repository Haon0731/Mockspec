"""Classify Jib observation evidence without equating CI membership with support."""
from __future__ import annotations

import argparse
import copy
import hashlib
import importlib.util
import json
import re
from pathlib import Path
from typing import Any

from jib_library_evidence import extract_method_fact_copy_constructor_witness
from jib_control_evidence import map_control_evidence
from jib_optional_evidence import extract_setter_bytecode_evidence, map_optional_evidence
from jib_observation_adapter import adapt_jib_observations, validate_adapter_observations


HERE = Path(__file__).resolve().parent
EXPECTATIONS = HERE / "jib_observation_expectations.json"
SOURCE_OWNER_NAMES = {
    "CommonContainerConfigCliOptions": "com.google.cloud.tools.jib.cli.CommonContainerConfigCliOptions",
    "ArtifactProcessor": "com.google.cloud.tools.jib.cli.ArtifactProcessor",
}
FIELD_SETTERS = {
    "environment": ("JibContainerBuilder.setEnvironment", "ContainerBuildPlan$Builder.setEnvironment"),
    "labels": ("JibContainerBuilder.setLabels", "ContainerBuildPlan$Builder.setLabels"),
    "volumes": ("JibContainerBuilder.setVolumes", "ContainerBuildPlan$Builder.setVolumes"),
    "exposedPorts": ("JibContainerBuilder.setExposedPorts", "ContainerBuildPlan$Builder.setExposedPorts"),
    "entrypoint": ("JibContainerBuilder.setEntrypoint", "ContainerBuildPlan$Builder.setEntrypoint"),
    "layers": ("JibContainerBuilder.setFileEntriesLayers", "ContainerBuildPlan$Builder.setLayers"),
    "user": ("JibContainerBuilder.setUser", "ContainerBuildPlan$Builder.setUser"),
    "format": ("JibContainerBuilder.setFormat", "ContainerBuildPlan$Builder.setFormat"),
    "creationTime": ("JibContainerBuilder.setCreationTime", "ContainerBuildPlan$Builder.setCreationTime"),
}
REVIEWED_COPY_CONSTRUCTORS = {
    "java.util.ArrayList.<init>(Ljava/util/Collection;)V",
    "java.util.HashMap.<init>(Ljava/util/Map;)V",
    "java.util.HashSet.<init>(Ljava/util/Collection;)V",
}
REVIEWED_COPY_DOCUMENTATION = {
    "java.util.ArrayList.<init>(Ljava/util/Collection;)V": "https://docs.oracle.com/en/java/javase/11/docs/api/java.base/java/util/ArrayList.html#%3Cinit%3E(java.util.Collection)",
    "java.util.HashMap.<init>(Ljava/util/Map;)V": "https://docs.oracle.com/en/java/javase/11/docs/api/java.base/java/util/HashMap.html#%3Cinit%3E(java.util.Map)",
    "java.util.HashSet.<init>(Ljava/util/Collection;)V": "https://docs.oracle.com/en/java/javase/11/docs/api/java.base/java/util/HashSet.html#%3Cinit%3E(java.util.Collection)",
}


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _lambda_bootstrap_targets(javap_text: str) -> dict[str, str]:
    result: dict[str, str] = {}
    current: str | None = None
    for line in javap_text.splitlines():
        match = re.match(r"\s*(\d+):\s+#\d+\s+REF_invokeStatic", line)
        if match:
            current = match.group(1)
            continue
        target = re.search(r"REF_invokeVirtual\s+([\w/$]+)\.([\w$]+):", line)
        if current is not None and target:
            result[current] = target.group(1).replace("/", ".") + "." + target.group(2)
            current = None
    return result


def _regions(row: dict, kind: str) -> set[str]:
    return set(row.get(kind, []))


def _matching(rows: list[dict], method: str, opcode: str, owner: str, field: str) -> list[dict]:
    marker = f"{owner}, {field},"
    return [row for row in rows if method in row.get("method", "")
            and opcode in (row.get("instruction") or "")
            and marker in (row.get("instruction") or "")]


def _invoke(rows: list[dict], caller: str, target: str) -> dict | None:
    return next((row for row in rows if caller in row.get("method", "")
                 and target in (row.get("declared_target") or "")), None)


def _return(rows: list[dict], method: str) -> dict | None:
    return next((row for row in rows if method in row.get("method", "")
                 and (row.get("instruction") or "").startswith("return ")), None)


def _target_parameters(call: dict | None, method: str) -> list[int]:
    if call is None:
        return []
    detail = next((item for item in call.get("possible_target_details", [])
                   if method in item.get("method", "")), None)
    return detail.get("parameter_value_numbers", []) if detail else []


def _resolved_with_ir(call: dict | None, method: str) -> bool:
    return bool(call and any(method in item.get("method", "") and item.get("has_ir")
                             for item in call.get("possible_target_details", [])))


def _reference_return(target: object) -> bool:
    if not isinstance(target, str) or ")" not in target:
        return False
    descriptor = target.rsplit(")", 1)[1]
    return descriptor.startswith("L") or descriptor.startswith("[")


def _fact_rows(fact: dict) -> list[dict]:
    return [*fact.get("instructions", []), *fact.get("phi_instructions", [])]


def _value_path(rows: list[dict], start: int, goal: int) -> dict:
    graph: dict[int, list[tuple[int, dict, bool]]] = {}
    for row in rows:
        is_invoke = bool(row.get("declared_target"))
        resolved = (not is_invoke
                    or any(item.get("has_ir") for item in row.get("possible_target_details", [])))
        for definition in row.get("definitions", []):
            for use in row.get("uses", []):
                graph.setdefault(use, []).append((definition, row, resolved))
    queue: list[tuple[int, list[dict], bool]] = [(start, [], True)]
    seen = {start}
    while queue:
        value, path, resolved = queue.pop(0)
        if value == goal:
            return {"status": "complete" if resolved else "unknown_boundary", "path": path}
        for target, row, step_resolved in graph.get(value, []):
            if target in seen:
                continue
            seen.add(target)
            queue.append((target, [*path, {
                "instruction_index": row.get("instruction_index"),
                "basic_block": row.get("basic_block"),
                "instruction": row.get("instruction"),
                "from_value": value, "to_value": target,
            }], resolved and step_resolved))
    return {"status": "missing", "path": []}


def _bounded_copy_effect(rows: list[dict], source: int, goal: int) -> dict:
    for call in rows:
        target = call.get("declared_target") or ""
        uses = call.get("uses", [])
        definitions = call.get("definitions", [])
        if source not in uses or not target:
            continue
        if target in REVIEWED_COPY_CONSTRUCTORS and uses and source in uses[1:]:
            copied_value = uses[0]
            mechanism = "collection_copy_constructor_receiver"
        elif ".copyOf(" in target and definitions:
            copied_value = definitions[0]
            mechanism = "collection_copy_factory_return"
        else:
            continue
        path = _value_path(rows, copied_value, goal)
        if copied_value == goal:
            path = {"status": "complete", "path": []}
        if path["status"] == "complete":
            return {
                "status": "analysis_may_copy",
                "mechanism": mechanism,
                "call": {"instruction_index": call.get("instruction_index"),
                         "declared_target": target, "uses": uses,
                         "definitions": definitions},
                "effect_summary": {"status": "reviewed_standard_library_copy_constructor",
                                   "documentation": REVIEWED_COPY_DOCUMENTATION[target]},
                "copy_to_field_path": path,
                "limitations": ["collection contents and concrete values are not inferred"],
            }
    return {"status": "missing", "mechanism": None, "copy_to_field_path": {"status": "missing", "path": []}}


def _normal_return_dominance(fact: dict, call: dict) -> dict:
    blocks = {block["number"]: block for block in fact.get("cfg_blocks", [])}
    entry = next((number for number, block in blocks.items() if block.get("entry")), None)
    call_block = call.get("basic_block")
    return_blocks = {row.get("basic_block") for row in fact.get("instructions", [])
                     if (row.get("instruction") or "").startswith("return")}
    if entry is None or call_block is None or not return_blocks:
        return {"status": "unknown", "dominates_normal_returns": False}
    predecessors: dict[int, set[int]] = {number: set() for number in blocks}
    for number, block in blocks.items():
        for successor in block.get("normal_successor_blocks", []):
            predecessors.setdefault(successor, set()).add(number)
    all_blocks = set(blocks)
    dominators = {number: ({entry} if number == entry else set(all_blocks)) for number in blocks}
    changed = True
    while changed:
        changed = False
        for number in blocks:
            if number == entry:
                continue
            preds = predecessors.get(number, set())
            value = ({number} | set.intersection(*(dominators[pred] for pred in preds))
                     if preds else {number})
            if value != dominators[number]:
                dominators[number] = value
                changed = True
    dominates = all(call_block in dominators.get(block, set()) for block in return_blocks)
    return {"status": "complete" if dominates else "conditional",
            "dominates_normal_returns": dominates,
            "setter_block": call_block, "normal_return_blocks": sorted(return_blocks)}


def source_to_setter_witness(raw: dict, expected: dict) -> dict:
    setter_names = FIELD_SETTERS.get(expected["field"])
    if setter_names is None or not expected.get("source_calls"):
        return {"status": "not_applicable", "alternatives": [],
                "overwrite": {"status": "not_established"}}
    facts = raw.get("method_ssa_facts", [])
    mut_facts = [fact for fact in facts if "JarFiles.toJibContainerBuilder" in fact.get("method", "")]
    if len(mut_facts) != 1:
        return {"status": "incomplete", "alternatives": [],
                "missing": ["unique MUT method SSA facts"],
                "overwrite": {"status": "not_established"}}
    mut = mut_facts[0]
    mut_rows = _fact_rows(mut)
    wrapper_name, builder_name = setter_names
    wrapper_call = next((row for row in mut_rows
                         if wrapper_name in (row.get("declared_target") or "")), None)
    callback_binding: dict[str, object] | None = None
    alternatives: list[dict[str, Any]] = []
    for source in expected["source_calls"]:
        owner, method = source.rsplit(".", 1)
        owner = SOURCE_OWNER_NAMES.get(owner, owner)
        source_call = next((row for row in mut_rows
                            if f"{owner}.{method}(" in (row.get("declared_target") or "")), None)
        checks: dict[str, bool] = {}
        checks["source_normal_return"] = bool(source_call and source_call.get("definitions"))
        checks["wrapper_setter_call"] = bool(wrapper_call and len(wrapper_call.get("uses", [])) > 1)
        if source_call is not None and source_call.get("definitions") \
                and wrapper_call is not None and len(wrapper_call.get("uses", [])) > 1:
            path = _value_path(mut_rows, source_call["definitions"][0], wrapper_call["uses"][1])
        else:
            path = {"status": "missing", "path": []}
        if source_call is not None and source_call.get("definitions") and wrapper_call is None:
            source_index = source_call.get("instruction_index", -1)
            for lambda_row in mut_rows:
                target = lambda_row.get("declared_target") or ""
                match = re.search(r"\$JarFiles\$(\d+)\(", target)
                if not match or lambda_row.get("instruction_index", -1) <= source_index:
                    continue
                lambda_detail = next((detail for detail in lambda_row.get("possible_target_details", [])
                                      if detail.get("method") == target and detail.get("has_ir")), None)
                lambda_fact = next((fact for fact in facts if lambda_detail
                                    and fact.get("method") == lambda_detail.get("method")
                                    and fact.get("context") == lambda_detail.get("context")), None)
                callback_call = next((row for row in _fact_rows(lambda_fact or {})
                                      if wrapper_name in (row.get("declared_target") or "")), None)
                mapped_target = ((callback_call or {}).get("declared_target")
                                 or (raw.get("lambda_bootstrap_targets") or {}).get(match.group(1)))
                if wrapper_name not in str(mapped_target) or not lambda_row.get("definitions"):
                    continue
                if_present = next((row for row in mut_rows
                                   if "java.util.Optional.ifPresent(" in
                                   (row.get("declared_target") or "")
                                   and source_call["definitions"][0] in row.get("uses", [])
                                   and lambda_row["definitions"][0] in row.get("uses", [])), None)
                if if_present:
                    callback_binding = {
                        "bootstrap_index": int(match.group(1)),
                        "lambda_callsite": lambda_row.get("call_site"),
                        "lambda_target": target,
                        "lambda_method_context": ({"method": lambda_fact.get("method"),
                                                   "context": lambda_fact.get("context")}
                                                  if lambda_fact else None),
                        "callback_callsite": (callback_call or {}).get("call_site"),
                        "method_handle_target": mapped_target,
                        "if_present_callsite": if_present.get("call_site"),
                    }
                    checks["wrapper_setter_call"] = True
                    checks["conditional_callback_binding"] = True
                    path = {"status": "conditional_callback_may", "path": [callback_binding]}
                    break
        checks["source_to_wrapper_actual"] = path["status"] == "complete"
        checks["source_to_conditional_callback"] = path["status"] == "conditional_callback_may"
        alternatives.append({"source_call": f"{owner}.{method}", "checks": checks,
                             "ssa_path": path, "modality": "analysis_may_unknown_value"})

    wrapper_detail = next((detail for detail in (wrapper_call or {}).get("possible_target_details", [])
                           if wrapper_name in detail.get("method", "") and detail.get("has_ir")), None)
    if wrapper_detail is None and callback_binding is not None:
        callback_facts = [fact for fact in facts if wrapper_name in fact.get("method", "")]
        if len(callback_facts) == 1:
            wrapper_detail = {"method": callback_facts[0]["method"],
                              "context": callback_facts[0]["context"], "has_ir": True}
    wrapper_fact = next((fact for fact in facts if wrapper_detail
                         and fact.get("method") == wrapper_detail.get("method")
                         and fact.get("context") == wrapper_detail.get("context")), None)
    wrapper_rows = _fact_rows(wrapper_fact) if wrapper_fact else []
    builder_call = next((row for row in wrapper_rows
                         if builder_name in (row.get("declared_target") or "")), None)
    wrapper_parameters = (wrapper_fact or {}).get("parameter_value_numbers", [])
    wrapper_formal = wrapper_parameters[1] if len(wrapper_parameters) > 1 else None
    wrapper_to_builder = (_value_path(wrapper_rows, wrapper_formal, builder_call["uses"][1])
                          if wrapper_formal is not None and builder_call
                          and len(builder_call.get("uses", [])) > 1
                          else {"status": "missing", "path": []})
    builder_detail = next((detail for detail in (builder_call or {}).get("possible_target_details", [])
                           if builder_name in detail.get("method", "") and detail.get("has_ir")), None)
    builder_fact = next((fact for fact in facts if builder_detail
                         and fact.get("method") == builder_detail.get("method")
                         and fact.get("context") == builder_detail.get("context")), None)
    builder_rows = _fact_rows(builder_fact) if builder_fact else []
    builder_parameters = (builder_fact or {}).get("parameter_value_numbers", [])
    builder_formal = builder_parameters[1] if len(builder_parameters) > 1 else None
    field_writes = [row for row in builder_rows
                    if "putfield" in (row.get("instruction") or "")
                    and f"ContainerBuildPlan$Builder, {expected['field']}," in
                    (row.get("instruction") or "")]
    field_write = None
    builder_to_write = {"status": "missing", "path": []}
    for candidate in field_writes:
        if builder_formal is None or len(candidate.get("uses", [])) <= 1:
            continue
        direct = _value_path(builder_rows, builder_formal, candidate["uses"][1])
        if direct["status"] == "complete":
            field_write, builder_to_write = candidate, direct
            break
        copied = _bounded_copy_effect(builder_rows, builder_formal, candidate["uses"][1])
        if copied["status"] == "analysis_may_copy":
            field_write, builder_to_write = candidate, copied
            break
    cross_checks = {
        "resolved_wrapper_target": wrapper_detail is not None,
        "wrapper_formal_to_builder_actual": wrapper_to_builder["status"] == "complete",
        "resolved_builder_target": builder_detail is not None,
        "builder_formal_to_exact_field_write": builder_to_write["status"] in
        {"complete", "analysis_may_copy"},
    }
    complete_alternatives = [item for item in alternatives
                             if item["checks"]["source_to_wrapper_actual"]]
    conditional_alternatives = [item for item in alternatives
                                if item["checks"]["source_to_conditional_callback"]]
    chain_complete = bool((complete_alternatives or conditional_alternatives)
                          and all(cross_checks.values()))
    dominance = _normal_return_dominance(mut, wrapper_call) if wrapper_call else {
        "status": "unknown", "dominates_normal_returns": False}
    return {
        "status": ("conditional_complete" if chain_complete and conditional_alternatives
                   else "complete" if chain_complete else "incomplete"),
        "alternatives": alternatives,
        "cross_call_checks": cross_checks,
        "wrapper_formal_to_builder_actual": wrapper_to_builder,
        "builder_formal_to_field_write": builder_to_write,
        "field_write": ({"method": builder_fact.get("method"),
                         "context": builder_fact.get("context"),
                         "instruction_index": field_write.get("instruction_index"),
                         "instruction": field_write.get("instruction")}
                        if builder_fact and field_write else None),
        "conditional_callback_binding": callback_binding,
        "overwrite": {
            "status": ("setter_must_execute_on_normal_return"
                       if chain_complete and dominance["dominates_normal_returns"]
                       else "conditional_or_not_established"),
            "scope": "normal returns of the visible MUT only",
            "dominance": dominance,
            "limitations": ["exceptional exits and hidden aliasing are not resolved"],
        },
    }


def _typed_constructor_link_checks(
    from_parameters: list[int], ctor_call: dict | None, ctor_parameters: list[int],
    new_builder: dict | None, returned_builder: dict | None,
    common_call: dict | None, source_to_common_config: dict,
) -> dict[str, bool]:
    return {
        "source_actual_to_constructor_formal": bool(
            len(from_parameters) == 1 and ctor_call and len(ctor_call.get("uses", [])) > 1
            and len(ctor_parameters) > 1
            and ctor_call["uses"][1] == from_parameters[0]),
        "new_receiver_to_constructor_and_return": bool(
            new_builder and new_builder.get("definitions") and ctor_call and returned_builder
            and new_builder["definitions"][0] == ctor_call.get("uses", [None])[0]
            and returned_builder.get("uses", [None])[0] == new_builder["definitions"][0]),
        "typed_constructor_source_to_common_configuration":
            source_to_common_config["status"] == "complete",
        "same_builder_receiver_into_common_constructor": bool(
            common_call and ctor_parameters
            and common_call.get("uses", [None])[0] == ctor_parameters[0]),
    }


def _jib_dispatcher_witness(fact: dict | None) -> dict:
    if fact is None:
        return {"status": "incomplete", "branches": [], "missing": ["dispatcher facts"]}
    rows = _fact_rows(fact)
    parameters = fact.get("parameter_value_numbers", [])
    source = parameters[0] if parameters else None
    calls = [row for row in rows if
             (row.get("declared_target") or "").startswith(
                 "com.google.cloud.tools.jib.api.Jib.from(Lcom/google/cloud/tools/jib/api/")]
    returns = [row for row in rows if (row.get("instruction") or "").startswith("return ")]
    branches = []
    for call in calls:
        target = call.get("declared_target")
        source_path = (_value_path(rows, source, call["uses"][0])
                       if source is not None and call.get("uses") else
                       {"status": "missing", "path": []})
        matching_return = next((row for row in returns
                                if call.get("definitions") and row.get("uses")
                                and row["uses"][0] == call["definitions"][0]), None)
        target_detail = next((detail for detail in call.get("possible_target_details", [])
                              if detail.get("method") == target and detail.get("has_ir")), None)
        branches.append({
            "target": target,
            "source_formal_to_typed_actual": source_path["status"] in
            {"complete", "unknown_boundary"},
            "source_path_status": source_path["status"],
            "resolved_exact_typed_target": target_detail is not None,
            "typed_result_to_dispatcher_return": matching_return is not None,
            "callsite": call.get("call_site"),
            "return_instruction_index": (matching_return or {}).get("instruction_index"),
        })
    complete = (len(branches) == 3
                and len({item["target"] for item in branches}) == 3
                and all(all(value for key, value in item.items()
                            if key in {"source_formal_to_typed_actual",
                                       "resolved_exact_typed_target",
                                       "typed_result_to_dispatcher_return"})
                        for item in branches))
    return {
        "status": "complete" if complete else "incomplete",
        "method": fact.get("method"),
        "branches": branches,
        "missing": [] if complete else [
            "three exact source-derived typed Jib.from normal-return branches"],
        "modality": "normal-return input-may-influence-result",
        "limitations": [
            "intermediate named/at/parse return values use conservative opaque normal-return may edges",
            "value identity, nullness, exceptions, and branch feasibility are not established",
        ],
    }


def _resolved_call_to_fact(call: dict | None, fact: dict | None, argument_index: int = 0) -> bool:
    if call is None or fact is None or len(call.get("uses", [])) <= argument_index:
        return False
    detail = next((item for item in call.get("possible_target_details", [])
                   if item.get("method") == fact.get("method")
                   and item.get("context") == fact.get("context")
                   and item.get("has_ir")), None)
    parameters = (detail or {}).get("parameter_value_numbers", [])
    callee_returns = [row for row in _fact_rows(fact)
                      if (row.get("instruction") or "").startswith("return ")
                      and row.get("uses")]
    return bool(detail and len(parameters) > argument_index
                and call.get("definitions") and callee_returns)


def base_image_source_witness(raw: dict) -> dict:
    facts = raw.get("method_ssa_facts", [])
    mut = next((fact for fact in facts
                if "JarFiles.toJibContainerBuilder" in fact.get("method", "")), None)
    if mut is None:
        return {"status": "incomplete", "checks": {}, "alternatives": [],
                "missing": ["MUT method facts"]}
    rows = _fact_rows(mut)
    returned = next((row for row in rows
                     if (row.get("instruction") or "").startswith("return ")), None)
    return_value = returned.get("uses", [None])[0] if returned else None
    create = next((row for row in rows if "ContainerBuilders.create(" in
                   (row.get("declared_target") or "")), None)
    get_from_calls = [row for row in rows if "CommonContainerConfigCliOptions.getFrom(" in
                      (row.get("declared_target") or "")]
    optional_get = next((row for row in rows if "java.util.Optional.get(" in
                         (row.get("declared_target") or "")), None)
    present_source = next((row for row in get_from_calls if optional_get and row.get("definitions")
                           and row["definitions"][0] in optional_get.get("uses", [])), None)
    payload_to_create = bool(create and optional_get and optional_get.get("definitions")
                             and create.get("uses")
                             and _value_path(rows, optional_get["definitions"][0],
                                             create["uses"][0])["status"] == "complete")
    is_present = next((row for row in rows if "Optional.isPresent(" in
                       (row.get("declared_target") or "")), None)
    get_java = next((row for row in rows if "ArtifactProcessor.getJavaVersion(" in
                     (row.get("declared_target") or "")), None)
    int_value = next((row for row in rows if "Integer.intValue(" in
                      (row.get("declared_target") or "")), None)
    branch = next((row for row in rows if "conditional branch" in
                   (row.get("instruction") or "") and int_value and int_value.get("definitions")
                   and int_value["definitions"][0] in row.get("uses", [])), None)
    jib_from = [row for row in rows if "com.google.cloud.tools.jib.api.Jib.from(" in
                (row.get("declared_target") or "")]
    present_return = (_value_path(rows, create["definitions"][0], return_value)
                      if create and create.get("definitions") and return_value is not None
                      else {"status": "missing", "path": []})
    absent_returns = [(_value_path(rows, call["definitions"][0], return_value)
                       if call.get("definitions") and return_value is not None
                       else {"status": "missing", "path": []}) for call in jib_from]
    present_checks = {
        "presence_source_call": bool(get_from_calls and is_present),
        "optional_receiver_to_payload_may": present_source is not None,
        "payload_to_real_container_factory_actual": payload_to_create,
        "factory_result_to_mut_return": present_return["status"] == "complete",
    }
    absent_checks = {
        "java_version_return_to_unbox": bool(get_java and int_value and get_java.get("definitions")
                                              and get_java["definitions"][0] in int_value.get("uses", [])),
        "unboxed_value_to_branch": branch is not None,
        "two_jib_from_alternatives": len(jib_from) == 2,
        "both_builder_results_to_mut_return": len(absent_returns) == 2
        and all(path["status"] == "complete" for path in absent_returns),
    }
    mut_paths_complete = all(present_checks.values()) and all(absent_checks.values())

    def unique_fact(prefix: str) -> dict | None:
        matches = [fact for fact in facts if fact.get("method", "").startswith(prefix)]
        return matches[0] if len(matches) == 1 else None

    typed_from_facts = [fact for fact in facts
                        if fact.get("method", "").startswith(
                            "com.google.cloud.tools.jib.api.Jib.from(Lcom/google/cloud/tools/jib/api/")]
    container_dispatch = _jib_dispatcher_witness(unique_fact(
        "com.google.cloud.tools.jib.cli.ContainerBuilders.create("))
    string_dispatch = _jib_dispatcher_witness(unique_fact(
        "com.google.cloud.tools.jib.api.Jib.from(Ljava/lang/String;)"))
    container_dispatch_fact = unique_fact(
        "com.google.cloud.tools.jib.cli.ContainerBuilders.create(")
    string_dispatch_fact = unique_fact(
        "com.google.cloud.tools.jib.api.Jib.from(Ljava/lang/String;)")
    present_checks["resolved_factory_call_actual_formal_and_normal_return"] = (
        _resolved_call_to_fact(create, container_dispatch_fact))
    absent_checks["both_string_factory_calls_resolve_actual_formal_and_normal_return"] = (
        len(jib_from) == 2 and all(_resolved_call_to_fact(call, string_dispatch_fact)
                                  for call in jib_from))
    mut_paths_complete = all(present_checks.values()) and all(absent_checks.values())
    typed_constructor_checks: list[dict[str, object]] = []
    common_ctor = unique_fact(
        "com.google.cloud.tools.jib.api.JibContainerBuilder.<init>(Lcom/google/cloud/tools/jib/configuration/ImageConfiguration;")
    common_rows = _fact_rows(common_ctor or {})
    common_parameters = (common_ctor or {}).get("parameter_value_numbers", [])
    common_config_formal = common_parameters[1] if len(common_parameters) > 1 else None
    set_base = next((row for row in common_rows if
                     "ContainerBuildPlan$Builder.setBaseImage(" in
                     (row.get("declared_target") or "")), None)
    common_builder_load = next((row for row in common_rows
                                if "getfield" in (row.get("instruction") or "")
                                and "JibContainerBuilder, containerBuildPlanBuilder," in
                                (row.get("instruction") or "")), None)
    common_config_to_setter = (
        _value_path(common_rows, common_config_formal, set_base["uses"][1])
        if common_config_formal is not None and set_base and len(set_base.get("uses", [])) > 1
        else {"status": "missing", "path": []})
    set_base_detail = next((detail for detail in (set_base or {}).get(
        "possible_target_details", []) if detail.get("has_ir") and
        "ContainerBuildPlan$Builder.setBaseImage(" in detail.get("method", "")), None)
    set_base_fact = next((fact for fact in facts if set_base_detail
                          and fact.get("method") == set_base_detail.get("method")
                          and fact.get("context") == set_base_detail.get("context")), None)
    set_base_rows = _fact_rows(set_base_fact or {})
    set_base_parameters = (set_base_fact or {}).get("parameter_value_numbers", [])
    exact_base_write = next((row for row in set_base_rows
                             if "putfield" in (row.get("instruction") or "")
                             and "ContainerBuildPlan$Builder, baseImage," in
                             (row.get("instruction") or "")), None)
    setter_formal_to_write = bool(
        len(set_base_parameters) > 1 and exact_base_write
        and len(exact_base_write.get("uses", [])) > 1
        and exact_base_write["uses"][1] == set_base_parameters[1])
    to_plan_fact = unique_fact(
        "com.google.cloud.tools.jib.api.JibContainerBuilder.toContainerBuildPlan(")
    to_plan_rows = _fact_rows(to_plan_fact or {})
    to_plan_builder_load = next((row for row in to_plan_rows
                                 if "getfield" in (row.get("instruction") or "")
                                 and "JibContainerBuilder, containerBuildPlanBuilder," in
                                 (row.get("instruction") or "")), None)
    build_fact = unique_fact(
        "com.google.cloud.tools.jib.api.buildplan.ContainerBuildPlan$Builder.build(")
    build_base_read = next((row for row in _fact_rows(build_fact or {})
                            if "getfield" in (row.get("instruction") or "")
                            and "ContainerBuildPlan$Builder, baseImage," in
                            (row.get("instruction") or "")), None)
    builder_owner_region_intersection = sorted(
        _regions(common_builder_load or {}, "ref_regions")
        & _regions(to_plan_builder_load or {}, "ref_regions"))
    base_field_region_intersection = sorted(
        _regions(exact_base_write or {}, "mod_regions")
        & _regions(build_base_read or {}, "ref_regions"))

    for from_fact in typed_from_facts:
        from_rows = _fact_rows(from_fact)
        from_parameters = from_fact.get("parameter_value_numbers", [])
        ctor_call = next((row for row in from_rows if
                          "JibContainerBuilder.<init>(" in
                          (row.get("declared_target") or "")), None)
        returned_builder = next((row for row in from_rows
                                 if (row.get("instruction") or "").startswith("return ")), None)
        new_builder = next((row for row in from_rows
                            if "new <Extension,Lcom/google/cloud/tools/jib/api/JibContainerBuilder>" in
                            (row.get("instruction") or "")), None)
        ctor_detail = next((detail for detail in (ctor_call or {}).get(
            "possible_target_details", []) if detail.get("has_ir") and
            "JibContainerBuilder.<init>(" in detail.get("method", "")), None)
        ctor_fact = next((fact for fact in facts if ctor_detail
                          and fact.get("method") == ctor_detail.get("method")
                          and fact.get("context") == ctor_detail.get("context")), None)
        ctor_rows = _fact_rows(ctor_fact or {})
        ctor_parameters = (ctor_fact or {}).get("parameter_value_numbers", [])
        common_call = next((row for row in ctor_rows if
                            "JibContainerBuilder.<init>(Lcom/google/cloud/tools/jib/configuration/ImageConfiguration;" in
                            (row.get("declared_target") or "")), None)
        source_to_common_config = (
            _value_path(ctor_rows, ctor_parameters[1], common_call["uses"][1])
            if len(ctor_parameters) > 1 and common_call and len(common_call.get("uses", [])) > 1
            else {"status": "missing", "path": []})
        typed_constructor_checks.append({
            "method": from_fact.get("method"),
            **_typed_constructor_link_checks(
                from_parameters, ctor_call, ctor_parameters, new_builder,
                returned_builder, common_call, source_to_common_config),
        })
    downstream_checks = {
        "container_builders_dispatches_source_to_typed_normal_returns":
            container_dispatch["status"] == "complete",
        "string_jib_from_dispatches_source_to_typed_normal_returns":
            string_dispatch["status"] == "complete",
        "three_typed_jib_from_constructor_paths": len(typed_constructor_checks) == 3
        and all(all(value for key, value in item.items() if key != "method")
                for item in typed_constructor_checks),
        "common_configuration_to_set_base_actual":
            common_config_to_setter["status"] == "complete",
        "resolved_set_base_target": set_base_detail is not None,
        "set_base_formal_to_exact_field_write": setter_formal_to_write,
        "common_constructor_load_to_set_base_receiver": bool(
            common_builder_load and common_builder_load.get("definitions") and set_base
            and set_base.get("uses", [None])[0] == common_builder_load["definitions"][0]),
        "same_plan_builder_region_from_constructor_to_plan_build":
            bool(builder_owner_region_intersection),
        "set_base_write_to_build_read_region": bool(base_field_region_intersection),
    }
    downstream_complete = all(downstream_checks.values())
    return {
        "status": ("complete" if mut_paths_complete and downstream_complete
                   else "mut_paths_complete_downstream_incomplete" if mut_paths_complete
                   else "incomplete"),
        "alternatives": [
            {"name": "getFrom present", "checks": present_checks,
             "modality": "analysis_may",
             "limitations": ["concrete payload, selected factory branch, and path feasibility are unknown"]},
            {"name": "getFrom absent; Java version branch", "checks": absent_checks,
             "modality": "analysis_may",
             "limitations": ["Java version, branch feasibility, and selected concrete base image are unknown"]},
        ],
        "downstream_checks": downstream_checks,
        "typed_constructor_paths": typed_constructor_checks,
        "container_builders_dispatch": container_dispatch,
        "string_jib_from_dispatch": string_dispatch,
        "builder_owner_region_intersection": builder_owner_region_intersection,
        "base_field_region_intersection": base_field_region_intersection,
        "mechanism": ("exact MUT SSA actual/return paths, local CFG branch predicate, "
                      "resolved Jib.from constructor calls, and exact setBaseImage field write"),
        "missing": ["factory/Jib.from return to exact baseImage field write"]
        if mut_paths_complete and not downstream_complete
        else ([] if mut_paths_complete else ["one or more MUT source alternative SSA/CFG paths"]),
    }


def _method_value_path(rows: list[dict], method: str, start: int, goal: int) -> str:
    graph: dict[int, list[tuple[int, bool]]] = {}
    for row in rows:
        if method not in row.get("method", ""):
            continue
        resolved = (not row.get("declared_target")
                    or any(item.get("has_ir") for item in row.get("possible_target_details", [])))
        for definition in row.get("definitions", []):
            for use in row.get("uses", []):
                graph.setdefault(use, []).append((definition, resolved))
    queue = [(start, True)]
    seen = {start}
    while queue:
        value, fully_resolved = queue.pop(0)
        if value == goal:
            return "complete" if fully_resolved else "unknown_boundary"
        for target, resolved in graph.get(value, []):
            if target not in seen:
                seen.add(target)
                queue.append((target, fully_resolved and resolved))
    return "missing"


def observed_expression_witness(observation: dict, expected: dict, criterion: str) -> dict:
    rows = observation["statements"]
    getter_names = {
        "baseImage": "getBaseImage", "platforms": "getPlatforms",
        "creationTime": "getCreationTime", "format": "getFormat",
        "environment": "getEnvironment", "labels": "getLabels",
        "volumes": "getVolumes", "exposedPorts": "getExposedPorts",
        "user": "getUser", "workingDirectory": "getWorkingDirectory",
        "entrypoint": "getEntrypoint", "layers": "getLayers",
    }
    entry_rows = [row for row in rows if "JibObservationEntry.observe" in row.get("method", "")]
    sink = _invoke(entry_rows, "JibObservationEntry.observe", criterion)
    getter_candidates = [row for row in entry_rows
                         if getter_names[expected["field"]] in (row.get("declared_target") or "")]
    if not getter_candidates or sink is None or not sink.get("uses"):
        return {"status": "missing", "path": [], "unknown_calls": []}
    goal = sink["uses"][0]
    opaque_edges = {
        (edge.get("caller"), edge.get("context"), edge.get("instruction_index"),
         edge.get("call_site"), edge.get("declared_target"), edge.get("result_value")): (index, edge)
        for index, edge in enumerate(observation.get("opaque_return_edges", []))
        if edge.get("traversed")
    }
    graph: dict[int, list[tuple[int, dict, str, int | None]]] = {}
    for row in entry_rows:
        for definition in row.get("definitions", []):
            for use in row.get("uses", []):
                resolved = (not row.get("declared_target")
                            or any(item.get("has_ir") for item in row.get("possible_target_details", [])))
                opaque = opaque_edges.get((row.get("method"), row.get("context"),
                                           row.get("instruction_index"), row.get("call_site"),
                                           row.get("declared_target"), definition))
                result_facts = row.get("result_points_to")
                empty_result_region = (result_facts is not None
                                       and _reference_return(row.get("declared_target"))
                                       and result_facts.get("points_to_count") == 0)
                mechanism = ("opaque_may" if opaque else "unknown"
                             if empty_result_region else "resolved" if resolved else "unknown")
                graph.setdefault(use, []).append(
                    (definition, row, mechanism, opaque[0] if opaque else None))
    entry_method = entry_rows[0].get("method") if entry_rows else None
    entry_context = entry_rows[0].get("context") if entry_rows else None
    for step in observation.get("entry_ssa_traversal_steps", []):
        row = {**step, "method": entry_method, "context": entry_context}
        for definition in step.get("definitions", []):
            opaque = opaque_edges.get((entry_method, entry_context,
                                       step.get("instruction_index"), step.get("call_site"),
                                       step.get("declared_target"), definition))
            frontier = next((seed for seed in observation.get("expanded_seeds", [])
                             if seed.get("method") == entry_method
                             and seed.get("context") == entry_context
                             and seed.get("instruction_index") == step.get("instruction_index")
                             and seed.get("declared_target") == step.get("declared_target")
                             and (seed.get("call_site") is None
                                  or seed.get("call_site") == step.get("call_site"))
                             and seed.get("result_value") in step.get("definitions", [])
                             and seed.get("input_values") == step.get("uses")), None)
            mechanism = "opaque_may" if opaque else "resolved" if frontier or not step.get("declared_target") else "unknown"
            for use in step.get("uses", []):
                graph.setdefault(use, []).append(
                    (definition, row, mechanism, opaque[0] if opaque else None))
    mechanism_rank = {"unknown": 0, "resolved": 1, "opaque_may": 2}
    for use, edges in list(graph.items()):
        preferred: dict[tuple[object, ...], tuple[int, dict, str, int | None]] = {}
        for edge in edges:
            target, row, mechanism, opaque_index = edge
            key = (target, row.get("instruction_index"), row.get("call_site"),
                   row.get("declared_target"))
            current = preferred.get(key)
            if current is None or mechanism_rank[mechanism] > mechanism_rank[current[2]]:
                preferred[key] = (target, row, mechanism, opaque_index)
        graph[use] = list(preferred.values())
    candidates = []
    for getter in getter_candidates:
        if not getter.get("definitions"):
            continue
        start = getter["definitions"][0]
        getter_opaque = opaque_edges.get((getter.get("method"), getter.get("context"),
                                          getter.get("instruction_index"), getter.get("call_site"),
                                          getter.get("declared_target"), start))
        getter_facts = getter.get("result_points_to")
        getter_requires_opaque = (getter_facts is not None
                                  and getter_facts.get("points_to_count") == 0)
        initial_unknown = ([getter.get("declared_target") or getter.get("instruction")]
                           if getter_requires_opaque and getter_opaque is None else [])
        initial_opaque = [getter_opaque[0]] if getter_opaque else []
        initial_path = ([{"instruction_index": getter.get("instruction_index"),
                          "call_site": getter.get("call_site"),
                          "declared_target": getter.get("declared_target"),
                          "instruction": getter.get("instruction"),
                          "from_value": getter.get("uses", [None])[0],
                          "to_value": start}]
                        if getter_opaque else [])
        queue: list[tuple[int, list[dict], list[str], list[int]]] = [
            (start, initial_path, initial_unknown, initial_opaque)]
        seen = {start}
        while queue:
            value, path, unknown, opaque_path = queue.pop(0)
            if value == goal:
                if not _resolved_with_ir(getter, getter_names[expected["field"]]):
                    unknown = [getter.get("declared_target") or getter_names[expected["field"]], *unknown]
                frontier_seed = None
                for seed in observation.get("expanded_seeds", []):
                    step = next((item for item in path
                                 if item.get("instruction_index") == seed.get("instruction_index")
                                 and item.get("declared_target") == seed.get("declared_target")
                                 and (seed.get("call_site") is None
                                      or item.get("call_site") == seed.get("call_site"))
                                 and seed.get("result_value") == item.get("to_value")), None)
                    if step and set(opaque_path).issubset(seed.get("origin_edge_indexes", [])):
                        frontier_seed = {**seed, "call_site": step.get("call_site")}
                        break
                opaque_supported = not opaque_path or frontier_seed is not None
                status = ("unknown_boundary" if unknown or not opaque_supported
                          else "complete_with_opaque_may" if opaque_path else "complete")
                candidates.append({"status": status,
                                   "path": path, "unknown_calls": unknown,
                                   "opaque_edge_indexes": opaque_path,
                                   "expanded_frontier_seed": frontier_seed,
                                   "opaque_expansion_truncated": bool(
                                       observation.get("opaque_expansion_truncated")),
                                   "getter_instruction_index": getter.get("instruction_index"),
                                   "mechanism": "entry-method SSA def-use from exact plan getter to exact observation sink"})
                break
            for target, row, mechanism, opaque_index in graph.get(value, []):
                if target in seen:
                    continue
                seen.add(target)
                step = {"instruction_index": row.get("instruction_index"),
                        "call_site": row.get("call_site"),
                        "declared_target": row.get("declared_target"),
                        "instruction": row.get("instruction"), "from_value": value,
                        "to_value": target}
                missing = ([] if mechanism != "unknown" else
                           [str(row.get("declared_target") or row.get("instruction"))])
                edge_path = ([*opaque_path, opaque_index]
                             if opaque_index is not None else opaque_path)
                queue.append((target, [*path, step], [*unknown, *missing], edge_path))
    if candidates:
        if observation.get("opaque_expansion_truncated"):
            for candidate in candidates:
                candidate["status"] = "unknown_boundary"
        order = {"complete": 0, "complete_with_opaque_may": 1, "unknown_boundary": 2}
        return min(candidates, key=lambda item: (order.get(item["status"], 3), len(item["path"])))
    return {"status": "missing", "path": [], "unknown_calls": []}


def base_image_ssa_witness(rows: list[dict], criterion: str) -> dict:
    checks: dict[str, bool] = {}
    mut = _invoke(rows, "JibObservationEntry.observe", "JarFiles.toJibContainerBuilder")
    to_plan = _invoke(rows, "JibObservationEntry.observe", "JibContainerBuilder.toContainerBuildPlan")
    getter_call = _invoke(rows, "JibObservationEntry.observe", "ContainerBuildPlan.getBaseImage")
    sink = _invoke(rows, "JibObservationEntry.observe", criterion)
    checks["entry_mut_to_plan"] = bool(_resolved_with_ir(mut, "JarFiles.toJibContainerBuilder")
                                        and _resolved_with_ir(to_plan, "JibContainerBuilder.toContainerBuildPlan")
                                        and mut and to_plan and mut.get("definitions")
                                        and to_plan.get("uses", [None])[0] == mut["definitions"][0])
    checks["entry_plan_to_getter"] = bool(_resolved_with_ir(getter_call, "ContainerBuildPlan.getBaseImage")
                                          and to_plan and getter_call and to_plan.get("definitions")
                                          and getter_call.get("uses", [None])[0] == to_plan["definitions"][0])
    checks["entry_getter_to_sink"] = bool(getter_call and sink and getter_call.get("definitions")
                                          and sink.get("uses", [None])[0] == getter_call["definitions"][0])

    getter_read = next(iter(_matching(rows, "ContainerBuildPlan.getBaseImage", "getfield",
                                      "ContainerBuildPlan", "baseImage")), None)
    getter_return = _return(rows, "ContainerBuildPlan.getBaseImage")
    checks["getter_read_to_return"] = bool(getter_read and getter_return
                                            and getter_read.get("definitions")
                                            and getter_return.get("uses", [None])[0] == getter_read["definitions"][0])

    builder_load = next(iter(_matching(rows, "JibContainerBuilder.toContainerBuildPlan", "getfield",
                                       "JibContainerBuilder", "containerBuildPlanBuilder")), None)
    build_call = _invoke(rows, "JibContainerBuilder.toContainerBuildPlan", "ContainerBuildPlan$Builder.build")
    plan_return = _return(rows, "JibContainerBuilder.toContainerBuildPlan")
    checks["builder_receiver_to_build"] = bool(_resolved_with_ir(build_call, "ContainerBuildPlan$Builder.build")
                                                and builder_load and build_call and builder_load.get("definitions")
                                                and build_call.get("uses", [None])[0] == builder_load["definitions"][0])
    checks["build_result_to_plan_return"] = bool(build_call and plan_return and build_call.get("definitions")
                                                  and plan_return.get("uses", [None])[0] == build_call["definitions"][0])

    build_read = next(iter(_matching(rows, "ContainerBuildPlan$Builder.build", "getfield",
                                     "ContainerBuildPlan$Builder", "baseImage")), None)
    ctor_call = _invoke(rows, "ContainerBuildPlan$Builder.build", "ContainerBuildPlan.<init>")
    plan_write = next(iter(_matching(rows, "ContainerBuildPlan.<init>", "putfield",
                                     "ContainerBuildPlan", "baseImage")), None)
    ctor_params = _target_parameters(ctor_call, "ContainerBuildPlan.<init>")
    actual_index = (ctor_call.get("uses", []).index(build_read["definitions"][0])
                    if ctor_call and build_read and build_read.get("definitions")
                    and build_read["definitions"][0] in ctor_call.get("uses", []) else -1)
    formal = ctor_params[actual_index] if 0 <= actual_index < len(ctor_params) else None
    checks["build_read_actual_to_ctor_formal"] = bool(
        plan_write and formal is not None and len(plan_write.get("uses", [])) > 1
        and plan_write["uses"][1] == formal)

    jib_ctor_set = _invoke(rows, "JibContainerBuilder.<init>", "ContainerBuildPlan$Builder.setBaseImage")
    setter_write = next(iter(_matching(rows, "ContainerBuildPlan$Builder.setBaseImage", "putfield",
                                       "ContainerBuildPlan$Builder", "baseImage")), None)
    setter_params = _target_parameters(jib_ctor_set, "ContainerBuildPlan$Builder.setBaseImage")
    checks["setbase_actual_to_formal_write"] = bool(
        jib_ctor_set and setter_write and len(setter_params) > 1
        and len(setter_write.get("uses", [])) > 1 and setter_write["uses"][1] == setter_params[1])
    return {"status": "complete" if checks and all(checks.values()) else "incomplete",
            "checks": checks, "mechanism": "explicit IR def-use plus resolved actual/formal/return witnesses"}


def generic_state_materialization_witness(rows: list[dict], expected: dict,
                                          raw: dict | None = None) -> dict:
    getter_name = {
        "platforms": "getPlatforms", "creationTime": "getCreationTime", "format": "getFormat",
        "environment": "getEnvironment", "labels": "getLabels", "volumes": "getVolumes",
        "exposedPorts": "getExposedPorts", "user": "getUser",
        "workingDirectory": "getWorkingDirectory", "entrypoint": "getEntrypoint",
        "layers": "getLayers",
    }[expected["field"]]
    checks: dict[str, bool] = {}
    mut = _invoke(rows, "JibObservationEntry.observe", "JarFiles.toJibContainerBuilder")
    to_plan = _invoke(rows, "JibObservationEntry.observe", "JibContainerBuilder.toContainerBuildPlan")
    getter_call = _invoke(rows, "JibObservationEntry.observe", f"ContainerBuildPlan.{getter_name}")
    checks["entry_mut_to_plan"] = bool(
        _resolved_with_ir(mut, "JarFiles.toJibContainerBuilder")
        and _resolved_with_ir(to_plan, "JibContainerBuilder.toContainerBuildPlan")
        and mut and to_plan and mut.get("definitions")
        and to_plan.get("uses", [None])[0] == mut["definitions"][0])
    checks["entry_plan_to_getter"] = bool(
        _resolved_with_ir(to_plan, "JibContainerBuilder.toContainerBuildPlan")
        and _resolved_with_ir(getter_call, f"ContainerBuildPlan.{getter_name}")
        and to_plan and getter_call and to_plan.get("definitions")
        and getter_call.get("uses", [None])[0] == to_plan["definitions"][0])
    getter_read = next(iter(_matching(rows, f"ContainerBuildPlan.{getter_name}", "getfield",
                                      "ContainerBuildPlan", expected["field"])), None)
    getter_return = _return(rows, f"ContainerBuildPlan.{getter_name}")
    getter_path = (_method_value_path(rows, f"ContainerBuildPlan.{getter_name}",
                                      getter_read["definitions"][0], getter_return["uses"][0])
                   if getter_read and getter_return and getter_read.get("definitions")
                   and getter_return.get("uses") else "missing")
    getter_fact = next((fact for fact in (raw or {}).get("method_ssa_facts", [])
                        if f"ContainerBuildPlan.{getter_name}(" in fact.get("method", "")), None)
    library_copy_witness = (extract_method_fact_copy_constructor_witness(
        getter_fact, expected["field"]) if getter_fact else
        {"status": "incomplete", "checks": {}, "path": [],
         "missing": ["exact getter method facts"]})
    copy_effect = library_copy_witness.get("copy_content_effect")
    reviewed_copy = (library_copy_witness.get("status") == "complete"
                     and isinstance(copy_effect, dict)
                     and copy_effect.get("status") == "reviewed_api_summary")
    checks["getter_read_to_return"] = getter_path == "complete" or reviewed_copy
    builder_load = next(iter(_matching(rows, "JibContainerBuilder.toContainerBuildPlan", "getfield",
                                       "JibContainerBuilder", "containerBuildPlanBuilder")), None)
    build_call = _invoke(rows, "JibContainerBuilder.toContainerBuildPlan", "ContainerBuildPlan$Builder.build")
    plan_return = _return(rows, "JibContainerBuilder.toContainerBuildPlan")
    checks["builder_receiver_to_build"] = bool(
        _resolved_with_ir(build_call, "ContainerBuildPlan$Builder.build")
        and builder_load and build_call and builder_load.get("definitions")
        and build_call.get("uses", [None])[0] == builder_load["definitions"][0])
    checks["build_result_to_plan_return"] = bool(
        build_call and plan_return and build_call.get("definitions")
        and plan_return.get("uses", [None])[0] == build_call["definitions"][0])
    build_read = next(iter(_matching(rows, "ContainerBuildPlan$Builder.build", "getfield",
                                     "ContainerBuildPlan$Builder", expected["field"])), None)
    ctor_call = _invoke(rows, "ContainerBuildPlan$Builder.build", "ContainerBuildPlan.<init>")
    plan_write = next(iter(_matching(rows, "ContainerBuildPlan.<init>", "putfield",
                                     "ContainerBuildPlan", expected["field"])), None)
    ctor_params = _target_parameters(ctor_call, "ContainerBuildPlan.<init>")
    actual_index = (ctor_call.get("uses", []).index(build_read["definitions"][0])
                    if ctor_call and build_read and build_read.get("definitions")
                    and build_read["definitions"][0] in ctor_call.get("uses", []) else -1)
    formal = ctor_params[actual_index] if 0 <= actual_index < len(ctor_params) else None
    checks["build_read_actual_to_ctor_formal"] = bool(
        plan_write and formal is not None and len(plan_write.get("uses", [])) > 1
        and plan_write["uses"][1] == formal)
    return {"status": "complete" if checks and all(checks.values()) else "incomplete",
            "checks": checks,
            "library_copy_witness": library_copy_witness,
            "mechanism": "exact getter return plus Builder.build actual/formal to plan-field write"}


def classify(observation: dict, expected: dict, control_dependence: str = "FULL",
             raw: dict | None = None) -> dict:
    rows = observation["statements"]
    method_rows = [row for fact in (raw or {}).get("method_ssa_facts", [])
                   for row in _fact_rows(fact)]
    evidence_rows = [*rows, *method_rows]
    field = expected["field"]
    getter = _matching(evidence_rows, "ContainerBuildPlan.get", "getfield",
                       "ContainerBuildPlan", field)
    plan_write = _matching(evidence_rows, "ContainerBuildPlan.<init>", "putfield",
                           "ContainerBuildPlan", field)
    build_read = _matching(evidence_rows, "ContainerBuildPlan$Builder.build", "getfield",
                           "ContainerBuildPlan$Builder", field)
    all_writes = _matching(evidence_rows, "ContainerBuildPlan$Builder.", "putfield",
                           "ContainerBuildPlan$Builder", field)
    candidate_writes = all_writes
    overwritten_writes: list[dict] = []
    overwrite_precision = "not established; defaults and conditional setters remain alternatives"
    builder_intersection = sorted(set().union(*(_regions(row, "mod_regions") for row in candidate_writes))
                                  & set().union(*(_regions(row, "ref_regions") for row in build_read))) \
        if candidate_writes and build_read else []
    plan_intersection = sorted(set().union(*(_regions(row, "mod_regions") for row in plan_write))
                               & set().union(*(_regions(row, "ref_regions") for row in getter))) \
        if plan_write and getter else []
    exact_state = bool(builder_intersection and plan_intersection)
    ssa_witness = (base_image_ssa_witness(evidence_rows, observation["criterion"])
                   if expected["criterion"] == "baseImage"
                   else generic_state_materialization_witness(evidence_rows, expected, raw))
    expression_witness = observed_expression_witness(
        observation, expected, observation["criterion"])
    unrelated = []
    for row in evidence_rows:
        instruction = row.get("instruction") or ""
        if "ContainerBuildPlan$Builder," in instruction and "putfield" in instruction \
                and f", {field}," not in instruction:
            unrelated.append({"method": row["method"], "instruction": instruction})
    source_boundaries = []
    for source in expected["source_calls"]:
        owner, method = source.rsplit(".", 1)
        owner = SOURCE_OWNER_NAMES.get(owner, owner)
        qualified_source = f"{owner}.{method}"
        exact_target = qualified_source + "("
        matches = [row for row in evidence_rows if exact_target in (row.get("declared_target") or "")]
        result_facts = next((row.get("result_points_to") for row in matches
                             if row.get("result_points_to") is not None), None)
        source_boundaries.append({
            "call": qualified_source,
            "in_ci_slice_only": bool(matches),
            "dependency_status": "unknown",
            "callsite": matches[0].get("call_site") if matches else None,
            "context": matches[0].get("context") if matches else None,
            "declared_target": matches[0].get("declared_target") if matches else None,
            "result_points_to": result_facts,
            "empty_return_region": result_facts is not None and result_facts["points_to_count"] == 0,
            "value": "unknown", "null": "possible", "exception": "possible",
        })
    source_setter = source_to_setter_witness(raw or {}, expected)
    base_source = (base_image_source_witness(raw or {})
                   if expected["criterion"] == "baseImage" else None)
    selected_field_write = source_setter.get("field_write")
    selected_write_regions: set[str] = set()
    if selected_field_write:
        selected_row = next((row for fact in (raw or {}).get("method_ssa_facts", [])
                             if fact.get("method") == selected_field_write.get("method")
                             and fact.get("context") == selected_field_write.get("context")
                             for row in fact.get("instructions", [])
                             if row.get("instruction_index") ==
                             selected_field_write.get("instruction_index")), None)
        selected_write_regions = _regions(selected_row or {}, "mod_regions")
    selected_intersection = sorted(
        selected_write_regions
        & set().union(*(_regions(row, "ref_regions") for row in build_read))) if build_read else []
    overwrite_verified = bool(
        source_setter.get("status") == "complete"
        and source_setter.get("overwrite", {}).get("status") ==
        "setter_must_execute_on_normal_return"
        and selected_intersection)
    if overwrite_verified:
        overwritten_writes = [row for row in candidate_writes
                              if "<init>" in row.get("method", "")]
        overwrite_precision = (
            "selected setter write reaches the exact build field region and dominates "
            "visible MUT normal returns")
    missing = []
    for label, value in (
        ("exact getter field read", getter),
        ("plan constructor field write", plan_write),
        ("Builder.build field read", build_read),
        ("candidate builder field write", candidate_writes),
        ("builder write/read region intersection", builder_intersection),
        ("plan write/getter region intersection", plan_intersection),
    ):
        if not value:
            missing.append(label)
    state_flow_ready = (control_dependence == "NONE" and exact_state
                        and ssa_witness["status"] == "complete"
                        and expression_witness["status"] in
                        {"complete", "complete_with_opaque_may"})
    status = "verified_incomplete" if state_flow_ready else ("state_candidates_only" if exact_state else "unknown")
    source_alternatives: list[dict] = []
    if expected["criterion"] == "baseImage":
        source_alternatives = (base_source or {}).get("alternatives", [])
    return {
        "observation_id": expected["id"],
        "line": expected["line"],
        "end_line": expected["end_line"],
        "observed_line": expected["observed_line"],
        "expression": expected["expression"],
        "criterion": observation["criterion"],
        "field": field,
        "status": status,
        "state_evidence": {
            "modality": "analysis_may" if exact_state else "unknown_effect",
            "builder_region_intersection": builder_intersection,
            "plan_region_intersection": plan_intersection,
            "candidate_write_methods": sorted({row["method"] for row in candidate_writes}),
            "overwritten_write_methods": sorted({row["method"] for row in overwritten_writes}),
            "overwrite_precision": overwrite_precision,
            "selected_setter_build_region_intersection": selected_intersection,
            "mechanism": "exact field Mod/Ref chain through Builder.build and plan constructor",
        },
        "ssa_value_witness": ssa_witness,
        "observed_expression_witness": expression_witness,
        "source_boundaries": source_boundaries,
        "source_to_setter_witness": source_setter,
        "base_image_source_witness": base_source,
        "source_alternatives": source_alternatives,
        "execution_effects": {"normal_completion": "analysis_may", "exception_before_observation": "unknown_effect"},
        "channel": {"control_dependence": control_dependence,
                    "role": "value_state" if control_dependence == "NONE" else "execution_control"},
        "excluded_from_state_support": {
            "reason": "different builder field despite CI slice membership",
            "count": len(unrelated),
            "examples": unrelated[:8],
        },
        "missing": missing,
        "slice_statement_count": observation["slice_statement_count"],
        "retained_statement_count": observation["retained_statement_count"],
    }


def _contract(summary: dict, registration: dict, binding: dict, raw: dict,
              expectations: dict, peak_rss_bytes: int | None) -> dict:
    observations = [{
        "observation_id": item["id"],
        "location": {"source_id": "test", "start_line": item["line"], "end_line": item["end_line"]},
        "kind": "assertion_observed_expression", "origin": "explicit",
        "reason": item["expression"],
    } for item in expectations["observations"]]
    all_observation_ids = [item["id"] for item in expectations["observations"]]
    relations = [
        {"kind": "call_binding", "modality": "certain",
         "from": binding["target_call_site"], "to": binding["target_callee"],
         "provenance": f"issue21 automatic binding report {binding['report_sha256']}",
         "precision": "unique receiver, declaration, signature, argument indexes, and return binding",
         "assumptions": [], "observation_ids": all_observation_ids},
        {"kind": "function_origin", "modality": "certain",
         "from": "mut:src/main/java/com/google/cloud/tools/jib/cli/jar/JarFiles.java",
         "to": binding["target_callee"],
         "provenance": f"reviewed frozen visible MUT source {expectations['visible_mut_sha256']}",
         "precision": "exact frozen source identity and bound declaration",
         "assumptions": [], "observation_ids": all_observation_ids},
    ]
    for item in summary["observations"]:
        if item["status"] == "verified_incomplete":
            opaque = item["observed_expression_witness"]["status"] == "complete_with_opaque_may"
            implicit = item.get("implicit_initialization_evidence")
            conditional = item["source_to_setter_witness"].get("status") == "conditional_complete"
            relations.append({
                "kind": "state_flow", "modality": "analysis_may",
                "from": " | ".join(item["state_evidence"]["candidate_write_methods"]),
                "to": item["expression"],
                "provenance": (
                    "JLS_11_reference_default_initialization_plus_exact_build_plan_SSA"
                    if implicit else
                    "wala_bootstrap_handle_plus_saved_frozen_setter_bytecode_plus_build_plan_SSA"
                    if conditional else
                    "wala_modref_plus_frozen_library_bytecode_plus_entry_ssa_opaque_return_may"
                    if opaque else "wala_modref_plus_frozen_library_bytecode"),
                "precision": (
                    "implicit_initial_definition_only; later writes and final value unknown"
                    if implicit else
                    "exact conditional setter field identity; Optional payload/presence and final value unknown"
                    if conditional else
                    "exact_field_regions_plus_conservative_opaque_return_may"
                    if opaque else "exact_field_and_intersecting_receiver_region"),
                "assumptions": (["path feasibility and concrete value are not established"]
                                + (["constructor failure and later hidden writes remain possible"]
                                   if implicit else [])
                                + (["opaque edges model normal-return input-may-influence-result only and do not establish aliasing, freshness, non-nullness, index validity, concrete values or contents, exception behavior, or path feasibility"]
                                   if opaque else [])),
                "observation_ids": [item["observation_id"]],
            })
        source_setter = item["source_to_setter_witness"]
        if source_setter.get("status") in {"complete", "conditional_complete"}:
            for alternative in source_setter["alternatives"]:
                if not (alternative["checks"].get("source_to_wrapper_actual")
                        or alternative["checks"].get("source_to_conditional_callback")):
                    continue
                conditional = alternative["checks"].get("source_to_conditional_callback")
                relations.append({
                    "kind": "value_flow", "modality": "analysis_may",
                    "from": alternative["source_call"], "to": item["field"],
                    "provenance": ("wala_optional_receiver_plus_bootstrap_method_reference_plus_setter_internal_field"
                                   if conditional else
                                   "wala_method_ssa_actual_formal_plus_bounded_collection_copy_effect"),
                    "precision": ("exact Optional receiver, exact method-reference target, and exact setter-internal field write; payload extraction and callback formal value identity unknown"
                                  if conditional else
                                  "exact source return, setter actual/formal, and builder field identity"),
                    "assumptions": [
                        "normal-return value identity is unknown",
                        "copy contents, exceptional completion, and hidden alias effects are not established",
                    ] + (["Optional presence and callback execution remain unknown"]
                         if conditional else []),
                    "observation_ids": [item["observation_id"]],
                })
        base_source = item.get("base_image_source_witness")
        if base_source:
            downstream_complete = base_source.get("status") == "complete"
            destination = item["expression"] if downstream_complete else "MUT returned JibContainerBuilder identity"
            common_assumptions = ([] if downstream_complete else
                                  ["factory/Jib.from to exact baseImage field transformation is not established"])
            relations.extend([
                {"kind": "value_flow", "modality": "analysis_may",
                 "from": "CommonContainerConfigCliOptions.getFrom payload",
                 "to": destination,
                 "provenance": "WALA MUT SSA Optional payload to ContainerBuilders.create actual and returned builder",
                 "precision": "exact callsites and SSA actual/return; concrete payload unknown",
                 "assumptions": [*common_assumptions, "Optional presence is unknown"],
                 "observation_ids": [item["observation_id"]]},
                {"kind": "control_dependence", "modality": "analysis_may",
                 "from": "ArtifactProcessor.getJavaVersion branch",
                 "to": destination,
                 "provenance": "WALA MUT SSA unbox plus local CFG branch to two Jib.from alternatives",
                 "precision": "exact branch producer and returned builder alternatives; concrete version unknown",
                 "assumptions": [*common_assumptions, "branch feasibility is unknown"],
                 "observation_ids": [item["observation_id"]]},
            ])
    relations.extend(summary.get("control_evidence", {}).get("relations", []))
    relations.extend(summary.get("optional_evidence", {}).get("relations", []))
    covered = {item["observation_id"] for item in summary["observations"]}
    return {
        "schema_version": "mock-spec.backward-analysis-case/v1",
        "identity": {"case_id": expectations["dataset_case_id"], "dataset": "Stub-Tester Java frozen snapshot",
                     "language": "java", "snapshot_id": expectations["snapshot_id"],
                     "masking_contract": "masked MOCK-STUB region retained", "masked_test_sha256": expectations["masked_test_sha256"],
                     "target_selector": expectations["dataset_case_id"]},
        "source_scope": [
            {"source_id": "test", "logical_path": "src/test/java/com/google/cloud/tools/jib/cli/jar/JarFilesTest.java",
             "sha256": expectations["test_sha256"], "bytes": registration["test_bytes"], "selection_role": "masked_test"},
            {"source_id": "mut", "logical_path": "src/main/java/com/google/cloud/tools/jib/cli/jar/JarFiles.java",
             "sha256": expectations["visible_mut_sha256"], "bytes": registration["visible_mut_bytes"], "selection_role": "candidate_product"},
        ],
        "observations": observations,
        "parameterization": {"status": "not_expanded", "coverage": "selected frozen test method",
                             "reason": "the test is not parameterized"},
        "entry_point": {
            "test_selection": {"origin": "task_provided", "status": "unique",
                               "selector": expectations["dataset_case_id"], "evidence": [expectations["test_sha256"]]},
            "mut_binding": {"origin": "automatic", "status": "unique",
                            "candidates": [binding["target_callee"]],
                            "evidence": [binding], "assumptions": []},
        },
        "relations": relations,
        "relation_coverage": [
            {"kind": kind, "status": ("verified_incomplete" if
                                       any(relation["kind"] == kind for relation in relations)
                                       else "not_evaluated")}
            for kind in ("call_binding", "control_dependence", "exception_dependence", "function_origin",
                         "normal_return_dependence", "state_flow", "value_flow")
        ],
        "analysis_boundaries": {
            "assumptions": ["diagnostic observation mapping is manual; issue21 supplies automatic MUT binding"],
            "unknown_effects": [{"boundary_id": item["call"], "effect": "normal_return_null_state_exception",
                                 "scope": "exact native call", "provenance": "structural declaration",
                                 "precision": "unknown", "assumptions": []}
                                for result in summary["observations"] for item in result["source_boundaries"]],
            "unanalysed_regions": [{"scope": item["id"], "reason": "criterion not run in this bounded batch"}
                                   for item in expectations["observations"] if item["id"] not in covered],
        },
        "completion": {"status": "partial", "stage": "jib_observation_state_mapping",
                       "failure_categories": summary["outcome"]["failure_categories"],
                       "error": None},
        "semantic_validation": {"status": "verified_incomplete", "reviewer": "mechanical_exact_field_gate",
                                "evidence": ["field Mod/Ref intersections and mutation negatives"],
                                "notes": "Concrete values and native effects are deliberately unknown."},
        "cost": {
            "timings_seconds": {key: value / 1000 for key, value in raw["timings_millis"].items()},
            "total_seconds": raw["timings_millis"]["total"] / 1000,
            "peak_memory": {"status": "measured_by_usr_bin_time", "bytes": peak_rss_bytes},
            "cache_state": {"status": "unknown", "reason": "OS and Maven caches were not isolated"},
            "input_size": {"files": 2, "bytes": registration["test_bytes"] + registration["visible_mut_bytes"]},
            "graph_size": {"nodes": raw["entry_reachable_call_graph_nodes"], "edges": None},
            "unavailable_phases": [],
        },
    }


def summarize(directory: Path) -> dict:
    raw_path = directory / "analysis.json"
    raw = json.loads(raw_path.read_text(encoding="utf-8"))
    javap_path = directory / "javap-JarFiles.txt"
    if javap_path.exists():
        raw["lambda_bootstrap_targets"] = _lambda_bootstrap_targets(
            javap_path.read_text(encoding="utf-8"))
    registration = json.loads((directory / "pre-registration.json").read_text(encoding="utf-8"))
    binding = json.loads((directory / "automatic-binding-evidence.json").read_text(encoding="utf-8"))
    expectations = json.loads(EXPECTATIONS.read_text(encoding="utf-8"))
    if registration.get("expectations_sha256") != hashlib.sha256(EXPECTATIONS.read_bytes()).hexdigest():
        raise ValueError("analysis registration predates or differs from current expectations")
    by_criterion = {item["criterion"]: item for item in expectations["observations"]}
    control = raw["configuration"]["control_dependence"]
    results = [classify(item, by_criterion[item["criterion"].rsplit(".", 1)[-1].split("(", 1)[0]],
                        control, raw)
               for item in raw["observations"]]
    lambda_targets = [
        nested["implementation_target"]
        for fact in raw.get("method_ssa_facts", [])
        for instruction in fact.get("instructions", [])
        if isinstance((nested := instruction.get("lambda_method_reference")), dict)
        and isinstance(nested.get("implementation_target"), str)
    ]
    jib_builder_javap = directory / "javap-JibContainerBuilder.txt"
    plan_builder_javap = directory / "javap-ContainerBuildPlan-Builder.txt"
    setter_bytecode = extract_setter_bytecode_evidence(
        jib_builder_javap.read_text(encoding="utf-8"),
        plan_builder_javap.read_text(encoding="utf-8"),
        lambda_targets,
    ) if jib_builder_javap.exists() and plan_builder_javap.exists() else []
    optional_evidence = map_optional_evidence(results, raw, setter_bytecode)
    optional_fields = {
        relation["setter_chain"]["field"]: relation
        for relation in optional_evidence["relations"]
    }
    for item in results:
        optional_relation = optional_fields.get(item["field"])
        if optional_relation is not None:
            item["source_to_setter_witness"] = {
                "status": "conditional_complete",
                "alternatives": [{
                    "source_call": optional_relation["from"],
                    "checks": {"source_to_wrapper_actual": False,
                               "source_to_conditional_callback": True},
                    "ssa_path": optional_relation,
                    "modality": "analysis_may_unknown_value",
                }],
                "conditional_callback_binding": optional_relation,
                "overwrite": {
                    "status": "conditional_or_not_established",
                    "scope": "Java 11 Optional.ifPresent normal-present execution only",
                    "limitations": ["presence, callback execution, payload, and final value remain unknown"],
                },
            }
            item["status"] = "verified_incomplete"
            item["missing"] = [missing for missing in item["missing"]
                               if missing not in {"candidate builder field write",
                                                  "builder write/read region intersection"}]
            item["state_evidence"]["modality"] = "analysis_may"
            item["state_evidence"]["candidate_write_methods"] = [
                optional_relation["setter_chain"]["builder_setter"]]
            item["state_evidence"]["mechanism"] = (
                "exact BootstrapMethods target plus saved frozen-library setter bytecode; "
                "existing Builder.build/plan/getter SSA materialization")
        if item["field"] == "workingDirectory":
            implicit = {
                "status": "analysis_may_initial_definition",
                "field": "ContainerBuildPlan$Builder.workingDirectory",
                "initial_value": "null",
                "source": [
                    "https://docs.oracle.com/javase/specs/jls/se11/html/jls-4.html#jls-4.12.5",
                    "https://docs.oracle.com/javase/specs/jls/se11/html/jls-12.html#jls-12.5",
                ],
                "precision": "reference field default initialization at object allocation",
                "limitations": [
                    "does not prove the observed final value is null",
                    "later writes, constructor failure, aliases, and hidden effects remain unknown",
                ],
            }
            item["implicit_initialization_evidence"] = implicit
            item["status"] = "verified_incomplete"
            item["missing"] = [missing for missing in item["missing"]
                               if missing not in {"candidate builder field write",
                                                  "builder write/read region intersection"}]
            item["state_evidence"]["modality"] = "analysis_may"
            item["state_evidence"]["candidate_write_methods"] = [
                "JLS reference-field implicit default initialization"]
            item["state_evidence"]["mechanism"] = (
                "JLS 11 implicit reference-field initialization plus exact "
                "Builder.build/plan/getter SSA materialization")
    transition_path = directory / "probe-source-transition.json"
    transition = json.loads(transition_path.read_text(encoding="utf-8")) if transition_path.exists() else None
    if transition is not None and transition["analysis_probe_sha256"] != registration["probe_sha256"]:
        raise ValueError("probe source transition does not match the registered analysis probe")
    nested_ids = {"criterion:16", "criterion:17", "criterion:18"}
    nested_blocked = any(item["observation_id"] in nested_ids
                         and item["observed_expression_witness"]["status"] not in
                         {"complete", "complete_with_opaque_may"} for item in results)
    nested_state_incomplete = any(item["observation_id"] in nested_ids
                                  and item["status"] != "verified_incomplete" for item in results)
    missing_candidate = any("candidate builder field write" in item["missing"] for item in results)
    collection_fields = {"environment", "labels", "volumes", "exposedPorts", "entrypoint", "layers", "platforms"}
    collection_incomplete = any(
        item["field"] in collection_fields
        and item["ssa_value_witness"].get("library_copy_witness", {}).get("status") != "complete"
        for item in results)
    source_incomplete = any(
        item["source_to_setter_witness"].get("status") == "incomplete" for item in results)
    base_source_incomplete = any(
        item.get("base_image_source_witness")
        and item["base_image_source_witness"].get("status") != "complete" for item in results)
    conditional_source = any(
        item["source_to_setter_witness"].get("status") == "conditional_complete"
        for item in results)
    overwrite_incomplete = any(
        item["field"] in FIELD_SETTERS and item["source_to_setter_witness"].get("status") == "complete"
        and item["source_to_setter_witness"].get("overwrite", {}).get("status") !=
        "setter_must_execute_on_normal_return" for item in results)
    failure_categories = ["original_observation_mapping_remains_diagnostic_override"]
    blockers = [
        "original observation mapping still uses a diagnostic override",
    ]
    expected_unknown_boundaries = [
        "masked dependency contents, aliasing, nullness, and concrete values deliberately remain unknown and are not treated as recovered facts",
    ]
    if collection_incomplete:
        failure_categories.append("visible_collection_getter_copy_return_not_recovered")
        blockers.append("one or more visible ContainerBuildPlan collection getters still lack field-to-copy-to-return witnesses")
    if source_incomplete:
        failure_categories.append("conditional_or_unknown_source_to_setter_effect_not_established")
        blockers.append("one or more source returns, including conditional Optional.ifPresent callbacks, are not connected to exact builder writes")
    if base_source_incomplete:
        failure_categories.append("base_image_factory_to_field_transformation_incomplete")
        blockers.append("baseImage source alternatives reach the returned builder, but factory/Jib.from transformation to the exact baseImage field is incomplete")
    if conditional_source:
        expected_unknown_boundaries.append(
            "Optional source values are connected to exact callback setters, but presence and callback execution remain conditional")
    if overwrite_incomplete:
        failure_categories.append("bounded_normal_return_overwrite_not_established")
        blockers.append("one or more visible setters are not proven to execute on every normal return of the MUT")
    if nested_blocked:
        failure_categories.append("nested_layer_path_missing_in_value_state_channel")
    if nested_state_incomplete:
        failure_categories.append("nested_layer_state_source_closure_incomplete")
    if missing_candidate:
        failure_categories.append("missing_candidate_builder_write")
    if nested_blocked:
        blockers.append("one or more nested layer observations still lack a complete plan/getter-to-sink may witness")
    if nested_state_incomplete and not nested_blocked:
        blockers.append("nested layer expression paths are recovered only as may paths; exact getter-return/state-source closure remains incomplete")
    if missing_candidate:
        blockers.append("one or more observations have no retained candidate builder write in the NONE channel")
    summary = {
        "schema": "mockspec.jib-observation-summary/v1", "target_executed": False,
        "entry_origin": raw["entry_origin"], "observations": results,
        "raw_ci_warning": "CI slice membership is over-approximate and is never state support by itself.",
        "raw_evidence": {"path": str(raw_path), "bytes": raw_path.stat().st_size,
                         "sha256": _sha256_file(raw_path),
                         "probe_sha256": registration["probe_sha256"],
                         "probe_source_transition": transition},
        "outcome": {
            "status": "partial",
            "original_observation_count": len(expectations["observations"]),
            "observed_expression_complete": sum(
                item["observed_expression_witness"]["status"] == "complete" for item in results),
            "observed_expression_complete_with_opaque_may": sum(
                item["observed_expression_witness"]["status"] == "complete_with_opaque_may"
                for item in results),
            "observed_expression_unknown_boundary": sum(
                item["observed_expression_witness"]["status"] == "unknown_boundary" for item in results),
            "observed_expression_missing": sum(
                item["observed_expression_witness"]["status"] == "missing" for item in results),
            "state_flow_verified_incomplete": sum(
                item["status"] == "verified_incomplete" for item in results),
            "state_region_candidates_only": sum(
                item["status"] == "state_candidates_only" for item in results),
            "state_unknown": sum(item["status"] == "unknown" for item in results),
            "closure_gate": "blocked",
            "ticket_acceptance": "passed_bounded_diagnostic",
            "failure_categories": failure_categories,
            "blockers": blockers,
            "expected_unknown_boundaries": expected_unknown_boundaries,
        },
    }
    summary["control_evidence"] = map_control_evidence(results, raw)
    summary["optional_evidence"] = optional_evidence
    summary["analysis_consumers"] = {
        name: {"path": path.name, "sha256": _sha256_file(path)}
        for name, path in {
            "library_copy": HERE / "jib_library_evidence.py",
            "control": HERE / "jib_control_evidence.py",
            "optional": HERE / "jib_optional_evidence.py",
        }.items()
    }
    summary["optional_setter_bytecode_evidence"] = {
        "entries": setter_bytecode,
        "inputs": [
            {"path": str(path), "sha256": _sha256_file(path)}
            for path in (jib_builder_javap, plan_builder_javap)
        ],
        "provenance": "saved javap -c -p output from the selected frozen Jib libraries",
    }
    peak_rss_bytes = None
    for line in (directory / "analysis.time").read_text(encoding="utf-8").splitlines():
        if "Maximum resident set size (kbytes):" in line:
            peak_rss_bytes = int(line.rsplit(":", 1)[1].strip()) * 1024
            break
    contract = _contract(summary, registration, binding, raw, expectations, peak_rss_bytes)
    spec = importlib.util.spec_from_file_location(
        "result_contract", HERE.parent / "result_contract.py")
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load result contract")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.validate_result_contract(contract)
    summary["result_contract"] = contract
    (directory / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    return summary


def revalidate_source_adapter(source_run: Path, test_source: Path, output: Path) -> dict:
    """Append an automatic source-mapping receipt without changing prior analysis evidence."""
    if output.exists():
        raise FileExistsError(f"output must not exist: {output}")
    expectations = json.loads(EXPECTATIONS.read_text(encoding="utf-8"))
    if _sha256_file(test_source) != expectations["test_sha256"]:
        raise ValueError("adapter test source differs from the registered analysis source")
    adapter = adapt_jib_observations(test_source.read_text(encoding="utf-8"))
    validate_adapter_observations(adapter, expectations["observations"])
    archived_entry = (source_run / "skeleton/source/com/google/cloud/tools/jib/cli/jar/"
                      "JibObservationEntry.java")
    archived_sink = (source_run / "skeleton/source/com/google/cloud/tools/jib/cli/jar/"
                     "ObservationSink.java")
    if not archived_entry.exists() or not archived_sink.exists():
        raise ValueError("source run lacks archived generated observation sources")
    generated_entry_sha = hashlib.sha256(adapter["entry_source"].encode()).hexdigest()
    generated_sink_sha = hashlib.sha256(adapter["sink_source"].encode()).hexdigest()
    if generated_entry_sha != _sha256_file(archived_entry):
        raise ValueError("source-derived entry is not byte-identical to the analysed entry")
    if generated_sink_sha != _sha256_file(archived_sink):
        raise ValueError("source-derived sink is not byte-identical to the analysed sink")
    source_summary_path = source_run / "summary.json"
    source_summary = json.loads(source_summary_path.read_text(encoding="utf-8"))
    source_registration = json.loads(
        (source_run / "pre-registration.json").read_text(encoding="utf-8"))
    if source_registration.get("test_sha256") != adapter["original_test_sha256"]:
        raise ValueError("adapter source identity differs from the analysed registration")
    receipt = {
        "schema": "mockspec.jib-observation-adapter-revalidation/v1",
        "status": "verified",
        "append_only": True,
        "source_run": str(source_run),
        "source_summary_sha256": _sha256_file(source_summary_path),
        "source_registration_sha256": _sha256_file(source_run / "pre-registration.json"),
        "implementation_sources_sha256": {
            str(path): _sha256_file(path) for path in (
                HERE / "jib_observation_adapter.py", Path(__file__).resolve(),
                EXPECTATIONS, HERE.parent / "result_contract.py")
        },
        "analysis_json_sha256": _sha256_file(source_run / "analysis.json"),
        "analysis_probe_sha256": source_registration["probe_sha256"],
        "current_probe_sha256": _sha256_file(
            HERE / "backend/src/main/java/org/mockspec/research/JibObservationProbe.java"),
        "probe_identity_rule": (
            "the revalidation reuses only the registered analysis output and does not claim "
            "that the current probe produced it"),
        "original_test_sha256": adapter["original_test_sha256"],
        "generated_entry_sha256": generated_entry_sha,
        "analysed_entry_sha256": _sha256_file(archived_entry),
        "generated_sink_sha256": generated_sink_sha,
        "analysed_sink_sha256": _sha256_file(archived_sink),
        "mut_call": adapter["mut_call"],
        "plan_binding": adapter["plan_binding"],
        "criteria": adapter["criteria"],
        "parse_provenance": adapter["parse_provenance"],
        "supported_syntax": (
            "unique MUT result declaration immediately followed by its plan declaration; "
            "only complete top-level supported assertions thereafter; rebinding, calls, "
            "control prefixes and other executable statements are rejected"),
    }
    updated = copy.deepcopy(source_summary)
    updated["entry_origin"] = "automatic_source_derived_observation_adapter"
    updated["observation_adapter_revalidation"] = receipt
    outcome = updated["outcome"]
    outcome["failure_categories"] = [
        item for item in outcome.get("failure_categories", [])
        if item != "original_observation_mapping_remains_diagnostic_override"]
    outcome["blockers"] = [
        item for item in outcome.get("blockers", [])
        if item != "original observation mapping still uses a diagnostic override"]
    outcome["closure_gate"] = "passed" if not outcome["failure_categories"] else "blocked"
    outcome["ticket_acceptance"] = "passed_automatic_source_adapter"
    contract = updated["result_contract"]
    contract["entry_point"]["test_selection"] = {
        "origin": "automatic", "status": "unique",
        "selector": expectations["dataset_case_id"],
        "evidence": [receipt], "assumptions": [],
    }
    contract["analysis_boundaries"]["assumptions"] = [
        assumption for assumption in contract["analysis_boundaries"].get("assumptions", [])
        if "diagnostic observation mapping" not in assumption]
    contract["analysis_boundaries"]["assumptions"].append(receipt["supported_syntax"])
    contract["completion"]["failure_categories"] = outcome["failure_categories"]
    spec = importlib.util.spec_from_file_location(
        "result_contract_revalidation", HERE.parent / "result_contract.py")
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load result contract for adapter revalidation")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.validate_result_contract(contract)
    output.mkdir(parents=True)
    (output / "observation-adapter-revalidation.json").write_text(
        json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    (output / "summary.json").write_text(
        json.dumps(updated, indent=2) + "\n", encoding="utf-8")
    return updated


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("directory", type=Path, nargs="?")
    parser.add_argument("--revalidate-test-source", type=Path)
    parser.add_argument("--revalidation-output", type=Path)
    args = parser.parse_args()
    if args.revalidate_test_source is not None:
        if args.directory is None or args.revalidation_output is None:
            parser.error("adapter revalidation requires source directory and --revalidation-output")
        result = revalidate_source_adapter(
            args.directory, args.revalidate_test_source, args.revalidation_output)
    else:
        if args.directory is None:
            parser.error("directory is required")
        result = summarize(args.directory)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
