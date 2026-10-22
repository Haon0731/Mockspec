from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path
import sys

import pytest


if importlib.util.find_spec("cvc5") is None:
    pytest.skip("scenario-research cvc5 environment is not installed", allow_module_level=True)

RESEARCH = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RESEARCH))

import local_solver  # noqa: E402
from local_solver import check_local_model  # noqa: E402


def _int_model(operator: str = "le", literal: int = 8) -> dict:
    return {
        "sort": "Int",
        "operator": operator,
        "literal": literal,
        "bounds": [-2147483648, 2147483647],
    }


def _null_model(operator: str = "is_none") -> dict:
    return {"sort": "Bool", "operator": operator, "literal": None, "bounds": None}


def _check(model: dict, candidates: list[dict], **kwargs) -> dict:
    return asyncio.run(check_local_model(model, candidates, **kwargs))


def test_integer_candidate_is_checked_against_one_serialized_model() -> None:
    result = _check(_int_model(), [{"operator": "le", "literal": 8}])

    assert result["status"] == "unsat"
    assert result["locally_sufficient"] is True
    assert result["whole_test_verified"] is False
    assert result["symbol"] == {"name": "response", "sort": "Int"}
    assert result["formulas"] == {
        "declarations": ["(declare-fun response () Int)"],
        "background": ["(>= response (- 2147483648))", "(<= response 2147483647)"],
        "goal": "(<= response 8)",
        "candidate": "(<= response 8)",
    }
    assert len(result["formula_identity"]) == 64
    assert result["queries"]["consistency"]["status"] == "sat"
    assert result["queries"]["goal_reachability"]["status"] == "sat"
    assert result["queries"]["counterexample"]["status"] == "unsat"
    assert result["round_trip"]["matches"] is True
    assert result["resource_limits"] == {
        "address_space_bytes": 512 * 1024 * 1024,
        "cpu_seconds": 4,
        "file_bytes": 1024 * 1024,
        "core_bytes": 0,
    }


def test_threshold_and_operator_changes_change_checked_formula() -> None:
    old = _check(_int_model(), [{"operator": "le", "literal": 8}])
    threshold = _check(_int_model(literal=11), [{"operator": "le", "literal": 11}])
    reversed_branch = _check(
        _int_model(operator="gt", literal=8), [{"operator": "le", "literal": 8}]
    )

    assert threshold["formulas"]["goal"] == "(<= response 11)"
    assert threshold["formula_identity"] != old["formula_identity"]
    assert reversed_branch["formulas"]["goal"] == "(> response 8)"
    assert reversed_branch["status"] == "sat"
    assert reversed_branch["locally_sufficient"] is False
    assert reversed_branch["formula_identity"] != old["formula_identity"]


def test_none_and_not_none_are_distinct_boolean_nullness_terms() -> None:
    is_none = _check(_null_model(), [{"operator": "is_none", "literal": None}])
    is_not_none = _check(
        _null_model("is_not_none"),
        [{"operator": "is_not_none", "literal": None}],
    )

    assert is_none["formulas"]["goal"] == "response"
    assert is_not_none["formulas"]["goal"] == "(not response)"
    assert is_none["locally_sufficient"] is True
    assert is_not_none["locally_sufficient"] is True
    assert is_none["formula_identity"] != is_not_none["formula_identity"]
    assert is_none["semantics"]["bool_symbol"] == (
        "true means the dependency response is None"
    )


def test_false_branch_negates_the_source_bound_condition() -> None:
    result = _check(
        _int_model(), [{"operator": "gt", "literal": 8}], goal_branch=False
    )

    assert result["formulas"]["goal"] == "(not (<= response 8))"
    assert result["locally_sufficient"] is True


@pytest.mark.parametrize(
    ("model", "candidates", "message"),
    [
        (
            {"sort": "String", "operator": "eq", "literal": 1, "bounds": None},
            [],
            "sort is unsupported",
        ),
        (_int_model(), [{"operator": "is_none", "literal": None}], "not valid for Int"),
        (_null_model(), [{"operator": "eq", "literal": 1}], "not valid for Bool"),
        (_int_model(), [{"operator": "le", "literal": True}], "must be an integer"),
    ],
)
def test_unsupported_typed_structures_degrade_explicitly(
    model: dict, candidates: list[dict], message: str
) -> None:
    result = _check(model, candidates)

    assert result["status"] == "unsupported"
    assert result["locally_sufficient"] is False
    assert message in result["diagnostic"]


def test_contradictory_candidate_is_not_vacuously_sufficient() -> None:
    result = _check(
        _int_model(),
        [{"operator": "le", "literal": 7}, {"operator": "gt", "literal": 7}],
    )

    assert result["queries"]["consistency"]["status"] == "unsat"
    assert result["queries"]["goal_reachability"]["status"] == "unsat"
    assert result["queries"]["counterexample"]["status"] == "unsat"
    assert result["locally_sufficient"] is False


def test_formula_identity_mismatch_rejects_binding() -> None:
    result = _check(
        _int_model(),
        [{"operator": "le", "literal": 8}],
        expected_formula_identity="0" * 64,
    )

    assert result["status"] == "unsupported"
    assert result["actual_formula_identity"] != result["expected_formula_identity"]
    assert "does not match" in result["diagnostic"]


def test_native_abduction_uses_recorded_goal_atoms() -> None:
    result = _check(
        _int_model(), [{"operator": "le", "literal": 8}], infer=True
    )

    assert result["status"] == "unsat"
    assert result["inference"]["status"] == "candidate_found"
    assert result["inference"]["grammar_atoms"] == [
        "(<= response 8)", "(not (<= response 8))", "true", "false"
    ]
    assert result["inference"]["candidate"] in result["inference"]["grammar_atoms"]
    assert result["inference"]["consistency"]["status"] == "sat"
    assert result["inference"]["goal_reachability"]["status"] == "sat"
    assert result["inference"]["counterexample"]["status"] == "unsat"
    assert result["inference"]["locally_sufficient"] is True
    assert result["inference"]["formulas"]["candidate"] == (
        result["inference"]["candidate"]
    )
    assert result["inference"]["formulas"] == result["formulas"]
    assert result["inference"]["formula_identity"] == result["formula_identity"]
    assert result["formulas"]["inference"] == {
        **result["inference"]["invocation"],
        "invocation_identity": result["inference"]["invocation_identity"],
    }


def test_public_module_does_not_import_handwritten_answer_modules() -> None:
    source = (RESEARCH / "local_solver.py").read_text()
    worker = (RESEARCH / "local_solver_worker.py").read_text()

    for forbidden in ("import models", "import build_examples", "import poc"):
        assert forbidden not in source
        assert forbidden not in worker


def test_fixed_isolated_command_and_no_agent_selected_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen = {}

    async def fake_communicate(command, **kwargs):
        seen["command"] = command
        seen.update(kwargs)
        return 0, b'{"status":"unsupported"}', b""

    monkeypatch.setattr(local_solver, "communicate", fake_communicate)
    result = _check(_int_model(), [])

    assert result["status"] == "unsupported"
    assert seen["command"] == [
        sys.executable, "-I", str(RESEARCH / "local_solver_worker.py")
    ]
    assert seen["cwd"] != str(RESEARCH)
    assert b"path" not in seen["input_bytes"]
    assert b"script" not in seen["input_bytes"]


def test_solver_unknown_is_preserved(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_communicate(command, **kwargs):
        return 0, b'{"status":"unknown","reason":"RESOURCEOUT"}', b""

    monkeypatch.setattr(local_solver, "communicate", fake_communicate)

    result = _check(_int_model(), [])

    assert result["status"] == "unknown"
    assert result["reason"] == "RESOURCEOUT"
    assert result["locally_sufficient"] is False


@pytest.mark.parametrize(
    ("mode", "kwargs", "status"),
    [
        ("parse_error", {}, "parse_error"),
        ("crash", {}, "native_crash"),
        ("hang", {"timeout_seconds": 0.05}, "timeout"),
        ("output", {"max_output_bytes": 1024}, "output_limit"),
        ("memory", {}, "worker_error"),
        ("worker_error", {}, "worker_error"),
    ],
)
def test_native_failures_are_isolated_and_distinct(
    mode: str, kwargs: dict, status: str
) -> None:
    result = asyncio.run(local_solver._run_worker_for_test(mode, **kwargs))

    assert result["status"] == status
    assert result["locally_sufficient"] is False
    # A fresh worker still succeeds, proving the host process survived the failure.
    healthy = _check(_null_model(), [{"operator": "is_none", "literal": None}])
    assert healthy["locally_sufficient"] is True
