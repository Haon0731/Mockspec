"""Build two handcrafted contracts from reviewed Adapter/SourceStore inputs only."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from inputs import ReviewedInput, load_java, load_python
from schema_draft import SCHEMA_VERSION, validate_contract


HERE = Path(__file__).resolve().parent

JAVA_ORACLE_IDS = {
    "O_J_BASE_IMAGE", "O_J_PLATFORMS", "O_J_CREATION_TIME", "O_J_FORMAT",
    "O_J_ENV_EMPTY", "O_J_LABELS_EMPTY", "O_J_VOLUMES_EMPTY", "O_J_PORTS_EMPTY",
    "O_J_USER_NULL", "O_J_WORKDIR_NULL", "O_J_ENTRY_SIZE", "O_J_ENTRY_0",
    "O_J_ENTRY_1", "O_J_ENTRY_2", "O_J_ENTRY_3", "O_J_LAYER_SIZE",
    "O_J_LAYER_NAME", "O_J_LAYER_ENTRIES", "O_J_EXTRACTION_PATH",
}


def _sources(reviewed: ReviewedInput) -> list[dict[str, Any]]:
    result = []
    for source_id in reviewed.sources.ids():
        source = reviewed.sources.get(source_id)
        result.append({
            "id": source_id,
            "logical_path": source.logical_path,
            "start_line": source.start_line,
            "end_line": source.end_line,
            "sha256": reviewed.source_sha256[source_id],
        })
    return result


def _evidence(
    reviewed: ReviewedInput,
    evidence_id: str,
    source_id: str,
    start: int,
    end: int,
    supports: str,
    semantic_limit: str,
) -> dict[str, Any]:
    source = reviewed.sources.get(source_id)
    lines = source.text.splitlines()
    quote = "\n".join(lines[start - source.start_line : end - source.start_line + 1])
    return {
        "id": evidence_id,
        "source_id": source_id,
        "start_line": start,
        "end_line": end,
        "quote": quote,
        "supports": supports,
        "semantic_limit": semantic_limit,
    }


def _condition(formula_ref: str) -> dict[str, Any]:
    return {"status": "scoped", "formula_ref": formula_ref, "unknown_ref": None}


def _unknown_condition(unknown_ref: str) -> dict[str, Any]:
    return {"status": "unknown", "formula_ref": None, "unknown_ref": unknown_ref}


def _identity(reviewed: ReviewedInput) -> dict[str, Any]:
    return {
        "case_id": reviewed.task.case_id,
        "language": reviewed.task.language,
        "authoring_mode": "handcrafted_from_visible_input",
        "input_policy_id": reviewed.task.input_policy.policy_id,
        "source_hash_algorithm": "sha256-utf8-visible-slice",
    }


def _java_oracles() -> list[dict[str, Any]]:
    data = [
        ("O_J_BASE_IMAGE", "buildPlan.getBaseImage()", 'equals "adoptopenjdk:8-jre"', "E_J_ASSERT_BASE", "not_formalized"),
        ("O_J_PLATFORMS", "buildPlan.getPlatforms()", 'equals {Platform("amd64", "linux")}', "E_J_ASSERT_PLATFORMS", "not_formalized"),
        ("O_J_CREATION_TIME", "buildPlan.getCreationTime()", "equals Instant.EPOCH", "E_J_ASSERT_CREATION", "not_formalized"),
        ("O_J_FORMAT", "buildPlan.getFormat()", "equals ImageFormat.Docker", "E_J_ASSERT_FORMAT", "not_formalized"),
        ("O_J_ENV_EMPTY", "buildPlan.getEnvironment().size()", "equals 0", "E_J_ASSERT_ENV", "not_formalized"),
        ("O_J_LABELS_EMPTY", "buildPlan.getLabels().size()", "equals 0", "E_J_ASSERT_LABELS", "not_formalized"),
        ("O_J_VOLUMES_EMPTY", "buildPlan.getVolumes().size()", "equals 0", "E_J_ASSERT_VOLUMES", "not_formalized"),
        ("O_J_PORTS_EMPTY", "buildPlan.getExposedPorts().size()", "equals 0", "E_J_ASSERT_PORTS", "not_formalized"),
        ("O_J_USER_NULL", "buildPlan.getUser()", "is null", "E_J_ASSERT_USER", "not_formalized"),
        ("O_J_WORKDIR_NULL", "buildPlan.getWorkingDirectory()", "is null", "E_J_ASSERT_WORKDIR", "not_formalized"),
        ("O_J_ENTRY_SIZE", "buildPlan.getEntrypoint().size()", "equals 4", "E_J_ASSERT_ENTRY_SIZE", "not_formalized"),
        ("O_J_ENTRY_0", "buildPlan.getEntrypoint().get(0)", 'equals "java"', "E_J_ASSERT_ENTRY_0", "not_formalized"),
        ("O_J_ENTRY_1", "buildPlan.getEntrypoint().get(1)", 'equals "-cp"', "E_J_ASSERT_ENTRY_1", "not_formalized"),
        ("O_J_ENTRY_2", "buildPlan.getEntrypoint().get(2)", 'equals "/app/explodedJar:/app/dependencies/*"', "E_J_ASSERT_ENTRY_2", "not_formalized"),
        ("O_J_ENTRY_3", "buildPlan.getEntrypoint().get(3)", 'equals "HelloWorld"', "E_J_ASSERT_ENTRY_3", "not_formalized"),
        ("O_J_LAYER_SIZE", "buildPlan.getLayers().size()", "equals 1", "E_J_ASSERT_LAYER_SIZE", "not_formalized"),
        ("O_J_LAYER_NAME", "buildPlan.getLayers().get(0).getName()", 'equals "classes"', "E_J_ASSERT_LAYER_NAME", "not_formalized"),
        ("O_J_LAYER_ENTRIES", "buildPlan.getLayers().get(0).getEntries()", "array equals the expected single FileEntry", "E_J_ASSERT_LAYER_ENTRIES", "not_formalized"),
        ("O_J_EXTRACTION_PATH", "buildPlan.getLayers().get(0).getEntries().get(0).getExtractionPath()", 'equals AbsoluteUnixPath("/app/explodedJar/class1.class")', "E_J_ASSERT_EXTRACTION", "not_formalized"),
    ]
    return [{
        "id": oid,
        "parameter_row": None,
        "observation": observation,
        "expected": expected,
        "evidence_refs": [evidence],
        "formalization_status": status,
    } for oid, observation, expected, evidence, status in data]


def build_java(reviewed: ReviewedInput) -> dict[str, Any]:
    evidence_specs = [
        ("E_J_FROM_BRANCH", "mut", 60, 71, "Two getFrom calls select an explicit-from builder or a version-based default image.", "Visible control structure does not reveal ContainerBuilders.create semantics or guarantee repeated-call stability."),
        ("E_J_LAYERS_ENTRY", "mut", 74, 79, "createLayers and empty/non-empty entrypoint alternatives feed the builder.", "This syntax does not prove dependency return values or absence of exceptions."),
        ("E_J_BUILDER_WRITES", "mut", 81, 91, "The chosen entrypoint, layers, and configuration getters are passed to builder methods.", "External builder semantics and defaults remain outside the reviewed source."),
        ("E_J_ASSERT_BASE", "test", 71, 71, "Visible base-image oracle.", "The assertion alone does not make getFrom empty globally necessary."),
        ("E_J_ASSERT_PLATFORMS", "test", 72, 72, "Visible platform oracle.", "Not formalized in the local SMT fragment."),
        ("E_J_ASSERT_CREATION", "test", 73, 73, "Visible creation-time oracle.", "Not formalized in the local SMT fragment."),
        ("E_J_ASSERT_FORMAT", "test", 74, 74, "Visible image-format oracle.", "Not formalized in the local SMT fragment."),
        ("E_J_ASSERT_ENV", "test", 75, 75, "Visible empty-environment oracle.", "Not formalized in the local SMT fragment."),
        ("E_J_ASSERT_LABELS", "test", 76, 76, "Visible empty-labels oracle.", "Not formalized in the local SMT fragment."),
        ("E_J_ASSERT_VOLUMES", "test", 77, 77, "Visible empty-volumes oracle.", "Not formalized in the local SMT fragment."),
        ("E_J_ASSERT_PORTS", "test", 78, 78, "Visible empty-ports oracle.", "Not formalized in the local SMT fragment."),
        ("E_J_ASSERT_USER", "test", 79, 79, "Visible null-user oracle.", "Not formalized in the local SMT fragment."),
        ("E_J_ASSERT_WORKDIR", "test", 80, 80, "Visible null-working-directory oracle.", "Not formalized in the local SMT fragment."),
        ("E_J_ASSERT_ENTRY_SIZE", "test", 81, 81, "Visible entrypoint length oracle.", "Formalization covers only the abstract entrypoint value."),
        ("E_J_ASSERT_ENTRY_0", "test", 82, 82, "Visible entrypoint element 0 oracle.", "Formalization covers only the abstract entrypoint value."),
        ("E_J_ASSERT_ENTRY_1", "test", 83, 83, "Visible entrypoint element 1 oracle.", "Formalization covers only the abstract entrypoint value."),
        ("E_J_ASSERT_ENTRY_2", "test", 84, 84, "Visible entrypoint element 2 oracle.", "Formalization covers only the abstract entrypoint value."),
        ("E_J_ASSERT_ENTRY_3", "test", 85, 85, "Visible entrypoint element 3 oracle.", "Formalization covers only the abstract entrypoint value."),
        ("E_J_ASSERT_LAYER_SIZE", "test", 86, 86, "Visible layer-count oracle.", "Not formalized in the local SMT fragment."),
        ("E_J_ASSERT_LAYER_NAME", "test", 87, 87, "Visible first-layer name oracle.", "Not formalized in the local SMT fragment."),
        ("E_J_ASSERT_LAYER_ENTRIES", "test", 88, 96, "Visible layer-entry array oracle.", "Not formalized in the local SMT fragment."),
        ("E_J_ASSERT_EXTRACTION", "test", 97, 99, "Visible extraction-path oracle.", "Not formalized in the local SMT fragment."),
        ("E_J_PROCESSOR_INTERFACES", "processor", 53, 60, "Reviewed processor declarations identify three controllable dependency calls.", "The visible UnsupportedOperationException bodies are masking placeholders, not dependency semantics."),
    ]
    evidence = [_evidence(reviewed, *spec) for spec in evidence_specs]
    oracles = _java_oracles()
    all_oracles = [item["id"] for item in oracles]
    formalized = [item["id"] for item in oracles if item["formalization_status"] == "formalized"]

    bindings_data = [
        ("C_J_GET_FROM_1", "mockCommonContainerConfigCliOptions", "getFrom", [], 1, 60),
        ("C_J_GET_FROM_2", "mockCommonContainerConfigCliOptions", "getFrom", [], 2, 63),
        ("C_J_CREATE_EXPLICIT", "ContainerBuilders", "create", ["getFrom#2.value", "emptySet", "mockCommonCliOptions", "mockLogger"], 1, 62),
        ("C_J_GET_JAVA_VERSION", "mockStandardExplodedProcessor", "getJavaVersion", [], 1, 69),
        ("C_J_JIB_FROM_8", "Jib", "from", ['"adoptopenjdk:8-jre"'], 1, 70),
        ("C_J_JIB_FROM_11", "Jib", "from", ['"adoptopenjdk:11-jre"'], 2, 71),
        ("C_J_CREATE_LAYERS", "mockStandardExplodedProcessor", "createLayers", [], 1, 74),
        ("C_J_GET_ENTRYPOINT", "mockCommonContainerConfigCliOptions", "getEntrypoint", [], 1, 75),
        ("C_J_GET_JVM_FLAGS", "mockJarCommand", "getJvmFlags", [], 1, 78),
        ("C_J_COMPUTE_ENTRYPOINT", "mockStandardExplodedProcessor", "computeEntrypoint", ["getJvmFlags#1.return"], 1, 78),
        ("C_J_GET_PORTS", "mockCommonContainerConfigCliOptions", "getExposedPorts", [], 1, 84),
        ("C_J_GET_VOLUMES", "mockCommonContainerConfigCliOptions", "getVolumes", [], 1, 85),
        ("C_J_GET_ENV", "mockCommonContainerConfigCliOptions", "getEnvironment", [], 1, 86),
        ("C_J_GET_LABELS", "mockCommonContainerConfigCliOptions", "getLabels", [], 1, 87),
        ("C_J_GET_ARGS", "mockCommonContainerConfigCliOptions", "getProgramArguments", [], 1, 88),
        ("C_J_GET_USER", "mockCommonContainerConfigCliOptions", "getUser", [], 1, 89),
        ("C_J_GET_FORMAT", "mockCommonContainerConfigCliOptions", "getFormat", [], 1, 90),
        ("C_J_GET_CREATION", "mockCommonContainerConfigCliOptions", "getCreationTime", [], 1, 91),
    ]
    bindings = [{
        "id": bid, "receiver": recv, "method": method, "arguments": args,
        "call_ordinal": ordinal, "source_id": "mut", "start_line": line,
        "end_line": line, "resolution_status": "partial",
    } for bid, recv, method, args, ordinal, line in bindings_data]

    symbols_data = [
        ("S_J_FROM1_PRESENT", "Bool", "dependency_response", True, "C_J_GET_FROM_1", "from1_present"),
        ("S_J_FROM2_PRESENT", "Bool", "dependency_response", True, "C_J_GET_FROM_2", "from2_present"),
        ("S_J_FROM_VALUE", "String", "dependency_response", True, "C_J_GET_FROM_2", None),
        ("S_J_VERSION_IS_NULL", "Bool", "dependency_response", True, "C_J_GET_JAVA_VERSION", "version_null"),
        ("S_J_VERSION_THROWS", "Bool", "dependency_response", True, "C_J_GET_JAVA_VERSION", "version_throws"),
        ("S_J_VERSION", "Int", "dependency_response", True, "C_J_GET_JAVA_VERSION", "version"),
        ("S_J_VERSION_CALLED", "Bool", "execution_state", False, "C_J_GET_JAVA_VERSION", "version_called"),
        ("S_J_ENTRY_CUSTOM_EMPTY", "Bool", "dependency_response", True, "C_J_GET_ENTRYPOINT", "custom_empty"),
        ("S_J_ENTRY_VALUE", "String", "dependency_response", True, "C_J_GET_ENTRYPOINT", None),
        ("S_J_COMPUTED_ENTRY", "String", "dependency_response", True, "C_J_COMPUTE_ENTRYPOINT", None),
        ("S_J_CUSTOM_MATCHES", "Bool", "handcrafted_abstract_equality_to_four_item_expected_list", True, "C_J_GET_ENTRYPOINT", "custom_matches"),
        ("S_J_COMPUTED_MATCHES", "Bool", "handcrafted_abstract_equality_to_four_item_expected_list", True, "C_J_COMPUTE_ENTRYPOINT", "computed_matches"),
        ("S_J_JAVA8_SELECTED", "Bool", "local_observation", False, "C_J_JIB_FROM_8", "java8_selected"),
        ("S_J_EXTERNAL_IMAGE_MATCHES", "Bool", "unknown_external_relation", False, "C_J_CREATE_EXPLICIT", "external_image_matches"),
        ("S_J_ENTRYPOINT_MATCHES", "Bool", "local_observation", False, None, "entrypoint_matches"),
        ("S_J_ENTRY_OBSERVATION_REACHED", "Bool", "execution_state", False, None, "entrypoint_observation_reached"),
        ("S_J_IMAGE_OBSERVATION_REACHED", "Bool", "execution_state", False, None, "image_observation_reached"),
        ("S_J_ASSERT_REACHED", "Bool", "execution_state", False, None, None),
        ("S_J_NORMAL_EXIT", "Bool", "execution_state", False, None, None),
    ]
    symbols = [{
        "id": sid, "sort": sort, "role": role, "controllable": controllable,
        "binding_ref": binding, "model_symbol": model_symbol,
        "evidence_refs": ["E_J_FROM_BRANCH"] if "FROM" in sid or "VERSION" in sid or "IMAGE" in sid or "JAVA8" in sid else ["E_J_LAYERS_ENTRY"],
    } for sid, sort, role, controllable, binding, model_symbol in symbols_data]

    formulas_data = [
        ("F_J_DEFAULT_IMAGE_GUARD", "(assert (not S_J_FROM1_PRESENT))", "scenario_guard", ["S_J_FROM1_PRESENT"], ["E_J_FROM_BRANCH"]),
        ("F_J_EXPLICIT_IMAGE_GUARD", "(assert S_J_FROM1_PRESENT)", "scenario_guard", ["S_J_FROM1_PRESENT"], ["E_J_FROM_BRANCH"]),
        ("F_J_EMPTY_ENTRY_GUARD", "(assert S_J_ENTRY_CUSTOM_EMPTY)", "scenario_guard", ["S_J_ENTRY_CUSTOM_EMPTY"], ["E_J_LAYERS_ENTRY"]),
        ("F_J_CUSTOM_ENTRY_GUARD", "(assert (not S_J_ENTRY_CUSTOM_EMPTY))", "scenario_guard", ["S_J_ENTRY_CUSTOM_EMPTY"], ["E_J_LAYERS_ENTRY"]),
        ("F_J_VERSION_JAVA8", "(assert (and (not S_J_VERSION_IS_NULL) (not S_J_VERSION_THROWS) (<= S_J_VERSION 8)))", "scenario_local_obligation", ["S_J_VERSION_IS_NULL", "S_J_VERSION_THROWS", "S_J_VERSION"], ["E_J_FROM_BRANCH"]),
        ("F_J_VERSION_WITNESS_8", "(assert (= S_J_VERSION 8))", "implementation_choice", ["S_J_VERSION"], ["E_J_FROM_BRANCH"]),
        ("F_J_FROM_STABLE", "(assert (= S_J_FROM1_PRESENT S_J_FROM2_PRESENT))", "explicit_model_assumption", ["S_J_FROM1_PRESENT", "S_J_FROM2_PRESENT"], ["E_J_FROM_BRANCH"]),
        ("F_J_DEFAULT_BASE_TARGET", "(assert S_J_JAVA8_SELECTED)", "intermediate_program_target", ["S_J_JAVA8_SELECTED"], ["E_J_FROM_BRANCH", "E_J_ASSERT_BASE"]),
        ("F_J_ENTRY_TARGET", "(assert S_J_ENTRYPOINT_MATCHES)", "intermediate_program_target", ["S_J_ENTRYPOINT_MATCHES"], ["E_J_ASSERT_ENTRY_SIZE", "E_J_ASSERT_ENTRY_0", "E_J_ASSERT_ENTRY_1", "E_J_ASSERT_ENTRY_2", "E_J_ASSERT_ENTRY_3"]),
        ("F_J_CUSTOM_ENTRY_EQUALS_TARGET", "(assert S_J_CUSTOM_MATCHES)", "handcrafted_abstract_list_equality", ["S_J_CUSTOM_MATCHES"], ["E_J_ASSERT_ENTRY_SIZE", "E_J_ASSERT_ENTRY_0", "E_J_ASSERT_ENTRY_1", "E_J_ASSERT_ENTRY_2", "E_J_ASSERT_ENTRY_3"]),
        ("F_J_COMPUTED_ENTRY_EQUALS_TARGET", "(assert S_J_COMPUTED_MATCHES)", "handcrafted_abstract_list_equality", ["S_J_COMPUTED_MATCHES"], ["E_J_ASSERT_ENTRY_SIZE", "E_J_ASSERT_ENTRY_0", "E_J_ASSERT_ENTRY_1", "E_J_ASSERT_ENTRY_2", "E_J_ASSERT_ENTRY_3"]),
        ("F_J_SUCCESS_REACHED", "(assert (and S_J_NORMAL_EXIT S_J_ASSERT_REACHED))", "non_vacuity", ["S_J_NORMAL_EXIT", "S_J_ASSERT_REACHED"], ["E_J_ASSERT_BASE"]),
        ("F_J_EXPLICIT_BUILDER_SEMANTICS", "; unsupported: external_image_matches relates ContainerBuilders.create to the final base-image oracle and is forbidden from H", "external_relation", ["S_J_FROM_VALUE", "S_J_EXTERNAL_IMAGE_MATCHES"], ["E_J_FROM_BRANCH", "E_J_ASSERT_BASE"]),
    ]
    formulas = [{
        "id": fid, "format": "smtlib2", "text": text, "role": role,
        "symbol_refs": refs, "evidence_refs": erefs,
        "support_status": "descriptive_unsupported" if text.startswith("; unsupported") else "modeled",
    } for fid, text, role, refs, erefs in formulas_data]

    scenarios = [
        {"id": "SC_J_IMAGE_DEFAULT", "title": "Default image selected by Java version", "alternative_group": "java_image", "condition": _condition("F_J_DEFAULT_IMAGE_GUARD"), "oracle_refs": ["O_J_BASE_IMAGE"], "assumption_refs": [], "unknown_refs": ["U_J_VERSION_NULL_OR_THROW", "U_J_UNFORMALIZED_ASSERTIONS"], "status": "partial"},
        {"id": "SC_J_IMAGE_EXPLICIT", "title": "Explicit from value selects ContainerBuilders.create", "alternative_group": "java_image", "condition": _condition("F_J_EXPLICIT_IMAGE_GUARD"), "oracle_refs": ["O_J_BASE_IMAGE"], "assumption_refs": ["A_J_STABLE_FROM"], "unknown_refs": ["U_J_EXPLICIT_BUILDER"], "status": "partial"},
        {"id": "SC_J_ENTRY_COMPUTED", "title": "Empty custom entrypoint triggers computation", "alternative_group": "java_entrypoint", "condition": _condition("F_J_EMPTY_ENTRY_GUARD"), "oracle_refs": ["O_J_ENTRY_SIZE", "O_J_ENTRY_0", "O_J_ENTRY_1", "O_J_ENTRY_2", "O_J_ENTRY_3"], "assumption_refs": [], "unknown_refs": ["U_J_DEPENDENCY_EXCEPTIONS", "U_J_UNFORMALIZED_ASSERTIONS"], "status": "partial"},
        {"id": "SC_J_ENTRY_CUSTOM", "title": "Non-empty custom entrypoint already equals the oracle", "alternative_group": "java_entrypoint", "condition": _condition("F_J_CUSTOM_ENTRY_GUARD"), "oracle_refs": ["O_J_ENTRY_SIZE", "O_J_ENTRY_0", "O_J_ENTRY_1", "O_J_ENTRY_2", "O_J_ENTRY_3"], "assumption_refs": [], "unknown_refs": ["U_J_DEPENDENCY_EXCEPTIONS", "U_J_UNFORMALIZED_ASSERTIONS"], "status": "partial"},
    ]

    rules = [
        ("R_J_FROM_DEFAULT", ["SC_J_IMAGE_DEFAULT"], "F_J_DEFAULT_IMAGE_GUARD", "C_J_GET_FROM_1", "return", "Optional.empty()", ["F_J_DEFAULT_IMAGE_GUARD"], "scenario_local_obligation", ["O_J_BASE_IMAGE"], ["E_J_FROM_BRANCH"]),
        ("R_J_VERSION_JAVA8", ["SC_J_IMAGE_DEFAULT"], "F_J_DEFAULT_IMAGE_GUARD", "C_J_GET_JAVA_VERSION", "return", "any non-null Integer <= 8", ["F_J_VERSION_JAVA8"], "scenario_local_obligation", ["O_J_BASE_IMAGE"], ["E_J_FROM_BRANCH", "E_J_ASSERT_BASE"]),
        ("R_J_VERSION_WITNESS", ["SC_J_IMAGE_DEFAULT"], "F_J_DEFAULT_IMAGE_GUARD", "C_J_GET_JAVA_VERSION", "return", "8", ["F_J_VERSION_WITNESS_8"], "implementation_choice", ["O_J_BASE_IMAGE"], ["E_J_FROM_BRANCH"]),
        ("R_J_FROM_EXPLICIT_FIRST", ["SC_J_IMAGE_EXPLICIT"], "F_J_EXPLICIT_IMAGE_GUARD", "C_J_GET_FROM_1", "return", "Optional.of(fromValue)", [], "implementation_choice", ["O_J_BASE_IMAGE"], ["E_J_FROM_BRANCH"]),
        ("R_J_FROM_EXPLICIT_SECOND", ["SC_J_IMAGE_EXPLICIT"], "F_J_EXPLICIT_IMAGE_GUARD", "C_J_GET_FROM_2", "return", "Optional.of(same fromValue)", ["F_J_FROM_STABLE"], "implementation_choice", ["O_J_BASE_IMAGE"], ["E_J_FROM_BRANCH"]),
        ("R_J_ENTRY_EMPTY", ["SC_J_ENTRY_COMPUTED"], "F_J_EMPTY_ENTRY_GUARD", "C_J_GET_ENTRYPOINT", "return", "empty list", ["F_J_EMPTY_ENTRY_GUARD"], "scenario_local_obligation", ["O_J_ENTRY_SIZE"], ["E_J_LAYERS_ENTRY"]),
        ("R_J_ENTRY_COMPUTED", ["SC_J_ENTRY_COMPUTED"], "F_J_EMPTY_ENTRY_GUARD", "C_J_COMPUTE_ENTRYPOINT", "return", "[java, -cp, /app/explodedJar:/app/dependencies/*, HelloWorld]", ["F_J_COMPUTED_ENTRY_EQUALS_TARGET"], "scenario_local_obligation", ["O_J_ENTRY_SIZE", "O_J_ENTRY_0", "O_J_ENTRY_1", "O_J_ENTRY_2", "O_J_ENTRY_3"], ["E_J_LAYERS_ENTRY", "E_J_ASSERT_ENTRY_0"]),
        ("R_J_ENTRY_CUSTOM", ["SC_J_ENTRY_CUSTOM"], "F_J_CUSTOM_ENTRY_GUARD", "C_J_GET_ENTRYPOINT", "return", "[java, -cp, /app/explodedJar:/app/dependencies/*, HelloWorld]", ["F_J_CUSTOM_ENTRY_EQUALS_TARGET"], "scenario_local_obligation", ["O_J_ENTRY_SIZE", "O_J_ENTRY_0", "O_J_ENTRY_1", "O_J_ENTRY_2", "O_J_ENTRY_3"], ["E_J_LAYERS_ENTRY", "E_J_ASSERT_ENTRY_0"]),
    ]
    response_rules = [{
        "id": rid, "scenario_refs": scenario_refs, "condition": _condition(condition),
        "call_binding": binding, "reaction": {"kind": kind, "value": value, "constraint_refs": constraints},
        "logical_role": logical_role, "oracle_refs": oracle_refs, "formula_refs": constraints,
        "evidence_refs": erefs, "check_refs": [], "whole_test_verified": False,
    } for rid, scenario_refs, condition, binding, kind, value, constraints, logical_role, oracle_refs, erefs in rules]
    for rule in response_rules:
        scenario_id = rule["scenario_refs"][0]
        if scenario_id == "SC_J_IMAGE_DEFAULT":
            rule["check_refs"] = ["K_J_LOCAL_CONSISTENCY"]
        elif scenario_id == "SC_J_IMAGE_EXPLICIT":
            rule["check_refs"] = ["K_J_EXPLICIT_SEMANTICS"]
        elif scenario_id == "SC_J_ENTRY_CUSTOM":
            rule["check_refs"] = ["K_J_ENTRY_LOCAL"]

    expectations_data = [
        ("X_J_GET_FROM_ONCE_DEFAULT", ["SC_J_IMAGE_DEFAULT"], "F_J_DEFAULT_IMAGE_GUARD", "C_J_GET_FROM_1", "called once; the second getFrom call is not reached"),
        ("X_J_GET_FROM_TWICE_EXPLICIT", ["SC_J_IMAGE_EXPLICIT"], "F_J_EXPLICIT_IMAGE_GUARD", "C_J_GET_FROM_2", "both distinct getFrom call sites are reached in order"),
        ("X_J_VERSION_DEFAULT_ONLY", ["SC_J_IMAGE_DEFAULT"], "F_J_DEFAULT_IMAGE_GUARD", "C_J_GET_JAVA_VERSION", "called once after the first getFrom reports empty"),
        ("X_J_COMPUTE_EMPTY_ONLY", ["SC_J_ENTRY_COMPUTED"], "F_J_EMPTY_ENTRY_GUARD", "C_J_COMPUTE_ENTRYPOINT", "called once with the value returned by getJvmFlags"),
        ("X_J_NO_COMPUTE_CUSTOM", ["SC_J_ENTRY_CUSTOM"], "F_J_CUSTOM_ENTRY_GUARD", "C_J_COMPUTE_ENTRYPOINT", "not called when the custom list is non-empty"),
    ]
    expectations = [{
        "id": xid, "scenario_refs": srefs, "condition": _condition(formula),
        "binding_ref": binding, "expectation": expectation,
        "evidence_refs": ["E_J_FROM_BRANCH"] if "FROM" in xid or "VERSION" in xid else ["E_J_LAYERS_ENTRY"],
        "logical_role": "runtime_expectation",
    } for xid, srefs, formula, binding, expectation in expectations_data]
    verification_candidates = [{
        "id": "V_" + item["id"][2:],
        "scenario_refs": item["scenario_refs"],
        "condition": item["condition"],
        "expectation_ref": item["id"],
        "proposal": f"Consider verifying: {item['expectation']}",
        "behavior_protected": "Detects a path-selection regression if interaction verification is desired by the downstream test author.",
        "evidence_refs": item["evidence_refs"],
        "status": "candidate_only",
        "whole_test_verified": False,
    } for item in expectations]

    assumptions = [
        {"id": "A_J_STABLE_FROM", "statement": "The two distinct getFrom calls return compatible values in the explicit-from scenario.", "scope": "SC_J_IMAGE_EXPLICIT only", "evidence_refs": ["E_J_FROM_BRANCH"], "blocks_semantic_proof": True},
        {"id": "A_J_MODELED_BUILDER", "statement": "The local formula models only base-image and entrypoint observations; external builder setters preserve those abstract values.", "scope": "all Java local checks", "evidence_refs": ["E_J_BUILDER_WRITES"], "blocks_semantic_proof": True},
    ]
    unknowns = [
        {"id": "U_J_EXPLICIT_BUILDER", "statement": "ContainerBuilders.create semantics and defaults are not visible, so an explicit from value cannot be proved to yield the base-image oracle.", "impact": "The explicit-from scenario remains partial and cannot be consumed as verified.", "evidence_refs": ["E_J_FROM_BRANCH", "E_J_ASSERT_BASE"], "blocks_consumption": True},
        {"id": "U_J_VERSION_NULL_OR_THROW", "statement": "getJavaVersion returns Integer; null unboxing or a thrown exception prevents normal assertion reachability.", "impact": "Default-image consumption requires a normal, non-null return.", "evidence_refs": ["E_J_FROM_BRANCH", "E_J_PROCESSOR_INTERFACES"], "blocks_consumption": False},
        {"id": "U_J_DEPENDENCY_EXCEPTIONS", "statement": "createLayers, computeEntrypoint, configuration getters, and builder methods may throw or have unresolved semantics.", "impact": "The path alternatives are not a whole-test proof.", "evidence_refs": ["E_J_LAYERS_ENTRY", "E_J_BUILDER_WRITES", "E_J_PROCESSOR_INTERFACES"], "blocks_consumption": False},
        {"id": "U_J_UNFORMALIZED_ASSERTIONS", "statement": "Every visible assertion observes the final ContainerBuildPlan; propagation from the modeled image-selection and entrypoint values through external builder behavior is not formalized.", "impact": "Local formulas can check intermediate choices only; no Java final oracle or whole test is verified.", "evidence_refs": ["E_J_ASSERT_BASE", "E_J_ASSERT_ENTRY_0", "E_J_ASSERT_PLATFORMS", "E_J_ASSERT_LAYER_ENTRIES", "E_J_ASSERT_EXTRACTION"], "blocks_consumption": False},
    ]

    return {
        "schema_version": SCHEMA_VERSION,
        "identity": _identity(reviewed),
        "coverage": {
            "scope": "Local image-selection and pre-builder entrypoint alternatives for one visible test; every final ContainerBuildPlan assertion is retained but not formalized.",
            "formalized_oracle_refs": formalized,
            "unformalized_oracle_refs": [oid for oid in all_oracles if oid not in formalized],
            "whole_test_verified": False,
        },
        "sources": _sources(reviewed), "evidence": evidence, "oracles": oracles,
        "symbols": symbols, "bindings": bindings, "formulas": formulas,
        "scenarios": scenarios, "response_rules": response_rules,
        "runtime_expectations": expectations,
        "verification_candidates": verification_candidates,
        "assumptions": assumptions, "unknowns": unknowns,
        "checks": [
            {"id": "K_J_LOCAL_CONSISTENCY", "kind": "candidate_checking", "formula_refs": ["F_J_DEFAULT_IMAGE_GUARD", "F_J_VERSION_JAVA8", "F_J_DEFAULT_BASE_TARGET"], "scenario_refs": ["SC_J_IMAGE_DEFAULT"], "result": "not_run", "performed_by": "not_run", "details": "Proposed check only; actual evidence belongs to the host PoC sidecar.", "semantic_claim": False},
            {"id": "K_J_ENTRY_LOCAL", "kind": "candidate_checking", "formula_refs": ["F_J_CUSTOM_ENTRY_GUARD", "F_J_CUSTOM_ENTRY_EQUALS_TARGET", "F_J_ENTRY_TARGET"], "scenario_refs": ["SC_J_ENTRY_CUSTOM"], "result": "not_run", "performed_by": "not_run", "details": "Proposed local custom-entry check only; actual evidence belongs to the host PoC sidecar.", "semantic_claim": False},
            {"id": "K_J_EXPLICIT_SEMANTICS", "kind": "external_semantics", "formula_refs": ["F_J_EXPLICIT_BUILDER_SEMANTICS"], "scenario_refs": ["SC_J_IMAGE_EXPLICIT"], "result": "not_run", "performed_by": "not_run", "details": "Proposed unsupported-semantics check; actual classification belongs to the host PoC sidecar.", "semantic_claim": False},
        ],
        "consumption": {
            "selection_mode": "human_review_only",
            "selectable_scenarios": ["SC_J_IMAGE_DEFAULT", "SC_J_IMAGE_EXPLICIT", "SC_J_ENTRY_COMPUTED", "SC_J_ENTRY_CUSTOM"],
            "mutual_exclusion_groups": {"java_image": ["SC_J_IMAGE_DEFAULT", "SC_J_IMAGE_EXPLICIT"], "java_entrypoint": ["SC_J_ENTRY_COMPUTED", "SC_J_ENTRY_CUSTOM"]},
            "stub_guidance": [
                "Choose exactly one image scenario and one entrypoint scenario; the Cartesian combinations are candidates, not all semantically verified.",
                "For the default image, return a normal non-null Integer <= 8; 8 is one implementation witness, not the oracle requirement.",
                "For a custom entrypoint, the non-empty list may equal the asserted list; source category does not imply a different value.",
                "Treat getFrom call #1 and #2 separately; choosing stable responses opts into A_J_STABLE_FROM.",
            ],
            "runtime_vs_verification": [
                "runtime_expectations describe calls predicted under a selected path.",
                "verification_candidates remain optional candidate_only proposals and are not hidden Mock Assertions.",
            ],
            "blocking_unknown_refs": ["U_J_EXPLICIT_BUILDER"],
            "partial_unknown_refs": ["U_J_VERSION_NULL_OR_THROW", "U_J_DEPENDENCY_EXCEPTIONS", "U_J_UNFORMALIZED_ASSERTIONS"],
            "notes": ["Do not use the visible implementation-hidden UnsupportedOperationException bodies as semantic axioms.", "No production Mock, test edit, or whole-test result is generated."],
        },
    }


PY_ROWS = [
    ("PY_ROW_APP_404", "app_context_exists_404", True, "404", "()", "{}", "Aborted with 404", 12, 18),
    ("PY_ROW_APP_500", "app_context_exists_500_with_message", True, "500", '("Internal Server Error",)', "{}", "Aborted with 500", 19, 25),
    ("PY_ROW_NONE_403", "no_app_context_403", False, "403", "()", "{}", "Werkzeug abort 403", 26, 32),
    ("PY_ROW_NONE_400", "no_app_context_with_args_kwargs", False, "400", '("Bad Request",)', '{"description": "Invalid input"}', "Werkzeug abort 400", 33, 39),
    ("PY_ROW_APP_418", "app_context_custom_code", True, "418", "()", '{"description": "I\'m a teapot"}', "Aborted with 418", 40, 46),
]


def build_python(reviewed: ReviewedInput) -> dict[str, Any]:
    evidence_specs = [
        ("E_PY_MUT_BRANCH", "mut", 282, 285, "A non-None context invokes app.aborter, then normal return falls through to _wz_abort.", "Normal lookup, accessible attributes, call return/throw behavior, and external abort semantics remain assumptions or unknowns."),
        ("E_PY_IMPORTS", "imports", 11, 16, "_wz_abort and _cv_app are dependency names used by the MUT.", "Imports identify bindings, not runtime behavior."),
        ("E_PY_PARAM_HEADER", "test", 11, 11, "The visible parameter dimensions are scenario, has_app_context, code, args, and kwargs.", "Parameter names do not reveal hidden patch implementation."),
        ("E_PY_RAISES", "test", 101, 103, "Every parameter row requires some Exception from abort(code, *args, **kwargs).", "The broad type does not identify which dependency threw."),
        ("E_PY_APP_MESSAGE", "test", 111, 120, "Rows with has_app_context require an 'Aborted with {code}' substring.", "The message alone does not prove app.aborter was the exception source."),
        ("E_PY_NONE_MESSAGE", "test", 121, 126, "Rows without app context require a 'Werkzeug abort {code}' substring.", "The message alone does not establish complete external Werkzeug semantics."),
    ]
    for row_id, name, has_ctx, code, args, kwargs, message, start, end in PY_ROWS:
        evidence_specs.append((f"E_{row_id}", "test", start, end, f"Visible parameter row {name} with exact code, positional args, and keyword args.", "The row does not reveal hidden Mock responses or verification calls."))
    evidence = [_evidence(reviewed, *spec) for spec in evidence_specs]

    oracles = []
    for row_id, name, has_ctx, code, args, kwargs, message, _start, _end in PY_ROWS:
        oracles.append({
            "id": f"O_{row_id}",
            "parameter_row": row_id,
            "observation": f"abort({code}, *{args}, **{kwargs}) raises Exception and str(exc_info.value) contains the row-specific substring",
            "expected": f"Exception with message substring exactly {message!r}",
            "evidence_refs": [f"E_{row_id}", "E_PY_RAISES", "E_PY_APP_MESSAGE" if has_ctx else "E_PY_NONE_MESSAGE"],
            "formalization_status": "formalized",
        })

    bindings = []
    for row_id, _name, has_ctx, code, args, kwargs, _message, _start, _end in PY_ROWS:
        bindings.extend([
            {"id": f"C_{row_id}_LOOKUP", "receiver": "_cv_app", "method": "get", "arguments": ["None"], "call_ordinal": 1, "source_id": "mut", "start_line": 282, "end_line": 282, "resolution_status": "resolved"},
            {"id": f"C_{row_id}_ABORTER", "receiver": "ctx.app", "method": "aborter.__call__", "arguments": [code, f"*{args}", f"**{kwargs}"], "call_ordinal": 1, "source_id": "mut", "start_line": 283, "end_line": 283, "resolution_status": "partial"},
            {"id": f"C_{row_id}_FALLBACK", "receiver": "werkzeug.exceptions", "method": "abort", "arguments": [code, f"*{args}", f"**{kwargs}"], "call_ordinal": 1, "source_id": "mut", "start_line": 285, "end_line": 285, "resolution_status": "partial"},
        ])

    symbols = []
    for row_id, _name, _has_ctx, _code, _args, _kwargs, _message, _start, _end in PY_ROWS:
        symbols.extend([
            {"id": f"S_{row_id}_LOOKUP_NONE", "sort": "Bool", "role": "dependency_response", "controllable": True, "binding_ref": f"C_{row_id}_LOOKUP", "model_symbol": "ctx_none", "evidence_refs": ["E_PY_MUT_BRANCH"]},
            {"id": f"S_{row_id}_ABORTER_THROWS", "sort": "Bool", "role": "dependency_response", "controllable": True, "binding_ref": f"C_{row_id}_ABORTER", "model_symbol": "abort_throws", "evidence_refs": ["E_PY_MUT_BRANCH"]},
            {"id": f"S_{row_id}_ABORT_MESSAGE_MATCHES", "sort": "Bool", "role": "dependency_response", "controllable": True, "binding_ref": f"C_{row_id}_ABORTER", "model_symbol": "abort_message_matches", "evidence_refs": ["E_PY_APP_MESSAGE"]},
            {"id": f"S_{row_id}_FALLBACK_THROWS", "sort": "Bool", "role": "dependency_response", "controllable": True, "binding_ref": f"C_{row_id}_FALLBACK", "model_symbol": "fallback_throws", "evidence_refs": ["E_PY_MUT_BRANCH"]},
            {"id": f"S_{row_id}_FALLBACK_MESSAGE_MATCHES", "sort": "Bool", "role": "dependency_response", "controllable": True, "binding_ref": f"C_{row_id}_FALLBACK", "model_symbol": "fallback_message_matches", "evidence_refs": ["E_PY_NONE_MESSAGE"]},
            {"id": f"S_{row_id}_ABORT_CALLED", "sort": "Bool", "role": "execution_state", "controllable": False, "binding_ref": f"C_{row_id}_ABORTER", "model_symbol": "abort_called", "evidence_refs": ["E_PY_MUT_BRANCH"]},
            {"id": f"S_{row_id}_FALLBACK_REACHED", "sort": "Bool", "role": "execution_state", "controllable": False, "binding_ref": f"C_{row_id}_FALLBACK", "model_symbol": "fallback_called", "evidence_refs": ["E_PY_MUT_BRANCH"]},
            {"id": f"S_{row_id}_EXCEPTION_OBSERVED", "sort": "Bool", "role": "oracle_observation", "controllable": False, "binding_ref": None, "model_symbol": "exception_observed", "evidence_refs": ["E_PY_RAISES"]},
            {"id": f"S_{row_id}_MESSAGE_OK", "sort": "Bool", "role": "oracle_observation", "controllable": False, "binding_ref": None, "model_symbol": "message_observed_matches", "evidence_refs": ["E_PY_APP_MESSAGE" if "_APP_" in row_id else "E_PY_NONE_MESSAGE"]},
        ])

    def syms(suffix: str) -> list[str]:
        return [f"S_{row[0]}_{suffix}" for row in PY_ROWS]

    app_rows = [row for row in PY_ROWS if row[2]]
    no_app_rows = [row for row in PY_ROWS if not row[2]]
    formulas_data = [
        ("F_PY_ALL_ORACLES", "(assert (and " + " ".join(f"(and S_{row[0]}_EXCEPTION_OBSERVED S_{row[0]}_MESSAGE_OK)" for row in PY_ROWS) + "))", "oracle_target", syms("EXCEPTION_OBSERVED") + syms("MESSAGE_OK"), ["E_PY_RAISES", "E_PY_APP_MESSAGE", "E_PY_NONE_MESSAGE"]),
        ("F_PY_NONVACUOUS_OBSERVATION", "(assert (and " + " ".join(f"S_{row[0]}_EXCEPTION_OBSERVED" for row in PY_ROWS) + "))", "non_vacuity", syms("EXCEPTION_OBSERVED"), ["E_PY_RAISES"]),
    ]
    for row_id, _name, has_ctx, _code, _args, _kwargs, _message, _start, _end in PY_ROWS:
        if has_ctx:
            formulas_data.extend([
                (f"F_{row_id}_ABORTER_THROW", f"(assert (and (not S_{row_id}_LOOKUP_NONE) S_{row_id}_ABORTER_THROWS))", "row_local_scenario_guard", [f"S_{row_id}_LOOKUP_NONE", f"S_{row_id}_ABORTER_THROWS"], ["E_PY_MUT_BRANCH", f"E_{row_id}"]),
                (f"F_{row_id}_NO_FALLBACK_THROW", f"(assert (not S_{row_id}_FALLBACK_REACHED))", "row_local_runtime_relation", [f"S_{row_id}_FALLBACK_REACHED"], ["E_PY_MUT_BRANCH", f"E_{row_id}"]),
                (f"F_{row_id}_ABORTER_RETURN", f"(assert (and (not S_{row_id}_LOOKUP_NONE) (not S_{row_id}_ABORTER_THROWS)))", "row_local_scenario_guard", [f"S_{row_id}_LOOKUP_NONE", f"S_{row_id}_ABORTER_THROWS"], ["E_PY_MUT_BRANCH", f"E_{row_id}"]),
                (f"F_{row_id}_FALLBACK_AFTER_RETURN", f"(assert S_{row_id}_FALLBACK_REACHED)", "row_local_runtime_relation", [f"S_{row_id}_FALLBACK_REACHED"], ["E_PY_MUT_BRANCH", f"E_{row_id}"]),
            ])
        else:
            formulas_data.extend([
                (f"F_{row_id}_LOOKUP_NONE", f"(assert S_{row_id}_LOOKUP_NONE)", "row_local_scenario_guard", [f"S_{row_id}_LOOKUP_NONE"], ["E_PY_MUT_BRANCH", f"E_{row_id}"]),
                (f"F_{row_id}_FALLBACK_REACHED", f"(assert S_{row_id}_FALLBACK_REACHED)", "row_local_runtime_relation", [f"S_{row_id}_FALLBACK_REACHED"], ["E_PY_MUT_BRANCH", f"E_{row_id}"]),
            ])
    formulas = [{"id": fid, "format": "smtlib2", "text": text, "role": role, "symbol_refs": refs, "evidence_refs": erefs, "support_status": "modeled"} for fid, text, role, refs, erefs in formulas_data]

    scenarios = []
    for row_id, name, has_ctx, _code, _args, _kwargs, _message, _start, _end in PY_ROWS:
        oracle_refs = [f"O_{row_id}"]
        group = f"python_control_path_{row_id}"
        if has_ctx:
            scenarios.extend([
                {"id": f"SC_{row_id}_ABORTER_THROW", "title": f"{name}: context aborter throws", "alternative_group": group, "condition": _condition(f"F_{row_id}_ABORTER_THROW"), "oracle_refs": oracle_refs, "assumption_refs": ["A_PY_NORMAL_LOOKUP", "A_PY_ACCESSIBLE_ABORTER"], "unknown_refs": ["U_PY_DYNAMIC_FAILURES", "U_PY_EXCEPTION_SOURCE"], "status": "partial"},
                {"id": f"SC_{row_id}_ABORTER_RETURN", "title": f"{name}: aborter returns and fallback throws", "alternative_group": group, "condition": _condition(f"F_{row_id}_ABORTER_RETURN"), "oracle_refs": oracle_refs, "assumption_refs": ["A_PY_NORMAL_LOOKUP", "A_PY_ACCESSIBLE_ABORTER"], "unknown_refs": ["U_PY_DYNAMIC_FAILURES", "U_PY_FALLBACK_SEMANTICS", "U_PY_EXCEPTION_SOURCE"], "status": "partial"},
            ])
        else:
            scenarios.append({"id": f"SC_{row_id}_LOOKUP_NONE", "title": f"{name}: lookup None and fallback throws", "alternative_group": group, "condition": _condition(f"F_{row_id}_LOOKUP_NONE"), "oracle_refs": oracle_refs, "assumption_refs": ["A_PY_NORMAL_LOOKUP"], "unknown_refs": ["U_PY_DYNAMIC_FAILURES", "U_PY_FALLBACK_SEMANTICS"], "status": "partial"})

    rules = []
    for row_id, _name, has_ctx, code, args, kwargs, message, _start, _end in PY_ROWS:
        oracle_ref = f"O_{row_id}"
        if not has_ctx:
            scenario = f"SC_{row_id}_LOOKUP_NONE"
            guard = f"F_{row_id}_LOOKUP_NONE"
            rules.extend([
                (f"R_{row_id}_LOOKUP_NONE", [scenario], guard, f"C_{row_id}_LOOKUP", "return", "None", [], "scenario_local_obligation", [oracle_ref]),
                (f"R_{row_id}_FALLBACK_THROW", [scenario], guard, f"C_{row_id}_FALLBACK", "throw", f"Exception containing {message!r}", [], "implementation_choice", [oracle_ref]),
            ])
        else:
            throw_scenario = f"SC_{row_id}_ABORTER_THROW"
            return_scenario = f"SC_{row_id}_ABORTER_RETURN"
            throw_guard = f"F_{row_id}_ABORTER_THROW"
            return_guard = f"F_{row_id}_ABORTER_RETURN"
            rules.extend([
                (f"R_{row_id}_LOOKUP_CONTEXT_THROW", [throw_scenario], throw_guard, f"C_{row_id}_LOOKUP", "return", "non-None context", [], "scenario_local_obligation", [oracle_ref]),
                (f"R_{row_id}_ABORTER_THROW", [throw_scenario], throw_guard, f"C_{row_id}_ABORTER", "throw", f"Exception containing {message!r}", [], "implementation_choice", [oracle_ref]),
                (f"R_{row_id}_LOOKUP_CONTEXT_RETURN", [return_scenario], return_guard, f"C_{row_id}_LOOKUP", "return", "non-None context", [], "scenario_local_obligation", [oracle_ref]),
                (f"R_{row_id}_ABORTER_RETURN", [return_scenario], return_guard, f"C_{row_id}_ABORTER", "return", "normal return", [], "implementation_choice", [oracle_ref]),
                (f"R_{row_id}_FALLBACK_AFTER_RETURN", [return_scenario], return_guard, f"C_{row_id}_FALLBACK", "throw", f"Exception containing {message!r}", [], "implementation_choice", [oracle_ref]),
            ])
    response_rules = [{
        "id": rid, "scenario_refs": srefs, "condition": _condition(formula), "call_binding": binding,
        "reaction": {"kind": kind, "value": value, "constraint_refs": constraints},
        "logical_role": logical_role, "oracle_refs": orefs, "formula_refs": constraints,
        "evidence_refs": ["E_PY_MUT_BRANCH"],
        "check_refs": [], "whole_test_verified": False,
    } for rid, srefs, formula, binding, kind, value, constraints, logical_role, orefs in rules]
    for rule in response_rules:
        rule["check_refs"] = [f"K_{rule['scenario_refs'][0][3:]}"]

    expectations = []
    for row_id, _name, has_ctx, code, args, kwargs, _message, _start, _end in PY_ROWS:
        if has_ctx:
            expectations.extend([
                {"id": f"X_{row_id}_ABORTER_THROW", "scenario_refs": [f"SC_{row_id}_ABORTER_THROW"], "condition": _condition(f"F_{row_id}_ABORTER_THROW"), "binding_ref": f"C_{row_id}_ABORTER", "expectation": f"called with positional [{code}, *{args}] and keyword {kwargs}; throws before fallback", "evidence_refs": ["E_PY_MUT_BRANCH", f"E_{row_id}"], "logical_role": "runtime_expectation"},
                {"id": f"X_{row_id}_FALLBACK_RETURN", "scenario_refs": [f"SC_{row_id}_ABORTER_RETURN"], "condition": _condition(f"F_{row_id}_ABORTER_RETURN"), "binding_ref": f"C_{row_id}_FALLBACK", "expectation": f"called with positional [{code}, *{args}] and keyword {kwargs} after aborter returns", "evidence_refs": ["E_PY_MUT_BRANCH", f"E_{row_id}"], "logical_role": "runtime_expectation"},
            ])
        else:
            expectations.append({"id": f"X_{row_id}_FALLBACK_NONE", "scenario_refs": [f"SC_{row_id}_LOOKUP_NONE"], "condition": _condition(f"F_{row_id}_LOOKUP_NONE"), "binding_ref": f"C_{row_id}_FALLBACK", "expectation": f"called with positional [{code}, *{args}] and keyword {kwargs} after lookup returns None", "evidence_refs": ["E_PY_MUT_BRANCH", f"E_{row_id}"], "logical_role": "runtime_expectation"})
    verification_candidates = [{
        "id": "V_" + item["id"][2:], "scenario_refs": item["scenario_refs"], "condition": item["condition"],
        "expectation_ref": item["id"], "proposal": f"Consider verifying only for the selected path: {item['expectation']}",
        "behavior_protected": "Would distinguish which external abort path handled the parameter row, but the visible message oracle alone does not require this verification.",
        "evidence_refs": item["evidence_refs"], "status": "candidate_only", "whole_test_verified": False,
    } for item in expectations]

    assumptions = [
        {"id": "A_PY_NORMAL_LOOKUP", "statement": "_cv_app.get(None) returns normally for the modeled paths.", "scope": "all row-local Python scenarios", "evidence_refs": ["E_PY_MUT_BRANCH"], "blocks_semantic_proof": True},
        {"id": "A_PY_ACCESSIBLE_ABORTER", "statement": "For a non-None context, ctx.app.aborter attribute lookup succeeds.", "scope": "row-local ABORTER_THROW and ABORTER_RETURN scenarios", "evidence_refs": ["E_PY_MUT_BRANCH"], "blocks_semantic_proof": True},
    ]
    unknowns = [
        {"id": "U_PY_DYNAMIC_FAILURES", "statement": "Lookup and dynamic attribute access can have exceptions or behavior outside the modeled row-local paths.", "impact": "The scenario set is not a complete enumeration of Python runtime behavior and blocks automatic consumption.", "evidence_refs": ["E_PY_MUT_BRANCH", "E_PY_IMPORTS"], "blocks_consumption": True},
        {"id": "U_PY_FALLBACK_SEMANTICS", "statement": "The reviewed source exposes only the imported _wz_abort binding, not its implementation behavior.", "impact": "Fallback exception/message rules are explicit implementation choices and require human review before consumption.", "evidence_refs": ["E_PY_IMPORTS", "E_PY_NONE_MESSAGE"], "blocks_consumption": True},
        {"id": "U_PY_EXCEPTION_SOURCE", "statement": "The app-context message can arise from app.aborter or from fallback after normal aborter return.", "impact": "The visible oracle does not select one of the two app-context scenarios.", "evidence_refs": ["E_PY_MUT_BRANCH", "E_PY_APP_MESSAGE"], "blocks_consumption": False},
        {"id": "U_PY_HIDDEN_VERIFY_INTENT", "statement": "Masked verification slots reveal no required call-count or no-call assertions.", "impact": "Interaction assertions remain candidates only.", "evidence_refs": ["E_PY_RAISES", "E_PY_APP_MESSAGE", "E_PY_NONE_MESSAGE"], "blocks_consumption": False},
    ]

    return {
        "schema_version": SCHEMA_VERSION, "identity": _identity(reviewed),
        "coverage": {"scope": "All five visible parameter rows and exact exception-message substring oracles; finite normal-lookup path abstraction only.", "formalized_oracle_refs": [item["id"] for item in oracles], "unformalized_oracle_refs": [], "whole_test_verified": False},
        "sources": _sources(reviewed), "evidence": evidence, "oracles": oracles,
        "symbols": symbols, "bindings": bindings, "formulas": formulas,
        "scenarios": scenarios, "response_rules": response_rules,
        "runtime_expectations": expectations, "verification_candidates": verification_candidates,
        "assumptions": assumptions, "unknowns": unknowns,
        "checks": [
            {
                "id": f"K_{scenario['id'][3:]}",
                "kind": "candidate_checking",
                "formula_refs": (
                    [scenario["condition"]["formula_ref"], f"F_{scenario['id'][3:-len('_LOOKUP_NONE')]}_FALLBACK_REACHED"]
                    if scenario["id"].endswith("_LOOKUP_NONE")
                    else [scenario["condition"]["formula_ref"], f"F_{scenario['id'][3:-len('_ABORTER_THROW')]}_NO_FALLBACK_THROW"]
                    if scenario["id"].endswith("_ABORTER_THROW")
                    else [scenario["condition"]["formula_ref"], f"F_{scenario['id'][3:-len('_ABORTER_RETURN')]}_FALLBACK_AFTER_RETURN"]
                ),
                "scenario_refs": [scenario["id"]],
                "result": "not_run",
                "performed_by": "not_run",
                "details": "Proposed row-local check only; actual evidence belongs to the host PoC sidecar.",
                "semantic_claim": False,
            }
            for scenario in scenarios
        ],
        "consumption": {
            "selection_mode": "human_review_only",
            "selectable_scenarios": [scenario["id"] for scenario in scenarios],
            "mutual_exclusion_groups": {
                f"python_control_path_{row[0]}": [
                    scenario["id"] for scenario in scenarios
                    if scenario["alternative_group"] == f"python_control_path_{row[0]}"
                ]
                for row in PY_ROWS
            },
            "stub_guidance": [
                "For has_app_context=False rows choose lookup None and a fallback throw carrying the exact row-specific message substring.",
                "For has_app_context=True rows retain both alternatives: aborter throws, or aborter returns normally and fallback throws the same required substring.",
                "Preserve every row's code, *args, and **kwargs binding; do not merge rows into an untraceable generic call.",
                "The branch test is identity against None; non-None falsey objects still enter the app-context branch.",
            ],
            "runtime_vs_verification": ["Path-specific calls are runtime expectations.", "Call verification remains candidate_only because the visible oracle does not require a particular exception source."],
            "blocking_unknown_refs": ["U_PY_DYNAMIC_FAILURES", "U_PY_FALLBACK_SEMANTICS"],
            "partial_unknown_refs": ["U_PY_EXCEPTION_SOURCE", "U_PY_HIDDEN_VERIFY_INTENT"],
            "notes": ["Machine consumption is disabled; scenarios are selectable for human review only.", "No exact exception class is introduced beyond pytest.raises(Exception).", "No hidden slot content, reference Mock, archive candidate, or score is used."],
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, default=HERE / "examples")
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    examples = (
        ("java.contract.json", load_java(), build_java, JAVA_ORACLE_IDS, set()),
        (
            "python.contract.json",
            load_python(),
            build_python,
            {f"O_{row[0]}" for row in PY_ROWS},
            {row[0] for row in PY_ROWS},
        ),
    )
    for filename, reviewed, builder, expected_oracles, expected_rows in examples:
        contract = builder(reviewed)
        validate_contract(
            contract,
            reviewed,
            expected_oracle_ids=expected_oracles,
            expected_parameter_rows=expected_rows,
        )
        path = args.output_dir / filename
        path.write_text(json.dumps(contract, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
