from __future__ import annotations

from copy import deepcopy

from jib_library_evidence import (
    extract_copy_constructor_witness,
    extract_method_fact_copy_constructor_witness,
    verify_copy_constructor_witness,
)


GETTER = "example.Plan.getLayers()Ljava/util/List;"


def rows() -> list[dict]:
    return [
        {"kind": "PARAM_CALLEE", "method": GETTER,
         "statement": f"PARAM_CALLEE:Node: < Application, L{GETTER} > v1"},
        {"kind": "NORMAL", "method": GETTER, "context": "Everywhere",
         "instruction_index": 0, "instruction": "3 = new <Primordial,Ljava/util/ArrayList>@0",
         "uses": [], "definitions": [3]},
        {"kind": "NORMAL", "method": GETTER, "context": "Everywhere",
         "instruction_index": 3,
         "instruction": "4 = getfield < Application, Lexample/Plan, layers, <Primordial,Ljava/util/List> > 1",
         "uses": [1], "definitions": [4]},
        {"kind": "PARAM_CALLER", "method": GETTER, "context": "Everywhere",
         "instruction_index": 4,
         "instruction": "invokespecial < Primordial, Ljava/util/ArrayList, <init>(Ljava/util/Collection;)V > 3,4 @8 exception:5",
         "uses": [3, 4], "definitions": [5],
         "declared_target": "java.util.ArrayList.<init>(Ljava/util/Collection;)V",
         "possible_target_details": [{
             "method": "java.util.ArrayList.<init>(Ljava/util/Collection;)V",
             "context": "Everywhere", "has_ir": True,
             "parameter_value_numbers": [1, 2],
         }]},
        {"kind": "NORMAL", "method": GETTER, "context": "Everywhere",
         "instruction_index": 5, "instruction": "return 3",
         "uses": [3], "definitions": []},
    ]


def test_extracts_exact_field_constructor_receiver_actual_formal_and_return():
    result = extract_copy_constructor_witness(rows(), GETTER, "layers")
    assert result["status"] == "complete"
    assert verify_copy_constructor_witness(result)
    constructor = next(step for step in result["path"]
                       if step["kind"] == "resolved_constructor_call")
    assert constructor["receiver_actual"] == 3
    assert constructor["receiver_formal"] == 1
    assert constructor["source_actual"] == 4
    assert constructor["source_formal"] == 2
    assert "aliasing" in result["provenance"]["precision"]
    assert result["copy_content_effect"]["status"] == "reviewed_api_summary"
    assert "/javase/11/" in result["copy_content_effect"]["source"]


def test_structural_constructor_chain_does_not_imply_a_content_copy():
    value = deepcopy(rows())
    allocation = value[1]
    allocation["instruction"] = allocation["instruction"].replace(
        "java/util/ArrayList", "other/ArrayList")
    constructor = next(row for row in value if row.get("declared_target"))
    constructor["declared_target"] = "other.ArrayList.<init>(Ljava/util/Collection;)V"
    constructor["possible_target_details"][0]["method"] = constructor["declared_target"]
    result = extract_copy_constructor_witness(value, GETTER, "layers")
    assert result["status"] == "complete"
    assert result["copy_content_effect"]["status"] == "unknown"


def test_wrong_field_fails_closed():
    result = extract_copy_constructor_witness(rows(), GETTER, "labels")
    assert result["status"] == "incomplete"
    assert not verify_copy_constructor_witness(result)
    assert not result["checks"]["exact_instance_field_read"]


def test_copying_a_different_object_fails_closed():
    value = deepcopy(rows())
    constructor = next(row for row in value if row.get("declared_target"))
    constructor["uses"] = [3, 99]
    result = extract_copy_constructor_witness(value, GETTER, "layers")
    assert result["status"] == "incomplete"
    assert not result["checks"]["field_actual_maps_to_constructor_formal"]


def test_returning_a_different_object_fails_closed():
    value = deepcopy(rows())
    returned = next(row for row in value if row.get("instruction", "").startswith("return"))
    returned.update({"instruction": "return 99", "uses": [99]})
    result = extract_copy_constructor_witness(value, GETTER, "layers")
    assert result["status"] == "incomplete"
    assert not result["checks"]["constructor_receiver_is_returned_object"]


def test_same_named_constructor_on_unrelated_owner_fails_closed():
    value = deepcopy(rows())
    constructor = next(row for row in value if row.get("declared_target"))
    constructor["declared_target"] = "other.ArrayList.<init>(Ljava/util/Collection;)V"
    constructor["possible_target_details"][0]["method"] = constructor["declared_target"]
    result = extract_copy_constructor_witness(value, GETTER, "layers")
    assert result["status"] == "incomplete"
    assert not result["checks"]["resolved_exact_constructor_ir"]


def test_missing_phi_facts_keep_conditional_getter_incomplete():
    value = deepcopy(rows())
    value.insert(-1, {"kind": "PHI", "method": GETTER,
                      "instruction": None, "uses": None, "definitions": None})
    value.insert(-1, {"kind": "NORMAL", "method": GETTER, "instruction_index": 2,
                      "instruction": "conditional branch(eq, to iindex=6) 4,2",
                      "uses": [4, 2], "definitions": []})
    value[-1].update({"instruction": "return 8", "uses": [8]})
    result = extract_copy_constructor_witness(value, GETTER, "layers")
    assert result["status"] == "incomplete"
    assert "constructor_receiver_is_returned_object" in result["missing"]


def test_explicit_phi_can_scope_witness_to_fresh_allocation_return_arm():
    value = deepcopy(rows())
    value.insert(-1, {"kind": "PHI", "method": GETTER,
                      "instruction": "8 = phi 2,3", "uses": [2, 3], "definitions": [8]})
    value[-1].update({"instruction": "return 8", "uses": [8]})
    result = extract_copy_constructor_witness(value, GETTER, "layers")
    assert result["status"] == "complete"
    assert verify_copy_constructor_witness(result)
    assert "allocation arm" in result["provenance"]["normal_return_condition"]


def test_complete_method_fact_adds_normal_cfg_provenance():
    instructions = []
    for index, row in enumerate(rows()[1:]):
        block = index + 1
        instructions.append({**row, "basic_block": block,
                             "normal_successor_blocks": [block + 1],
                             "exceptional_successor_blocks": []})
    fact = {
        "method": GETTER,
        "context": "Everywhere",
        "parameter_value_numbers": [1],
        "cfg_blocks": [
            {"number": block, "normal_successor_blocks": [block + 1]}
            for block in range(1, 5)
        ],
        "phi_instructions": [],
        "instructions": instructions,
    }
    result = extract_method_fact_copy_constructor_witness(fact, "layers")
    assert result["status"] == "complete"
    assert result["checks"]["normal_cfg_paths"]
    assert result["provenance"]["method_fact"]["normal_cfg_paths"][
        "constructor_to_return"] == [3, 4]
