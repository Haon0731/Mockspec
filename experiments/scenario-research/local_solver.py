"""Host boundary for source-derived, finite local cvc5 models.

The public API accepts only host-normalized typed data.  It never accepts SMT,
paths, commands, or symbol names from an agent-facing request.
"""

from __future__ import annotations

import asyncio
import json
import math
from pathlib import Path
import signal
import sys
import tempfile
from typing import Any

from mock_spec.analysis_process import communicate


_HERE = Path(__file__).resolve().parent
_WORKER = (_HERE / "local_solver_worker.py").resolve()
_MAX_OUTPUT_BYTES = 256_000
_OPERATORS = frozenset({
    "le", "lt", "gt", "ge", "eq", "ne", "is_none", "is_not_none",
})
_INT_OPERATORS = frozenset({"le", "lt", "gt", "ge", "eq", "ne"})
_NONE_OPERATORS = frozenset({"is_none", "is_not_none"})


def _unsupported(message: str) -> dict[str, Any]:
    return {
        "status": "unsupported",
        "locally_sufficient": False,
        "whole_test_verified": False,
        "diagnostic": message,
    }


def _validate_condition(value: object, *, sort: str, label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != {"operator", "literal"}:
        raise ValueError(f"{label} must contain exactly operator and literal")
    operator = value["operator"]
    literal = value["literal"]
    if not isinstance(operator, str) or operator not in _OPERATORS:
        raise ValueError(f"{label} operator is unsupported")
    if sort == "Int":
        if operator not in _INT_OPERATORS:
            raise ValueError(f"{label} operator is not valid for Int")
        if type(literal) is not int:
            raise ValueError(f"{label} literal must be an integer")
    else:
        if operator not in _NONE_OPERATORS:
            raise ValueError(f"{label} operator is not valid for Bool/nullness")
        if literal is not None:
            raise ValueError(f"{label} nullness literal must be null")
    return {"operator": operator, "literal": literal}


def _normalize_request(
    model: object,
    candidates: object,
    *,
    goal_branch: object,
    infer: object,
) -> dict[str, Any]:
    if not isinstance(model, dict) or set(model) != {
        "sort", "operator", "literal", "bounds"
    }:
        raise ValueError("model must contain exactly sort, operator, literal, and bounds")
    sort = model["sort"]
    if not isinstance(sort, str) or sort not in {"Int", "Bool"}:
        raise ValueError("model sort is unsupported")
    goal = _validate_condition(
        {"operator": model["operator"], "literal": model["literal"]},
        sort=sort,
        label="model",
    )
    bounds = model["bounds"]
    normalized_bounds = None
    if sort == "Int":
        if bounds is not None:
            if (
                not isinstance(bounds, list)
                or len(bounds) != 2
                or any(type(item) is not int for item in bounds)
                or bounds[0] > bounds[1]
            ):
                raise ValueError("Int bounds must be [minimum, maximum]")
            normalized_bounds = [bounds[0], bounds[1]]
    elif bounds is not None:
        raise ValueError("Bool/nullness models do not have numeric bounds")
    if not isinstance(candidates, list):
        raise ValueError("candidates must be a list")
    normalized_candidates = [
        _validate_condition(value, sort=sort, label=f"candidate[{index}]")
        for index, value in enumerate(candidates)
    ]
    if type(goal_branch) is not bool:
        raise ValueError("goal_branch must be boolean")
    if type(infer) is not bool:
        raise ValueError("infer must be boolean")
    return {
        "action": "check",
        "model": {
            "sort": sort,
            "operator": goal["operator"],
            "literal": goal["literal"],
            "bounds": normalized_bounds,
        },
        "candidates": normalized_candidates,
        "goal_branch": goal_branch,
        "infer": infer,
    }


async def _invoke_worker(
    payload: dict[str, Any],
    *,
    timeout_seconds: float,
    max_output_bytes: int = _MAX_OUTPUT_BYTES,
) -> dict[str, Any]:
    request = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    try:
        with tempfile.TemporaryDirectory(prefix="mockspec-local-solver-") as work:
            code, stdout, stderr = await communicate(
                [sys.executable, "-I", str(_WORKER)],
                input_bytes=request,
                timeout_seconds=timeout_seconds,
                cwd=work,
                max_output_bytes=max_output_bytes,
            )
    except asyncio.TimeoutError:
        return _process_failure("timeout", "local solver worker exceeded its wall-clock limit")
    except asyncio.CancelledError:
        raise
    except OverflowError:
        return _process_failure("output_limit", "local solver worker exceeded its output limit")
    except OSError:
        return _process_failure("worker_error", "local solver worker could not be launched")

    if code < 0:
        signal_number = -code
        try:
            signal_name = signal.Signals(signal_number).name
        except ValueError:
            signal_name = f"signal {signal_number}"
        return _process_failure(
            "native_crash", f"local solver worker terminated by {signal_name}", exit_code=code
        )
    if code != 0:
        return _process_failure(
            "worker_error", "local solver worker exited unsuccessfully", exit_code=code
        )
    if stderr:
        return _process_failure("worker_error", "local solver worker wrote to stderr")
    try:
        result = json.loads(stdout.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return _process_failure("worker_error", "local solver worker returned invalid JSON")
    if not isinstance(result, dict) or result.get("status") not in {
        "sat", "unsat", "unknown", "unsupported", "parse_error", "worker_error"
    }:
        return _process_failure("worker_error", "local solver worker returned an invalid record")
    result.setdefault("locally_sufficient", False)
    result.setdefault("whole_test_verified", False)
    return result


def _process_failure(status: str, diagnostic: str, **details: object) -> dict[str, Any]:
    return {
        "status": status,
        "locally_sufficient": False,
        "whole_test_verified": False,
        "diagnostic": diagnostic,
        **details,
    }


async def check_local_model(
    model: object,
    candidates: object,
    *,
    goal_branch: bool = True,
    infer: bool = False,
    expected_formula_identity: str | None = None,
    timeout_seconds: float = 5.0,
) -> dict[str, Any]:
    """Check a candidate against one host-normalized local branch model.

    ``status`` is the counterexample query's SAT/UNSAT/UNKNOWN status when all
    three checks run.  ``locally_sufficient`` additionally requires a SAT
    consistency query, SAT goal-reachability query, and UNSAT counterexample.
    """

    if (
        type(timeout_seconds) not in {int, float}
        or not math.isfinite(timeout_seconds)
        or timeout_seconds <= 0
    ):
        raise ValueError("timeout_seconds must be positive and finite")
    if expected_formula_identity is not None and (
        not isinstance(expected_formula_identity, str)
        or len(expected_formula_identity) != 64
        or any(character not in "0123456789abcdef" for character in expected_formula_identity)
    ):
        return _unsupported("expected_formula_identity must be a lowercase SHA-256 digest")
    try:
        request = _normalize_request(
            model, candidates, goal_branch=goal_branch, infer=infer
        )
    except ValueError as exc:
        return _unsupported(str(exc))
    result = await _invoke_worker(request, timeout_seconds=float(timeout_seconds))
    actual_identity = result.get("formula_identity")
    if (
        expected_formula_identity is not None
        and actual_identity is not None
        and actual_identity != expected_formula_identity
    ):
        return {
            **_unsupported("recorded formula identity does not match the checked formula"),
            "expected_formula_identity": expected_formula_identity,
            "actual_formula_identity": actual_identity,
        }
    return result


async def _run_worker_for_test(
    mode: str,
    *,
    timeout_seconds: float = 1.0,
    max_output_bytes: int = _MAX_OUTPUT_BYTES,
) -> dict[str, Any]:
    """Exercise process failure paths without exposing them through the public API."""

    if mode not in {
        "parse_error", "crash", "hang", "output", "memory", "worker_error"
    }:
        raise ValueError("unknown internal test mode")
    return await _invoke_worker(
        {"action": "internal_test", "mode": mode},
        timeout_seconds=timeout_seconds,
        max_output_bytes=max_output_bytes,
    )


__all__ = ["check_local_model"]
