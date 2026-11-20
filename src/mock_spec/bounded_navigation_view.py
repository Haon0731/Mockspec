"""Build a deterministic, budget-bounded view of navigation facts.

The full navigation facts remain the audit artifact.  This module only derives
an independently serializable delivery view and records how to retrieve every
budget-omitted category from that artifact.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from collections.abc import Callable, Iterable
from copy import deepcopy
from typing import Any

from .backward_result import BINDING_ORIGINS, BINDING_STATUSES
from .navigation_facts import SCHEMA_VERSION as FACTS_VERSION


SCHEMA_VERSION = "mock-spec.navigation-facts-bounded/v1"
DEFAULT_MAX_TOKENS = 8_192
DEFAULT_COUNTER_NAME = "utf8_bytes_v1"

TokenCounter = Callable[[bytes], int]

__all__ = [
    "DEFAULT_COUNTER_NAME",
    "DEFAULT_MAX_TOKENS",
    "SCHEMA_VERSION",
    "build_bounded_navigation_view",
    "measure_bounded_navigation_view",
    "serialize_bounded_navigation_view",
]


def serialize_bounded_navigation_view(view: dict[str, Any]) -> bytes:
    """Serialize a bounded view in the exact form used for budget accounting."""
    return json.dumps(
        view,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _encoded(value: Any) -> bytes:
    return serialize_bounded_navigation_view(value)


def _sha256(value: Any) -> str:
    return hashlib.sha256(_encoded(value)).hexdigest()


def _canonical_file_sha256(value: Any) -> str:
    return hashlib.sha256(_encoded(value) + b"\n").hexdigest()


def _default_counter(payload: bytes) -> int:
    return len(payload)


def _count_tokens(counter: TokenCounter, payload: bytes) -> int:
    count = counter(payload)
    if type(count) is not int or count < 0:
        raise ValueError("token_counter must return a non-negative integer")
    return count


def _compact_observations(observations: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Store required observation semantics without repeating location field names."""
    columns = [
        "observation_id",
        "kind",
        "modality",
        "origin",
        "path_ref",
        "start_line",
        "end_line",
    ]
    rows = []
    paths: list[str | None] = []
    for observation in observations:
        location = observation.get("location", {})
        path = location.get("logical_path")
        if path not in paths:
            paths.append(path)
        rows.append(
            [
                observation.get("observation_id"),
                observation.get("kind"),
                observation.get("modality"),
                observation.get("origin"),
                paths.index(path),
                location.get("start_line"),
                location.get("end_line"),
            ]
        )
    return {"columns": columns, "paths": paths, "rows": rows}


def _reason_pattern(reason: Any) -> str:
    """Group instance-specific numbers while retaining the boundary class."""
    return re.sub(r"\b\d+\b", "{n}", str(reason))


def _compact_boundaries(boundaries: Any) -> Any:
    if not isinstance(boundaries, dict):
        return boundaries
    compact: dict[str, Any] = {}
    for category in sorted(boundaries):
        value = boundaries[category]
        if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
            compact[category] = value
            continue
        groups: dict[str, dict[str, Any]] = {}
        for index, item in enumerate(value):
            reason = _reason_pattern(item.get("reason", "Unknown"))
            group = groups.setdefault(
                str(reason),
                {"reason_pattern": reason, "scopes": [], "archive_indices": []},
            )
            scope = item.get("scope")
            if scope is not None and scope not in group["scopes"]:
                group["scopes"].append(scope)
            group["archive_indices"].append(index)
        compact[category] = {
            "archive_pointer": f"/analysis_boundaries/{category}",
            "columns": ["reason_pattern", "scopes", "archive_indices"],
            "rows": [
                [
                    group["reason_pattern"],
                    group["scopes"],
                    group["archive_indices"],
                ]
                for _, group in sorted(groups.items())
            ],
        }
    return compact


def _dependency_steps(value: Any) -> int:
    return len(value) if isinstance(value, list) else 0


def _compact_source(
    source: dict[str, Any], index: int, archive_sha256: str
) -> dict[str, Any]:
    result = deepcopy(source)
    provenance = result.get("provenance")
    if not isinstance(provenance, dict) or "dependency_path" not in provenance:
        return result
    dependency_path = provenance.pop("dependency_path")
    provenance["dependency_path_ref"] = {
        "archive_sha256": archive_sha256,
        "archive_pointer": f"/evidence_sources/{index}/provenance/dependency_path",
        "sha256": _sha256(dependency_path),
        "step_count": _dependency_steps(dependency_path),
    }
    return result


def _compact_guard(guard: dict[str, Any]) -> dict[str, Any]:
    keys = ("guard_id", "fact_ref", "observation_ids")
    return {key: guard[key] for key in keys if key in guard}


def _observation_ids(category: str, item: dict[str, Any]) -> tuple[str, ...]:
    identifiers = set(item.get("observation_ids", []))
    if category == "calls":
        for effect in item.get("effects", []):
            identifiers.update(effect.get("observation_ids", []))
        for flow in item.get("other_flows", []):
            identifiers.update(flow.get("observation_ids", []))
    return tuple(sorted(identifier for identifier in identifiers if isinstance(identifier, str)))


def _fact_references(value: Any) -> set[str]:
    references: set[str] = set()
    if isinstance(value, dict):
        reference = value.get("fact_ref")
        if isinstance(reference, str):
            references.add(reference)
        listed = value.get("fact_refs")
        if isinstance(listed, list):
            references.update(item for item in listed if isinstance(item, str))
        for nested in value.values():
            references.update(_fact_references(nested))
    elif isinstance(value, list):
        for nested in value:
            references.update(_fact_references(nested))
    return references


def _source_path_length(source: dict[str, Any] | None) -> int:
    if not source:
        return 0
    provenance = source.get("provenance")
    if not isinstance(provenance, dict):
        return 0
    return _dependency_steps(provenance.get("dependency_path"))


def _candidate_priority(
    category: str,
    item: dict[str, Any],
    relation_sources: dict[str, dict[str, Any]],
) -> tuple[Any, ...]:
    identifier = item.get("call_id") or item.get("guard_id") or item.get("fact_id") or ""
    has_location = isinstance(item.get("location"), dict)
    direct = has_location and item.get("structural_role") in {
        "entry_call",
        "internal_call",
        "outbound_call",
    }
    path_length = 0
    if category == "relations":
        path_length = _source_path_length(relation_sources.get(item.get("fact_id", "")))
    elif category == "calls":
        lengths = [
            _source_path_length(relation_sources.get(reference))
            for reference in _fact_references(item)
        ]
        path_length = min(lengths, default=0)
    elif category == "guards":
        path_length = _source_path_length(relation_sources.get(item.get("fact_ref", "")))
    return (not direct, not has_location, path_length, str(identifier))


def _ordered_candidates(
    facts: dict[str, Any], relation_sources: dict[str, dict[str, Any]]
) -> list[tuple[str, dict[str, Any]]]:
    observation_order = [
        item["observation_id"]
        for item in facts["observations"]
        if isinstance(item, dict) and isinstance(item.get("observation_id"), str)
    ]
    buckets: dict[str, list[tuple[str, dict[str, Any]]]] = defaultdict(list)
    unassigned: list[tuple[str, dict[str, Any]]] = []
    for category in ("calls", "guards", "relations"):
        for item in facts.get(category, []):
            candidate = (category, item)
            identifiers = _observation_ids(category, item)
            if identifiers:
                for identifier in identifiers:
                    buckets[identifier].append(candidate)
            else:
                unassigned.append(candidate)
    for candidates in buckets.values():
        candidates.sort(
            key=lambda row: _candidate_priority(row[0], row[1], relation_sources)
        )
    unassigned.sort(
        key=lambda row: _candidate_priority(row[0], row[1], relation_sources)
    )

    ordered: list[tuple[str, dict[str, Any]]] = []
    seen: set[tuple[str, str]] = set()
    max_length = max((len(buckets[key]) for key in observation_order), default=0)
    for offset in range(max_length):
        for identifier in observation_order:
            candidates = buckets[identifier]
            if offset >= len(candidates):
                continue
            category, item = candidates[offset]
            item_id = str(item.get("call_id") or item.get("guard_id") or item.get("fact_id"))
            if (category, item_id) not in seen:
                seen.add((category, item_id))
                ordered.append((category, item))
    for category, item in unassigned:
        item_id = str(item.get("call_id") or item.get("guard_id") or item.get("fact_id"))
        if (category, item_id) not in seen:
            seen.add((category, item_id))
            ordered.append((category, item))
    return ordered


def _category_counts(facts: dict[str, Any], view: dict[str, Any]) -> dict[str, dict[str, Any]]:
    counts = {}
    pointers = {
        "calls": "/calls",
        "guards": "/guards",
        "relations": "/relations",
        "endpoints": "/endpoints",
        "evidence_sources": "/evidence_sources",
    }
    for category, pointer in pointers.items():
        total = len(facts.get(category, []))
        included = len(view[category])
        counts[category] = {
            "total": total,
            "included": included,
            "omitted": total - included,
            "archive_pointer": pointer,
        }
    return counts


def measure_bounded_navigation_view(
    view: dict[str, Any],
    *,
    token_counter: TokenCounter | None = None,
    token_counter_name: str | None = None,
) -> dict[str, Any]:
    """Measure a finalized view without creating a self-referential payload."""
    counter = token_counter or _default_counter
    counter_name = token_counter_name or DEFAULT_COUNTER_NAME
    if token_counter is not None and token_counter_name is None:
        raise ValueError("token_counter_name is required with a custom token_counter")
    if token_counter is None and token_counter_name is not None:
        raise ValueError("token_counter is required with a custom token_counter_name")
    expected = view.get("budget", {}).get("counter")
    if expected != counter_name:
        raise ValueError(f"view was built for {expected!r}, not {counter_name!r}")
    payload = serialize_bounded_navigation_view(view)
    return {
        "counter": counter_name,
        "serialized_bytes": len(payload),
        "tokens": _count_tokens(counter, payload),
    }


_TABLE_IDS = {
    "observations": "observation_id",
    "calls": "call_id",
    "guards": "guard_id",
    "relations": "fact_id",
    "endpoints": "endpoint_id",
    "evidence_sources": "source_id",
}


def _require_shape(value: Any, expected: type, path: str) -> None:
    if not isinstance(value, expected):
        raise ValueError(f"{path} must be a {expected.__name__}")


def _require_id(value: Any, path: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{path} must be a non-empty string")
    return value


def _check_reference(value: Any, path: str, table: dict, name: str) -> None:
    if _require_id(value, path) not in table:
        raise ValueError(f"{path} refers to missing {name} ID {value!r}")


def _check_card_references(value: Any, path: str, tables: dict) -> None:
    if isinstance(value, list):
        for i, item in enumerate(value):
            _check_card_references(item, f"{path}[{i}]", tables)
    elif isinstance(value, dict):
        for key, item in value.items():
            field = f"{path}.{key}"
            if key == "fact_ref":
                _check_reference(item, field, tables["relations"], "relations")
            elif key in ("fact_refs", "observation_ids"):
                name = "relations" if key == "fact_refs" else "observations"
                _require_shape(item, list, field)
                for i, ref in enumerate(item):
                    _check_reference(ref, f"{field}[{i}]", tables[name], name)
            else:
                _check_card_references(item, field, tables)


def _validated_tables(facts: dict[str, Any]) -> dict[str, dict[str, dict]]:
    _require_shape(facts, dict, "facts")
    if facts.get("schema_version") != FACTS_VERSION:
        raise ValueError(f"schema_version must be {FACTS_VERSION}")
    for field in ("identity", "entry_binding", "analysis_boundaries", "projection"):
        _require_shape(facts.get(field), dict, field)
    for key in ("dataset", "case_id", "snapshot_id", "language"):
        _require_id(facts["identity"].get(key), f"identity.{key}")
    if facts["identity"]["language"] not in ("python", "java"):
        raise ValueError("identity.language must be python or java")
    for key, choices in (("origin", BINDING_ORIGINS), ("status", BINDING_STATUSES)):
        value = _require_id(facts["entry_binding"].get(key), f"entry_binding.{key}")
        if value not in choices:
            raise ValueError(f"entry_binding.{key} is invalid")
    for key in ("assumptions", "unknown_effects", "unanalysed_regions"):
        _require_shape(facts["analysis_boundaries"].get(key), list, f"analysis_boundaries.{key}")

    tables: dict[str, dict[str, dict]] = {}
    for name, key in _TABLE_IDS.items():
        _require_shape(facts.get(name), list, name)
        index: dict[str, dict] = {}
        for i, row in enumerate(facts[name]):
            path = f"{name}[{i}]"
            _require_shape(row, dict, path)
            row_id = _require_id(row.get(key), f"{path}.{key}")
            if row_id in index:
                # Canonical JSON distinguishes true from 1 and ignores key order.
                if name == "relations" and _encoded(row) == _encoded(index[row_id]):
                    continue
                raise ValueError(f"{path}.{key} duplicates {row_id!r}")
            index[row_id] = row
        tables[name] = index

    for name in ("calls", "guards", "relations"):
        for i, row in enumerate(facts[name]):
            path = f"{name}[{i}]"
            _check_card_references(row, path, tables)
            if name == "relations":
                for key, target in (("from_ref", "endpoints"), ("to_ref", "endpoints"),
                                    ("source_ref", "evidence_sources")):
                    _check_reference(row.get(key), f"{path}.{key}", tables[target], target)
    return tables


def _add_relation_closure(
    view: dict[str, Any],
    relation_id: str,
    relations: dict[str, dict[str, Any]],
    endpoints: dict[str, dict[str, Any]],
    sources: dict[str, dict[str, Any]],
) -> None:
    if any(item["fact_id"] == relation_id for item in view["relations"]):
        return
    relation = relations.get(relation_id)
    if relation is None:
        return
    view["relations"].append(deepcopy(relation))
    endpoint_ids = {item["endpoint_id"] for item in view["endpoints"]}
    for reference in (relation.get("from_ref"), relation.get("to_ref")):
        if reference in endpoints and reference not in endpoint_ids:
            view["endpoints"].append(deepcopy(endpoints[reference]))
            endpoint_ids.add(reference)
    source_ids = {item["source_id"] for item in view["evidence_sources"]}
    reference = relation.get("source_ref")
    if reference in sources and reference not in source_ids:
        view["evidence_sources"].append(deepcopy(sources[reference]))


def _add_candidate(
    view: dict[str, Any],
    category: str,
    item: dict[str, Any],
    relations: dict[str, dict[str, Any]],
    endpoints: dict[str, dict[str, Any]],
    sources: dict[str, dict[str, Any]],
) -> None:
    if category == "calls":
        view["calls"].append(deepcopy(item))
        for reference in sorted(_fact_references(item)):
            _add_relation_closure(view, reference, relations, endpoints, sources)
    elif category == "guards":
        view["guards"].append(_compact_guard(item))
        guard_reference = item.get("fact_ref")
        if isinstance(guard_reference, str):
            _add_relation_closure(view, guard_reference, relations, endpoints, sources)
    else:
        _add_relation_closure(view, item["fact_id"], relations, endpoints, sources)
    for name in ("calls", "guards", "relations", "endpoints", "evidence_sources"):
        key = _TABLE_IDS[name]
        view[name].sort(key=lambda row: str(row[key]))


def build_bounded_navigation_view(
    facts: dict[str, Any],
    *,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    token_counter: TokenCounter | None = None,
    token_counter_name: str | None = None,
    archive_sha256: str | None = None,
    archive_locator: str = "facts.json",
) -> dict[str, Any]:
    """Derive a deterministic view whose exact serialization fits ``max_tokens``.

    The built-in counter defines one token as one UTF-8 byte.  Callers with a
    target-model tokenizer can inject its exact count function and name.
    """
    if type(max_tokens) is not int or max_tokens < 1:
        raise ValueError("max_tokens must be a positive integer")
    counter = token_counter or _default_counter
    counter_name = token_counter_name or DEFAULT_COUNTER_NAME
    if token_counter is not None and token_counter_name is None:
        raise ValueError("token_counter_name is required with a custom token_counter")
    if token_counter is None and token_counter_name is not None:
        raise ValueError("token_counter is required with a custom token_counter_name")
    tables = _validated_tables(facts)
    canonical_sha256 = _canonical_file_sha256(facts)
    archive_sha = archive_sha256 or canonical_sha256

    source_rows = facts["evidence_sources"]
    compact_sources = {
        source["source_id"]: _compact_source(source, index, archive_sha)
        for index, source in enumerate(source_rows)
    }
    relation_rows = tables["relations"]
    if len(relation_rows) != len(facts["relations"]):
        facts = {**facts, "relations": list(relation_rows.values())}
    endpoint_rows = tables["endpoints"]
    original_sources = tables["evidence_sources"]
    view: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "archive": {
            "sha256": archive_sha,
            "sha256_basis": (
                "caller_supplied_file"
                if archive_sha256
                else "canonical_json_file_with_trailing_newline"
            ),
            "locator": archive_locator,
        },
        "identity": deepcopy(facts["identity"]),
        "entry_binding": deepcopy(facts["entry_binding"]),
        "observations": _compact_observations(facts["observations"]),
        "analysis_boundaries": _compact_boundaries(facts["analysis_boundaries"]),
        "calls": [],
        "guards": [],
        "relations": [],
        "endpoints": [],
        "evidence_sources": [],
        "omissions": {
            "truncated": False,
            "semantic": "budget_omission_is_not_semantic_absence",
            "categories": {},
        },
        "budget": {
            "max_tokens": max_tokens,
            "counter": counter_name,
        },
    }
    view["omissions"]["categories"] = _category_counts(facts, view)
    if _count_tokens(counter, serialize_bounded_navigation_view(view)) > max_tokens:
        raise ValueError("mandatory navigation fields exceed the token budget")

    relation_sources: dict[str, dict[str, Any]] = {
        relation_id: original_sources[row["source_ref"]]
        for relation_id, row in relation_rows.items()
    }
    for category, item in _ordered_candidates(facts, relation_sources):
        candidate = deepcopy(view)
        _add_candidate(
            candidate,
            category,
            item,
            relation_rows,
            endpoint_rows,
            compact_sources,
        )
        candidate["omissions"]["categories"] = _category_counts(facts, candidate)
        candidate["omissions"]["truncated"] = any(
            row["omitted"] > 0
            for row in candidate["omissions"]["categories"].values()
        )
        if _count_tokens(counter, serialize_bounded_navigation_view(candidate)) <= max_tokens:
            view = candidate

    view["omissions"]["categories"] = _category_counts(facts, view)
    view["omissions"]["truncated"] = any(
        row["omitted"] > 0 for row in view["omissions"]["categories"].values()
    )
    payload = serialize_bounded_navigation_view(view)
    if _count_tokens(counter, payload) > max_tokens:
        raise AssertionError("bounded navigation view exceeded its hard token limit")
    return view
