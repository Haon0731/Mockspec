from __future__ import annotations

from copy import deepcopy

from jib_optional_evidence import extract_setter_bytecode_evidence, map_optional_evidence


MUT = "com.google.cloud.tools.jib.cli.jar.JarFiles.toJibContainerBuilder(...)Builder"
CASES = {
    "user": ("getUser", "Ljava/lang/String;", "criterion:8"),
    "format": (
        "getFormat", "Lcom/google/cloud/tools/jib/api/buildplan/ImageFormat;", "criterion:3"
    ),
    "creationTime": ("getCreationTime", "Ljava/time/Instant;", "criterion:2"),
}


def inst(index, target=None, definitions=(), uses=(), normal=()):
    row = {
        "instruction_index": index,
        "instruction": f"instruction {index}",
        "definitions": list(definitions),
        "uses": list(uses),
        "cfg_successors": [
            {"instruction_index": item, "edge_kind": "normal"} for item in normal
        ],
    }
    if target:
        row.update(declared_target=target, call_site=f"@{index}")
    return row


def method(name, instructions, parameters):
    return {
        "method": name, "context": "Everywhere", "instructions": instructions,
        "parameter_value_numbers": parameters, "cfg_blocks": [], "phi_instructions": [],
    }


def observations():
    return [
        {"observation_id": criterion, "field": field}
        for field, (_, _, criterion) in CASES.items()
    ]


def payload():
    mut_rows = []
    references = []
    methods = []
    next_index = 1
    for ordinal, (field, (getter, descriptor, _)) in enumerate(CASES.items()):
        getter_index, factory_index, if_index = next_index, next_index + 1, next_index + 2
        next_index += 3
        optional_value, consumer_value = 10 + ordinal * 10, 11 + ordinal * 10
        suffix = field[0].upper() + field[1:]
        implementation = (
            f"com.google.cloud.tools.jib.api.JibContainerBuilder.set{suffix}({descriptor})"
            "Lcom/google/cloud/tools/jib/api/JibContainerBuilder;"
        )
        builder = (
            "com.google.cloud.tools.jib.api.buildplan.ContainerBuildPlan$Builder."
            f"set{suffix}({descriptor})"
            "Lcom/google/cloud/tools/jib/api/buildplan/ContainerBuildPlan$Builder;"
        )
        mut_rows += [
            inst(getter_index,
                 f"com.google.cloud.tools.jib.cli.CommonContainerConfigCliOptions.{getter}()"
                 "Ljava/util/Optional;", (optional_value, 100 + ordinal), (4,),
                 (factory_index,)),
            inst(factory_index,
                 "java.lang.invoke.LambdaMetafactory.accept(Builder)Consumer",
                 (consumer_value, 110 + ordinal), (33,), (if_index,)),
            inst(if_index, "java.util.Optional.ifPresent(Ljava/util/function/Consumer;)V",
                 (120 + ordinal,), (optional_value, consumer_value), (next_index,)),
        ]
        references.append({
            "factory_instruction_index": factory_index,
            "factory_call_site": f"@{factory_index}",
            "implementation_reference_kind": "REF_invokeVirtual",
            "implementation_target": implementation,
            "instantiated_method_type": f"({descriptor})V",
            "captured_value_numbers": [33],
        })
        methods += [
            method(implementation, [
                inst(1, builder, uses=(50, 2), normal=(2,)),
                {**inst(2, uses=(1,)), "instruction": "return 1", "instruction_type": "return"},
            ], [1, 2]),
            method(builder, [
                {**inst(1, uses=(1, 2), normal=(2,)), "instruction": (
                    f"putfield 1.< X, ContainerBuildPlan$Builder, {field}, {descriptor} > = 2"
                )},
                {**inst(2, uses=(1,)), "instruction": "return 1", "instruction_type": "return"},
            ], [1, 2]),
        ]
    mut_rows.append({
        **inst(next_index, uses=(33,)), "instruction": "return 33", "instruction_type": "return"
    })
    return {
        "method_ssa_facts": [method(MUT, mut_rows, [1, 2, 3, 4, 5])] + methods,
        "lambda_method_references": references,
    }


def test_three_callbacks_map_to_exact_fields_as_may_only():
    result = map_optional_evidence(observations(), payload())
    assert result["support"] == {
        "if_present_count": 3, "field_specific_mapping_count": 3,
        "unsupported_callback_count": 0,
    }
    by_field = {row["effect"]["field"]: row for row in result["relations"]}
    assert set(by_field) == set(CASES)
    for field, (_, _, criterion) in CASES.items():
        assert by_field[field]["observation_ids"] == [criterion]
        assert by_field[field]["modality"] == "analysis_may"
        assert by_field[field]["effect"]["must_write"] is False
        assert by_field[field]["effect"]["default_overwrite"] == "not_established"


def test_wrong_lambda_target_type_fails_closed():
    value = payload()
    value["lambda_method_references"][0]["implementation_target"] = (
        value["lambda_method_references"][1]["implementation_target"]
    )
    result = map_optional_evidence(observations(), value)
    assert result["support"]["field_specific_mapping_count"] == 2
    assert result["unsupported_callbacks"][0]["reason"] == (
        "lambda instantiated/implementation type mismatch"
    )


def test_jvm_numeric_invokevirtual_handle_kind_is_accepted():
    value = payload()
    value["lambda_method_references"][0]["implementation_reference_kind"] = 5
    result = map_optional_evidence(observations(), value)
    assert result["support"]["field_specific_mapping_count"] == 3
    assert result["relations"][0]["implementation_reference_kind"] == "REF_invokeVirtual"


def test_wrong_receiver_and_mixed_callsite_do_not_cross_pair():
    for mutation, reason in (("receiver", "receiver or consumer definition is not unique"),
                             ("callsite", "lambda metadata callsite mismatch")):
        value = payload()
        if mutation == "receiver":
            value["method_ssa_facts"][0]["instructions"][2]["uses"][0] = 999
        else:
            value["lambda_method_references"][0]["factory_call_site"] = "@wrong"
        result = map_optional_evidence(observations(), value)
        assert result["support"]["field_specific_mapping_count"] == 2
        assert result["unsupported_callbacks"][0]["reason"] == reason


def test_wrong_captured_builder_receiver_is_rejected():
    value = payload()
    value["method_ssa_facts"][0]["instructions"][1]["uses"] = [999]
    value["lambda_method_references"][0]["captured_value_numbers"] = [999]
    result = map_optional_evidence(observations(), value)
    assert result["support"]["field_specific_mapping_count"] == 2
    assert result["unsupported_callbacks"][0]["reason"] == (
        "captured receiver is not the returned MUT builder"
    )


def test_mixed_getter_consumer_callsite_remains_unknown():
    value = payload()
    mixed = deepcopy(value["method_ssa_facts"][0]["instructions"][5])
    mixed.update(instruction_index=99, call_site="@99", uses=[10, 31])
    value["method_ssa_facts"][0]["instructions"].append(mixed)
    result = map_optional_evidence(observations(), value)
    extra = next(row for row in result["unsupported_callbacks"]
                 if row["if_present"]["instruction_index"] == 99)
    assert extra["observation_ids"] == []


def test_missing_field_chain_keeps_payload_and_mock_behavior_unknown():
    value = payload()
    target = value["lambda_method_references"][0]["implementation_target"]
    value["method_ssa_facts"] = [
        row for row in value["method_ssa_facts"] if row["method"] != target
    ]
    result = map_optional_evidence(observations(), value)
    assert result["support"]["field_specific_mapping_count"] == 2
    assert result["unsupported_callbacks"][0]["observation_ids"] == []
    assert result["boundaries"]["payload_values"] == "unknown"
    assert result["boundaries"]["mock_behavior"] == "not inferred"


def test_saved_javap_chain_is_exact_and_wrong_field_type_is_rejected():
    value = payload()
    target = value["lambda_method_references"][0]["implementation_target"]
    value["method_ssa_facts"] = [
        row for row in value["method_ssa_facts"]
        if "setUser" not in row["method"]
    ]
    jib_javap = """
  public com.google.cloud.tools.jib.api.JibContainerBuilder setUser(java.lang.String);
    Code:
       5: invokevirtual #1 // Method com/google/cloud/tools/jib/api/buildplan/ContainerBuildPlan$Builder.setUser:(Ljava/lang/String;)Lcom/google/cloud/tools/jib/api/buildplan/ContainerBuildPlan$Builder;
"""
    builder_javap = """
  public com.google.cloud.tools.jib.api.buildplan.ContainerBuildPlan$Builder setUser(java.lang.String);
    Code:
       2: putfield #2 // Field user:Ljava/lang/String;
"""
    evidence = extract_setter_bytecode_evidence(jib_javap, builder_javap, [target])
    result = map_optional_evidence(observations(), value, evidence)
    assert result["support"]["field_specific_mapping_count"] == 3
    assert evidence[0]["field"] == "user"

    evidence[0]["field_descriptor"] = "Ljava/time/Instant;"
    result = map_optional_evidence(observations(), value, evidence)
    assert result["support"]["field_specific_mapping_count"] == 2
    assert result["unsupported_callbacks"][0]["reason"] == (
        "setter bytecode payload/field type mismatch"
    )
