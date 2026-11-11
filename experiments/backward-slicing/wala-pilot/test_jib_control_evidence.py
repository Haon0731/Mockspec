from __future__ import annotations

import pytest

from jib_control_evidence import ControlEvidenceError, map_control_evidence


ENTRY = "com.google.cloud.tools.jib.cli.jar.JibObservationEntry.observe(...)Object[]"
MUT = "com.google.cloud.tools.jib.cli.jar.JarFiles.toJibContainerBuilder(...)JibContainerBuilder"


def instruction(index, *, target=None, definitions=(), uses=()):
    value = {
        "instruction_index": index,
        "basic_block": index,
        "instruction": f"instruction {index}",
        "definitions": list(definitions),
        "uses": list(uses),
    }
    if target is not None:
        value.update({"call_site": f"@{index}", "declared_target": target})
    return value


def block(index, normal=(), exceptional=()):
    return {
        "number": index,
        "first_instruction_index": index,
        "last_instruction_index": index,
        "normal_successor_blocks": list(normal),
        "exceptional_successor_blocks": list(exceptional),
    }


def observations():
    return [
        {
            "observation_id": f"criterion:{index}",
            "criterion": (
                "com.google.cloud.tools.jib.cli.jar.ObservationSink."
                f"value{index}(Ljava/lang/Object;)V"
            ),
        }
        for index in range(19)
    ]


def entry_facts(context="Everywhere"):
    items = [
        instruction(
            0,
            target=(
                "com.google.cloud.tools.jib.cli.jar.JarFiles."
                "toJibContainerBuilder(...)JibContainerBuilder"
            ),
            definitions=(10, 90),
        )
    ]
    items.extend(
        instruction(index + 1, target=observation["criterion"], uses=(10,))
        for index, observation in enumerate(observations())
    )
    blocks = [block(0, (1,), (99,))]
    blocks.extend(block(index, (index + 1,) if index < 19 else ()) for index in range(1, 20))
    blocks.append({"number": 99, "exceptional_exit": True})
    return {
        "method": ENTRY,
        "context": context,
        "instructions": items,
        "cfg_blocks": blocks,
    }


def mut_facts():
    get_from = "com.google.cloud.tools.jib.cli.CommonContainerConfigCliOptions.getFrom()Optional"
    is_present = "java.util.Optional.isPresent()Z"
    get_version = "com.google.cloud.tools.jib.cli.ArtifactProcessor.getJavaVersion()Integer"
    unbox = "java.lang.Integer.intValue()I"
    items = [
        instruction(0, target=get_from, definitions=(10, 100)),
        instruction(1, target=is_present, definitions=(11, 101), uses=(10,)),
        instruction(2, uses=(11,)),
        instruction(3, target="java.util.Optional.get()Object", definitions=(12, 102), uses=(10,)),
        instruction(4, target="com.google.cloud.tools.jib.cli.ContainerBuilders.create(...)Builder"),
        instruction(6, target=get_version, definitions=(20, 106)),
        instruction(7, target=unbox, definitions=(21, 107), uses=(20,)),
        instruction(8, uses=(21,)),
        instruction(9, target="com.google.cloud.tools.jib.api.Jib.from(Ljava/lang/String;)Builder"),
        instruction(10, target="com.google.cloud.tools.jib.api.Jib.from(Ljava/lang/String;)Builder"),
        instruction(11, target="com.google.common.collect.Collections.emptySet()Set"),
        {
            **instruction(12, uses=(30,)),
            "instruction": "return 30",
            "instruction_type": "return",
        },
    ]
    blocks = [
        block(0, (1,), (99,)),
        block(1, (2,), (99,)),
        block(2, (3, 6)),
        block(3, (4,), (99,)),
        block(4, (11,), (99,)),
        block(6, (7,), (99,)),
        block(7, (8,), (99,)),
        block(8, (9, 10)),
        block(9, (11,), (99,)),
        block(10, (11,), (99,)),
        block(11, (12,)),
        block(12),
        {"number": 99, "exceptional_exit": True},
    ]
    return {"method": MUT, "context": "Everywhere", "instructions": items, "cfg_blocks": blocks}


def payload():
    return {"method_ssa_facts": [entry_facts(), mut_facts()]}


def test_maps_all_observations_to_normal_and_exceptional_mut_completion():
    result = map_control_evidence(observations(), payload())
    normal = [row for row in result["relations"] if row["kind"] == "normal_return_dependence"]
    entry_normal = [row for row in normal if row["from"].get("method") == ENTRY]
    entry_exceptions = [
        row
        for row in result["relations"]
        if row["kind"] == "exception_dependence"
        and row["from"].get("method") == ENTRY
    ]

    assert result["support"]["mapped_observation_count"] == 19
    assert {row["observation_ids"][0] for row in normal} == {
        f"criterion:{index}" for index in range(19)
    }
    assert len(entry_exceptions) == 19
    assert len(entry_normal) == 19
    assert all(row["modality"] == "analysis_may" for row in entry_normal)
    assert all(
        "dominance/necessity is not established" in row["precision"]
        for row in entry_normal
    )
    assert all(row["modality"] == "analysis_may" for row in entry_exceptions)
    assert all("exception type" in row["precision"] for row in entry_exceptions)


def test_bypass_route_never_upgrades_existing_normal_path_to_necessity():
    value = payload()
    entry = value["method_ssa_facts"][0]
    entry["instructions"].append({
        "instruction_index": -1,
        "instruction": "conditional branch bypassing MUT",
        "definitions": [],
        "uses": [1],
        "cfg_successors": [
            {"instruction_index": 0, "edge_kind": "normal"},
            {"instruction_index": 1, "edge_kind": "normal"},
        ],
    })

    result = map_control_evidence(observations(), value)
    normal = [row for row in result["relations"]
              if row["kind"] == "normal_return_dependence"
              and row["from"].get("method") == ENTRY]

    assert len(normal) == 19
    assert all(row["modality"] == "analysis_may" for row in normal)
    assert all("necessity is not established" in row["precision"] for row in normal)


def test_real_cfg_recovers_getfrom_and_java_version_branch_conditions():
    result = map_control_evidence(observations(), payload())
    controls = [row for row in result["relations"] if row["kind"] == "control_dependence"]

    create = next(
        row for row in controls if "ContainerBuilders.create" in row["to"]["declared_target"]
    )
    assert create["from"]["instruction_index"] == 2
    assert create["predicate_targets"] == [
        "com.google.cloud.tools.jib.cli.CommonContainerConfigCliOptions.getFrom()Optional",
        "java.util.Optional.isPresent()Z",
    ]
    assert create["observation_ids"] == [f"criterion:{index}" for index in range(19)]
    assert create["observation_attribution"]["status"] == "composed_execution_may"
    assert "does not claim" in create["observation_attribution"]["semantic_boundary"]
    jib_from = [row for row in controls if "Jib.from" in row["to"]["declared_target"]]
    assert len(jib_from) == 2
    assert all(row["from"]["instruction_index"] == 8 for row in jib_from)
    assert all(
        row["predicate_targets"] == [
            "com.google.cloud.tools.jib.cli.ArtifactProcessor.getJavaVersion()Integer",
            "java.lang.Integer.intValue()I",
        ]
        for row in jib_from
    )
    assert {row["branch_successor_ordinal"] for row in jib_from} == {0, 1}


def test_conditional_calls_are_not_reported_as_must_execute():
    result = map_control_evidence(observations(), payload())
    controls = [row for row in result["relations"] if row["kind"] == "control_dependence"]
    assert controls
    assert all(row["modality"] == "analysis_may" for row in controls)
    assert all("path satisfiability not solved" in row["precision"] for row in controls)


def test_control_observation_attribution_requires_normal_and_exception_composition():
    value = payload()
    create_block = next(
        item for item in value["method_ssa_facts"][1]["cfg_blocks"] if item["number"] == 4
    )
    create_block["exceptional_successor_blocks"] = []

    result = map_control_evidence(observations(), value)
    create = next(
        row
        for row in result["relations"]
        if row["kind"] == "control_dependence"
        and "ContainerBuilders.create" in row["to"]["declared_target"]
    )

    assert create["observation_ids"] == []
    assert create["observation_attribution"]["status"] == "not_attributed"


def test_empty_phi_block_is_traversed_and_post_merge_call_is_not_controlled():
    value = payload()
    cfg = value["method_ssa_facts"][1]["cfg_blocks"]
    for item in cfg:
        if item["number"] in {4, 9, 10}:
            item["normal_successor_blocks"] = [13]
    cfg.append(
        {
            "number": 13,
            "first_instruction_index": -1,
            "last_instruction_index": -2,
            "normal_successor_blocks": [11],
            "exceptional_successor_blocks": [],
        }
    )
    value["method_ssa_facts"][1]["phi_instructions"] = [
        {
            "basic_block": 13,
            "definitions": [30],
            "uses": [12, 20],
            "instruction_type": "SSAPhiInstruction",
        }
    ]

    result = map_control_evidence(observations(), value)
    controls = [row for row in result["relations"] if row["kind"] == "control_dependence"]

    assert not any(row["to"].get("instruction_index") == 11 for row in controls)


def test_pure_normal_call_gets_no_opaque_or_exception_effect():
    result = map_control_evidence(observations(), payload())
    empty_set_rows = [
        row
        for row in result["relations"]
        if "declared_target" in row.get("from", {})
        and "Collections.emptySet" in row["from"]["declared_target"]
    ]
    assert {row["kind"] for row in empty_set_rows} == {"normal_return_dependence"}
    assert all(row["modality"] == "analysis_may" for row in empty_set_rows)
    assert result["boundaries"]["native_call_effects"] == (
        "not inferred from CFG or slice membership"
    )


def test_optional_callback_condition_stays_unknown():
    value = payload()
    fact = value["method_ssa_facts"][1]["instructions"][-2]
    fact["declared_target"] = "java.util.Optional.ifPresent(Ljava/util/function/Consumer;)V"
    fact["call_site"] = "@ifPresent"

    result = map_control_evidence(observations(), value)

    assert result["support"]["unknown_callback_condition_count"] == 1
    unknown = result["unknown_execution_conditions"][0]
    assert unknown["status"] == "unknown_condition"
    assert "not established by CFG" in unknown["precision"]
    assert unknown["observation_ids"] == []
    assert unknown["data_field_attribution"] == "not_evaluated"


@pytest.mark.parametrize("mutation", ["wrong_context", "wrong_callsite", "wrong_target"])
def test_wrong_context_callsite_or_target_does_not_pass(mutation):
    value = payload()
    if mutation == "wrong_context":
        value["method_ssa_facts"][0]["context"] = "Wrong"
        value["method_ssa_facts"].append(entry_facts())
    elif mutation == "wrong_callsite":
        duplicate = instruction(20, target=observations()[0]["criterion"])
        value["method_ssa_facts"][0]["instructions"].append(duplicate)
        value["method_ssa_facts"][0]["cfg_blocks"].append(block(20))
    else:
        value["method_ssa_facts"][0]["instructions"][1]["declared_target"] = (
            "com.google.cloud.tools.jib.cli.jar.ObservationSink.wrong(Ljava/lang/Object;)V"
        )

    with pytest.raises(ControlEvidenceError):
        map_control_evidence(observations(), value)
