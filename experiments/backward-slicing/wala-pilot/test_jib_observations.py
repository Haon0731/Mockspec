from __future__ import annotations

import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_jib_skeleton import build
from summarize_jib_observations import (
    _jib_dispatcher_witness, _resolved_call_to_fact,
    _typed_constructor_link_checks, classify, observed_expression_witness,
    revalidate_source_adapter, source_to_setter_witness,
)


def row(method: str, instruction: str, *, mods=(), refs=()):
    return {"method": method, "instruction": instruction, "mod_regions": list(mods),
            "ref_regions": list(refs), "context": "Everywhere", "kind": "NORMAL",
            "loader": "Extension", "statement": instruction}


def observation():
    builder = "builder.baseImage@same-region"
    plan = "plan.baseImage@same-region"
    return {"criterion": "ObservationSink.baseImage(Ljava/lang/Object;)V",
            "slice_statement_count": 100, "retained_statement_count": 12,
            "statements": [
                row("ContainerBuildPlan$Builder.<init>()V",
                    "putfield 1.< X, ContainerBuildPlan$Builder, baseImage, String > = 2", mods=(builder,)),
                row("ContainerBuildPlan$Builder.setBaseImage(String)",
                    "putfield 1.< X, ContainerBuildPlan$Builder, baseImage, String > = 2", mods=(builder,)),
                row("ContainerBuildPlan$Builder.setLabels(Map)",
                    "putfield 1.< X, ContainerBuildPlan$Builder, labels, Map > = 2", mods=("builder.labels",)),
                row("ContainerBuildPlan$Builder.build()",
                    "3 = getfield < X, ContainerBuildPlan$Builder, baseImage, String > 1", refs=(builder,)),
                row("ContainerBuildPlan.<init>(String)",
                    "putfield 1.< X, ContainerBuildPlan, baseImage, String > = 2", mods=(plan,)),
                row("ContainerBuildPlan.getBaseImage()",
                    "3 = getfield < X, ContainerBuildPlan, baseImage, String > 1", refs=(plan,)),
                dict(row("JarFiles.toJibContainerBuilder()", "invoke CommonContainerConfigCliOptions, getFrom"),
                     declared_target="com.google.cloud.tools.jib.cli.CommonContainerConfigCliOptions.getFrom()Optional"),
                dict(row("JarFiles.toJibContainerBuilder()", "invoke ArtifactProcessor, getJavaVersion"),
                     declared_target="com.google.cloud.tools.jib.cli.ArtifactProcessor.getJavaVersion()Integer"),
            ]}


def expected():
    return {"id": "criterion:0", "line": 71, "end_line": 71, "observed_line": 71,
            "expression": "buildPlan.getBaseImage()", "criterion": "baseImage", "field": "baseImage",
            "source_calls": ["CommonContainerConfigCliOptions.getFrom",
                             "ArtifactProcessor.getJavaVersion"]}


def test_exact_field_chain_excludes_unrelated_and_overwritten_writes():
    result = classify(observation(), expected())
    assert result["status"] == "state_candidates_only"
    assert result["state_evidence"]["candidate_write_methods"] == [
        "ContainerBuildPlan$Builder.<init>()V",
        "ContainerBuildPlan$Builder.setBaseImage(String)"]
    assert result["state_evidence"]["overwritten_write_methods"] == []
    assert result["excluded_from_state_support"]["count"] == 1
    assert all(item["in_ci_slice_only"] for item in result["source_boundaries"])
    assert all(item["dependency_status"] == "unknown" for item in result["source_boundaries"])


def test_generic_may_reach_or_wrong_field_cannot_satisfy_criterion():
    value = observation()
    value["statements"] = [item for item in value["statements"]
                           if "setBaseImage" not in item["method"] and "Builder.<init>" not in item["method"]]
    result = classify(value, expected())
    assert result["status"] == "unknown"
    assert "candidate builder field write" in result["missing"]


def test_deleted_selected_region_edge_invalidates_support():
    value = observation()
    for item in value["statements"]:
        if "setBaseImage" in item["method"]:
            item["mod_regions"] = ["different-context.baseImage"]
        if "Builder.<init>" in item["method"]:
            item["mod_regions"] = ["different-context.defaultBaseImage"]
    result = classify(value, expected())
    assert result["status"] == "unknown"
    assert "builder write/read region intersection" in result["missing"]


def test_empty_native_return_region_stays_unknown_despite_default_field_chain():
    value = observation()
    source = next(item for item in value["statements"] if "getFrom" in item["instruction"])
    source.update({"call_site": "@1", "declared_target": "com.google.cloud.tools.jib.cli.CommonContainerConfigCliOptions.getFrom()Optional",
                   "result_points_to": {"value_number": 8, "pointer_key": "local:v8",
                                        "points_to_count": 0, "instance_keys": []}})
    result = classify(value, expected())
    boundary = next(item for item in result["source_boundaries"] if item["call"].endswith("getFrom"))
    assert boundary["empty_return_region"] is True
    assert boundary["dependency_status"] == "unknown"
    assert result["status"] == "state_candidates_only"


def test_deleted_unknown_return_call_is_not_silently_replaced_by_other_slice_members():
    value = observation()
    value["statements"] = [item for item in value["statements"] if "getFrom" not in item["instruction"]]
    result = classify(value, expected())
    boundary = next(item for item in result["source_boundaries"] if item["call"].endswith("getFrom"))
    assert boundary["in_ci_slice_only"] is False
    assert boundary["dependency_status"] == "unknown"


def test_wrong_getter_cannot_satisfy_observed_expression_witness():
    rows = [{"method": "JibObservationEntry.observe", "declared_target": "ContainerBuildPlan.getLabels()Map",
             "definitions": [10], "uses": [1], "possible_target_details": [
                 {"method": "ContainerBuildPlan.getLabels()Map", "has_ir": True}]},
            {"method": "JibObservationEntry.observe", "declared_target": "ObservationSink.baseImage(Object)V",
             "definitions": [], "uses": [10], "possible_target_details": []}]
    result = observed_expression_witness(
        {"statements": rows, "opaque_return_edges": [], "expanded_seeds": []},
        expected(), "ObservationSink.baseImage(Object)V")
    assert result["status"] == "missing"


def test_opaque_expression_requires_every_edge_and_exact_getter_seed():
    getter_target = "com.google.cloud.tools.jib.api.buildplan.ContainerBuildPlan.getLayers()Ljava/util/List;"
    list_target = "java.util.List.get(I)Ljava/lang/Object;"
    name_target = "com.google.cloud.tools.jib.api.buildplan.LayerObject.getName()Ljava/lang/String;"
    sink_target = "ObservationSink.layer0Name(Ljava/lang/Object;)V"
    rows = [
        {"method": "JibObservationEntry.observe", "instruction_index": 79,
         "call_site": "@79", "declared_target": getter_target, "definitions": [10], "uses": [1],
         "possible_target_details": [{"method": getter_target, "has_ir": True}]},
        {"method": "JibObservationEntry.observe", "instruction_index": 81,
         "call_site": "@81", "declared_target": list_target, "definitions": [20], "uses": [10, 2],
         "result_points_to": {"points_to_count": 1},
         "possible_target_details": [{"method": "java.util.ArrayList.get(I)Object", "has_ir": True}]},
        {"method": "JibObservationEntry.observe", "instruction_index": 83,
         "call_site": "@83", "declared_target": name_target, "definitions": [30], "uses": [99],
         "result_points_to": {"points_to_count": 0},
         "possible_target_details": []},
        {"method": "JibObservationEntry.observe", "instruction_index": 84,
         "call_site": "@84", "declared_target": sink_target, "definitions": [], "uses": [30],
         "possible_target_details": []},
    ]
    for item in rows:
        item["context"] = "Everywhere"
    edges = [
        {"caller": "JibObservationEntry.observe", "context": "Everywhere",
         "instruction_index": 83, "call_site": "@83", "declared_target": name_target,
         "result_value": 30, "traversed": True},
    ]
    value = {"statements": rows, "opaque_return_edges": edges,
             "expanded_seeds": [{"instruction_index": 81, "call_site": "@81",
                                  "declared_target": list_target,
                                  "method": "JibObservationEntry.observe", "context": "Everywhere",
                                  "result_value": 20, "input_values": [10, 2],
                                  "origin_edge_indexes": [0]}],
             "entry_ssa_traversal_steps": [
                 {"instruction_index": 83, "call_site": "@83", "declared_target": name_target,
                  "definitions": [30], "uses": [99], "origin_edge_indexes": []},
                 {"instruction_index": 82, "instruction": "99 = checkcast 20",
                  "definitions": [99], "uses": [20], "origin_edge_indexes": [0]},
                 {"instruction_index": 81, "call_site": "@81", "declared_target": list_target,
                  "definitions": [20], "uses": [10, 2], "origin_edge_indexes": [0]},
             ],
             "opaque_expansion_truncated": False}
    layer = dict(expected(), criterion="layer0Name", field="layers")
    result = observed_expression_witness(value, layer, sink_target)
    assert result["status"] == "complete_with_opaque_may"
    assert result["opaque_edge_indexes"] == [0]

    value["entry_ssa_traversal_steps"][1]["uses"] = [777]
    assert observed_expression_witness(value, layer, sink_target)["status"] != "complete_with_opaque_may"
    value["entry_ssa_traversal_steps"][1]["uses"] = [20]

    value["opaque_return_edges"] = [{**edges[0], "traversed": False}]
    result = observed_expression_witness(value, layer, sink_target)
    assert result["status"] != "complete_with_opaque_may"

    value["opaque_return_edges"] = edges
    value["expanded_seeds"][0]["call_site"] = "@wrong"
    assert observed_expression_witness(value, layer, sink_target)["status"] != "complete_with_opaque_may"
    value["expanded_seeds"][0]["call_site"] = "@81"
    value["expanded_seeds"][0]["origin_edge_indexes"] = []
    assert observed_expression_witness(value, layer, sink_target)["status"] != "complete_with_opaque_may"


def test_direct_empty_pa_getter_requires_its_opaque_edge_and_seed():
    getter_target = "com.google.cloud.tools.jib.api.buildplan.ContainerBuildPlan.getUser()Ljava/lang/String;"
    sink_target = "ObservationSink.user(Ljava/lang/Object;)V"
    getter = {"method": "JibObservationEntry.observe", "instruction_index": 45,
              "context": "Everywhere",
              "call_site": "@45", "declared_target": getter_target, "definitions": [10], "uses": [1],
              "result_points_to": {"points_to_count": 0},
              "possible_target_details": [{"method": getter_target, "has_ir": True}]}
    sink = {"method": "JibObservationEntry.observe", "instruction_index": 46,
            "context": "Everywhere",
            "call_site": "@46", "declared_target": sink_target, "definitions": [], "uses": [10],
            "possible_target_details": []}
    edge = {"caller": "JibObservationEntry.observe", "context": "Everywhere",
            "instruction_index": 45, "call_site": "@45", "declared_target": getter_target,
            "result_value": 10, "traversed": True}
    value = {"statements": [getter, sink], "opaque_return_edges": [edge],
             "expanded_seeds": [{"instruction_index": 45, "call_site": "@45",
                                  "declared_target": getter_target,
                                  "method": "JibObservationEntry.observe", "context": "Everywhere",
                                  "result_value": 10, "input_values": [1],
                                  "origin_edge_indexes": [0]}],
             "opaque_expansion_truncated": False}
    user = dict(expected(), criterion="user", field="user")
    assert observed_expression_witness(value, user, sink_target)["status"] == "complete_with_opaque_may"
    value["opaque_return_edges"][0]["traversed"] = False
    assert observed_expression_witness(value, user, sink_target)["status"] == "unknown_boundary"


def test_truncated_opaque_expansion_fails_closed():
    getter_target = "com.google.cloud.tools.jib.api.buildplan.ContainerBuildPlan.getLayers()Ljava/util/List;"
    sink_target = "ObservationSink.layers(Ljava/lang/Object;)V"
    rows = [
        {"method": "JibObservationEntry.observe", "instruction_index": 1,
         "call_site": "@1", "declared_target": getter_target, "definitions": [10], "uses": [1],
         "possible_target_details": [{"method": getter_target, "has_ir": True}]},
        {"method": "JibObservationEntry.observe", "instruction_index": 2,
         "call_site": "@2", "declared_target": sink_target, "definitions": [], "uses": [10],
         "possible_target_details": []},
    ]
    value = {"statements": rows, "opaque_return_edges": [], "expanded_seeds": [],
             "opaque_expansion_truncated": True}
    layer = dict(expected(), criterion="layers", field="layers")
    assert observed_expression_witness(value, layer, sink_target)["status"] == "unknown_boundary"


def test_source_boundary_does_not_match_same_method_name_on_plan_owner():
    value = observation()
    value["statements"].append({
        "method": "JibObservationEntry.observe(CommonContainerConfigCliOptions)",
        "instruction": "invoke ContainerBuildPlan.getCreationTime",
        "declared_target": "com.google.cloud.tools.jib.api.buildplan.ContainerBuildPlan.getCreationTime()Instant",
        "uses": [1], "definitions": [2], "mod_regions": [], "ref_regions": [],
        "context": "Everywhere", "kind": "NORMAL", "loader": "Application", "statement": "wrong owner"})
    creation = dict(expected(), criterion="creationTime", field="creationTime",
                    source_calls=["CommonContainerConfigCliOptions.getCreationTime"])
    result = classify(value, creation)
    assert result["source_boundaries"][0]["in_ci_slice_only"] is False


def test_source_boundary_does_not_match_same_simple_class_in_other_package():
    value = observation()
    source = next(item for item in value["statements"] if "getFrom" in item["instruction"])
    source["declared_target"] = "other.package.CommonContainerConfigCliOptions.getFrom()Optional"
    result = classify(value, expected())
    boundary = next(item for item in result["source_boundaries"] if item["call"].endswith(".getFrom"))
    assert boundary["in_ci_slice_only"] is False


def test_arbitrary_container_builders_is_not_reported_as_reviewed(tmp_path, monkeypatch):
    jar_files = tmp_path / "JarFiles.java"
    container = tmp_path / "ContainerBuilders.java"
    jar_files.write_text("class JarFiles {}", encoding="utf-8")
    container.write_text("class ContainerBuilders {}", encoding="utf-8")
    monkeypatch.setattr("build_jib_skeleton.subprocess.run",
                        lambda *args, **kwargs: SimpleNamespace(returncode=1, stdout=b"", stderr=b"x"))
    receipt = build(jar_files, "", tmp_path / "output", container)
    mapping = next(item for item in receipt["mappings"]
                   if item["generated_path"].endswith("ContainerBuilders.java"))
    assert mapping["origin"] == "provided_source"
    assert mapping["provenance"] is None


def test_mismatched_authenticated_source_fails_closed(tmp_path):
    jar_files = tmp_path / "JarFiles.java"
    container = tmp_path / "ContainerBuilders.java"
    jar_files.write_text("class JarFiles {}", encoding="utf-8")
    container.write_text("class ContainerBuilders {}", encoding="utf-8")
    provenance = {"source_id": "repository:x", "logical_path": "src/ContainerBuilders.java",
                  "sha256": "0" * 64, "snapshot_id": "snapshot", "case_id": "case"}
    with pytest.raises(ValueError, match="hash mismatch"):
        build(jar_files, "", tmp_path / "output", container, provenance)


def test_multiline_assertions_keep_original_criterion_identity():
    data = json.loads((Path(__file__).parent / "jib_observation_expectations.json").read_text())
    assert [(item["id"], item["line"], item["end_line"], item["observed_line"])
            for item in data["observations"][-2:]] == [
                ("criterion:17", 88, 96, 96), ("criterion:18", 97, 99, 99)]


def source_setter_raw(*, wrong_actual=False, wrong_field=False, conditional=False):
    wrapper_method = "com.google.cloud.tools.jib.api.JibContainerBuilder.setEnvironment(Map)Builder"
    builder_method = "com.google.cloud.tools.jib.api.buildplan.ContainerBuildPlan$Builder.setEnvironment(Map)Builder"
    wrapper_call_block = 1
    blocks = ([{"number": 0, "entry": True, "normal_successor_blocks": [1, 2]},
               {"number": 1, "normal_successor_blocks": [3]},
               {"number": 2, "normal_successor_blocks": [3]},
               {"number": 3, "exit": True, "normal_successor_blocks": []}]
              if conditional else
              [{"number": 0, "entry": True, "normal_successor_blocks": [1]},
               {"number": 1, "normal_successor_blocks": [2]},
               {"number": 2, "exit": True, "normal_successor_blocks": []}])
    mut_rows = [
        {"instruction_index": 1, "basic_block": 0,
         "declared_target": "com.google.cloud.tools.jib.cli.CommonContainerConfigCliOptions.getEnvironment()Map",
         "definitions": [10], "uses": [4], "possible_target_details": []},
        {"instruction_index": 2, "basic_block": wrapper_call_block,
         "declared_target": wrapper_method, "definitions": [11],
         "uses": [5, 99 if wrong_actual else 10],
         "possible_target_details": [{"method": wrapper_method, "context": "Everywhere", "has_ir": True}]},
        {"instruction_index": 3, "basic_block": 1 if conditional else 1,
         "instruction": "return 11", "definitions": [], "uses": [11]},
    ]
    if conditional:
        mut_rows.append({"instruction_index": 4, "basic_block": 2,
                         "instruction": "return 12", "definitions": [], "uses": [12]})
    return {"method_ssa_facts": [
        {"method": "com.google.cloud.tools.jib.cli.jar.JarFiles.toJibContainerBuilder(...)Builder",
         "context": "Everywhere", "parameter_value_numbers": [1, 2, 3, 4, 5],
         "cfg_blocks": blocks, "instructions": mut_rows, "phi_instructions": []},
        {"method": wrapper_method, "context": "Everywhere", "parameter_value_numbers": [1, 2],
         "cfg_blocks": [], "phi_instructions": [], "instructions": [
             {"instruction_index": 1, "declared_target": builder_method,
              "definitions": [5], "uses": [4, 2],
              "possible_target_details": [{"method": builder_method, "context": "Everywhere", "has_ir": True}]},
         ]},
        {"method": builder_method, "context": "Everywhere", "parameter_value_numbers": [1, 2],
         "cfg_blocks": [], "phi_instructions": [], "instructions": [
             {"instruction_index": 1, "instruction": "4 = new HashMap", "definitions": [4], "uses": []},
             {"instruction_index": 2,
              "declared_target": "java.util.HashMap.<init>(Ljava/util/Map;)V",
              "instruction": "invokespecial HashMap.<init> 4,2", "definitions": [5], "uses": [4, 2]},
             {"instruction_index": 3,
              "instruction": ("putfield Builder, wrong, Map = 4" if wrong_field
                              else "putfield < X, ContainerBuildPlan$Builder, environment, Map > = 4"),
              "definitions": [], "uses": [1, 4]},
         ]},
    ]}


def test_source_return_reaches_exact_setter_field_through_copy_constructor():
    environment = dict(expected(), criterion="environmentSize", field="environment",
                       source_calls=["CommonContainerConfigCliOptions.getEnvironment"])
    witness = source_to_setter_witness(source_setter_raw(), environment)
    assert witness["status"] == "complete"
    assert witness["builder_formal_to_field_write"]["status"] == "analysis_may_copy"
    assert witness["overwrite"]["status"] == "setter_must_execute_on_normal_return"


def test_source_setter_witness_fails_on_wrong_actual_or_field_and_bounds_overwrite():
    environment = dict(expected(), criterion="environmentSize", field="environment",
                       source_calls=["CommonContainerConfigCliOptions.getEnvironment"])
    assert source_to_setter_witness(source_setter_raw(wrong_actual=True), environment)["status"] == "incomplete"
    assert source_to_setter_witness(source_setter_raw(wrong_field=True), environment)["status"] == "incomplete"
    conditional = source_to_setter_witness(source_setter_raw(conditional=True), environment)
    assert conditional["status"] == "complete"
    assert conditional["overwrite"]["status"] == "conditional_or_not_established"


def test_unreviewed_constructor_name_cannot_supply_copy_effect():
    environment = dict(expected(), criterion="environmentSize", field="environment",
                       source_calls=["CommonContainerConfigCliOptions.getEnvironment"])
    raw = source_setter_raw()
    builder = raw["method_ssa_facts"][2]
    constructor = next(row for row in builder["instructions"] if row.get("declared_target"))
    constructor["declared_target"] = "other.HashMap.<init>(Map)V"
    witness = source_to_setter_witness(raw, environment)
    assert witness["status"] == "incomplete"
    assert witness["builder_formal_to_field_write"]["status"] == "missing"


def test_base_image_constructor_link_requires_exact_actual_and_same_receiver():
    valid = _typed_constructor_link_checks(
        [7], {"uses": [3, 7]}, [1, 2], {"definitions": [3]}, {"uses": [3]},
        {"uses": [1, 8, 9]}, {"status": "complete"})
    assert all(valid.values())

    wrong_actual = _typed_constructor_link_checks(
        [7], {"uses": [3, 99]}, [1, 2], {"definitions": [3]}, {"uses": [3]},
        {"uses": [1, 8, 9]}, {"status": "complete"})
    assert wrong_actual["source_actual_to_constructor_formal"] is False

    wrong_receiver = _typed_constructor_link_checks(
        [7], {"uses": [3, 7]}, [1, 2], {"definitions": [3]}, {"uses": [3]},
        {"uses": [99, 8, 9]}, {"status": "complete"})
    assert wrong_receiver["same_builder_receiver_into_common_constructor"] is False


def dispatcher_fact(*, wrong_actual=False, wrong_return=False):
    instructions = []
    for index, image_type in enumerate(("DockerDaemonImage", "TarImage", "RegistryImage")):
        actual = 90 + index
        result = 100 + index
        target = ("com.google.cloud.tools.jib.api.Jib.from(Lcom/google/cloud/tools/"
                  f"jib/api/{image_type};)Lcom/google/cloud/tools/jib/api/JibContainerBuilder;")
        instructions.extend([
            {"instruction_index": index * 3, "instruction": f"{actual} = transform 1",
             "definitions": [actual], "uses": [99 if wrong_actual and index == 0 else 1]},
            {"instruction_index": index * 3 + 1, "instruction": f"{result} = invoke",
             "definitions": [result], "uses": [actual], "declared_target": target,
             "call_site": f"site:{index}",
             "possible_target_details": [{"method": target, "has_ir": True}]},
            {"instruction_index": index * 3 + 2,
             "instruction": f"return {999 if wrong_return and index == 0 else result}",
             "definitions": [], "uses": [999 if wrong_return and index == 0 else result]},
        ])
    return {"method": "dispatcher", "parameter_value_numbers": [1],
            "instructions": instructions, "phi_instructions": []}


def test_jib_dispatcher_requires_source_actual_and_matching_branch_return():
    assert _jib_dispatcher_witness(dispatcher_fact())["status"] == "complete"
    assert _jib_dispatcher_witness(dispatcher_fact(wrong_actual=True))["status"] == "incomplete"
    assert _jib_dispatcher_witness(dispatcher_fact(wrong_return=True))["status"] == "incomplete"


def test_outer_factory_call_requires_exact_resolved_method_context():
    fact = dispatcher_fact()
    fact.update(method="exact.Dispatcher(String)Builder", context="Everywhere")
    call = {
        "uses": [7], "definitions": [8],
        "possible_target_details": [{
            "method": fact["method"], "context": "Everywhere", "has_ir": True,
            "parameter_value_numbers": [1],
        }],
    }
    assert _resolved_call_to_fact(call, fact)
    call["possible_target_details"][0]["context"] = "wrong-context"
    assert not _resolved_call_to_fact(call, fact)
    call["possible_target_details"] = []
    assert not _resolved_call_to_fact(call, fact)


def test_adapter_revalidation_rejects_nonidentical_analysed_entry(tmp_path, monkeypatch):
    source_text = """class T {
  void test(ArtifactProcessor p, Jar j, CommonCliOptions c,
      CommonContainerConfigCliOptions x, ConsoleLogger l) throws Exception {
    JibContainerBuilder b = JarFiles.toJibContainerBuilder(p, j, c, x, l);
    ContainerBuildPlan plan = b.toContainerBuildPlan();
    assertNull(plan.getUser());
  }
}
"""
    source = tmp_path / "T.java"
    source.write_text(source_text, encoding="utf-8")
    expectations = tmp_path / "expectations.json"
    import hashlib
    expectations.write_text(json.dumps({
        "test_sha256": hashlib.sha256(source_text.encode()).hexdigest(),
        "observations": [{
            "id": "criterion:0", "criterion": "user", "line": 6,
            "end_line": 6, "observed_line": 6, "expression": "plan.getUser()",
        }],
    }), encoding="utf-8")
    run = tmp_path / "run"
    generated = run / "skeleton/source/com/google/cloud/tools/jib/cli/jar"
    generated.mkdir(parents=True)
    (generated / "JibObservationEntry.java").write_text("different", encoding="utf-8")
    (generated / "ObservationSink.java").write_text("different", encoding="utf-8")
    monkeypatch.setattr("summarize_jib_observations.EXPECTATIONS", expectations)
    with pytest.raises(ValueError, match="not byte-identical"):
        revalidate_source_adapter(run, source, tmp_path / "revalidated")
