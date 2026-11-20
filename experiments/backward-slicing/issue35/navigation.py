"""Deterministic projection of a backward-analysis contract for generation.

The projection deliberately carries evidence rather than interpreting it.  In
particular, ``analysis_may`` and ``unknown_effect`` modalities remain unchanged
and relations are associated with observations only by an explicit reference
or an exact, directed endpoint chain.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

import result_contract


SCHEMA_VERSION = "mock-spec.compact-navigation/v1"
FULL_SCHEMA_VERSION = "mock-spec.full-slice-evidence/v1"
RELATION_DETAIL_FIELDS = (
    "branch_successor_ordinal", "predicate_producers", "predicate_targets",
    "observation_attribution", "if_present", "consumer_factory", "implementation_target",
    "implementation_reference_kind", "instantiated_method_type", "captured_receiver_values",
    "setter_chain", "evidence_components", "effect", "normal_continuation",
)
COMPACT_RELATION_DETAIL_FIELDS = (
    "branch_successor_ordinal", "predicate_targets", "captured_receiver_values",
    "effect", "normal_continuation",
)


def _encoded(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode()


def _digest(value: Any) -> str:
    return hashlib.sha256(_encoded(value)).hexdigest()


def _contract(case_result: dict[str, Any]) -> dict[str, Any]:
    contract = case_result.get("result_contract", case_result)
    result_contract.validate_result_contract(contract)
    return contract


def _normalized_contract(contract: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(contract)
    normalized["relations"] = sorted(contract["relations"], key=_digest)
    return normalized


def _source_records(visible_sources: Any) -> list[dict[str, Any]]:
    if isinstance(visible_sources, dict) and "sources" in visible_sources:
        visible_sources = visible_sources["sources"]
    if isinstance(visible_sources, dict):
        records = []
        for source_id, value in visible_sources.items():
            if isinstance(value, str):
                records.append({
                    "source_id": source_id, "logical_path": source_id,
                    "text": value, "start_line": 1,
                })
            elif isinstance(value, dict):
                records.append({"source_id": source_id, **value})
            else:
                raise ValueError("source map values must be source objects or text")
        visible_sources = records
    if not isinstance(visible_sources, list):
        raise ValueError("visible_sources must be a source array, path-to-text map, or sources object")

    records = []
    for source in visible_sources:
        if not isinstance(source, dict):
            raise ValueError("each visible source must be an object")
        path = source.get("logical_path")
        text = source.get("text")
        start_line = source.get("start_line", 1)
        if not isinstance(path, str) or not path:
            raise ValueError("each visible source requires logical_path")
        if text is not None and not isinstance(text, str):
            raise ValueError("visible source text must be a string when supplied")
        if type(start_line) is not int or start_line < 1:
            raise ValueError("visible source start_line must be a positive integer")
        record = {key: value for key, value in source.items() if key != "text"}
        record.update({"logical_path": path, "start_line": start_line})
        if text is not None:
            record["text"] = text
        records.append(record)
    return sorted(
        records, key=lambda row: (row["logical_path"], row["start_line"], row.get("text", "")),
    )


def _endpoint_key(endpoint: Any) -> str:
    return _encoded(endpoint).decode()


def _relation_sort_key(relation: dict[str, Any]) -> tuple[Any, ...]:
    endpoint = relation.get("from")
    if isinstance(endpoint, dict):
        location = (
            endpoint.get("logical_path", ""), endpoint.get("start_line", -1),
            endpoint.get("end_line", -1),
        )
    else:
        location = ("", -1, -1)
    return (*location, relation["fact_id"])


def _line_evidence(relation: dict[str, Any], sources: list[dict[str, Any]]) -> list[dict[str, Any]]:
    evidence = []
    seen = set()
    for endpoint_name in ("from", "to"):
        endpoint = relation.get(endpoint_name)
        if not isinstance(endpoint, dict):
            continue
        path = endpoint.get("logical_path")
        start = endpoint.get("start_line")
        end = endpoint.get("end_line", start)
        if not isinstance(path, str) or type(start) is not int or type(end) is not int:
            continue
        for source in sources:
            if source["logical_path"] != path or "text" not in source:
                continue
            lines = source["text"].splitlines()
            source_start = source["start_line"]
            source_end = source_start + len(lines) - 1
            if start < source_start or end > source_end:
                continue
            key = (path, start, end)
            if key in seen:
                continue
            seen.add(key)
            evidence.append({
                "logical_path": path,
                "start_line": start,
                "end_line": end,
                "text": "\n".join(lines[start - source_start:end - source_start + 1]),
            })
    return evidence


def _project_relation(relation: dict[str, Any], *, compact: bool) -> dict[str, Any]:
    fact_id = f"r:{_digest(relation)[:16]}"
    fact = {
        "fact_id": fact_id,
        "kind": relation["kind"],
        "modality": relation["modality"],
        "from": relation["from"],
        "to": relation["to"],
        "precision": relation["precision"],
        "provenance": (
            {"full_relation_ref": fact_id} if compact else relation["provenance"]
        ),
    }
    fact["assumptions"] = relation["assumptions"]
    if not compact:
        fact["observation_ids"] = relation.get("observation_ids", [])
    detail_fields = COMPACT_RELATION_DETAIL_FIELDS if compact else RELATION_DETAIL_FIELDS
    details = {key: relation[key] for key in detail_fields if key in relation}
    if details:
        fact["bindings_and_path_details"] = details
    return fact


def _project_facts(
    contract: dict[str, Any], sources: list[dict[str, Any]], *, compact: bool,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    facts_by_id: dict[str, dict[str, Any]] = {}
    lines_by_id: dict[str, dict[str, Any]] = {}
    for relation in contract["relations"]:
        fact = _project_relation(relation, compact=compact)
        source_line_refs = []
        for source_line in _line_evidence(relation, sources):
            line_id = f"l:{_digest(source_line)[:16]}"
            lines_by_id[line_id] = {"source_line_id": line_id, **source_line}
            source_line_refs.append(line_id)
        if source_line_refs:
            fact["source_line_refs"] = sorted(source_line_refs)
        previous = facts_by_id.get(fact["fact_id"])
        if previous is not None and previous != fact:
            raise ValueError(f"relation fact ID collision: {fact['fact_id']}")
        facts_by_id[fact["fact_id"]] = fact
    return (
        sorted(facts_by_id.values(), key=_relation_sort_key),
        sorted(lines_by_id.values(), key=lambda row: row["source_line_id"]),
    )


def _with_observation_lines(
    lines: list[dict[str, Any]], contract: dict[str, Any], sources: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    paths = {source["source_id"]: source["logical_path"] for source in contract["source_scope"]}
    by_id = {line["source_line_id"]: line for line in lines}
    for observation in contract["observations"]:
        location = observation["location"]
        path = paths.get(location["source_id"])
        if path is None:
            continue
        probe = {"from": {"logical_path": path, **{
            key: location[key] for key in ("start_line", "end_line")
        }}}
        for source_line in _line_evidence(probe, sources):
            line_id = f"l:{_digest(source_line)[:16]}"
            by_id[line_id] = {"source_line_id": line_id, **source_line}
    return sorted(by_id.values(), key=lambda row: row["source_line_id"])


def _observation_location_key(
    observation: dict[str, Any], source_paths: dict[str, str],
) -> str | None:
    location = observation["location"]
    path = source_paths.get(location["source_id"])
    if path is None:
        return None
    return _endpoint_key({
        "logical_path": path,
        "start_line": location["start_line"],
        "end_line": location["end_line"],
    })


def _fact_references(
    observation: dict[str, Any], relations: list[dict[str, Any]], source_paths: dict[str, str],
) -> list[str]:
    observation_id = observation["observation_id"]
    selected = {
        relation["fact_id"] for relation in relations
        if observation_id in relation.get("observation_ids", [])
    }
    frontier = {
        _endpoint_key(relation["from"]) for relation in relations
        if relation["fact_id"] in selected
    }
    location_key = _observation_location_key(observation, source_paths)
    if location_key is not None:
        frontier.add(location_key)

    # Traverse only exact from -> to chains, backwards from the observation.
    while frontier:
        next_frontier = set()
        for relation in relations:
            fact_id = relation["fact_id"]
            explicit_ids = relation.get("observation_ids", [])
            if (
                fact_id in selected
                or (explicit_ids and observation_id not in explicit_ids)
                or _endpoint_key(relation["to"]) not in frontier
            ):
                continue
            selected.add(fact_id)
            next_frontier.add(_endpoint_key(relation["from"]))
        frontier = next_frontier
    return sorted(selected)


def _reference_set(references: list[str], sets: dict[str, list[str]]) -> str:
    references = sorted(set(references))
    set_id = f"s:{_digest(references)[:16]}"
    sets[set_id] = references
    return set_id


def _visible_locations(
    facts: list[dict[str, Any]], references: list[str], fallback: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    by_id = {fact["fact_id"]: fact for fact in facts}
    locations: dict[tuple[Any, ...], dict[str, Any]] = {}
    boundary_references = [
        reference for reference in references
        if by_id[reference]["modality"] == "unknown_effect"
    ]
    if not boundary_references:
        boundary_references = [
            reference for reference in references if by_id[reference]["kind"] == "call_binding"
        ]
    for reference in boundary_references:
        fact = by_id[reference]
        for endpoint_name in ("from", "to"):
            endpoint = fact[endpoint_name]
            if not isinstance(endpoint, dict):
                continue
            if not all(key in endpoint for key in ("logical_path", "start_line")):
                continue
            location = {
                "logical_path": endpoint["logical_path"],
                "start_line": endpoint["start_line"],
                "end_line": endpoint.get("end_line", endpoint["start_line"]),
                "fact_ref": reference,
                "endpoint": endpoint_name,
            }
            key = (
                location["logical_path"], location["start_line"], location["end_line"],
                reference, endpoint_name,
            )
            locations[key] = location
    if not locations and fallback is not None:
        location = {
            key: fallback[key] for key in ("logical_path", "start_line", "end_line")
        }
        location["basis"] = "observation_location_only"
        locations[(location["logical_path"], location["start_line"], location["end_line"])] = location
    return [locations[key] for key in sorted(locations)]


def _generation_completion(contract: dict[str, Any]) -> dict[str, Any]:
    completion = contract["completion"]
    return {
        "status": completion["status"],
        "stage": completion["stage"],
        "failure_categories": [
            category for category in completion["failure_categories"]
            if not category.startswith("semantic_validation")
        ],
    }


def _navigation_fields(
    references: list[str], facts: list[dict[str, Any]], fact_set_id: str,
    fallback: dict[str, Any] | None,
) -> dict[str, Any]:
    by_id = {fact["fact_id"]: fact for fact in facts}
    limitations = sorted({
        assumption
        for reference in references
        for assumption in by_id[reference].get("assumptions", [])
    })
    return {
        "last_visible_locations": _visible_locations(facts, references, fallback),
        "fact_ref_set_id": fact_set_id,
        "known_facts": {"select_role": "known"},
        "guards": {
            "test": {"select_role": "guard:test"},
            "product": {"select_role": "guard:product"},
            "unclassified": {"select_role": "guard:unclassified"},
        },
        "successors": {
            "normal": {"select_role": "successor:normal"},
            "exceptional": {"select_role": "successor:exceptional"},
        },
        "unknown": {
            "select_role": "unknown",
            "limitations": limitations,
            "boundary_refs": [],
            "case_unknown_ref": "case_navigation.unknown",
        },
    }


def format_navigation(
    case_result: dict[str, Any], visible_sources: Any,
) -> dict[str, Any]:
    """Return a deterministic, traceable navigation contract.

    ``case_result`` may be the external result contract itself or a batch result
    containing it under ``result_contract``.  ``visible_sources`` contains only
    generation-authorized source intervals.
    """
    contract = _contract(case_result)
    sources = _source_records(visible_sources)
    source_paths = {
        source["source_id"]: source["logical_path"] for source in contract["source_scope"]
    }
    test_paths = {
        source["logical_path"] for source in contract["source_scope"]
        if source["selection_role"] == "masked_test"
    }

    association_facts, _ = _project_facts(contract, sources, compact=False)
    facts, source_lines = _project_facts(contract, sources, compact=True)
    source_lines = _with_observation_lines(source_lines, contract, sources)
    for fact in facts:
        if fact["kind"] == "control_dependence":
            endpoint = fact["from"]
            if not isinstance(endpoint, dict) or not isinstance(endpoint.get("logical_path"), str):
                guard_scope = "unclassified"
            else:
                guard_scope = "test" if endpoint["logical_path"] in test_paths else "product"
        else:
            guard_scope = None
        if fact["kind"] == "exception_dependence":
            successor = "exceptional"
        elif (
            fact["kind"] == "normal_return_dependence"
            or "normal_continuation" in fact.get("bindings_and_path_details", {})
        ):
            successor = "normal"
        else:
            successor = None
        roles = ["unknown" if fact["modality"] == "unknown_effect" else "known"]
        if guard_scope is not None:
            roles.append(f"guard:{guard_scope}")
        if successor is not None:
            roles.append(f"successor:{successor}")
        fact["roles"] = roles

    observations = []
    referenced = set()
    fact_sets: dict[str, list[str]] = {}
    for observation in sorted(
        contract["observations"],
        key=lambda row: (
            source_paths.get(row["location"]["source_id"], ""),
            row["location"]["start_line"], row["observation_id"],
        ),
    ):
        references = _fact_references(observation, association_facts, source_paths)
        referenced.update(references)
        fact_set_id = _reference_set(references, fact_sets)
        location = {
            **observation["location"],
            "logical_path": source_paths.get(observation["location"]["source_id"]),
        }
        observations.append({
            "observation_id": observation["observation_id"],
            "kind": observation["kind"],
            "origin": observation["origin"],
            "location": location,
            "reason": observation.get("reason"),
            "fact_ref_set_id": fact_set_id,
            "navigation": _navigation_fields(
                references, facts, fact_set_id, location,
            ),
        })

    case_references = [fact["fact_id"] for fact in facts if fact["fact_id"] not in referenced]
    case_fact_set_id = _reference_set(case_references, fact_sets)
    case_navigation = _navigation_fields(
        case_references, facts, case_fact_set_id, None,
    )
    case_navigation["unknown"]["boundary_refs"] = sorted({
        boundary.get("boundary_id")
        for boundary in contract["analysis_boundaries"]["unknown_effects"]
        if boundary.get("boundary_id")
    })
    case_navigation["unknown"].pop("case_unknown_ref")
    case_navigation["unknown"]["limitations"] = sorted(set(
        case_navigation["unknown"]["limitations"]
        + [region["reason"] for region in contract["analysis_boundaries"]["unanalysed_regions"]]
    ))

    payload = {
        "schema_version": SCHEMA_VERSION,
        "identity": contract["identity"],
        "observations": observations,
        "facts": facts,
        "fact_sets": [
            {"fact_set_id": fact_set_id, "fact_refs": fact_sets[fact_set_id]}
            for fact_set_id in sorted(fact_sets)
        ],
        "source_lines": source_lines,
        "case_fact_ref_set_id": case_fact_set_id,
        "case_navigation": case_navigation,
        "entry_point": contract["entry_point"],
        "parameterization": contract["parameterization"],
        "relation_kinds_present": sorted({relation["kind"] for relation in contract["relations"]}),
        "boundaries": contract["analysis_boundaries"],
        "completion": _generation_completion(contract),
        "interpretation": {
            "may_and_unknown_preserved": True,
            "call_binding_is_interaction_candidate": True,
            "mandatory_interactions_require_separate_visible_support": True,
        },
    }
    payload_bytes = _encoded(payload)
    return {
        **payload,
        "projection": {
            "input_contract_sha256": _digest(_normalized_contract(contract)),
            "visible_sources_sha256": _digest(sources),
            "output_payload_sha256": hashlib.sha256(payload_bytes).hexdigest(),
            "output_payload_bytes": len(payload_bytes),
            "input_relation_count": len(contract["relations"]),
            "projected_fact_count": len(facts),
            "projected_observation_count": len(observations),
            "covered_relation_fact_ids": [fact["fact_id"] for fact in facts],
        },
    }


def format_full_evidence(
    case_result: dict[str, Any], visible_sources: Any,
) -> dict[str, Any]:
    """Return the full generation-visible B package from the same frozen inputs."""
    contract = _contract(case_result)
    sources = _source_records(visible_sources)
    relations, source_lines = _project_facts(contract, sources, compact=False)
    source_lines = _with_observation_lines(source_lines, contract, sources)
    payload = {
        "schema_version": FULL_SCHEMA_VERSION,
        "identity": contract["identity"],
        "source_scope": contract["source_scope"],
        "observations": [{
            key: observation[key]
            for key in ("observation_id", "kind", "origin", "location", "reason")
            if key in observation
        } for observation in contract["observations"]],
        "parameterization": contract["parameterization"],
        "entry_point": contract["entry_point"],
        "relations": relations,
        "source_lines": source_lines,
        "relation_kinds_present": sorted({relation["kind"] for relation in contract["relations"]}),
        "boundaries": contract["analysis_boundaries"],
        "completion": _generation_completion(contract),
        "interpretation": {
            "may_and_unknown_preserved": True,
            "call_binding_is_interaction_candidate": True,
            "mandatory_interactions_require_separate_visible_support": True,
        },
    }
    payload_bytes = _encoded(payload)
    return {
        **payload,
        "projection": {
            "input_contract_sha256": _digest(_normalized_contract(contract)),
            "visible_sources_sha256": _digest(sources),
            "output_payload_sha256": hashlib.sha256(payload_bytes).hexdigest(),
            "output_payload_bytes": len(payload_bytes),
            "input_relation_count": len(contract["relations"]),
            "projected_relation_count": len(relations),
            "projected_source_line_count": len(source_lines),
            "covered_relation_fact_ids": [relation["fact_id"] for relation in relations],
        },
    }
