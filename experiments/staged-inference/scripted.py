"""Fixed SDK model and synthetic replay fixtures for M1 result states."""

from __future__ import annotations

import json
from copy import deepcopy
from typing import Literal

from agents.models.interface import Model, ModelResponse
from agents.usage import Usage
from openai.types.responses import ResponseOutputMessage, ResponseOutputText


def synthetic_task() -> dict:
    return {
        "schema_version": "mock-spec.task/v1", "language": "python",
        "case_id": "synthetic_python_delegation",
        "mut_source": {
            "source_id": "mut:0", "logical_path": "src/example.py",
            "text": "def target(value):\n    return dependency(value)",
            "symbol": "example.target", "start_line": 10, "end_line": 11,
            "resolution_status": "resolved", "target_precision": "function",
        },
        "masked_test": {
            "source_id": "test:0", "logical_path": "tests/test_example.py",
            "text": "def test_target():\n    assert target(1) == 2",
            "symbol": "test_example.test_target", "start_line": 4, "end_line": 5,
            "resolution_status": "resolved", "target_precision": "function",
        },
        "permitted_interfaces": [{
            "symbol": "example.dependency", "signature": "dependency(value)",
            "source_id": "support:0", "start_line": 1, "end_line": 1,
            "resolution_status": "partial",
        }],
        "supporting_sources": [{
            "source_id": "support:0", "logical_path": "src/dependency.py",
            "text": "def dependency(value): ...", "symbol": "example.dependency",
            "start_line": 1, "end_line": 1, "resolution_status": "partial",
            "target_precision": "function",
        }],
        "execution_context": {
            "runtime_name": "python", "runtime_version": "3.10",
            "test_framework": "pytest", "test_framework_version": None,
        },
        "input_policy": {
            "policy_id": "synthetic-replay-v1",
            "protocol_version": "synthetic_replay_v1",
            "masking_contract": "artifact-aware-slot-v1",
            "visibility": "reviewed_frozen_sources",
        },
        "sources": [
            {"source_id": "mut:0", "logical_path": "src/example.py", "role": "mut"},
            {"source_id": "test:0", "logical_path": "tests/test_example.py", "role": "masked_test"},
            {"source_id": "support:0", "logical_path": "src/dependency.py", "role": "supporting"},
        ],
    }


ReplayCase = Literal[
    "selected", "ambiguous", "inconclusive", "error",
    "cache_miss", "cache_unspecified", "legal_variation", "second_revision",
]


def cache_task(*, require_refill: bool) -> dict:
    """Synthetic cache case whose only difference is the explicit task requirement."""
    requirement = (
        "    # Requirement: on a cache miss, fetch and refill the cache.\n"
        if require_refill else ""
    )
    return {
        "schema_version": "mock-spec.task/v1", "language": "python",
        "case_id": "synthetic_python_cache",
        "mut_source": {
            "source_id": "mut:0", "logical_path": "src/cache_service.py",
            "text": (
                "def load(key, cache, backend):\n"
                "    cached = cache.get(key)\n"
                "    if cached is not None:\n"
                "        return cached\n"
                "    value = backend.fetch(key)\n"
                "    cache.set(key, value)\n"
                "    return value"
            ),
            "symbol": "cache_service.load", "start_line": 10, "end_line": 16,
            "resolution_status": "resolved", "target_precision": "function",
        },
        "masked_test": {
            "source_id": "test:0", "logical_path": "tests/test_cache_service.py",
            "text": (
                "def test_load_returns_record():\n"
                f"{requirement}"
                "    assert load('k', cache, backend) == {'id': 1, 'name': 'Ada'}"
            ),
            "symbol": "test_cache_service.test_load_returns_record", "start_line": 4,
            "end_line": 6 if require_refill else 5,
            "resolution_status": "resolved", "target_precision": "function",
        },
        "permitted_interfaces": [
            {"symbol": "cache.get", "signature": "get(key)", "source_id": "support:0",
             "start_line": 1, "end_line": 1, "resolution_status": "partial"},
            {"symbol": "cache.set", "signature": "set(key, value)", "source_id": "support:0",
             "start_line": 2, "end_line": 2, "resolution_status": "partial"},
            {"symbol": "backend.fetch", "signature": "fetch(key)", "source_id": "support:0",
             "start_line": 3, "end_line": 3, "resolution_status": "partial"},
        ],
        "supporting_sources": [{
            "source_id": "support:0", "logical_path": "src/ports.py",
            "text": "cache.get(key)\ncache.set(key, value)\nbackend.fetch(key)",
            "symbol": "cache_service.ports", "start_line": 1, "end_line": 3,
            "resolution_status": "partial", "target_precision": "class",
        }],
        "execution_context": {
            "runtime_name": "python", "runtime_version": "3.10",
            "test_framework": "pytest", "test_framework_version": None,
        },
        "input_policy": {
            "policy_id": "synthetic-replay-v1", "protocol_version": "synthetic_replay_v1",
            "masking_contract": "artifact-aware-slot-v1",
            "visibility": "reviewed_frozen_sources",
        },
        "sources": [
            {"source_id": "mut:0", "logical_path": "src/cache_service.py", "role": "mut"},
            {"source_id": "test:0", "logical_path": "tests/test_cache_service.py",
             "role": "masked_test"},
            {"source_id": "support:0", "logical_path": "src/ports.py",
             "role": "supporting"},
        ],
    }


def task_for_replay_case(replay_case: ReplayCase) -> dict:
    if replay_case in ("cache_miss", "legal_variation", "second_revision"):
        return cache_task(require_refill=True)
    if replay_case == "cache_unspecified":
        return cache_task(require_refill=False)
    return synthetic_task()


def _source_locator(reference: dict) -> dict:
    """Build the explicit v0.2 wire locator used by deterministic fixtures."""
    return {
        "source_id": reference["source_id"],
        "start_line": reference["start_line"],
        "end_line": reference["end_line"],
    }


def _contract_selection(contract: dict | None) -> dict | None:
    if contract is None:
        return None
    selected = deepcopy(contract)
    selected["evidence"] = [
        {
            **_source_locator(reference),
            "id": reference["id"],
            "rationale": reference["rationale"],
        }
        for reference in selected["evidence"]
    ]
    return selected


def _fixture_spec(
    contract: dict | None, *, result_status: str, allowed_variations: list[str],
    remaining_question: str, remaining_impact: str,
) -> dict | None:
    """Build explicit semantics for fixed synthetic fixtures only.

    Production code must not infer these judgments from a completed contract.
    """
    if contract is None:
        return None
    evidence = contract["evidence"]
    evidence_ids = [item["id"] for item in evidence]
    test_evidence = [
        item["id"] for item in evidence if item["source_id"].startswith("test:")
    ]
    implementation_evidence = [
        item["id"] for item in evidence if not item["source_id"].startswith("test:")
    ]
    scenario_ids = [item["id"] for item in contract["scenario_hypotheses"]]
    ambiguous = result_status == "AMBIGUOUS"
    ambiguity_id = "unresolved-scenario-choice"
    premises = [
        {"id": "fixture-implementation", "kind": "implementation_fact",
         "statement": "The synthetic MUT evidence defines the visible execution paths.",
         "evidence_ids": implementation_evidence, "assumption_impact": None},
        {"id": "fixture-requirement", "kind": "test_requirement",
         "statement": "The synthetic test evidence defines the fixed observable goals.",
         "evidence_ids": test_evidence, "assumption_impact": None},
    ]
    unknowns = [{
        "id": ambiguity_id if ambiguous else "unobserved-internals",
        "question": remaining_question,
        "impact": remaining_impact,
        "disposition": "blocking" if ambiguous else "nonblocking",
        "goal_indexes": list(range(len(contract["test_objectives"]))),
        "constraint_indexes": list(range(len(contract["dependency_constraints"]))),
        "scenario_ids": scenario_ids,
        "formal_uncertainty_indexes": list(range(len(contract["uncertainty"]))),
        "rationale": (
            "The missing selection basis blocks a safe branch choice."
            if ambiguous else
            "The fixture constrains observable behavior without specifying internals."
        ),
    }]
    return {
        "schema_version": "staged-spec-semantics/v0.1",
        "delivery_status": "partial" if ambiguous else "ready",
        "fixed_inputs": [{
            "statement": "The synthetic test inputs and invocation conditions are fixed.",
            "goal_indexes": list(range(len(contract["test_objectives"]))),
            "evidence_ids": test_evidence,
        }],
        "replacement_boundary": [{
            "id": "fixture-dependencies", "target": "The dependency calls in the formal contract",
            "replaceable_behavior": "Only the declared dependency responses and effects are controlled.",
            "preserved_behavior": "The synthetic MUT computation and branch logic remain real.",
            "status": "confirmed", "evidence_ids": implementation_evidence,
            "premise_ids": ["fixture-implementation"], "unknown_ids": [],
        }],
        "preserved_real_logic": [{
            "statement": "The visible synthetic MUT path and computation remain real code.",
            "evidence_ids": implementation_evidence,
            "premise_ids": ["fixture-implementation"],
        }],
        "premises": premises,
        "constraint_basis": [{
            "constraint_index": index,
            "premise_ids": ["fixture-implementation", "fixture-requirement"],
        } for index in range(len(contract["dependency_constraints"]))],
        "branches": [{
            "scenario_id": scenario_id,
            "disposition": "unresolved" if ambiguous else "allowed",
            "rationale": (
                "The fixture lacks evidence that makes this candidate an allowed choice."
                if ambiguous else
                "The implementation facts and test requirement support this complete branch."
            ),
            "evidence_ids": [] if ambiguous else evidence_ids,
            "premise_ids": [] if ambiguous else [
                "fixture-implementation", "fixture-requirement"
            ],
            "unknown_ids": [ambiguity_id] if ambiguous else [],
        } for scenario_id in scenario_ids],
        "goal_coverage": [{
            "goal_index": index,
            "disposition": "blocked" if ambiguous else "constrained",
            "constraint_indexes": (
                [] if ambiguous else list(range(len(contract["dependency_constraints"])))
            ),
            "unknown_ids": [ambiguity_id] if ambiguous else [],
            "rationale": (
                "Candidate meaning remains unresolved."
                if ambiguous else "The mapped dependency constraints support this fixed goal."
            ),
            "evidence_ids": goal["evidence_ids"],
            "premise_ids": ["fixture-implementation", "fixture-requirement"],
        } for index, goal in enumerate(contract["test_objectives"])],
        "unknowns": unknowns,
        "allowed_variations": [{
            "id": f"fixture-variation-{index + 1}",
            "statement": statement,
            "scenario_ids": scenario_ids,
            "constraint_indexes": list(range(len(contract["dependency_constraints"]))),
            "evidence_ids": evidence_ids,
            "premise_ids": ["fixture-implementation", "fixture-requirement"],
        } for index, statement in enumerate(allowed_variations)],
        "conflicts": [],
    }


def _wire_outputs(outputs: list[dict]) -> list[dict]:
    """Render canonical fixture semantics in the current model-facing protocol."""
    rendered = deepcopy(outputs)
    initial_unresolved = list(rendered[0].get("unresolved", []))
    for output in rendered:
        version = output["schema_version"]
        if version == "staged-extract/v0.1":
            output["schema_version"] = "staged-extract/v0.2"
            output["original_goals"] = [
                _source_locator(item) for item in output["original_goals"]
            ]
            for candidate in output["candidates"]:
                candidate["evidence"] = [
                    _source_locator(item) for item in candidate["evidence"]
                ]
        elif version == "staged-query/v0.1":
            output["schema_version"] = "staged-query/v0.4"
            assessment = output.get("previous_receipt_assessment")
            if assessment is not None:
                assessment.pop("attempt_sequence", None)
            stop = output.get("stop_explanation")
            if stop is not None:
                stop["evidence"] = [_source_locator(item) for item in stop["evidence"]]
                updates = []
                for index, unknown in enumerate(stop["remaining_unknowns"]):
                    question_id = f"q{index + 1}" if index < len(initial_unresolved) else None
                    question = (
                        initial_unresolved[index]
                        if question_id is not None else unknown["question"]
                    )
                    unknown["question"] = question
                    updates.append({
                        "question_id": question_id,
                        "question": question,
                        "goal_indexes": [0],
                        "impact": unknown["impact"],
                        "state": "open" if unknown["blocking"] else "nonblocking",
                        "rationale": (
                            "The remaining gap blocks a safe scenario decision."
                            if unknown["blocking"] else
                            "The cited goal evidence makes this detail unnecessary for the result."
                        ),
                        # For an initial-only run this evidence identifies the
                        # observed behavior affected by the inaccessible gap;
                        # it does not claim to resolve the blocking question.
                        "evidence": list(stop["evidence"]),
                        "acquisition": "unavailable" if unknown["blocking"] else "not_needed",
                        "attempted_sequences": [],
                    })
                output["question_updates"] = updates
            else:
                output["question_updates"] = []
            output["candidate_updates"] = []
        elif version == "staged-construct/v0.1":
            output["schema_version"] = "staged-construct/v0.3"
            output["contract"] = _contract_selection(output["contract"])
            output.setdefault("investigation_requests", [])
        elif version == "staged-revise/v0.1":
            output["schema_version"] = "staged-revise/v0.2"
            revised = output["revised_construct"]
            revised["schema_version"] = "staged-construct/v0.3"
            revised["contract"] = _contract_selection(revised["contract"])
            revised.setdefault("investigation_requests", [])
        elif version == "staged-deliver/v0.1":
            output["schema_version"] = "staged-deliver/v0.3"
            output["contract"] = _contract_selection(output["contract"])
    return rendered


def _contract(*, ambiguous: bool = False) -> dict:
    scenario_ids = ["dependency_returns_two"]
    if ambiguous:
        scenario_ids.append("dependency_returns_float_two")
    objective = {
        "statement": "The visible test expects target(1) to equal 2.",
        "status": "required", "scenario_ids": scenario_ids,
        "condition": "The visible test invokes target(1).", "evidence_ids": ["assertion"],
    }
    return {
        "schema_version": "mock-spec.scenario/v0.1",
        "case_id": "synthetic_python_delegation", "language": "python",
        "test_objectives": [objective],
        "scenario_hypotheses": [{
            "id": "dependency_returns_two", "description": "The dependency returns 2.",
            "condition": "target receives value 1.", "status": "candidate",
            "evidence_ids": ["delegation", "assertion"],
        }, *([{
            "id": "dependency_returns_float_two",
            "description": "The dependency returns 2.0, which the ordinary equality accepts.",
            "condition": "target receives value 1.", "status": "candidate",
            "evidence_ids": ["delegation", "assertion"],
        }] if ambiguous else [])],
        "dependency_constraints": ([{
            "statement": "dependency(1) returns 2.",
            "status": "candidate" if ambiguous else "required",
            "scenario_ids": ["dependency_returns_two"],
            "condition": "target delegates value 1 and completes normally.",
            "evidence_ids": ["delegation", "assertion"],
        }, {
            "statement": "dependency(1) returns the float 2.0.", "status": "candidate",
            "scenario_ids": ["dependency_returns_float_two"],
            "condition": "target delegates value 1 and completes normally.",
            "evidence_ids": ["delegation", "assertion"],
        }] if ambiguous else [{
            "statement": "dependency(1) returns 2.", "status": "required",
            "scenario_ids": ["dependency_returns_two"],
            "condition": "target delegates value 1 and completes normally.",
            "evidence_ids": ["delegation", "assertion"],
        }]),
        "expected_interactions": [{
            "statement": "dependency is called with 1.", "status": "required",
            "scenario_ids": scenario_ids,
            "condition": "target is called with 1.",
            "evidence_ids": ["delegation", "assertion"],
            "assertion_policy": "not_implied",
        }],
        "verification_candidates": [],
        "evidence": [
            {"id": "delegation", "source_id": "mut:0", "start_line": 11,
             "end_line": 11, "quote": "    return dependency(value)",
             "rationale": "The target directly returns the dependency result."},
            {"id": "assertion", "source_id": "test:0", "start_line": 5,
             "end_line": 5, "quote": "    assert target(1) == 2",
             "rationale": "The visible ordinary oracle checks the returned value."},
        ],
        "uncertainty": [{
            "statement": "The dependency implementation is not visible.", "status": "unknown",
            "scenario_ids": [], "condition": None, "evidence_ids": [],
        }],
    }


def _cache_contract(*, ambiguous: bool, revised: bool = False) -> dict:
    miss_id = "cache_miss_refill"
    scenario_ids = [miss_id, "cached_hit"] if ambiguous else [miss_id]
    test_line = 5 if ambiguous else 6
    objectives = [{
        "statement": "The visible test expects load('k', ...) to return the record.",
        "status": "required", "scenario_ids": scenario_ids,
        "condition": "The visible test calls load with key 'k'.", "evidence_ids": ["assertion"],
    }]
    if not ambiguous:
        objectives.append({
            "statement": "On a cache miss, the target fetches and refills the cache.",
            "status": "required", "scenario_ids": [miss_id],
            "condition": "cache.get('k') returns None.",
            "evidence_ids": ["miss_requirement"],
        })
    hypotheses = [{
        "id": miss_id,
        "description": "A cache miss fetches the record and refills the cache.",
        "condition": "cache.get('k') returns None.", "status": "candidate",
        "evidence_ids": (["miss_branch", "assertion"] if ambiguous else
                         ["miss_requirement", "miss_branch", "assertion"]),
    }]
    if ambiguous:
        hypotheses.append({
            "id": "cached_hit",
            "description": "The cache directly returns the asserted record.",
            "condition": "cache.get('k') returns the record.", "status": "candidate",
            "evidence_ids": ["hit_branch", "assertion"],
        })
    constraints = [
        {"statement": (
            "cache.get('k') returns None normally; this read does not itself change cache state."
         ), "status": "required",
         "scenario_ids": [miss_id], "condition": "The cache-miss scenario is used.",
         "evidence_ids": ["miss_branch"]},
        {"statement": (
            "backend.fetch receives the same key and returns the asserted record normally; "
            "no additional backend state effect is required."
         ),
         "status": "required", "scenario_ids": [miss_id],
         "condition": "cache.get returns None.", "evidence_ids": ["miss_branch", "assertion"]},
        {"statement": (
            "cache.set receives the same key and fetched value, completes normally, and records "
            "the refill request; its return is ignored and no stronger cache state is assumed."
         ),
         "status": "required", "scenario_ids": [miss_id],
         "condition": "backend.fetch returns normally.", "evidence_ids": ["refill"]},
    ]
    if ambiguous:
        constraints.append({
            "statement": "cache.get('k') may directly return the asserted record.",
            "status": "candidate", "scenario_ids": ["cached_hit"],
            "condition": "The cache-hit scenario is used.",
            "evidence_ids": ["hit_branch", "assertion"],
        })
    evidence = [
        {"id": "hit_branch", "source_id": "mut:0", "start_line": 12, "end_line": 13,
         "quote": "    if cached is not None:\n        return cached",
         "rationale": "The implementation contains a cache-hit path."},
        {"id": "miss_branch", "source_id": "mut:0", "start_line": 14, "end_line": 14,
         "quote": "    value = backend.fetch(key)",
         "rationale": "A miss reaches the backend with the same key."},
        {"id": "refill", "source_id": "mut:0", "start_line": 15, "end_line": 15,
         "quote": "    cache.set(key, value)",
         "rationale": "The miss path refills the cache with the fetched value."},
        {"id": "assertion", "source_id": "test:0", "start_line": test_line,
         "end_line": test_line,
         "quote": "    assert load('k', cache, backend) == {'id': 1, 'name': 'Ada'}",
         "rationale": "The ordinary oracle constrains only the returned value."},
    ]
    if not ambiguous:
        evidence.append({
            "id": "miss_requirement", "source_id": "test:0", "start_line": 5,
            "end_line": 5,
            "quote": "    # Requirement: on a cache miss, fetch and refill the cache.",
            "rationale": "The visible task explicitly requires miss and refill behavior.",
        })
    interactions = [{
        "statement": "backend.fetch and cache.set participate in the miss/refill path.",
        "status": "required" if not ambiguous else "candidate", "scenario_ids": [miss_id],
        "condition": "cache.get('k') returns None and backend.fetch returns normally.",
        "evidence_ids": ["miss_branch", "refill"], "assertion_policy": "not_implied",
    }]
    if ambiguous:
        interactions.append({
            "statement": "The hit path returns without backend.fetch or cache.set.",
            "status": "candidate", "scenario_ids": ["cached_hit"],
            "condition": "cache.get('k') returns the record.",
            "evidence_ids": ["hit_branch"], "assertion_policy": "not_implied",
        })
    verification = []
    if revised:
        verification = [{
            "statement": "Observe backend.fetch('k') and cache.set('k', fetched_value).",
            "status": "candidate", "scenario_ids": [miss_id],
            "condition": "The authorized verification slot can express the miss/refill observation.",
            "evidence_ids": ["miss_requirement", "miss_branch", "refill"],
        }]
    return {
        "schema_version": "mock-spec.scenario/v0.1", "case_id": "synthetic_python_cache",
        "language": "python", "test_objectives": objectives,
        "scenario_hypotheses": hypotheses, "dependency_constraints": constraints,
        "expected_interactions": interactions, "verification_candidates": verification,
        "evidence": evidence,
        "uncertainty": [{
            "statement": "Cache and backend concrete implementations remain hidden.",
            "status": "unknown", "scenario_ids": [], "condition": None, "evidence_ids": [],
        }],
    }


def _cache_outputs(replay_case: ReplayCase, *, variant: Literal["B", "C"]) -> list[dict]:
    ambiguous = replay_case == "cache_unspecified"
    contract = _cache_contract(ambiguous=ambiguous)
    revised_contract = _cache_contract(ambiguous=False, revised=True)
    selected = None if ambiguous else "cache_miss_refill"
    construct = {
        "schema_version": "staged-construct/v0.1",
        "result_status": "AMBIGUOUS" if ambiguous else "SELECTED",
        "selected_scenario_id": selected,
        "selection_rationale": (
            "The ordinary return assertion and implementation branches do not say whether the task is a hit or miss."
            if ambiguous else
            "The explicit visible requirement selects the cache-miss fetch-and-refill scenario."
        ),
        "claim_basis": [
            {"claim": "returned record", "basis": "task_requirement",
             "target": "formal_claim", "target_index": 0,
             "semantic_role": "test_objective", "evidence_ids": ["assertion"],
             "support": "evidence"},
            {"claim": "cache branches", "basis": "implementation_fact",
             "target": "formal_claim", "target_index": 0,
             "semantic_role": "mut_path_condition", "evidence_ids": ["miss_branch"],
             "support": "evidence"},
            {"claim": "miss/refill scenario", "basis": "scenario_inference",
             "target": "selection_rationale", "target_index": 0,
             "semantic_role": "dependency_contract",
             "evidence_ids": ["miss_requirement", "miss_branch"], "support": "evidence"},
            {"claim": "allowed implementation variation", "basis": "scenario_inference",
             "target": "allowed_variation", "target_index": 0,
             "semantic_role": "dependency_contract", "evidence_ids": [],
             "support": "assumption",
             "assumption_reason": "The visible task does not constrain unrelated object details."},
            *([{"claim": "hit versus miss remains unresolved", "basis": "scenario_inference",
                "target": "unresolved", "target_index": 0,
                "semantic_role": "test_objective", "evidence_ids": ["assertion"],
                "support": "evidence"}] if ambiguous else []),
        ],
        "critical_behaviors": [] if ambiguous else [
            {"behavior": "A miss fetches using the requested key.",
             "observation": "backend.fetch receives 'k'.", "evidence_ids": ["miss_requirement", "miss_branch"],
             "semantic_role": "dependency_contract", "formal_claim_index": 1,
             "support": "evidence"},
            {"behavior": "The fetched value is written back under the same key.",
             "observation": "cache.set receives 'k' and the fetched value.", "evidence_ids": ["miss_requirement", "refill"],
             "semantic_role": "dependency_contract", "formal_claim_index": 2,
             "support": "evidence"},
        ],
        "allowed_variations": [
            "Record construction form, unrelated fields on mock objects, and ignored cache.set return values may vary."
        ],
        "unresolved": ([
            "No visible task requirement distinguishes the cache-hit and cache-miss/refill candidates."
        ] if ambiguous else []),
        "spec": _fixture_spec(
            contract,
            result_status="AMBIGUOUS" if ambiguous else "SELECTED",
            allowed_variations=[
                "Record construction form, unrelated fields on mock objects, and ignored cache.set return values may vary."
            ],
            remaining_question=(
                "No visible task requirement distinguishes the cache-hit and cache-miss/refill candidates."
                if ambiguous else "What concrete cache implementation is used?"
            ),
            remaining_impact=(
                "Both remain candidates; the ambiguity blocks selection only."
                if ambiguous else
                "It does not change the selected conditional behavior."
            ),
        ),
        "investigation_requests": [],
        "contract": contract,
    }
    extract = {
        "schema_version": "staged-extract/v0.1",
        "original_goals": [{
            "source_id": "test:0", "start_line": 5 if ambiguous else 6,
            "end_line": 5 if ambiguous else 6,
            "quote": "    assert load('k', cache, backend) == {'id': 1, 'name': 'Ada'}",
        }, *([{
            "source_id": "test:0", "start_line": 5, "end_line": 5,
            "quote": "    # Requirement: on a cache miss, fetch and refill the cache.",
        }] if not ambiguous else [])],
        "candidates": [
            {"scenario_id": "cache_miss_refill", "description": "Miss, fetch, and refill.",
             "evidence": [{"source_id": "mut:0", "start_line": 14, "end_line": 15,
                           "quote": "    value = backend.fetch(key)\n    cache.set(key, value)"}]},
            *([{"scenario_id": "cached_hit", "description": "Cached value is returned directly.",
                "evidence": [{"source_id": "mut:0", "start_line": 12, "end_line": 13,
                              "quote": "    if cached is not None:\n        return cached"}]}]
              if ambiguous else []),
        ],
        "unresolved": construct["unresolved"],
    }
    deliver_construct = construct
    outputs: list[dict] = [
        extract,
        {"schema_version": "staged-query/v0.1", "decision": "finish_queries",
         "query_attempt": None, "previous_receipt_assessment": None,
         "impact": "The visible branches and task statement are sufficient for construction.",
         "stop_explanation": {
             "reason": "no_further_evidence" if ambiguous else "unknowns_nonblocking",
             "rationale": (
                 "The return assertion supports both candidates; their distinction remains explicit."
                 if ambiguous else
                 "The visible miss requirement, branches, and assertion support construction."
             ),
             "evidence": [{
                 "source_id": "test:0", "start_line": 5 if ambiguous else 6,
                 "end_line": 5 if ambiguous else 6,
                 "quote": "    assert load('k', cache, backend) == {'id': 1, 'name': 'Ada'}",
             }],
             "remaining_unknowns": ([{
                 "question": "Was the intended scenario a cache hit or miss?",
                 "impact": "Both remain candidates; the ambiguity blocks selection only.",
                 "blocking": True,
             }] if ambiguous else [{
                 "question": "What concrete cache implementation is used?",
                 "impact": "It does not change the selected conditional behavior.",
                 "blocking": False,
             }]),
         }},
        construct,
    ]
    if variant == "C":
        contrast = {
            "schema_version": "staged-contrast/v0.2", "status": "proposed",
            "execution_status": "not_executed",
            "alternative": "Mock cache.get('k') to return the asserted record directly.",
            "bypass_behavior": "The correct return bypasses backend.fetch and cache.set.",
            "evidence_ids": ["hit_branch", "assertion"] + ([] if ambiguous else ["miss_requirement"]),
            "distinguishing_observation": "Observe backend.fetch('k') and cache.set('k', fetched_value).",
            "revision_requested": replay_case in ("legal_variation", "second_revision"),
            "revision_reason": (
                "Preserve the observation while allowing equivalent record construction and unrelated mock state."
                if replay_case in ("legal_variation", "second_revision") else None
            ),
            "support": "evidence", "assumption_reason": None,
            "semantic_role": "dependency_contract",
            "weakness_review": "A direct cache hit can satisfy the return assertion without the miss path.",
            "overconstraint_review": "Record construction and ignored return values remain variable.",
            "observation_limits": "The static contrast is not executed and proves no defect detection.",
            "investigation_requests": [],
        }
        outputs.append(contrast)
        if contrast["revision_requested"]:
            revised = deepcopy(construct)
            revised["contract"] = revised_contract
            revised_variations = [
                "Any record construction form is allowed when equality and the fetch-to-refill value relationship are preserved; unrelated mock fields are unconstrained."
            ]
            revised["allowed_variations"] = revised_variations
            revised["spec"] = _fixture_spec(
                revised_contract,
                result_status="SELECTED",
                allowed_variations=revised_variations,
                remaining_question="What concrete cache implementation is used?",
                remaining_impact="It does not change the selected conditional behavior.",
            )
            outputs.append({
                "schema_version": "staged-revise/v0.1", "revised_construct": revised,
                "modified_objects": [
                    "contract.verification_candidates", "allowed_variations",
                    "spec.allowed_variations",
                ],
                "evidence_reason": "The contrast exposes an observation gap, not a basis for exact construction or unrelated fields.",
                "evidence_ids": ["miss_requirement", "miss_branch", "refill", "assertion"],
                "request_another_revision": replay_case == "second_revision",
            })
            deliver_construct = revised
            if replay_case == "second_revision":
                # A request for another semantic pass now re-enters QUERY; it
                # is not a protocol error or a fixed one-revision ceiling.
                outputs.extend([
                    deepcopy(outputs[1]),
                    deepcopy(revised),
                    {
                        **deepcopy(contrast),
                        "status": "none_identified",
                        "alternative": None,
                        "bypass_behavior": None,
                        "evidence_ids": [],
                        "distinguishing_observation": None,
                        "revision_requested": False,
                        "revision_reason": None,
                        "weakness_review": "The prior observation gap is retained in the revised contract.",
                        "overconstraint_review": "The revision preserves equivalent construction choices.",
                        "observation_limits": "No further static counterexample is established by this fixture.",
                    },
                ])
    outputs.append({
        "schema_version": "staged-deliver/v0.1",
        "result_status": deliver_construct["result_status"],
        "selected_scenario_id": deliver_construct["selected_scenario_id"],
        "contract": deliver_construct["contract"],
    })
    rendered = _wire_outputs(outputs)
    if replay_case == "second_revision":
        query_outputs = [
            output for output in rendered
            if output["schema_version"] == "staged-query/v0.4"
        ]
        for update in query_outputs[1]["question_updates"]:
            update["question_id"] = "q1"
    return rendered


def scripted_outputs(
    replay_case: ReplayCase = "selected", *, variant: Literal["B", "C"] = "B"
) -> list[dict]:
    if replay_case not in (
        "selected", "ambiguous", "inconclusive", "error", "cache_miss",
        "cache_unspecified", "legal_variation", "second_revision",
    ):
        raise ValueError(f"unsupported replay case: {replay_case}")
    if variant not in ("B", "C"):
        raise ValueError(f"unsupported variant: {variant}")
    if replay_case in ("cache_miss", "cache_unspecified", "legal_variation", "second_revision"):
        return _cache_outputs(replay_case, variant=variant)
    ambiguous = replay_case == "ambiguous"
    contract = _contract(ambiguous=ambiguous)
    selected_scenario_id = None if replay_case in ("ambiguous", "inconclusive") else (
        "dependency_returns_two"
    )
    result_status = {
        "selected": "SELECTED",
        "ambiguous": "AMBIGUOUS",
        "inconclusive": "INCONCLUSIVE",
        "error": "SELECTED",
    }[replay_case]
    unresolved = []
    if replay_case == "ambiguous":
        unresolved = [
            "The visible equality does not distinguish integer 2 from float 2.0."
        ]
    elif replay_case == "inconclusive":
        unresolved = [
            "The dependency binding and return contract are missing, so no safe response "
            "constraint can be delivered to a generator."
        ]
    construct = {
        "schema_version": "staged-construct/v0.1", "result_status": result_status,
        "selected_scenario_id": selected_scenario_id,
        "selection_rationale": (
            "The ordinary equality accepts two type-distinct dependency results, and the "
            "visible evidence contains no basis for choosing between them."
            if replay_case == "ambiguous" else
            "The missing dependency binding prevents construction of a complete contract."
            if replay_case == "inconclusive" else
            "Direct delegation and the ordinary assertion jointly constrain the result."
        ),
        "claim_basis": [
            {"claim": "objective", "basis": "task_requirement",
             "target": "formal_claim", "target_index": 0,
             "semantic_role": "test_objective", "evidence_ids": ["assertion"],
             "support": "evidence"},
            {"claim": "delegation", "basis": "implementation_fact",
             "target": "formal_claim", "target_index": 0,
             "semantic_role": "mut_path_condition", "evidence_ids": ["delegation"],
             "support": "evidence"},
            {"claim": "selected_scenario", "basis": "scenario_inference",
             "target": "selection_rationale", "target_index": 0,
             "semantic_role": "dependency_contract",
             "evidence_ids": ["delegation", "assertion"], "support": "evidence"},
            {"claim": "allowed variation", "basis": "scenario_inference",
             "target": "allowed_variation", "target_index": 0,
             "semantic_role": "dependency_contract", "evidence_ids": [],
             "support": "assumption",
             "assumption_reason": "Unrelated dependency state is outside the visible objective."},
            *([{"claim": "candidate distinction remains unresolved",
                "basis": "scenario_inference", "target": "unresolved", "target_index": 0,
                "semantic_role": "test_objective", "evidence_ids": ["assertion"],
                "support": "evidence"}] if unresolved else []),
        ],
        "critical_behaviors": ([] if replay_case != "selected" and replay_case != "error" else [{
            "behavior": "dependency(1) yields 2",
            "observation": "target(1) returns 2 to the visible assertion",
            "evidence_ids": ["delegation", "assertion"],
            "semantic_role": "dependency_contract", "formal_claim_index": 0,
            "support": "evidence",
        }]),
        "allowed_variations": [
            "Internal dependency state and unrelated calls may vary when the returned value remains 2."
        ],
        "unresolved": unresolved,
        "spec": _fixture_spec(
            None if replay_case == "inconclusive" else contract,
            result_status=result_status,
            allowed_variations=[
                "Internal dependency state and unrelated calls may vary when the returned value remains 2."
            ],
            remaining_question=(
                unresolved[0]
                if replay_case == "ambiguous" else
                "What is the hidden dependency implementation?"
            ),
            remaining_impact=(
                "The missing selection basis prevents choosing either candidate."
                if replay_case == "ambiguous" else
                "It does not invalidate the explicit candidate or ambiguity."
            ),
        ),
        "investigation_requests": [],
        "contract": None if replay_case == "inconclusive" else contract,
    }
    extract_candidates = [{
        "scenario_id": "dependency_returns_two",
        "description": "Dependency returns the value observed by the ordinary oracle.",
        "evidence": [
            {"source_id": "mut:0", "start_line": 11, "end_line": 11,
             "quote": "    return dependency(value)"},
            {"source_id": "test:0", "start_line": 5, "end_line": 5,
             "quote": "    assert target(1) == 2"},
        ],
    }]
    if ambiguous:
        float_candidate = deepcopy(extract_candidates[0])
        float_candidate["scenario_id"] = "dependency_returns_float_two"
        float_candidate["description"] = (
            "Dependency returns float 2.0, also accepted by the ordinary equality."
        )
        extract_candidates.append(float_candidate)
    deliver = {
        "schema_version": "staged-deliver/v0.1", "result_status": result_status,
        "selected_scenario_id": selected_scenario_id,
        "contract": None if replay_case == "inconclusive" else contract,
    }
    if replay_case == "error":
        forged_contract = deepcopy(contract)
        forged_contract["case_id"] = "forged_case_id"
        deliver["contract"] = forged_contract
    outputs: list[dict] = [
        {
            "schema_version": "staged-extract/v0.1",
            "original_goals": [{"source_id": "test:0", "start_line": 5, "end_line": 5,
                                "quote": "    assert target(1) == 2"}],
            "candidates": extract_candidates, "unresolved": unresolved,
        },
        {"schema_version": "staged-query/v0.1", "decision": "finish_queries",
         "query_attempt": None, "previous_receipt_assessment": None,
         "impact": "The visible input is sufficient; no query is needed.",
         "stop_explanation": {
             "reason": (
                 "no_further_evidence" if replay_case == "inconclusive" else
                 "no_further_evidence" if replay_case == "ambiguous" else
                 "unknowns_nonblocking"
             ),
             "rationale": (
                 "The missing dependency contract cannot be recovered from visible sources."
                 if replay_case == "inconclusive" else
                 "The equality evidence preserves both candidates without forcing a choice."
                 if replay_case == "ambiguous" else
                 "The visible delegation and assertion support the conditional scenario."
             ),
             "evidence": [{"source_id": "test:0", "start_line": 5, "end_line": 5,
                           "quote": "    assert target(1) == 2"}],
             "remaining_unknowns": [{
                 "question": "What is the hidden dependency implementation?",
                 "impact": (
                     "No safe response constraint can be delivered."
                     if replay_case == "inconclusive" else
                     "The missing selection basis prevents choosing either candidate."
                     if replay_case == "ambiguous" else
                     "It does not invalidate the explicit candidate or ambiguity."
                 ),
                 "blocking": replay_case in ("ambiguous", "inconclusive"),
             }],
         }},
        construct,
    ]
    if variant == "C" and result_status in ("SELECTED", "AMBIGUOUS"):
        outputs.append({
            "schema_version": "staged-contrast/v0.2", "status": "none_identified",
            "execution_status": "not_executed", "alternative": None,
            "bypass_behavior": None, "evidence_ids": [],
            "distinguishing_observation": None, "revision_requested": False,
            "revision_reason": None,
            "support": "assumption",
            "assumption_reason": "No evidence-backed static bypass was identified.",
            "semantic_role": "dependency_contract",
            "weakness_review": "No evidence-backed weaker constraint was identified in this fixture.",
            "overconstraint_review": "Unrelated internal state remains an allowed variation.",
            "observation_limits": "The static review does not prove the contract globally correct.",
            "investigation_requests": [],
        })
    outputs.append(deliver)
    return _wire_outputs(outputs)


def query_script(
    count: int, replay_case: ReplayCase = "selected", *, variant: Literal["B", "C"] = "B"
) -> list[dict]:
    """Replay sequential queries across all three source tools, including repeats."""
    if type(count) is not int or count < 0:
        raise ValueError("query count must be a non-negative integer")
    outputs = scripted_outputs(replay_case, variant=variant)
    final_query = deepcopy(outputs[1])
    for update in final_query["question_updates"]:
        update["attempted_sequences"] = list(range(1, count + 1))
    queries = []
    for index in range(count + 1):
        assessment = None if index == 0 else {
            "impact": "no_new_information",
            "rationale": "The receipt does not resolve the hidden dependency behavior.",
        }
        attempt = None
        if index < count:
            tool = ("inspect_source", "inspect_program_structure",
                    "inspect_dependency_usage")[index % 3]
            arguments: dict = {"source_id": "mut:0"}
            if tool == "inspect_source":
                arguments.update(start_line=10, end_line=11)
            attempt = {
                "question": "Does visible syntax distinguish the candidate's dependency result?",
                "scenario_id": "dependency_returns_two", "tool": tool,
                "arguments_json": json.dumps(arguments),
            }
        queries.append({
            "schema_version": "staged-query/v0.4",
            "decision": "finish_queries" if attempt is None else "request_query",
            "query_attempt": attempt, "previous_receipt_assessment": assessment,
            "impact": (
                "Preserve the current candidate pending construction."
                if attempt is not None else final_query["impact"]
            ),
            "stop_explanation": (
                None if attempt is not None else final_query["stop_explanation"]
            ),
            "question_updates": (
                [] if attempt is not None else final_query["question_updates"]
            ),
            "candidate_updates": [],
        })
    return [outputs[0], *queries, *outputs[2:]]


def _response(value: dict) -> ModelResponse:
    return ModelResponse(
        output=[ResponseOutputMessage(
            id="scripted-message", type="message", role="assistant", status="completed",
            content=[ResponseOutputText(
                type="output_text", text=json.dumps(value, ensure_ascii=False), annotations=[]
            )],
        )],
        usage=Usage(requests=1, input_tokens=100, output_tokens=100, total_tokens=200),
        response_id=None,
    )


class FixedScriptModel(Model):
    synthetic_replay_identity = "mockspec-fixed-script/v0.1"

    def __init__(
        self,
        outputs: list[dict] | None = None,
        *,
        replay_case: ReplayCase = "selected",
        variant: Literal["B", "C"] = "B",
    ):
        self.outputs = list(
            scripted_outputs(replay_case, variant=variant) if outputs is None else outputs
        )
        self.inputs: list[object] = []
        self.calls = 0

    async def get_response(self, *args, **kwargs):
        self.inputs.append(kwargs["input"])
        if kwargs["tools"] or kwargs["handoffs"]:
            raise AssertionError("staged queries use the host request protocol, not SDK tools")
        if self.calls >= len(self.outputs):
            raise AssertionError("scripted response sequence exhausted")
        response = _response(self.outputs[self.calls])
        self.calls += 1
        return response

    async def stream_response(self, *args, **kwargs):
        raise AssertionError("streaming is not used")
        yield


__all__ = [
    "FixedScriptModel", "ReplayCase", "cache_task", "query_script", "scripted_outputs",
    "synthetic_task", "task_for_replay_case",
]
