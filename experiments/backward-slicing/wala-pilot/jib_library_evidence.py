"""Extract exact library copy-constructor witnesses from WALA SSA rows.

The witness is deliberately structural.  It proves that an exact instance field is
passed to a resolved constructor whose receiver is a freshly allocated object, and
that this same object is returned after the constructor call.  It does *not* claim
element equality, index values, alias identity, or absence of constructor effects.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence, cast


Row = Mapping[str, Any]

# These API contracts supply the content effect; SSA below supplies the actual
# field, receiver, argument/formal, and return bindings at the selected call.
_COPY_API_CONTRACTS = {
    "java.util.ArrayList.<init>(Ljava/util/Collection;)V": "ArrayList",
    "java.util.HashMap.<init>(Ljava/util/Map;)V": "HashMap",
    "java.util.HashSet.<init>(Ljava/util/Collection;)V": "HashSet",
    "java.util.LinkedHashSet.<init>(Ljava/util/Collection;)V": "LinkedHashSet",
}

_FIELD_RE = re.compile(
    r"getfield\s+<\s*[^,]+,\s*L(?P<owner>[^,]+),\s*"
    r"(?P<field>[^,]+),"
)
_NEW_RE = re.compile(r"new\s+<\s*[^,]+,L(?P<type>[^>]+)>")
_CTOR_RE = re.compile(
    r"^(?P<owner>[\w.$/]+)\.<init>\((?P<arguments>.*)\)V$"
)
_PARAM_RE = re.compile(r"\bv(?P<value>\d+)\s*$")


def _values(row: Row, key: str) -> list[int]:
    values = row.get(key)
    if not isinstance(values, list) or not all(isinstance(value, int) for value in values):
        return []
    return values


def _exact_method_rows(rows: Iterable[Row], method: str) -> list[Row]:
    return [row for row in rows if row.get("method") == method]


def _field_fact(row: Row) -> tuple[str, str] | None:
    instruction = row.get("instruction")
    match = _FIELD_RE.search(instruction) if isinstance(instruction, str) else None
    return (match.group("owner").replace("/", "."), match.group("field")) if match else None


def _allocation_type(row: Row) -> str | None:
    instruction = row.get("instruction")
    match = _NEW_RE.search(instruction) if isinstance(instruction, str) else None
    return match.group("type").replace("/", ".") if match else None


def _constructor_fact(row: Row) -> tuple[str, str] | None:
    target = row.get("declared_target")
    match = _CTOR_RE.fullmatch(target) if isinstance(target, str) else None
    return (match.group("owner").replace("/", "."), match.group("arguments")) if match else None


def _target_detail(row: Row) -> Mapping[str, Any] | None:
    target = row.get("declared_target")
    details = row.get("possible_target_details")
    if not isinstance(target, str) or not isinstance(details, list):
        return None
    exact = [detail for detail in details if isinstance(detail, dict)
             and detail.get("method") == target and detail.get("has_ir") is True]
    return exact[0] if len(exact) == 1 else None


def _this_value(rows: Sequence[Row]) -> int | None:
    values = set()
    for row in rows:
        if row.get("kind") != "PARAM_CALLEE":
            continue
        statement = row.get("statement")
        match = _PARAM_RE.search(statement) if isinstance(statement, str) else None
        if match:
            values.add(int(match.group("value")))
    return next(iter(values)) if len(values) == 1 else None


def _safe_return_path(rows: Sequence[Row], start: int, goal: int) -> list[Row] | None:
    """Find a path through identity-preserving SSA operations only."""
    if start == goal:
        return []
    permitted = {"PHI", "PI"}
    edges: dict[int, list[tuple[int, Row]]] = {}
    for row in rows:
        instruction = row.get("instruction")
        identity = (row.get("kind") in permitted
                    or isinstance(instruction, str) and "checkcast" in instruction)
        if not identity:
            continue
        for use in _values(row, "uses"):
            for definition in _values(row, "definitions"):
                edges.setdefault(use, []).append((definition, row))
    queue: list[tuple[int, list[Row]]] = [(start, [])]
    seen = {start}
    while queue:
        value, path = queue.pop(0)
        for definition, row in edges.get(value, []):
            if definition == goal:
                return [*path, row]
            if definition not in seen:
                seen.add(definition)
                queue.append((definition, [*path, row]))
    return None


def _step(kind: str, row: Row, **values: Any) -> dict[str, Any]:
    return {
        "kind": kind,
        "method": row.get("method"),
        "context": row.get("context"),
        "instruction_index": row.get("instruction_index"),
        "instruction": row.get("instruction"),
        "uses": _values(row, "uses"),
        "definitions": _values(row, "definitions"),
        "basic_block": row.get("basic_block"),
        "normal_successor_blocks": row.get("normal_successor_blocks", []),
        "exceptional_successor_blocks": row.get("exceptional_successor_blocks", []),
        **values,
    }


def extract_copy_constructor_witness(
    rows: Sequence[Row], getter_method: str, expected_field: str,
) -> dict[str, Any]:
    """Return a fail-closed field -> copy-constructor -> return witness.

    ``getter_method`` must be the complete WALA method name including descriptor.
    A complete result applies only to successful normal return from the represented
    return site. Branching getters remain incomplete unless the returned copy arm
    has explicit identity PHI/PI facts; other arms are not strengthened.
    """
    method_rows = _exact_method_rows(rows, getter_method)
    owner = getter_method.split("(", 1)[0].rsplit(".", 1)[0]
    this_value = _this_value(method_rows)
    field_rows = [row for row in method_rows if _field_fact(row) == (owner, expected_field)]
    allocations = [row for row in method_rows if _allocation_type(row) is not None]
    constructors = [row for row in method_rows if _constructor_fact(row) is not None]
    returns = [row for row in method_rows
               if isinstance(row.get("instruction"), str)
               and row["instruction"].startswith("return ")]
    candidates: list[tuple[Row, Row, Row, Row, Mapping[str, Any], list[Row]]] = []
    for field_row in field_rows:
        field_defs = _values(field_row, "definitions")
        field_uses = _values(field_row, "uses")
        if len(field_defs) != 1 or len(field_uses) != 1 or field_uses[0] != this_value:
            continue
        for allocation in allocations:
            allocation_defs = _values(allocation, "definitions")
            allocation_type = _allocation_type(allocation)
            if len(allocation_defs) != 1 or allocation_type is None:
                continue
            for constructor in constructors:
                ctor = _constructor_fact(constructor)
                detail = _target_detail(constructor)
                uses = _values(constructor, "uses")
                formals = _values(detail or {}, "parameter_value_numbers")
                if (ctor is None or detail is None or ctor[0] != allocation_type
                        or len(uses) != len(formals) or len(uses) < 2
                        or uses[0] != allocation_defs[0]
                        or field_defs[0] not in uses[1:]):
                    continue
                source_index = uses.index(field_defs[0])
                if source_index == 0 or formals[source_index] == formals[0]:
                    continue
                for returned in returns:
                    return_uses = _values(returned, "uses")
                    if len(return_uses) != 1:
                        continue
                    identity_path = _safe_return_path(
                        method_rows, allocation_defs[0], return_uses[0]
                    )
                    indexes = [field_row.get("instruction_index"), allocation.get("instruction_index"),
                               constructor.get("instruction_index"), returned.get("instruction_index")]
                    numeric_indexes = [index for index in indexes if isinstance(index, int)]
                    ordered = (len(numeric_indexes) == 4
                               and numeric_indexes[0] < numeric_indexes[2] < numeric_indexes[3]
                               and numeric_indexes[1] < numeric_indexes[2])
                    if identity_path is not None and ordered:
                        candidates.append((field_row, allocation, constructor, returned,
                                           detail, identity_path))

    unique = []
    seen = set()
    for candidate in candidates:
        key = tuple((row.get("instruction_index"), row.get("instruction"))
                    for row in candidate[:4])
        if key not in seen:
            seen.add(key)
            unique.append(candidate)

    checks = {
        "exact_getter_method": bool(method_rows),
        "exact_instance_field_read": bool(field_rows),
        "field_receiver_is_getter_receiver": this_value is not None and any(
            _values(row, "uses") == [this_value] for row in field_rows),
        "fresh_copy_receiver": bool(unique),
        "resolved_exact_constructor_ir": bool(unique),
        "field_actual_maps_to_constructor_formal": bool(unique),
        "constructor_receiver_is_returned_object": bool(unique),
        "single_normal_object_return": len(returns) == 1,
        "unique_witness": len(unique) == 1,
    }
    missing = [name for name, complete in checks.items() if not complete]
    evidence_path: list[dict[str, Any]] = []
    provenance: dict[str, Any] = {
        "library_class": owner,
        "library_method": getter_method,
        "field": {"owner": owner, "name": expected_field},
        "normal_return_condition": "not established",
        "precision": (
            "structural SSA relation only; collection elements, order, indexes, equality, aliasing, "
            "and constructor transformation details are not inferred"
        ),
    }
    copy_content_effect: dict[str, Any] = {
        "status": "unknown",
        "reason": "no reviewed normal-return content summary for this constructor",
    }
    if len(unique) == 1:
        field_row, allocation, constructor, returned, detail, transfers = unique[0]
        uses = _values(constructor, "uses")
        formals = _values(detail, "parameter_value_numbers")
        source_index = uses.index(_values(field_row, "definitions")[0])
        evidence_path = [
            _step("exact_field_read", field_row),
            _step("fresh_allocation", allocation, allocated_type=_allocation_type(allocation)),
            _step("resolved_constructor_call", constructor,
                  declared_target=constructor.get("declared_target"),
                  receiver_actual=uses[0], receiver_formal=formals[0],
                  source_actual=uses[source_index], source_formal=formals[source_index],
                  source_argument_index=source_index),
            *(_step("identity_transfer", row) for row in transfers),
            _step("normal_return", returned),
        ]
        provenance["constructor"] = {
            "method": constructor.get("declared_target"),
            "context": detail.get("context"),
            "has_ir": detail.get("has_ir"),
        }
        api_class = _COPY_API_CONTRACTS.get(constructor.get("declared_target", ""))
        if api_class is not None:
            copy_content_effect = {
                "status": "reviewed_api_summary",
                "effect": "input contents may influence the newly allocated receiver state",
                "origin": "Java SE 11 API contract; not an analysis of the constructor body",
                "source": (
                    "https://docs.oracle.com/en/java/javase/11/docs/api/java.base/java/util/"
                    f"{api_class}.html"
                ),
                "condition": "normal return from the exact constructor invocation",
                "unknown": [
                    "concrete contents and indexes", "element identity and aliasing",
                    "input callbacks and other exceptions", "path feasibility",
                ],
                "null_input": "the API declares NullPointerException; this witness assumes normal return",
            }
        provenance["normal_return_condition"] = (
            "successful completion of the exact constructor invocation, followed by selection of "
            "the fresh allocation arm at the sole object return"
            if transfers else
            "successful completion of the exact constructor invocation before the sole object return"
        )
    status = "complete" if checks and all(checks.values()) else "incomplete"
    return {
        "status": status,
        "checks": checks,
        "path": evidence_path,
        "mechanism": (
            "exact WALA SSA field def -> resolved constructor source actual/formal; "
            "fresh allocation -> constructor receiver actual/formal -> sole normal return"
        ),
        "missing": missing,
        "provenance": provenance,
        "copy_content_effect": copy_content_effect,
    }


def _cfg_path(cfg_blocks: Sequence[Row], start: int, goal: int) -> list[int] | None:
    successors = {
        block.get("number"): block.get("normal_successor_blocks", [])
        for block in cfg_blocks if isinstance(block.get("number"), int)
    }
    queue: list[tuple[int, list[int]]] = [(start, [start])]
    seen = {start}
    while queue:
        block, path = queue.pop(0)
        if block == goal:
            return path
        for successor in successors.get(block, []):
            if isinstance(successor, int) and successor not in seen:
                seen.add(successor)
                queue.append((successor, [*path, successor]))
    return None


def extract_method_fact_copy_constructor_witness(
    method_fact: Row, expected_field: str,
) -> dict[str, Any]:
    """Extract a witness from the probe's complete, CFG-backed method fact."""
    method = method_fact.get("method")
    context = method_fact.get("context")
    parameters = _values(method_fact, "parameter_value_numbers")
    instructions = method_fact.get("instructions")
    phis = method_fact.get("phi_instructions")
    cfg_blocks = method_fact.get("cfg_blocks")
    if not isinstance(method, str):
        method = ""
    rows: list[Row] = []
    if parameters:
        rows.append({"kind": "PARAM_CALLEE", "method": method,
                     "statement": f"method fact receiver v{parameters[0]}"})
    for row in instructions if isinstance(instructions, list) else []:
        if isinstance(row, dict):
            rows.append({**row, "method": method, "context": context})
    for row in phis if isinstance(phis, list) else []:
        if isinstance(row, dict):
            rows.append({**row, "kind": "PHI", "method": method, "context": context})

    witness = extract_copy_constructor_witness(rows, method, expected_field)
    path_by_kind = {step["kind"]: step for step in witness["path"]
                    if isinstance(step, dict) and isinstance(step.get("kind"), str)}
    required = ["fresh_allocation", "exact_field_read",
                "resolved_constructor_call", "normal_return"]
    block_values = [path_by_kind.get(kind, {}).get("basic_block") for kind in required]
    normal_paths: dict[str, list[int] | None] = {}
    if (isinstance(cfg_blocks, list)
            and all(isinstance(block, int) for block in block_values)):
        allocation_block, field_block, constructor_block, return_block = cast(
            tuple[int, int, int, int], tuple(block_values)
        )
        normal_paths = {
            "allocation_to_constructor": _cfg_path(
                cfg_blocks, allocation_block, constructor_block),
            "field_read_to_constructor": _cfg_path(
                cfg_blocks, field_block, constructor_block),
            "constructor_to_return": _cfg_path(
                cfg_blocks, constructor_block, return_block),
        }
    witness["checks"]["exact_method_fact_parameters"] = bool(parameters)
    witness["checks"]["normal_cfg_paths"] = bool(
        normal_paths and all(normal_paths.values())
    )
    witness["missing"] = [name for name, complete in witness["checks"].items()
                          if not complete]
    witness["status"] = "complete" if not witness["missing"] else "incomplete"
    witness["provenance"]["method_fact"] = {
        "context": context,
        "parameter_value_numbers": parameters,
        "normal_cfg_paths": normal_paths,
        "exception_boundary": (
            "exceptional successors are excluded; the witness applies only along listed normal CFG paths"
        ),
    }
    return witness


def verify_copy_constructor_witness(witness: Mapping[str, Any]) -> bool:
    """Validate a witness without reconstructing or strengthening its claims."""
    checks = witness.get("checks")
    path = witness.get("path")
    provenance = witness.get("provenance")
    required_kinds = {"exact_field_read", "fresh_allocation",
                      "resolved_constructor_call", "normal_return"}
    return bool(
        witness.get("status") == "complete"
        and isinstance(checks, dict) and checks and all(value is True for value in checks.values())
        and isinstance(path, list) and required_kinds.issubset(
            {step.get("kind") for step in path if isinstance(step, dict)})
        and isinstance(provenance, dict)
        and isinstance(provenance.get("library_method"), str)
        and isinstance(provenance.get("field"), dict)
        and isinstance(provenance.get("normal_return_condition"), str)
        and isinstance(provenance.get("precision"), str)
        and witness.get("missing") == []
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("analysis", type=Path)
    parser.add_argument("getter_method")
    parser.add_argument("field")
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    analysis: dict[str, Any] = json.loads(args.analysis.read_text(encoding="utf-8"))
    reports: list[dict[str, Any]] = []
    matching_facts = [fact for fact in analysis.get("method_ssa_facts", [])
                      if fact.get("method") == args.getter_method]
    for fact in matching_facts:
        reports.append({
            "evidence_channel": "complete_method_ssa_fact",
            "witness": extract_method_fact_copy_constructor_witness(fact, args.field),
        })
    if not matching_facts:
        for index, observation in enumerate(analysis.get("observations", [])):
            witness = extract_copy_constructor_witness(
                observation.get("statements", []), args.getter_method, args.field)
            if witness["checks"]["exact_getter_method"]:
                reports.append({"evidence_channel": "criterion_slice",
                                "observation_index": index,
                                "criterion": observation.get("criterion"),
                                "witness": witness})
    payload = {
        "schema": "mockspec.jib-library-copy-evidence.v1",
        "source": {
            "analysis": str(args.analysis),
            "analysis_sha256": _sha256(args.analysis),
            "analysis_schema": analysis.get("schema"),
            "wala_version": analysis.get("wala_version"),
            "target_executed": analysis.get("target_executed"),
        },
        "getter_method": args.getter_method,
        "field": args.field,
        "reports": reports,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return 0 if reports and all(verify_copy_constructor_witness(item["witness"])
                                for item in reports) else 1


if __name__ == "__main__":
    raise SystemExit(_main())
