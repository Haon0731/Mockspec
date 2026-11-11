"""Versioned external result contract for one backward-analysis case.

The contract records what an analysis reported and what it did not evaluate. It
does not turn the current candidate graph into semantic dependency evidence.
"""
from __future__ import annotations

import hashlib
import json
import math
from typing import Any


RESULT_SCHEMA_VERSION = "mock-spec.backward-analysis-case/v1"
RELATION_KINDS = frozenset({
    "call_binding", "function_origin", "value_flow", "control_dependence",
    "normal_return_dependence", "exception_dependence", "state_flow",
})
RELATION_MODALITIES = frozenset({"certain", "analysis_may", "unknown_effect"})
BINDING_ORIGINS = frozenset({"automatic", "task_provided", "diagnostic_override"})
BINDING_STATUSES = frozenset({"unique", "ambiguous", "unresolved", "not_evaluated"})
VALIDATION_STATUSES = frozenset({
    "not_evaluated", "verified_correct", "verified_incomplete", "verified_incorrect",
})


class ResultContractError(ValueError):
    """The case result cannot be interpreted under the versioned contract."""


def _require_dict(value: Any, label: str) -> dict:
    if not isinstance(value, dict):
        raise ResultContractError(f"{label} must be an object")
    return value


def _require_list(value: Any, label: str) -> list:
    if not isinstance(value, list):
        raise ResultContractError(f"{label} must be an array")
    return value


def _require_string(value: Any, label: str, *, nullable: bool = False) -> str | None:
    if nullable and value is None:
        return None
    if type(value) is not str or not value:
        raise ResultContractError(f"{label} must be a non-empty string")
    return value


def _source_scope(result: dict) -> list[dict]:
    return [{
        "source_id": source["source_id"],
        "logical_path": source["logical_path"],
        "sha256": source["sha256"],
        "bytes": source["bytes"],
        "selection_role": "masked_test" if source["source_id"] == "test" else "candidate_product",
    } for source in result.get("sources", [])]


def _observations(result: dict) -> list[dict]:
    target = result.get("target_extraction", {})
    observations = []
    for index, criterion in enumerate(target.get("criteria", [])):
        observations.append({
            "observation_id": criterion.get("criterion_id", f"criterion:{index}"),
            "location": {"source_id": "test", "start_line": criterion["line"],
                         "end_line": criterion["line"]},
            "kind": criterion["kind"],
            "origin": "explicit" if criterion.get("confidence") == "explicit" else "candidate",
            "reason": criterion.get("reason"),
        })
    return observations


def _unknown_effects(result: dict) -> list[dict]:
    nodes: set[str] = set()
    for criterion in result.get("evidence", {}).get("criteria", []):
        nodes.update(criterion.get("unknown_call_nodes", []))
    return [{
        "boundary_id": node_id,
        "effect": "unclassified_call_effect",
        "scope": "unknown",
        "provenance": "candidate_graph_marker",
        "precision": "unresolved",
        "assumptions": [],
    } for node_id in sorted(nodes)]


def _unanalysed_regions(result: dict) -> list[dict]:
    regions: list[dict] = []
    if result.get("source_selection", {}).get("truncated"):
        regions.append({"scope": "source_selection", "reason": "source_budget"})
    status = result.get("status")
    if status in {"failed", "timeout", "unknown"}:
        regions.append({
            "scope": result.get("stage", "case"),
            "reason": ",".join(result.get("failure_categories", [])) or status,
        })
    elif status == "partial":
        regions.append({
            "scope": "semantic_relations",
            "reason": "candidate_graph_has_no_validated_dependency_semantics",
        })
    return regions


def _cost(result: dict) -> dict:
    timings = result.get("timings_seconds", {})
    missing_reasons = {
        "input_admission": "adapter admission occurs outside the per-case analysis timer",
        "call_graph": "the candidate backend does not report call-graph time separately",
        "pointer_analysis": "the candidate backend does not provide pointer analysis",
        "front_end": "the candidate backend does not report parsing time separately",
        "dependency_analysis": "backend_total combines parsing, graph construction, and slicing",
        "slice": "the candidate backend does not report semantic slicing time separately",
        "source_mapping": "the backend does not report mapping separately; evidence_summary measures host reachability summarization",
    }
    return {
        "timings_seconds": dict(timings),
        "total_seconds": result.get("elapsed_seconds"),
        "total_scope": "run_case only; excludes input admission, shared backend compilation and output serialization",
        "peak_memory": {"status": "unavailable", "bytes": None,
                        "reason": "case-level peak memory is not measured by this runner"},
        "cache_state": {
            "status": "unknown",
            "reason": "the runner does not isolate OS, runtime, parser, or shared compilation caches",
        },
        "input_size": {"files": len(result.get("sources", [])),
                       "bytes": sum(source.get("bytes", 0) for source in result.get("sources", []))},
        "graph_size": {"nodes": result.get("evidence", {}).get("nodes"),
                       "edges": result.get("evidence", {}).get("edges")},
        "unavailable_phases": [{"phase": phase, "reason": missing_reasons[phase]} for phase in (
            "input_admission", "front_end", "call_graph", "pointer_analysis", "dependency_analysis",
            "slice", "source_mapping",
        ) if phase not in timings],
    }


def build_result_contract(result: dict, identity: dict) -> dict:
    """Build a self-contained contract without claiming unverified semantics."""
    sources = result.get("sources", [])
    test_source = next((source for source in sources if source.get("source_id") == "test"), None)
    target = result.get("target_extraction", {})
    target_scope = target.get("target_scope", {})
    input_context = result.get("input_context", {})
    selector = input_context.get("target_selector", target_scope.get("selector"))
    language = identity["language"]
    return {
        "schema_version": RESULT_SCHEMA_VERSION,
        "identity": {
            "case_id": identity["case_id"], "dataset": identity["dataset"],
            "language": language, "snapshot_id": identity["snapshot_id"],
            "masking_contract": input_context.get("masking_contract"),
            "masked_test_sha256": input_context.get(
                "masked_test_sha256", test_source["sha256"] if test_source else None
            ),
            "target_selector": selector,
        },
        "source_scope": _source_scope(result),
        "observations": _observations(result),
        "parameterization": {"status": "not_expanded", "coverage": "selected_test_definition",
                             "reason": "the runner does not enumerate parameter rows"},
        "entry_point": {
            "test_selection": {
                "origin": "task_provided",
                "status": "unique" if selector and target_scope.get("status") == "located" else "unresolved",
                "selector": selector, "evidence": target_scope.get("locations", []),
            },
            "mut_binding": {"origin": "automatic", "status": "not_evaluated",
                            "candidates": [], "evidence": [], "assumptions": []},
        },
        "relations": [],
        "relation_coverage": [{"kind": kind, "status": "not_evaluated"}
                              for kind in sorted(RELATION_KINDS)],
        "analysis_boundaries": {"assumptions": [], "unknown_effects": _unknown_effects(result),
                                "unanalysed_regions": _unanalysed_regions(result)},
        "completion": {"status": result["status"], "stage": result.get("stage", "admission"),
                       "failure_categories": list(result.get("failure_categories", [])),
                       "error": result.get("error")},
        "semantic_validation": {
            "status": "not_evaluated", "reviewer": None, "evidence": [],
            "notes": "candidate reachability is not semantic dependency validation",
        },
        "cost": _cost(result),
    }


def validate_result_contract(contract: dict) -> None:
    """Reject missing or malformed fields needed by downstream integrations."""
    contract = _require_dict(contract, "result_contract")
    required = {
        "schema_version", "identity", "source_scope", "observations", "parameterization",
        "entry_point", "relations", "relation_coverage", "analysis_boundaries", "completion",
        "semantic_validation", "cost",
    }
    if set(contract) != required:
        raise ResultContractError("result_contract fields are invalid")
    if contract["schema_version"] != RESULT_SCHEMA_VERSION:
        raise ResultContractError("unsupported result_contract schema_version")

    identity = _require_dict(contract["identity"], "identity")
    for key in ("case_id", "dataset", "language", "snapshot_id"):
        _require_string(identity.get(key), f"identity.{key}")
    _require_string(identity.get("masking_contract"), "identity.masking_contract", nullable=True)
    if identity["language"] not in {"java", "python"}:
        raise ResultContractError("identity.language is invalid")
    mask = identity.get("masked_test_sha256")
    if mask is not None and (type(mask) is not str or len(mask) != 64):
        raise ResultContractError("identity.masked_test_sha256 is invalid")
    _require_string(identity.get("target_selector"), "identity.target_selector", nullable=True)

    for source in _require_list(contract["source_scope"], "source_scope"):
        source = _require_dict(source, "source_scope item")
        if set(source) != {"source_id", "logical_path", "sha256", "bytes", "selection_role"}:
            raise ResultContractError("source_scope item fields are invalid")
        for key in ("source_id", "logical_path", "sha256", "selection_role"):
            _require_string(source[key], f"source_scope.{key}")
        if type(source["bytes"]) is not int or source["bytes"] < 0:
            raise ResultContractError("source_scope.bytes is invalid")

    observation_ids = set()
    for observation in _require_list(contract["observations"], "observations"):
        observation = _require_dict(observation, "observation")
        for key in ("observation_id", "kind", "origin"):
            _require_string(observation.get(key), f"observation.{key}")
        if observation["origin"] not in {"explicit", "candidate"}:
            raise ResultContractError("observation.origin is invalid")
        if observation["observation_id"] in observation_ids:
            raise ResultContractError("duplicate observation identity")
        observation_ids.add(observation["observation_id"])
        location = _require_dict(observation.get("location"), "observation.location")
        _require_string(location.get("source_id"), "observation.location.source_id")
        if type(location.get("start_line")) is not int or type(location.get("end_line")) is not int:
            raise ResultContractError("observation.location lines are invalid")
        if location["start_line"] < 1 or location["end_line"] < location["start_line"]:
            raise ResultContractError("observation.location range is invalid")

    parameterization = _require_dict(contract["parameterization"], "parameterization")
    for key in ("status", "coverage", "reason"):
        _require_string(parameterization.get(key), f"parameterization.{key}")
    coverage_kinds = []
    for coverage in _require_list(contract["relation_coverage"], "relation_coverage"):
        coverage = _require_dict(coverage, "relation_coverage item")
        if coverage.get("kind") not in RELATION_KINDS or coverage.get("status") not in VALIDATION_STATUSES:
            raise ResultContractError("relation_coverage item is invalid")
        coverage_kinds.append(coverage["kind"])
    if set(coverage_kinds) != RELATION_KINDS or len(coverage_kinds) != len(RELATION_KINDS):
        raise ResultContractError("relation_coverage must cover each kind exactly once")

    entry = _require_dict(contract["entry_point"], "entry_point")
    for label in ("test_selection", "mut_binding"):
        binding = _require_dict(entry.get(label), f"entry_point.{label}")
        if binding.get("origin") not in BINDING_ORIGINS:
            raise ResultContractError(f"entry_point.{label}.origin is invalid")
        if binding.get("status") not in BINDING_STATUSES:
            raise ResultContractError(f"entry_point.{label}.status is invalid")

    for relation in _require_list(contract["relations"], "relations"):
        relation = _require_dict(relation, "relation")
        if relation.get("kind") not in RELATION_KINDS:
            raise ResultContractError("relation.kind is invalid")
        if relation.get("modality") not in RELATION_MODALITIES:
            raise ResultContractError("relation.modality is invalid")
        for key in ("from", "to", "provenance"):
            if key not in relation or not relation[key]:
                raise ResultContractError(f"relation.{key} is required")
        _require_string(relation.get("precision"), "relation.precision")
        _require_list(relation.get("assumptions"), "relation.assumptions")
        if "observation_ids" in relation:
            references = _require_list(relation["observation_ids"], "relation.observation_ids")
            seen = set()
            for reference in references:
                _require_string(reference, "relation observation reference")
                if reference not in observation_ids:
                    raise ResultContractError("relation references an unknown observation")
                if reference in seen:
                    raise ResultContractError("relation repeats an observation reference")
                seen.add(reference)

    boundaries = _require_dict(contract["analysis_boundaries"], "analysis_boundaries")
    for key in ("assumptions", "unknown_effects", "unanalysed_regions"):
        _require_list(boundaries.get(key), f"analysis_boundaries.{key}")
    for effect in boundaries["unknown_effects"]:
        effect = _require_dict(effect, "unknown_effect")
        if "observation_ids" in effect:
            references = _require_list(effect["observation_ids"], "unknown_effect.observation_ids")
            if not references:
                raise ResultContractError("unknown_effect observation references must not be empty")
            seen = set()
            for reference in references:
                _require_string(reference, "unknown_effect observation reference")
                if reference not in observation_ids:
                    raise ResultContractError("unknown_effect references an unknown observation")
                if reference in seen:
                    raise ResultContractError("unknown_effect repeats an observation reference")
                seen.add(reference)
    completion = _require_dict(contract["completion"], "completion")
    _require_string(completion.get("status"), "completion.status")
    _require_string(completion.get("stage"), "completion.stage")
    _require_list(completion.get("failure_categories"), "completion.failure_categories")
    validation = _require_dict(contract["semantic_validation"], "semantic_validation")
    if validation.get("status") not in VALIDATION_STATUSES:
        raise ResultContractError("semantic_validation.status is invalid")
    _require_list(validation.get("evidence"), "semantic_validation.evidence")

    cost = _require_dict(contract["cost"], "cost")
    timings = _require_dict(cost.get("timings_seconds"), "cost.timings_seconds")
    for phase, seconds in timings.items():
        _require_string(phase, "cost timing phase")
        if type(seconds) not in {int, float} or not math.isfinite(seconds) or seconds < 0:
            raise ResultContractError("cost timing is invalid")
    total = cost.get("total_seconds")
    if total is not None and (type(total) not in {int, float} or not math.isfinite(total) or total < 0):
        raise ResultContractError("cost.total_seconds is invalid")
    _require_dict(cost.get("peak_memory"), "cost.peak_memory")
    _require_dict(cost.get("cache_state"), "cost.cache_state")
    _require_dict(cost.get("input_size"), "cost.input_size")
    _require_dict(cost.get("graph_size"), "cost.graph_size")
    unavailable = _require_list(cost.get("unavailable_phases"), "cost.unavailable_phases")
    for phase in unavailable:
        phase = _require_dict(phase, "cost.unavailable_phases item")
        _require_string(phase.get("phase"), "cost unavailable phase")
        _require_string(phase.get("reason"), "cost unavailable reason")


def attach_result_contract(result: dict, identity: dict) -> dict:
    contract = build_result_contract(result, identity)
    validate_result_contract(contract)
    result["result_contract"] = contract
    return result


def contract_digest(contract: dict) -> str:
    encoded = json.dumps(contract, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    return hashlib.sha256(encoded).hexdigest()
