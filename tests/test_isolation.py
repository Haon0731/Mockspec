import pytest

from mock_spec.schemas import UnifiedTask
from mock_spec.validation import InputValidationError, SourceStore


def _task() -> UnifiedTask:
    return UnifiedTask.from_dict(
        {
            "schema_version": "mock-spec.task/v1",
            "language": "python",
            "case_id": "isolated_case",
            "mut_source": {
                "source_id": "mut",
                "logical_path": "src/target.py",
                "text": "def target(): ...",
                "symbol": "target",
                "start_line": 1,
                "end_line": 1,
                "resolution_status": "resolved",
                "target_precision": "function",
            },
            "masked_test": {
                "source_id": "test",
                "logical_path": "tests/test_target.py",
                "text": "def test_target(): ...",
                "symbol": "test_target",
                "start_line": 1,
                "end_line": 1,
                "resolution_status": "resolved",
                "target_precision": "function",
            },
            "permitted_interfaces": [],
            "supporting_sources": [],
            "execution_context": {
                "runtime_name": "python",
                "runtime_version": None,
                "test_framework": "pytest",
                "test_framework_version": None,
            },
            "input_policy": {
                "policy_id": "mock-spec-reviewed-frozen/v1",
                "protocol_version": "test-v1",
                "masking_contract": "test-mask-v1",
                "visibility": "reviewed_frozen_sources",
            },
            "sources": [
                {"source_id": "mut", "logical_path": "src/target.py", "role": "mut"},
                {
                    "source_id": "test",
                    "logical_path": "tests/test_target.py",
                    "role": "masked_test",
                },
            ],
        }
    )


def test_source_store_exposes_only_source_id_lookup() -> None:
    store = SourceStore(_task())

    assert store.ids() == ("mut", "test")
    assert store.get("mut").text == "def target(): ..."
    assert not hasattr(store, "root")
    assert not hasattr(store, "path")
    assert not hasattr(store, "read_path")
    with pytest.raises(InputValidationError, match="unknown source_id"):
        store.get("src/target.py")
    with pytest.raises(InputValidationError, match="unknown source_id"):
        store.get("../private/gold.json")


@pytest.mark.parametrize("source_id", [None, 1, True])
def test_source_store_rejects_non_string_source_ids(source_id: object) -> None:
    with pytest.raises(InputValidationError, match="unknown source_id"):
        SourceStore(_task()).get(source_id)  # type: ignore[arg-type]
