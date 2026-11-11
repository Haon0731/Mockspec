"""Map backend state-effect evidence to the shared per-observation result contract."""
from __future__ import annotations

import argparse
import ast
import csv
import hashlib
import json
from pathlib import Path
import sys
from typing import Any


HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from result_contract import RELATION_KINDS, RESULT_SCHEMA_VERSION, validate_result_contract  # noqa: E402


class StateMappingEvidenceError(ValueError):
    """Backend evidence cannot support the registered state-observation claim."""


def load_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def has_unique_call_at(source: Path, function_name: str, line: int) -> bool:
    tree = ast.parse(source.read_text(encoding="utf-8"))
    functions = [node for node in tree.body
                 if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                 and node.name == function_name]
    if len(functions) != 1:
        return False
    calls = [node for node in ast.walk(functions[0])
             if isinstance(node, ast.Call) and node.lineno == line]
    return len(calls) == 1


def parse_time_files(results: Path) -> tuple[dict[str, float], dict[str, Any]]:
    from observation_mapping import parse_time_files as parse

    return parse(results, "probe-")


def _relation(source_line: int, observation: dict[str, Any], provenance: str,
              modality: str, precision: str) -> dict[str, Any]:
    return {
        "kind": "state_flow",
        "modality": modality,
        "from": {"logical_path": "mechanism_probe.py", "start_line": source_line,
                 "end_line": source_line},
        "to": {"logical_path": "mechanism_probe.py", "start_line": observation["line"],
               "end_line": observation["line"], "field": observation["field"]},
        "provenance": provenance,
        "precision": precision,
        "assumptions": [],
        "observation_ids": [observation["observation_id"]],
    }


def build_state_study(results: Path, expectations_path: Path) -> dict[str, Any]:
    expectations = json.loads(expectations_path.read_text(encoding="utf-8"))
    prereg = json.loads((results / "state-pre-registration.json").read_text(encoding="utf-8"))
    source = HERE / "mechanism_probe.py"
    if prereg != {
        "schema": "mock-spec.python-state-observation-pre-registration/v1",
        "expectations_sha256": sha256(expectations_path),
        "source_sha256": sha256(source),
        "query_sha256": sha256(HERE / "state-boundary-flow.ql"),
        "visible_query_sha256": sha256(HERE / "visible-state-flow.ql"),
        "mapper_sha256": sha256(Path(__file__)),
    }:
        raise StateMappingEvidenceError("state pre-registration does not match current inputs")

    summary_path = results / "demand-mechanism-summary.json"
    if summary_path.is_file():
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        if summary.get("schema") != "mock-spec.python-demand-mechanism-summary/v1" \
                or summary.get("status") != "authenticated":
            raise StateMappingEvidenceError("demand mechanism summary is invalid")
        from observation_mapping import verify_demand_receipts
        try:
            verify_demand_receipts(results, summary, {
                "probe-visible-state-flow", "probe-unknown-effect-flow",
                "probe-state-boundary-flow", "probe-unknown-exception-state",
            })
        except ValueError as error:
            raise StateMappingEvidenceError(str(error)) from error
        boundary = summary.get("state_boundary_evidence", {})
    else:
        verdict = json.loads((results / "verdict.json").read_text(encoding="utf-8"))
        boundary = verdict.get("state_boundary_evidence", {})
    if boundary.get("status") != "passed" or not all(boundary.get("checks", {}).values()):
        raise StateMappingEvidenceError("state boundary mechanism gate did not pass")

    visible = load_csv(results / "probe-visible-state-flow.csv")
    unknown = load_csv(results / "probe-unknown-effect-flow.csv")
    boundary_rows = load_csv(results / "probe-state-boundary-flow.csv")
    exceptional = load_csv(results / "probe-unknown-exception-state.csv")
    observations = [{
        "observation_id": item["observation_id"],
        "location": {"source_id": "probe", "start_line": item["line"], "end_line": item["line"]},
        "kind": "field_read_observation",
        "origin": "explicit",
        "reason": f"registered {item['scope']} {item['field']}-field observation",
    } for item in expectations["observations"]]
    relations: list[dict[str, Any]] = []

    for item in expectations["observations"]:
        relation_name = item["relation"]
        function = item["function"]
        if relation_name == "visible_write_to_read":
            matches = [row for row in visible if row.get("relation") == relation_name
                       and row.get("functionName") == function
                       and row.get("fieldName") == item["field"]
                       and row.get("observedLine") == str(item["line"])]
            if len(matches) != 1 or not matches[0].get("writeLine", "").isdigit():
                raise StateMappingEvidenceError(f"visible write evidence is not unique for {function}")
            relations.append(_relation(
                int(matches[0]["writeLine"]), item,
                "CodeQL generic visible AttrWrite/AttrRead region flow",
                "analysis_may", "same local source and backend CFG reachability",
            ))
        elif relation_name in {"unknown_may_write_to_read", "return_alias_write_to_argument_read"}:
            matches = [row for row in unknown if row.get("relation") == relation_name
                       and row.get("functionName") == function
                       and row.get("fieldName") == item["field"]
                       and row.get("observedLine") == str(item["line"])]
            if len(matches) != 1 or not matches[0].get("callLine", "").isdigit():
                raise StateMappingEvidenceError(f"unknown argument evidence is not unique for {function}")
            relations.append(_relation(
                int(matches[0]["callLine"]), item, "CodeQL bounded opaque-call region model",
                "unknown_effect", f"{item['scope']} may-write; concrete value unknown",
            ))
        elif relation_name.startswith("unknown_"):
            matches = [row for row in boundary_rows if row.get("relation") == relation_name
                       and row.get("functionName") == function
                       and row.get("fieldName") == item["field"]
                       and row.get("observedLine") == str(item["line"])]
            if len(matches) != 1 or not matches[0].get("callLine", "").isdigit():
                raise StateMappingEvidenceError(f"state boundary evidence is not unique for {function}")
            relations.append(_relation(
                int(matches[0]["callLine"]), item, "CodeQL bounded opaque-call boundary path",
                "unknown_effect", f"{item['scope']} region path; concrete write unknown",
            ))
        else:
            expected_kind = "handler" if item["scope"] == "exception_handler" else "finally"
            matches = [row for row in exceptional if row.get("relation") == relation_name
                       and row.get("functionName") == function and row.get("caseKind") == expected_kind
                       and row.get("fieldName") == item["field"]
                       and row.get("observedLine") == str(item["line"])]
            call_lines = {row["callLine"] for row in matches}
            if (not matches or not all(row.get("callLine", "").isdigit() for row in matches)
                    or len(call_lines) != 1
                    or not has_unique_call_at(source, function, int(next(iter(call_lines))))):
                raise StateMappingEvidenceError(f"exception state evidence is not unique for {item['scope']}")
            relations.append(_relation(
                int(call_lines.pop()), item, "CodeQL exceptional CFG plus opaque state model",
                "unknown_effect", f"write-before-raise may reach {item['scope']} read",
            ))

    negative_functions = {item["function"] for item in expectations["exclude"]}
    backend_functions = {row.get("functionName") for row in boundary_rows}
    unexpected = sorted(negative_functions & backend_functions)
    if unexpected:
        raise StateMappingEvidenceError(f"negative state cases emitted relations: {unexpected}")

    unknown_cases = expectations.get("unknown_cases", [])
    for item in unknown_cases:
        matches = [row for row in boundary_rows
                   if row.get("relation") == item["relation"]
                   and row.get("caseKind") == "unknown"
                   and row.get("scopeKind") == item["scope"]
                   and row.get("functionName") == item["function"]]
        valid_matches = [row for row in matches
                         if row.get("fieldName")
                         and row.get("regionKey")
                         and all(row.get(key, "").isdigit()
                                 for key in ("callLine", "regionLine", "observedLine"))
                         and row.get("pathId") == (
                             f"{item['function']}:{row.get('callLine')}:{row.get('observedLine')}"
                         )
                         and int(row["callLine"]) <= int(row["regionLine"])
                         and int(row["regionLine"]) <= int(row["observedLine"])]
        if not valid_matches:
            raise StateMappingEvidenceError(
                f"unknown state boundary is missing for {item['function']}"
            )

    timings, peak = parse_time_files(results)
    source_bytes = source.stat().st_size
    contract = {
        "schema_version": RESULT_SCHEMA_VERSION,
        "identity": {
            "case_id": "python-state-mechanism-probe",
            "dataset": "analysis-mechanism-probe",
            "language": "python",
            "snapshot_id": sha256(source),
            "masking_contract": None,
            "masked_test_sha256": sha256(source),
            "target_selector": "mechanism_probe.py::bounded_state_mechanisms",
        },
        "source_scope": [{
            "source_id": "probe", "logical_path": "mechanism_probe.py", "sha256": sha256(source),
            "bytes": source_bytes, "selection_role": "mechanism_probe",
        }],
        "observations": observations,
        "parameterization": {"status": "not_applicable", "coverage": "registered probe functions",
                             "reason": "static mechanism fixture has no parameter rows"},
        "entry_point": {
            "test_selection": {"origin": "diagnostic_override", "status": "unique",
                               "selector": "mechanism_probe.py::bounded_state_mechanisms",
                               "evidence": [{"source_id": "probe", "functions": [
                                   item["function"] for item in expectations["observations"]
                               ]}]},
            "mut_binding": {"origin": "diagnostic_override", "status": "not_evaluated",
                            "candidates": [], "evidence": [], "assumptions": []},
        },
        "relations": relations,
        "relation_coverage": [{
            "kind": kind,
            "status": "verified_correct" if kind == "state_flow" else "not_evaluated",
        } for kind in sorted(RELATION_KINDS)],
        "analysis_boundaries": {
            "assumptions": [
                "opaque boundary calls are calls through the registered dependency parameter",
                "module-global may-write is conservative and does not assert a concrete write",
            ],
            "unknown_effects": [{
                "boundary_id": f"state-boundary:{item['observation_id']}",
                "effect": f"may_write_{item['field']}",
                "scope": item["scope"],
                "provenance": "backend state relation",
                "precision": "unresolved concrete value",
                "assumptions": [],
            } for item in expectations["observations"] if item["scope"] != "direct_visible_write"] + [{
                "boundary_id": f"state-boundary:{item['function']}",
                "effect": (
                    "unresolved_effect_after_global_rebind"
                    if item["scope"] == "module_global_rebind"
                    else "unresolved_alias_relation"
                ),
                "scope": item["scope"],
                "provenance": (
                    "backend global-binding boundary"
                    if item["scope"] == "module_global_rebind"
                    else "backend parameter-alias boundary"
                ),
                "precision": (
                    "post-rebind identity unresolved"
                    if item["scope"] == "module_global_rebind"
                    else "runtime parameter alias unresolved"
                ),
                "assumptions": [],
            } for item in unknown_cases],
            "unanalysed_regions": [
                {"scope": item, "reason": "outside bounded state model"}
                for item in expectations["unknown"]
            ] + [
                {"scope": item["scope"], "reason": item["reason"]}
                for item in unknown_cases
            ],
        },
        "completion": {"status": "partial", "stage": "state_observation_mapping",
                       "failure_categories": ["unsupported_state_regions"], "error": None},
        "semantic_validation": {
            "status": "verified_incomplete",
            "reviewer": "pre_registered_mechanism_oracle",
            "evidence": [{"observation_id": item["observation_id"],
                          "expectation": item["relation"]} for item in expectations["observations"]],
            "notes": "registered state paths and negatives verified; unsupported regions remain unknown",
        },
        "cost": {
            "timings_seconds": timings, "total_seconds": round(sum(timings.values()), 4),
            "total_scope": "probe database and queries; mapper serialization excluded",
            "peak_memory": peak,
            "cache_state": {"status": "unknown", "reason": "OS and CodeQL caches not isolated"},
            "input_size": {"files": 1, "bytes": source_bytes},
            "graph_size": {"nodes": None, "edges": None, "relation_count": len(relations),
                           "reason": "CodeQL graph size is not exported"},
            "unavailable_phases": [
                {"phase": "source_mapping", "reason": "mapper execution is not separately timed"},
                {"phase": "path_satisfiability", "reason": "not evaluated"},
            ],
        },
    }
    validate_result_contract(contract)
    return {
        "schema": "mock-spec.python-state-observation-study/v1",
        "case_result": contract,
        "pre_registered_expectations": expectations,
        "state_boundary_evidence": boundary,
        "negative_functions": sorted(negative_functions),
        "unknown_cases": unknown_cases,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--expectations", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    study = build_state_study(args.results, args.expectations)
    args.output.write_text(json.dumps(study, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
