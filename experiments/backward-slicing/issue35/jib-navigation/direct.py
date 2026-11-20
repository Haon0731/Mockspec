"""Build smaller, direct-reading Jib navigation prompts.

The readable layer places independently supported relations beside each visible
assertion.  It never joins those relations into a new chain.  A lossless,
dictionary-compressed archive retains the complete compact package so every
fact, precision qualifier, assumption, and Unknown can be recovered exactly.
"""

from __future__ import annotations

from collections import Counter
import copy
import hashlib
import re
from typing import Any

from variants import encode, load_frozen_prompts, restore_details, split_prompt


SCHEMA_VERSION = "mock-spec.issue35-jib-direct-navigation/v1"
STRING_REF = "$string"
MIN_INTERN_LENGTH = 24


def _walk_strings(value: Any, counts: Counter[str]) -> None:
    if isinstance(value, str):
        counts[value] += 1
    elif isinstance(value, dict):
        for child in value.values():
            _walk_strings(child, counts)
    elif isinstance(value, list):
        for child in value:
            _walk_strings(child, counts)


def compress_archive(value: dict[str, Any]) -> dict[str, Any]:
    counts: Counter[str] = Counter()
    _walk_strings(value, counts)
    strings = sorted(
        (text for text, count in counts.items() if count > 1 and len(text) >= MIN_INTERN_LENGTH),
        key=lambda text: (-len(text) * (counts[text] - 1), text),
    )
    identifiers = {text: f"s{index}" for index, text in enumerate(strings)}

    def replace(child: Any) -> Any:
        if isinstance(child, str) and child in identifiers:
            return {STRING_REF: identifiers[child]}
        if isinstance(child, dict):
            return {key: replace(value) for key, value in child.items()}
        if isinstance(child, list):
            return [replace(value) for value in child]
        return child

    return {
        "string_table": {identifiers[text]: text for text in strings},
        "payload": replace(value),
    }


def decompress_archive(archive: dict[str, Any]) -> dict[str, Any]:
    strings = archive["string_table"]

    def restore(child: Any) -> Any:
        if isinstance(child, dict) and set(child) == {STRING_REF}:
            return strings[child[STRING_REF]]
        if isinstance(child, dict):
            return {key: restore(value) for key, value in child.items()}
        if isinstance(child, list):
            return [restore(value) for value in child]
        return child

    return restore(archive["payload"])


def _assertion_text(observation: dict[str, Any], source_lines: list[dict[str, Any]]) -> str:
    location = observation["location"]
    matches = [
        line["text"] for line in source_lines
        if line["logical_path"] == location["logical_path"]
        and line["start_line"] <= location["start_line"] <= line["end_line"]
    ]
    if len(matches) != 1:
        raise ValueError(f"observation source line is not unique: {observation['observation_id']}")
    return matches[0]


def _observation_terms(assertion: str) -> set[str]:
    methods = re.findall(r"\.(get[A-Z]\w*)", assertion)
    terms = set(methods)
    for method in methods:
        property_name = method[3:]
        terms.add(property_name)
        terms.add(property_name[:1].lower() + property_name[1:])
    return terms


def _relation_text(fact: dict[str, Any]) -> str:
    return encode({
        "from": fact["from"],
        "to": fact["to"],
        "details": fact.get("bindings_and_path_details", {}),
    })


def _endpoint_label(endpoint: Any) -> str:
    if isinstance(endpoint, str):
        return endpoint
    if not isinstance(endpoint, dict):
        return str(endpoint)
    value = endpoint.get("declared_target") or endpoint.get("instruction")
    if isinstance(value, str):
        return value.split("(", 1)[0]
    path = endpoint.get("logical_path")
    if isinstance(path, str):
        return f"{path}:{endpoint.get('start_line', '?')}"
    return "structured backend endpoint; recover full fact from complete_evidence_archive"


def _restored_summary(fact: dict[str, Any], restored: bool) -> dict[str, Any]:
    details = fact.get("bindings_and_path_details", {})
    evidence = {
        field: copy.deepcopy(details[field])
        for field in ("predicate_producers", "setter_chain") if restored and field in details
    }
    if not evidence:
        return {}
    summary = {}
    if "predicate_producers" in evidence:
        summary["predicate_producers"] = [
            _endpoint_label(producer) for producer in evidence["predicate_producers"]
        ]
    if "setter_chain" in evidence:
        chain = evidence["setter_chain"]
        if isinstance(chain, dict):
            summary["setter_chain"] = {
                key: copy.deepcopy(chain[key]) for key in (
                    "field", "builder_setter", "implementation_target", "field_write",
                ) if key in chain
            }
        else:
            summary["setter_chain"] = [
                _endpoint_label(item) for item in chain
            ] if isinstance(chain, list) else _endpoint_label(chain)
    return summary


def direct_package(
    compact: dict[str, Any], *, restored: bool,
    selection_source: dict[str, Any] | None = None,
) -> dict[str, Any]:
    facts = {fact["fact_id"]: fact for fact in compact["facts"]}
    selection_facts = {
        fact["fact_id"]: fact for fact in (selection_source or compact)["facts"]
    }
    sets = {row["fact_set_id"]: row["fact_refs"] for row in compact["fact_sets"]}
    cards = []
    unknowns = {}
    for observation in compact["observations"]:
        assertion = _assertion_text(observation, compact["source_lines"])
        terms = _observation_terms(assertion)
        references = sets[observation["navigation"]["fact_ref_set_id"]]
        selected = [
            facts[fact_id] for fact_id in references
            if terms and any(term in _relation_text(selection_facts[fact_id]) for term in terms)
        ]
        unknown = observation["navigation"]["unknown"]
        unknown_id = "u:" + hashlib.sha256(encode(unknown).encode()).hexdigest()[:16]
        unknowns[unknown_id] = unknown
        cards.append({
            "observation_id": observation["observation_id"],
            "location": observation["location"],
            "assertion_source": assertion,
            "independent_relations": [{
                "fact_id": fact["fact_id"],
                "kind": fact["kind"],
                "modality": fact["modality"],
                "from": _endpoint_label(fact["from"]),
                "to": _endpoint_label(fact["to"]),
                "restored_guard_or_setter_evidence": _restored_summary(fact, restored),
                "interpretation": "independent visible relation; no composed chain is claimed",
            } for fact in selected],
            "unknown_ref": unknown_id,
            "selection_basis": {
                "visible_assertion_terms": sorted(terms),
                "rule": "literal term match against an already-associated fact endpoint or detail",
            },
        })
    archive = compress_archive(compact)
    return {
        "schema_version": SCHEMA_VERSION,
        "identity": compact["identity"],
        "observation_cards": cards,
        "unknown_lookup": {unknown_id: unknowns[unknown_id] for unknown_id in sorted(unknowns)},
        "case_unknown": compact["case_navigation"]["unknown"],
        "complete_evidence_archive": archive,
        "archive_contract": {
            "lossless": True,
            "dictionary_values_are_exact_strings": True,
            "readable_relations_are_parallel_evidence": True,
            "no_new_chain_claims": True,
        },
    }


def _digest(value: Any) -> str:
    return hashlib.sha256(encode(value).encode()).hexdigest()


def build_direct_prompts() -> tuple[dict[str, str], dict[str, Any]]:
    full_prompt, compact_prompt = load_frozen_prompts()
    full_prefix, full = split_prompt(full_prompt)
    prefix, compact = split_prompt(compact_prompt)
    if full_prefix != prefix:
        raise ValueError("frozen B/C prefixes differ")
    restored, restoration = restore_details(compact, full)
    packages = {
        "DC": direct_package(compact, restored=False, selection_source=compact),
        "DR": direct_package(restored, restored=True, selection_source=compact),
    }
    prompts = {arm: prefix + encode(package) + "\n" for arm, package in packages.items()}
    recoveries = {
        "DC": decompress_archive(packages["DC"]["complete_evidence_archive"]),
        "DR": decompress_archive(packages["DR"]["complete_evidence_archive"]),
    }
    proof = {
        "arms": {"DC": "direct-compact", "DR": "direct-restored"},
        "compact_archive": {
            "equivalent": recoveries["DC"] == compact,
            "source_sha256": _digest(compact),
            "recovered_sha256": _digest(recoveries["DC"]),
        },
        "restored_archive": {
            "equivalent": recoveries["DR"] == restored,
            "source_sha256": _digest(restored),
            "recovered_sha256": _digest(recoveries["DR"]),
        },
        "restoration": restoration,
        "prompt_bytes": {arm: len(prompt.encode()) for arm, prompt in prompts.items()},
        "old_c_prompt_bytes": len(compact_prompt.encode()),
        "rendered_examples": [
            card for card in packages["DR"]["observation_cards"]
            if card["independent_relations"]
        ][:3],
    }
    if not all(proof[name]["equivalent"] for name in ("compact_archive", "restored_archive")):
        raise ValueError("direct archive is not lossless")
    if any(size >= proof["old_c_prompt_bytes"] for size in proof["prompt_bytes"].values()):
        raise ValueError("direct prompt is not smaller than the frozen C prompt")
    return prompts, proof
