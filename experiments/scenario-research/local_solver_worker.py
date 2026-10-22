"""Resource-limited native worker for the scenario-research local solver."""

from __future__ import annotations

import hashlib
import json
import os
import resource
import signal
import sys
import time
from typing import Any


_ADDRESS_SPACE_BYTES = 512 * 1024 * 1024
_CPU_SECONDS = 4
_FILE_BYTES = 1 * 1024 * 1024


def _set_limits() -> None:
    resource.setrlimit(resource.RLIMIT_AS, (_ADDRESS_SPACE_BYTES, _ADDRESS_SPACE_BYTES))
    resource.setrlimit(resource.RLIMIT_CPU, (_CPU_SECONDS, _CPU_SECONDS))
    resource.setrlimit(resource.RLIMIT_FSIZE, (_FILE_BYTES, _FILE_BYTES))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))


_set_limits()

# The native extension is deliberately imported only after process limits exist.
import cvc5  # noqa: E402
from cvc5 import Kind as K  # noqa: E402


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _conjunction(tm: Any, terms: list[Any]) -> Any:
    if not terms:
        return tm.mkBoolean(True)
    if len(terms) == 1:
        return terms[0]
    return tm.mkTerm(K.AND, *terms)


def _condition(tm: Any, response: Any, condition: dict[str, Any]) -> Any:
    operator = condition["operator"]
    if operator == "is_none":
        return response
    if operator == "is_not_none":
        return tm.mkTerm(K.NOT, response)
    literal = tm.mkInteger(condition["literal"])
    kind = {
        "le": K.LEQ,
        "lt": K.LT,
        "gt": K.GT,
        "ge": K.GEQ,
        "eq": K.EQUAL,
        "ne": K.DISTINCT,
    }[operator]
    return tm.mkTerm(kind, response, literal)


def _solver(tm: Any, *, abduct: bool = False) -> Any:
    solver = cvc5.Solver(tm)
    solver.setLogic("ALL")
    solver.setOption("tlimit-per", "2500")
    if abduct:
        solver.setOption("produce-abducts", "true")
    return solver


def _query(tm: Any, assertions: list[Any]) -> dict[str, Any]:
    solver = _solver(tm)
    for term in assertions:
        solver.assertFormula(term)
    started = time.perf_counter()
    result = solver.checkSat()
    if result.isSat():
        status = "sat"
    elif result.isUnsat():
        status = "unsat"
    else:
        status = "unknown"
    serialized = [str(term) for term in assertions]
    record = {
        "status": status,
        "raw_result": str(result),
        "assertions": serialized,
        "assertions_identity": _sha256(json.dumps(serialized, separators=(",", ":"))),
        "duration_ms": round((time.perf_counter() - started) * 1000, 3),
    }
    if result.isUnknown():
        record["reason"] = str(result.getUnknownExplanation())
    return record


def _smt_source(declaration: str, assertions: list[Any]) -> str:
    lines = ["(set-logic ALL)", declaration]
    lines.extend(f"(assert {term})" for term in assertions)
    lines.append("(check-sat)")
    return "\n".join(lines) + "\n"


def _round_trip(source: str, expected: str) -> dict[str, Any]:
    solver = cvc5.Solver()
    solver.setOption("tlimit-per", "2500")
    parser = cvc5.InputParser(solver)
    parser.setStringInput(
        cvc5.InputLanguage.SMT_LIB_2_6, source, "host-generated-local-model.smt2"
    )
    commands = []
    result = None
    allowed = {"set-logic", "declare-fun", "assert", "check-sat"}
    try:
        while True:
            command = parser.nextCommand()
            if command.isNull():
                break
            name = command.getCommandName()
            if name not in allowed:
                return {"status": "parse_error", "diagnostic": "generated command is not allowed"}
            commands.append(name)
            invoked = command.invoke(solver, parser.getSymbolManager())
            if name == "check-sat":
                result = str(invoked).strip()
    except RuntimeError as exc:
        return {"status": "parse_error", "diagnostic": str(exc)}
    return {
        "status": "sat" if result == "sat" else ("unsat" if result == "unsat" else "unknown"),
        "result": result,
        "matches": result == expected,
        "commands": commands,
        "source_identity": _sha256(source),
    }


def _infer(tm: Any, background: list[Any], goal: Any) -> tuple[Any | None, dict[str, Any]]:
    solver = _solver(tm, abduct=True)
    for term in background:
        solver.assertFormula(term)
    nonterminal = tm.mkVar(tm.getBooleanSort(), "H")
    grammar = solver.mkGrammar([], [nonterminal])
    grammar_atoms = [goal, tm.mkTerm(K.NOT, goal), tm.mkBoolean(True), tm.mkBoolean(False)]
    grammar.addRules(nonterminal, grammar_atoms)
    try:
        abduct = solver.getAbduct(goal, grammar)
    except RuntimeError as exc:
        return None, {"status": "worker_error", "diagnostic": str(exc)}
    if abduct.isNull():
        return None, {
            "status": "unknown",
            "diagnostic": "getAbduct returned a null term",
            "grammar_atoms": [str(term) for term in grammar_atoms],
        }
    invocation = {
        "api": "getAbduct",
        "background": [str(term) for term in background],
        "goal": str(goal),
        "grammar_atoms": [str(term) for term in grammar_atoms],
        "returned_candidate": str(abduct),
    }
    return abduct, {
        "candidate": str(abduct),
        "candidate_identity": _sha256(str(abduct)),
        "grammar_atoms": [str(term) for term in grammar_atoms],
        "invocation": invocation,
        "invocation_identity": _sha256(
            json.dumps(invocation, sort_keys=True, separators=(",", ":"))
        ),
    }


def _check(request: dict[str, Any]) -> dict[str, Any]:
    model = request["model"]
    tm = cvc5.TermManager()
    sort = tm.getIntegerSort() if model["sort"] == "Int" else tm.getBooleanSort()
    response = tm.mkConst(sort, "response")
    declaration = f"(declare-fun response () {sort})"

    background = []
    if model["bounds"] is not None:
        minimum, maximum = model["bounds"]
        background = [
            tm.mkTerm(K.GEQ, response, tm.mkInteger(minimum)),
            tm.mkTerm(K.LEQ, response, tm.mkInteger(maximum)),
        ]
    atomic_goal = _condition(tm, response, model)
    goal = atomic_goal if request["goal_branch"] else tm.mkTerm(K.NOT, atomic_goal)
    inference = None
    if request["infer"]:
        candidate, inference = _infer(tm, background, goal)
        if candidate is None:
            return {
                **inference,
                "locally_sufficient": False,
                "whole_test_verified": False,
            }
    else:
        candidates = [_condition(tm, response, value) for value in request["candidates"]]
        candidate = _conjunction(tm, candidates)
    negated_goal = tm.mkTerm(K.NOT, goal)

    consistency_terms = [*background, candidate]
    reachability_terms = [*consistency_terms, goal]
    counterexample_terms = [*consistency_terms, negated_goal]
    consistency = _query(tm, consistency_terms)
    reachability = _query(tm, reachability_terms)
    counterexample = _query(tm, counterexample_terms)

    formulas = {
        "declarations": [declaration],
        "background": [str(term) for term in background],
        "goal": str(goal),
        "candidate": str(candidate),
    }
    if inference is not None:
        formulas["inference"] = {
            **inference["invocation"],
            "invocation_identity": inference["invocation_identity"],
        }
    formula_identity = _sha256(json.dumps(formulas, sort_keys=True, separators=(",", ":")))
    source = _smt_source(declaration, counterexample_terms)
    round_trip = _round_trip(source, counterexample["status"])
    if round_trip["status"] == "parse_error":
        return {**round_trip, "formula_identity": formula_identity, "formulas": formulas}
    if not round_trip["matches"]:
        return {
            "status": "worker_error",
            "diagnostic": "generated formula round-trip changed the query result",
            "formula_identity": formula_identity,
            "formulas": formulas,
            "round_trip": round_trip,
        }

    locally_sufficient = (
        consistency["status"] == "sat"
        and reachability["status"] == "sat"
        and counterexample["status"] == "unsat"
    )
    query_statuses = {
        consistency["status"], reachability["status"], counterexample["status"]
    }
    overall_status = "unknown" if "unknown" in query_statuses else counterexample["status"]
    record = {
        "status": overall_status,
        "locally_sufficient": locally_sufficient,
        "whole_test_verified": False,
        "symbol": {"name": "response", "sort": model["sort"]},
        "formulas": formulas,
        "formula_identity": formula_identity,
        "queries": {
            "consistency": consistency,
            "goal_reachability": reachability,
            "counterexample": counterexample,
        },
        "consistency": consistency,
        "goal_reachability": reachability,
        "counterexample": counterexample,
        "round_trip": round_trip,
        "solver": {
            "cvc5": cvc5.__version__,
            "version": cvc5.Solver().getVersion().decode(),
        },
        "resource_limits": {
            "address_space_bytes": resource.getrlimit(resource.RLIMIT_AS)[0],
            "cpu_seconds": resource.getrlimit(resource.RLIMIT_CPU)[0],
            "file_bytes": resource.getrlimit(resource.RLIMIT_FSIZE)[0],
            "core_bytes": resource.getrlimit(resource.RLIMIT_CORE)[0],
        },
        "semantics": {
            "scope": "normal return value and one local branch condition only",
            "bool_symbol": "true means the dependency response is None",
            "exceptions": "not modeled",
        },
    }
    if inference is not None:
        record["inference"] = {
            **inference,
            "status": "candidate_found",
            "formulas": formulas,
            "formula_identity": formula_identity,
            "consistency": consistency,
            "goal_reachability": reachability,
            "counterexample": counterexample,
            "locally_sufficient": locally_sufficient,
        }
    return record


def _internal_test(mode: str) -> dict[str, Any]:
    if mode == "crash":
        os.kill(os.getpid(), signal.SIGSEGV)
    if mode == "hang":
        while True:
            time.sleep(1)
    if mode == "output":
        sys.stdout.write("x" * (2 * 1024 * 1024))
        sys.stdout.flush()
        while True:
            time.sleep(1)
    if mode == "worker_error":
        raise RuntimeError("internal worker failure")
    if mode == "memory":
        bytearray(_ADDRESS_SPACE_BYTES * 2)
    if mode == "parse_error":
        malformed = "(set-logic ALL)\n(assert missing_symbol)\n(check-sat)\n"
        result = _round_trip(malformed, "sat")
        # Avoid native parser teardown after its known error path.
        sys.stdout.write(json.dumps(result, sort_keys=True, separators=(",", ":")))
        sys.stdout.flush()
        os._exit(0)
    raise ValueError("unknown internal test mode")


def main() -> int:
    try:
        request = json.loads(sys.stdin.read())
        if request.get("action") == "internal_test":
            result = _internal_test(request["mode"])
        elif request.get("action") == "check":
            result = _check(request)
        else:
            result = {"status": "unsupported", "diagnostic": "unsupported worker action"}
    except Exception:
        result = {
            "status": "worker_error",
            "locally_sufficient": False,
            "whole_test_verified": False,
            "diagnostic": "local solver worker rejected the request",
        }
    sys.stdout.write(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
