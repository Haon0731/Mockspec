from __future__ import annotations

import copy
import json
from pathlib import Path
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from build_examples import JAVA_ORACLE_IDS, PY_ROWS  # noqa: E402
from inputs import load_java, load_python  # noqa: E402
from schema_draft import DraftValidationError, validate_contract  # noqa: E402


def _contract(language: str) -> dict:
    return json.loads((ROOT / "examples" / f"{language}.contract.json").read_text())


def _validate(language: str, contract: dict) -> None:
    if language == "java":
        validate_contract(
            contract,
            load_java(),
            expected_oracle_ids=JAVA_ORACLE_IDS,
            expected_parameter_rows=set(),
        )
    else:
        validate_contract(
            contract,
            load_python(),
            expected_oracle_ids={f"O_{row[0]}" for row in PY_ROWS},
            expected_parameter_rows={row[0] for row in PY_ROWS},
        )


def test_complete_examples_validate_against_reviewed_inputs() -> None:
    _validate("java", _contract("java"))
    _validate("python", _contract("python"))


@pytest.mark.parametrize(
    "mutate",
    [
        lambda value: value["scenarios"][0]["condition"].pop("status"),
        lambda value: value["scenarios"][0]["condition"].update(formula_ref="F_MISSING"),
        lambda value: value["coverage"].update(whole_test_verified=True),
        lambda value: value["evidence"][0].update(quote="valid location, false quotation"),
        lambda value: value["checks"][0].update(
            result="sat", performed_by="host", semantic_claim=True
        ),
        lambda value: value["verification_candidates"][0].update(
            status="verified_obligation"
        ),
    ],
)
def test_invalid_claim_shapes_are_rejected(mutate) -> None:
    contract = _contract("java")
    mutate(contract)
    with pytest.raises(DraftValidationError):
        _validate("java", contract)


def test_condition_states_are_explicit_and_distinct() -> None:
    contract = _contract("java")
    contract["scenarios"][0]["condition"] = {
        "status": "explicit_unconditional",
        "formula_ref": "true",
        "unknown_ref": None,
    }
    _validate("java", contract)

    contract["scenarios"][0]["condition"] = {
        "status": "unknown",
        "formula_ref": None,
        "unknown_ref": "U_J_VERSION_NULL_OR_THROW",
    }
    _validate("java", contract)

    contract["scenarios"][0]["condition"] = {
        "status": "unknown",
        "formula_ref": None,
        "unknown_ref": None,
    }
    with pytest.raises(DraftValidationError):
        _validate("java", contract)


def test_host_inventory_detects_missing_parameter_row() -> None:
    contract = _contract("python")
    missing = contract["oracles"].pop()
    contract["coverage"]["formalized_oracle_refs"].remove(missing["id"])
    with pytest.raises(DraftValidationError, match="inventory"):
        _validate("python", contract)


def test_parameter_row_oracle_requires_an_applicable_scenario_rule() -> None:
    contract = _contract("python")
    oracle_id = "O_PY_ROW_APP_404"
    removed_expectations = {
        item["id"]
        for item in contract["runtime_expectations"]
        if "PY_ROW_APP_404" in item["id"]
    }
    contract["response_rules"] = [
        item for item in contract["response_rules"]
        if oracle_id not in item["oracle_refs"]
    ]
    contract["runtime_expectations"] = [
        item for item in contract["runtime_expectations"]
        if item["id"] not in removed_expectations
    ]
    contract["verification_candidates"] = [
        item for item in contract["verification_candidates"]
        if item["expectation_ref"] not in removed_expectations
    ]
    with pytest.raises(DraftValidationError, match="no applicable scenario response rule"):
        _validate("python", contract)


def test_python_keeps_exception_and_exact_message_oracles_for_all_rows() -> None:
    expected = {
        "PY_ROW_APP_404": "Aborted with 404",
        "PY_ROW_APP_500": "Aborted with 500",
        "PY_ROW_NONE_403": "Werkzeug abort 403",
        "PY_ROW_NONE_400": "Werkzeug abort 400",
        "PY_ROW_APP_418": "Aborted with 418",
    }
    actual = {
        oracle["parameter_row"]: oracle["expected"]
        for oracle in _contract("python")["oracles"]
    }
    assert set(actual) == set(expected)
    for row, message in expected.items():
        assert "Exception" in actual[row]
        assert repr(message) in actual[row]


def test_python_paths_are_row_local_partial_and_human_review_only() -> None:
    contract = _contract("python")
    scenarios = contract["scenarios"]
    assert len(scenarios) == 8
    assert all(item["status"] == "partial" for item in scenarios)
    assert all(len(item["oracle_refs"]) == 1 for item in scenarios)
    groups = contract["consumption"]["mutual_exclusion_groups"]
    assert set(groups) == {f"python_control_path_{row[0]}" for row in PY_ROWS}
    assert sorted(len(refs) for refs in groups.values()) == [1, 1, 2, 2, 2]
    assert contract["consumption"]["selection_mode"] == "human_review_only"
    assert set(contract["consumption"]["blocking_unknown_refs"]) == {
        "U_PY_DYNAMIC_FAILURES",
        "U_PY_FALLBACK_SEMANTICS",
    }
    path_formulas = {
        item["id"] for item in contract["formulas"]
        if item["role"] == "row_local_scenario_guard"
    }
    assert len(path_formulas) == 8
    assert "F_PY_LOOKUP_NONE" not in path_formulas
    assert "F_PY_CONTEXT_THROW" not in path_formulas
    assert "F_PY_CONTEXT_RETURN" not in path_formulas


def test_java_final_build_plan_oracles_are_not_claimed_by_local_formulas() -> None:
    contract = _contract("java")
    assert contract["coverage"]["formalized_oracle_refs"] == []
    assert set(contract["coverage"]["unformalized_oracle_refs"]) == JAVA_ORACLE_IDS
    assert all(
        oracle["formalization_status"] == "not_formalized"
        for oracle in contract["oracles"]
    )
    formulas = {item["id"]: item for item in contract["formulas"]}
    assert formulas["F_J_CUSTOM_ENTRY_EQUALS_TARGET"]["text"] == "(assert S_J_CUSTOM_MATCHES)"
    assert formulas["F_J_CUSTOM_ENTRY_EQUALS_TARGET"]["symbol_refs"] == [
        "S_J_CUSTOM_MATCHES"
    ]
    assert formulas["F_J_COMPUTED_ENTRY_EQUALS_TARGET"]["text"] == (
        "(assert S_J_COMPUTED_MATCHES)"
    )
    assert formulas["F_J_COMPUTED_ENTRY_EQUALS_TARGET"]["symbol_refs"] == [
        "S_J_COMPUTED_MATCHES"
    ]


def test_unsupported_dynamic_semantics_remain_visible() -> None:
    java = _contract("java")
    python = _contract("python")
    assert any(
        formula["id"] == "F_J_EXPLICIT_BUILDER_SEMANTICS"
        and formula["support_status"] == "descriptive_unsupported"
        for formula in java["formulas"]
    )
    assert any(item["id"] == "U_PY_DYNAMIC_FAILURES" for item in python["unknowns"])
    assert all(
        candidate["status"] == "candidate_only"
        for candidate in java["verification_candidates"]
        + python["verification_candidates"]
    )
