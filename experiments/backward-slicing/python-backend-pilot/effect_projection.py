"""Project observation-linked CodeQL effect rows into the research contract.

The projector does not infer aliases or effects.  It preserves the backend's
modality and refuses rows that are not attached to one unambiguous observation.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
import hashlib
from typing import Any


PROVEN_RELATIONS = {"visible_write_to_observation", "visible_product_write_to_observation"}
UNKNOWN_RELATIONS = {
    "unknown_region_effect_to_observation",
    "possible_alias_effect_to_observation",
    "unknown_exception_path_to_observation",
    "unknown_exception_to_pytest_observation",
}
EXCLUDED_RELATIONS = {"region_effect_excluded_by_overwrite"}
ALL_RELATIONS = PROVEN_RELATIONS | UNKNOWN_RELATIONS | EXCLUDED_RELATIONS


class EffectProjectionError(ValueError):
    """The backend row cannot be safely attached to an observation."""


@dataclass(frozen=True)
class Observation:
    observation_id: str
    line: int


def _integer(row: Mapping[str, str], key: str) -> int:
    try:
        return int(row[key])
    except (KeyError, TypeError, ValueError) as error:
        raise EffectProjectionError(f"invalid {key}: {row.get(key)!r}") from error


def _endpoint(row: Mapping[str, str], prefix: str) -> dict[str, Any]:
    return {
        "logical_path": row[f"{prefix}Path"],
        "start_line": _integer(row, f"{prefix}Line"),
        "end_line": _integer(row, f"{prefix}EndLine"),
    }


def _call_evidence(row: Mapping[str, str]) -> tuple[dict[str, Any] | None,
                                                     dict[str, Any] | None]:
    call_line = _integer(row, "callLine")
    call_end_line = _integer(row, "callEndLine")
    call_column = _integer(row, "callColumn")
    call_end_column = _integer(row, "callEndColumn")
    product_line = _integer(row, "productLine")
    product_end_line = _integer(row, "productEndLine")
    call_path = row.get("callPath", "")
    product_path = row.get("productPath", "")
    product_name = row.get("productName", "")
    if not call_path:
        if any((call_line, call_end_line, call_column, call_end_column, product_path,
                product_line, product_end_line, product_name)):
            raise EffectProjectionError("partial empty call evidence")
        return None, None
    if min(call_line, call_end_line, call_column, call_end_column) < 1:
        raise EffectProjectionError("invalid concrete call location")
    call = {"logical_path": call_path, "start_line": call_line,
            "end_line": call_end_line, "start_column": call_column,
            "end_column": call_end_column}
    if not product_path:
        if any((product_line, product_end_line, product_name)):
            raise EffectProjectionError("partial empty product target evidence")
        return call, None
    if product_line < 1 or product_end_line < product_line or not product_name:
        raise EffectProjectionError("invalid product target evidence")
    return call, {"logical_path": product_path, "start_line": product_line,
                  "end_line": product_end_line, "symbol": product_name}


def project_effect_rows(
    observations: Iterable[Observation], rows: Iterable[Mapping[str, str]], *, query_sha256: str
) -> dict[str, list[dict[str, Any]]]:
    """Return typed candidates, unknown effects and audited exclusions.

    Duplicate observation lines are rejected because the query row only carries
    the original line.  This mirrors the generic backend's ambiguity gate.
    """

    by_line: dict[int, list[str]] = {}
    for observation in observations:
        by_line.setdefault(observation.line, []).append(observation.observation_id)

    result: dict[str, list[dict[str, Any]]] = {
        "relations": [], "unknown_effects": [], "exclusions": []
    }
    seen: set[tuple[object, ...]] = set()
    for row in rows:
        relation = row.get("relation", "")
        if relation not in ALL_RELATIONS:
            raise EffectProjectionError(f"unsupported relation: {relation!r}")
        observation_line = _integer(row, "observationLine")
        observation_ids = by_line.get(observation_line, [])
        if len(observation_ids) != 1:
            raise EffectProjectionError(
                f"observation line {observation_line} maps to {len(observation_ids)} ids"
            )
        source = _endpoint(row, "source")
        target = _endpoint(row, "target")
        source_column = _integer(row, "sourceColumn")
        source_end_column = _integer(row, "sourceEndColumn")
        target_column = _integer(row, "targetColumn")
        target_end_column = _integer(row, "targetEndColumn")
        if target["start_line"] != observation_line:
            raise EffectProjectionError("target line does not match authenticated observation line")
        argument_index = _integer(row, "argumentIndex")
        call, product_target = _call_evidence(row)
        key = (
            relation, source["logical_path"], source["start_line"], source_column,
            source_end_column, target["logical_path"], observation_line,
            target_column, target_end_column, row.get("fieldName", ""),
            row.get("regionKey", ""), argument_index, row.get("route", ""),
            row.get("detail", ""),
            None if call is None else tuple(call.values()),
            None if product_target is None else tuple(product_target.values()),
        )
        if key in seen:
            continue
        seen.add(key)
        common = {
            "observation_ids": observation_ids,
            "from": source,
            "to": target,
            "field": row.get("fieldName", ""),
            "region": row.get("regionKey", ""),
            "route": row.get("route", ""),
            "argument_index": argument_index,
            "effect_location": {"start_column": source_column,
                                "end_column": source_end_column},
            "observation_location": {"start_column": target_column,
                                     "end_column": target_end_column},
            "call": call,
            "product_target": product_target,
            "assumptions": [
                "ordinary Python attribute semantics; descriptor, __getattribute__, and "
                "__setattr__ effects are not modelled"
            ],
            "provenance": {
                "backend": "CodeQL",
                "query_relation": relation,
                "query_detail": row.get("detail", ""),
                "query_sha256": query_sha256,
            },
        }
        if relation in PROVEN_RELATIONS:
            if row.get("certainty") != "candidate":
                raise EffectProjectionError("visible write must remain an analysis candidate")
            result["relations"].append({
                "kind": "state_flow", "modality": "analysis_may", **common,
                "precision": (
                    "same unique local-source region and field with CFG reachability; "
                    "path feasibility and runtime dispatch remain unreviewed"
                ),
                "assumptions": [
                    "CodeQL local-source identity represents the same runtime object region",
                    "ordinary Python attribute semantics; descriptor, __getattribute__, and "
                    "__setattr__ effects are not modelled",
                ],
            })
        elif relation in UNKNOWN_RELATIONS:
            if row.get("certainty") != "unknown_effect":
                raise EffectProjectionError("opaque effect cannot be promoted to a relation")
            effect = {
                "unknown_region_effect_to_observation": "call_may_modify_observed_region",
                "possible_alias_effect_to_observation": (
                    "call_may_modify_possible_alias_of_observed_region"
                ),
                "unknown_exception_path_to_observation": (
                    "call_exception_may_reach_observation_handler"
                ),
                "unknown_exception_to_pytest_observation": (
                    "call_exception_may_satisfy_pytest_raises_type_compatibility_unknown"
                ),
            }[relation]
            identity = "\x1f".join(str(part) for part in key)
            result["unknown_effects"].append({
                "boundary_id": f"effect:{hashlib.sha256(identity.encode()).hexdigest()[:20]}",
                "evidence_identity": {
                    "relation": relation, "field": common["field"],
                    "region": common["region"], "argument_index": argument_index,
                    "route": common["route"], "observation_id": observation_ids[0],
                    "source_column": source_column,
                    "source_end_column": source_end_column,
                    "call": call, "product_target": product_target,
                },
                "effect": effect,
                "precision": "unknown_effect",
                "observation_ids": observation_ids,
                "provenance": common["provenance"],
                "assumptions": common["assumptions"],
                "scope": common,
            })
        else:
            if row.get("certainty") != "excluded":
                raise EffectProjectionError("overwrite exclusion requires excluded certainty")
            result["exclusions"].append({
                "reason": "dominating_same_region_same_field_overwrite",
                **common,
            })
    return result
