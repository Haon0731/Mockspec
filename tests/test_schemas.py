from copy import deepcopy
from dataclasses import FrozenInstanceError

import pytest

from mock_spec.schemas import SchemaValidationError, UnifiedTask


def valid_task_data() -> dict:
    return {
        "schema_version": "mock-spec.task/v1",
        "language": "python",
        "case_id": "example_1",
        "mut_source": {
            "source_id": "mut:0",
            "logical_path": "src/example.py",
            "text": "def target(value):\n    return dependency(value)",
            "symbol": "example.target",
            "start_line": 10,
            "end_line": 11,
            "resolution_status": "resolved",
            "target_precision": "function",
        },
        "masked_test": {
            "source_id": "test:0",
            "logical_path": "tests/test_example.py",
            "text": "def test_target():\n    assert target(1) == 2",
            "symbol": "test_example.test_target",
            "start_line": 4,
            "end_line": 5,
            "resolution_status": "resolved",
            "target_precision": "function",
        },
        "permitted_interfaces": [
            {
                "symbol": "example.dependency",
                "signature": "dependency(value)",
                "source_id": "support:0",
                "start_line": 1,
                "end_line": 1,
                "resolution_status": "partial",
            }
        ],
        "supporting_sources": [
            {
                "source_id": "support:0",
                "logical_path": "src/dependency.py",
                "text": "def dependency(value): ...",
                "symbol": "example.dependency",
                "start_line": 1,
                "end_line": 1,
                "resolution_status": "partial",
                "target_precision": "function",
            }
        ],
        "execution_context": {
            "runtime_name": "python",
            "runtime_version": "3.10",
            "test_framework": "pytest",
            "test_framework_version": None,
        },
        "input_policy": {
            "policy_id": "frozen-v1",
            "protocol_version": "developer_realistic_workspace_v1",
            "masking_contract": "artifact-aware-slot-v1",
            "visibility": "reviewed_frozen_sources",
        },
        "sources": [
            {
                "source_id": "mut:0",
                "logical_path": "src/example.py",
                "role": "mut",
            },
            {
                "source_id": "test:0",
                "logical_path": "tests/test_example.py",
                "role": "masked_test",
            },
            {
                "source_id": "support:0",
                "logical_path": "src/dependency.py",
                "role": "supporting",
            },
        ],
    }


def test_round_trip_uses_immutable_recursive_schema() -> None:
    data = valid_task_data()

    task = UnifiedTask.from_dict(data)

    assert task.to_dict() == data
    assert isinstance(task.supporting_sources, tuple)
    assert isinstance(task.permitted_interfaces, tuple)
    with pytest.raises(FrozenInstanceError):
        task.case_id = "changed"  # type: ignore[misc]


@pytest.mark.parametrize("field", ["reference_mock", "gold", "historical_score"])
def test_rejects_prohibited_or_unknown_top_level_fields(field: str) -> None:
    data = valid_task_data()
    data[field] = "secret"

    with pytest.raises(SchemaValidationError, match="unknown fields"):
        UnifiedTask.from_dict(data)


def test_rejects_unknown_nested_fields() -> None:
    data = valid_task_data()
    data["input_policy"]["metadata"] = {"gold_path": "/private/gold"}

    with pytest.raises(SchemaValidationError, match="unknown fields"):
        UnifiedTask.from_dict(data)


def test_rejects_missing_fields() -> None:
    data = valid_task_data()
    del data["execution_context"]["runtime_name"]

    with pytest.raises(SchemaValidationError, match="missing fields"):
        UnifiedTask.from_dict(data)


def test_rejects_bool_as_line_number() -> None:
    data = valid_task_data()
    data["mut_source"]["start_line"] = True

    with pytest.raises(SchemaValidationError, match="must be an integer"):
        UnifiedTask.from_dict(data)


@pytest.mark.parametrize(
    "logical_path",
    [
        "/host/data.py",
        "../data.py",
        "src/../data.py",
        "src\\data.py",
        "./data.py",
        ".",
        "C:/host/data.py",
        "src/data.py\x00ignored",
    ],
)
def test_rejects_unsafe_or_non_normalized_logical_paths(logical_path: str) -> None:
    data = valid_task_data()
    data["mut_source"]["logical_path"] = logical_path

    with pytest.raises(SchemaValidationError):
        UnifiedTask.from_dict(data)


@pytest.mark.parametrize(
    "source_id",
    ["mut/0", "../mut", "0mut", "mut value", "mut\x00hidden", ""],
)
def test_rejects_source_ids_that_are_not_tokens(source_id: str) -> None:
    data = valid_task_data()
    data["mut_source"]["source_id"] = source_id
    data["sources"][0]["source_id"] = source_id

    with pytest.raises(SchemaValidationError):
        UnifiedTask.from_dict(data)


def test_accepts_source_id_token_punctuation() -> None:
    data = valid_task_data()
    data["mut_source"]["source_id"] = "Mut.case_1:-source"
    data["sources"][0]["source_id"] = "Mut.case_1:-source"

    assert UnifiedTask.from_dict(data).mut_source.source_id == "Mut.case_1:-source"


def test_source_range_must_match_source_text() -> None:
    data = valid_task_data()
    data["masked_test"]["end_line"] = 7

    with pytest.raises(SchemaValidationError, match="line range"):
        UnifiedTask.from_dict(data)


def test_interface_must_cite_an_existing_source() -> None:
    data = valid_task_data()
    data["permitted_interfaces"][0]["source_id"] = "private:gold"

    with pytest.raises(SchemaValidationError, match="unknown source ID"):
        UnifiedTask.from_dict(data)


def test_interface_range_must_be_within_cited_source() -> None:
    data = valid_task_data()
    data["permitted_interfaces"][0]["end_line"] = 2

    with pytest.raises(SchemaValidationError, match="outside its source"):
        UnifiedTask.from_dict(data)


def test_source_index_must_match_source_role_and_path() -> None:
    data = valid_task_data()
    data["sources"][0]["role"] = "supporting"

    with pytest.raises(SchemaValidationError, match="does not match"):
        UnifiedTask.from_dict(data)


def test_source_index_must_cover_exactly_the_visible_sources() -> None:
    data = valid_task_data()
    data["sources"] = data["sources"][:-1]

    with pytest.raises(SchemaValidationError, match="index every task source"):
        UnifiedTask.from_dict(data)


def test_source_ids_must_be_unique() -> None:
    data = valid_task_data()
    data["supporting_sources"][0]["source_id"] = "mut:0"
    data["sources"][2]["source_id"] = "mut:0"

    with pytest.raises(SchemaValidationError, match="source IDs must be unique"):
        UnifiedTask.from_dict(data)


@pytest.mark.parametrize(
    ("path", "value", "message"),
    [
        (("language",), "ruby", "must be one of"),
        (("schema_version",), "mock-spec.task/v2", "schema_version"),
        (("input_policy", "visibility"), "all_files", "visibility"),
        (("mut_source", "resolution_status"), "guessed", "must be one of"),
        (("mut_source", "target_precision"), "statement", "must be one of"),
    ],
)
def test_rejects_values_outside_closed_enumerations(
    path: tuple[str, ...], value: str, message: str
) -> None:
    data = valid_task_data()
    target = data
    for part in path[:-1]:
        target = target[part]
    target[path[-1]] = value

    with pytest.raises(SchemaValidationError, match=message):
        UnifiedTask.from_dict(data)


def test_from_dict_does_not_mutate_input() -> None:
    data = valid_task_data()
    original = deepcopy(data)

    UnifiedTask.from_dict(data)

    assert data == original
