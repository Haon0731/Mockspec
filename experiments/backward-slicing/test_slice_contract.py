from __future__ import annotations

from copy import deepcopy
import importlib.util
import json
import math
from pathlib import Path
import sys

import pytest
from pydantic import ValidationError


ROOT = Path(__file__).parent
_spec = importlib.util.spec_from_file_location("slice_contract_subject", ROOT / "slice_contract.py")
assert _spec is not None and _spec.loader is not None
contract_module = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = contract_module
_spec.loader.exec_module(contract_module)


def test_checked_in_json_schema_matches_model() -> None:
    checked_in = json.loads((ROOT / "slice_contract.schema.json").read_text())
    assert checked_in == contract_module.SliceClueContract.model_json_schema()


def minimal_contract() -> dict:
    digest = "a" * 64
    return {
        "schema_version": "mock-spec.slice-evidence/v1",
        "identity": {"case_id": "case", "dataset": "data", "language": "python",
                     "snapshot_id": "snapshot", "entry_source": "task_provided",
                     "analysis_version": "test/v1"},
        "sources": [{"source_id": "test", "logical_path": "test.py", "sha256": digest,
                     "start_line": 1, "end_line": 1}],
        "contexts": [{"context_id": "row:0", "label": "row", "input_facts": {},
                      "evidence_ids": ["src:test:1"]}],
        "evidence": [{"evidence_id": "src:test:1", "kind": "source",
                      "source": {"source_id": "test", "source_sha256": digest,
                                 "start_line": 1, "start_column": 0,
                                 "end_line": 1, "end_column": 8, "quote": "assert x"},
                      "rule": None, "provenance": "test"}],
        "observations": [{"observation_id": "obs", "kind": "value",
                          "expression": "x", "evidence_id": "src:test:1"}],
        "slice_nodes": [
            {"node_id": "a", "kind": "statement", "context_ids": ["row:0"],
             "evidence_id": "src:test:1", "relevance": "value_source"},
            {"node_id": "b", "kind": "observation", "context_ids": ["row:0"],
             "evidence_id": "src:test:1", "relevance": "criterion"},
        ],
        "premises": [],
        "relations": [{"relation_id": "r", "kind": "data", "from_node_id": "a",
                       "to_node_id": "b", "observation_ids": ["obs"],
                       "context_id": "row:0", "premise_ids": [],
                       "evidence_ids": ["src:test:1"], "status": "conditional"}],
        "unknowns": [], "exclusions": [],
        "coverage": {"status": "partial", "declared_scope": "test",
                     "included_context_ids": ["row:0"], "unknown_ids": []},
        "cost": {"wall_seconds": None, "model_requests": 0, "target_executions": 0,
                 "note": "test"},
        "semantic_claim": "clues_only_not_semantic_proof",
    }


def test_strict_contract_accepts_clues_without_claiming_semantic_proof() -> None:
    parsed = contract_module.SliceClueContract.model_validate(minimal_contract())
    assert parsed.semantic_claim == "clues_only_not_semantic_proof"
    assert parsed.relations[0].status == "conditional"


@pytest.mark.parametrize(
    "mutation",
    ["unknown_evidence", "unknown_node", "unknown_context", "context_mismatch", "digest_mismatch"],
)
def test_contract_rejects_dangling_or_cross_context_references(mutation: str) -> None:
    value = minimal_contract()
    if mutation == "unknown_evidence":
        value["relations"][0]["evidence_ids"] = ["missing"]
    elif mutation == "unknown_node":
        value["relations"][0]["to_node_id"] = "missing"
    elif mutation == "unknown_context":
        value["relations"][0]["context_id"] = "missing"
    elif mutation == "context_mismatch":
        value["contexts"].append({"context_id": "row:1", "label": "other", "input_facts": {},
                                  "evidence_ids": ["src:test:1"]})
        value["coverage"]["included_context_ids"].append("row:1")
        value["relations"][0]["context_id"] = "row:1"
    else:
        value["evidence"][0]["source"]["source_sha256"] = "b" * 64
    with pytest.raises(ValidationError):
        contract_module.SliceClueContract.model_validate(value)


def test_contract_has_no_temporal_relation_escape_hatch() -> None:
    value = minimal_contract()
    value["relations"][0]["kind"] = "happens_before"
    with pytest.raises(ValidationError):
        contract_module.SliceClueContract.model_validate(value)


def test_contract_rejects_duplicate_relationship_references() -> None:
    value = minimal_contract()
    value["relations"][0]["premise_ids"] = ["premise:binding", "premise:binding"]
    with pytest.raises(ValidationError, match="duplicate relation premise"):
        contract_module.SliceClueContract.model_validate(value)


def test_external_source_validation_rejects_quote_or_content_drift() -> None:
    text = "assert x"
    value = minimal_contract()
    import hashlib
    digest = hashlib.sha256(text.encode()).hexdigest()
    value["sources"][0]["sha256"] = digest
    value["evidence"][0]["source"]["source_sha256"] = digest
    parsed = contract_module.SliceClueContract.model_validate(value)
    contract_module.validate_source_quotes(parsed, {"test": text})
    with pytest.raises(ValueError, match="digest mismatch"):
        contract_module.validate_source_quotes(parsed, {"test": "assert y"})
    changed = deepcopy(value)
    changed["evidence"][0]["source"]["quote"] = "assert y"
    parsed_changed = contract_module.SliceClueContract.model_validate(changed)
    with pytest.raises(ValueError, match="quote differs"):
        contract_module.validate_source_quotes(parsed_changed, {"test": text})


def test_source_validation_uses_ast_utf8_byte_columns() -> None:
    text = "值 = result"
    value = minimal_contract()
    import hashlib
    digest = hashlib.sha256(text.encode()).hexdigest()
    value["sources"][0]["sha256"] = digest
    reference = value["evidence"][0]["source"]
    reference.update(source_sha256=digest, start_column=6, end_column=12, quote="result")
    parsed = contract_module.SliceClueContract.model_validate(value)
    contract_module.validate_source_quotes(parsed, {"test": text})


def test_contract_rejects_non_json_or_nonfinite_context_and_empty_source_span() -> None:
    value = minimal_contract()
    value["contexts"][0]["input_facts"] = {"bad": {1, 2}}
    with pytest.raises(ValidationError):
        contract_module.SliceClueContract.model_validate(value)
    value = minimal_contract()
    value["cost"]["wall_seconds"] = math.nan
    with pytest.raises(ValidationError):
        contract_module.SliceClueContract.model_validate(value)
    value = minimal_contract()
    value["evidence"][0]["source"]["end_column"] = 0
    with pytest.raises(ValidationError):
        contract_module.SliceClueContract.model_validate(value)
