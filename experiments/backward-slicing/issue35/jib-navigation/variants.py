"""Construct Jib navigation variants without running a model.

The experiment crosses an original/path-oriented layout with compact/restored
details.  The path layout is lossless and only indexes facts using their
existing kind and roles.  Restored details come verbatim from the frozen B
package and are joined through the fact IDs already shared by B and C.
"""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any


HERE = Path(__file__).resolve().parent
JIB_PROMPTS = HERE.parent / "prompts" / "jib"
LAYOUT_SCHEMA = "mock-spec.issue35-jib-observation-path-layout/v1"
RESTORED_FIELDS = ("predicate_producers", "setter_chain")
ARM_NAMES = {
    "OC": "original-compact",
    "PC": "path-compact",
    "OR": "original-restored",
    "PR": "path-restored",
}


def encode(value: Any) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False,
    )


def digest(value: Any) -> str:
    data = value if isinstance(value, bytes) else encode(value).encode()
    return hashlib.sha256(data).hexdigest()


def split_prompt(prompt: str) -> tuple[str, dict[str, Any]]:
    marker = "Navigation evidence:\n"
    prefix, evidence = prompt.split(marker, 1)
    if not evidence.endswith("\n"):
        raise ValueError("frozen prompt lacks its final newline")
    return prefix + marker, json.loads(evidence)


def load_frozen_prompts() -> tuple[str, str]:
    return (
        (JIB_PROMPTS / "B.txt").read_text(encoding="utf-8"),
        (JIB_PROMPTS / "C.txt").read_text(encoding="utf-8"),
    )


def restore_details(
    compact: dict[str, Any], full: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Restore exactly two omitted B detail fields into the C facts."""
    restored = copy.deepcopy(compact)
    full_by_id = {relation["fact_id"]: relation for relation in full["relations"]}
    changed: dict[str, dict[str, Any]] = {}
    for fact in restored["facts"]:
        source_details = full_by_id[fact["fact_id"]].get("bindings_and_path_details", {})
        additions = {
            field: copy.deepcopy(source_details[field])
            for field in RESTORED_FIELDS if field in source_details
        }
        if not additions:
            continue
        details = fact.setdefault("bindings_and_path_details", {})
        overlap = set(details) & set(additions)
        if overlap:
            raise ValueError(f"restored detail already exists: {sorted(overlap)}")
        details.update(additions)
        changed[fact["fact_id"]] = additions
    return restored, {
        "restored_fields": list(RESTORED_FIELDS),
        "changed_fact_ids": sorted(changed),
        "restored_values_sha256": digest(changed),
    }


def _fact_summary(fact: dict[str, Any]) -> dict[str, Any]:
    locations = []
    for endpoint_name in ("from", "to"):
        endpoint = fact.get(endpoint_name)
        if not isinstance(endpoint, dict) or "logical_path" not in endpoint:
            continue
        locations.append({
            "endpoint": endpoint_name,
            "logical_path": endpoint["logical_path"],
            "start_line": endpoint.get("start_line"),
            "end_line": endpoint.get("end_line"),
        })
    return {
        "fact_id": fact["fact_id"],
        "kind": fact["kind"],
        "modality": fact["modality"],
        "roles": fact.get("roles", []),
        "source_locations": locations,
    }


def _step_groups(
    fact_refs: list[str], facts: dict[str, dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    groups = {name: [] for name in ("guards", "calls", "returns", "exceptions", "remaining")}
    for fact_id in fact_refs:
        fact = facts[fact_id]
        roles = set(fact.get("roles", []))
        if any(role.startswith("guard:") for role in roles):
            group = "guards"
        elif fact["kind"] == "call_binding":
            group = "calls"
        elif "successor:exceptional" in roles or fact["kind"] == "exception_dependence":
            group = "exceptions"
        elif "successor:normal" in roles or fact["kind"] == "normal_return_dependence":
            group = "returns"
        else:
            group = "remaining"
        groups[group].append(_fact_summary(fact))
    return groups


def path_layout(compact: dict[str, Any]) -> dict[str, Any]:
    """Group exact fact references under observations without inventing chains."""
    source = copy.deepcopy(compact)
    facts = source.pop("facts")
    fact_sets = source.pop("fact_sets")
    observations = source.pop("observations")
    facts_by_id = {fact["fact_id"]: fact for fact in facts}
    sets_by_id = {row["fact_set_id"]: row for row in fact_sets}
    paths = []
    assigned_set_ids = set()
    for observation in observations:
        fact_set_id = observation["navigation"]["fact_ref_set_id"]
        fact_set = sets_by_id[fact_set_id]
        assigned_set_ids.add(fact_set_id)
        paths.append({
            "observation": observation,
            "fact_set": fact_set,
            "steps": _step_groups(fact_set["fact_refs"], facts_by_id),
        })
    source.update({
        "layout_schema": LAYOUT_SCHEMA,
        "observation_paths": paths,
        "fact_lookup": {fact_id: facts_by_id[fact_id] for fact_id in sorted(facts_by_id)},
        "layout_order": {
            "fact_ids": [fact["fact_id"] for fact in facts],
            "fact_set_ids": [row["fact_set_id"] for row in fact_sets],
        },
        "unassigned_fact_sets": [
            row for row in fact_sets if row["fact_set_id"] not in assigned_set_ids
        ],
    })
    return source


def recover_original_layout(path_package: dict[str, Any]) -> dict[str, Any]:
    source = copy.deepcopy(path_package)
    if source.pop("layout_schema") != LAYOUT_SCHEMA:
        raise ValueError("unsupported path layout")
    paths = source.pop("observation_paths")
    lookup = source.pop("fact_lookup")
    order = source.pop("layout_order")
    unassigned = source.pop("unassigned_fact_sets")
    source["observations"] = [row["observation"] for row in paths]
    set_by_id = {row["fact_set"]["fact_set_id"]: row["fact_set"] for row in paths}
    set_by_id.update({row["fact_set_id"]: row for row in unassigned})
    source["fact_sets"] = [set_by_id[fact_set_id] for fact_set_id in order["fact_set_ids"]]
    source["facts"] = [lookup[fact_id] for fact_id in order["fact_ids"]]
    return source


def layout_proof(original: dict[str, Any], organized: dict[str, Any]) -> dict[str, Any]:
    recovered = recover_original_layout(organized)
    original_bytes = encode(original).encode()
    recovered_bytes = encode(recovered).encode()
    return {
        "equivalent": recovered_bytes == original_bytes,
        "original_sha256": digest(original_bytes),
        "recovered_sha256": digest(recovered_bytes),
        "fact_count": len(original["facts"]),
        "observation_count": len(original["observations"]),
        "fact_set_count": len(original["fact_sets"]),
    }


def build_prompts() -> tuple[dict[str, str], dict[str, Any]]:
    """Return OC/PC/OR/PR prompts and mechanical construction proofs."""
    full_prompt, compact_prompt = load_frozen_prompts()
    full_prefix, full = split_prompt(full_prompt)
    prefix, compact = split_prompt(compact_prompt)
    if full_prefix != prefix:
        raise ValueError("frozen B/C prompt prefixes differ")
    restored, restoration = restore_details(compact, full)
    packages = {
        "OC": compact,
        "PC": path_layout(compact),
        "OR": restored,
        "PR": path_layout(restored),
    }
    prompts = {arm: prefix + encode(package) + "\n" for arm, package in packages.items()}
    if prompts["OC"] != compact_prompt:
        raise ValueError("OC differs from the frozen Jib C prompt")
    example_path = packages["PC"]["observation_paths"][0]
    example_refs = example_path["fact_set"]["fact_refs"][:3]
    return prompts, {
        "arms": ARM_NAMES,
        "original_compact_is_frozen_c": True,
        "compact_layout": layout_proof(compact, packages["PC"]),
        "restored_layout": layout_proof(restored, packages["PR"]),
        "restoration": restoration,
        "prompt_sha256": {arm: digest(prompt.encode()) for arm, prompt in prompts.items()},
        "prompt_bytes": {arm: len(prompt.encode()) for arm, prompt in prompts.items()},
        "rendered_example": {
            "observation_id": example_path["observation"]["observation_id"],
            "location": example_path["observation"]["location"],
            "last_visible_locations": example_path["observation"]["navigation"]["last_visible_locations"],
            "steps": example_path["steps"],
            "facts": {
                fact_id: packages["PC"]["fact_lookup"][fact_id] for fact_id in example_refs
            },
        },
    }
