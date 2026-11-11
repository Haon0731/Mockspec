"""Map bounded CodeQL evidence to one frozen case's external observation result."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path
import re
import sys
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from result_contract import RESULT_SCHEMA_VERSION, validate_result_contract


STUDY_SCHEMA = "mock-spec.python-observation-study/v1"


class MappingEvidenceError(ValueError):
    pass


def load_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise MappingEvidenceError(f"missing required evidence: {path.name}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise MappingEvidenceError(f"invalid object: {path.name}")
    return value


def load_csv(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        raise MappingEvidenceError(f"missing per-observation backend evidence: {path.name}")
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def location(path: str, line: int) -> dict[str, Any]:
    return {"logical_path": path, "start_line": line, "end_line": line}


def relation(kind: str, modality: str, source: dict, target: dict, provenance: str,
             precision: str, observation_ids: list[str] | None = None,
             assumptions: list[str] | None = None) -> dict[str, Any]:
    return {
        "kind": kind, "modality": modality, "from": source, "to": target,
        "provenance": provenance, "precision": precision,
        "assumptions": assumptions or [], "observation_ids": observation_ids or [],
    }


def parse_time_files(results: Path, prefix: str) -> tuple[dict[str, float], dict[str, Any]]:
    timings: dict[str, float] = {}
    peak_kib = 0
    for path in sorted(results.glob(f"{prefix}*.time")):
        text = path.read_text(encoding="utf-8", errors="replace")
        elapsed_line = next((line for line in text.splitlines()
                             if "Elapsed (wall clock) time" in line), None)
        rss = re.search(r"Maximum resident set size \(kbytes\):\s*(\d+)", text)
        if elapsed_line:
            value = elapsed_line.rsplit("):", 1)[-1].strip()
            parts = value.split(":")
            seconds = float(parts[-1])
            if len(parts) >= 2:
                seconds += 60 * int(parts[-2])
            if len(parts) >= 3:
                seconds += 3600 * int(parts[-3])
            timings[path.stem] = seconds
        if rss:
            peak_kib = max(peak_kib, int(rss.group(1)))
    return timings, {
        "status": "measured_per_process_max" if peak_kib else "unavailable",
        "bytes": peak_kib * 1024 if peak_kib else None,
        "reason": "maximum of per-process max RSS; not a simultaneous whole-run peak" if peak_kib
                  else "no /usr/bin/time evidence",
    }


def evaluate_pytest_model_evidence(results: Path) -> dict[str, Any]:
    rows = load_csv(results / "probe-pytest-exception-flow.csv")
    paths = {(row.get("sourceLine"), row.get("testCallLine"), row.get("withLine"),
              row.get("observationLine"), row.get("typeCompatibility"),
              row.get("suppressedContinuationLine")) for row in rows}
    required = {
        ("10", "19", "18", "22", "known_subtype_match", "20"),
        ("10", "34", "33", "35", "known_subtype_match", "35"),
        ("14", "37", "36", "38", "known_subtype_match", "38"),
    }
    forbidden = {
        ("10", "27", "26", "29"),
        ("10", "43", "42", "44"),
        ("10", "34", "33", "38"),
        ("14", "37", "36", "35"),
    }
    forbidden_with_lines = {
        42, 56, 63, 76, 82, 95, 101, 107, 114, 120, 126, 132, 139,
    }
    forbidden_hit = any(path[:4] in forbidden or int(path[2] or 0) in forbidden_with_lines
                        for path in paths)
    return {
        "status": "passed" if required <= paths and not forbidden_hit else "blocked",
        "required_paths_present": required <= paths,
        "negative_paths_excluded": not forbidden_hit,
        "path_count": len(paths),
    }


def load_demand_mechanism_summary(results: Path) -> dict[str, Any] | None:
    path = results / "demand-mechanism-summary.json"
    if not path.is_file():
        return None
    summary = load_json(path)
    if summary.get("schema") != "mock-spec.python-demand-mechanism-summary/v1" \
            or summary.get("status") != "authenticated" \
            or summary.get("pytest_capture_model", {}).get("status") != "passed" \
            or summary.get("model_evidence", {}).get("status") != "passed":
        raise MappingEvidenceError("demand mechanism summary is missing required verified scope")
    return summary


def verify_demand_receipts(results: Path, summary: dict[str, Any], stems: set[str]) -> None:
    declared = summary.get("query_receipts", {})
    for stem in stems:
        csv_path = results / f"{stem}.csv"
        receipt_path = results / f"{stem}.receipt.json"
        if not csv_path.is_file() or not receipt_path.is_file() or stem not in declared:
            raise MappingEvidenceError(f"missing current-run relation receipt: {stem}")
        receipt = load_json(receipt_path)
        if receipt.get("status") not in {"executed", "cache_hit"} \
                or receipt.get("result_sha256") != hashlib.sha256(csv_path.read_bytes()).hexdigest() \
                or not receipt.get("query_sha256") or not receipt.get("root_selector_sha256") \
                or declared[stem].get("receipt_sha256") != hashlib.sha256(
                    receipt_path.read_bytes()
                ).hexdigest() \
                or declared[stem].get("result_sha256") != receipt.get("result_sha256"):
            raise MappingEvidenceError(f"invalid current-run relation receipt: {stem}")


def build_study_result(results: Path, expectations_path: Path) -> dict[str, Any]:
    manifest = load_json(results / "input-manifest.json")
    expectations = load_json(expectations_path)
    pytest_model_path = Path(__file__).with_name("pytest_model_assumptions.json")
    pytest_model = load_json(pytest_model_path)
    pytest_source_receipt = load_json(results / "pytest-model-source-receipt.json")
    if pytest_source_receipt.get("schema") != "mock-spec.pytest-model-source-receipt/v1" \
            or pytest_source_receipt.get("status") != "passed" \
            or pytest_source_receipt.get("source_role") != "host_verified_reference" \
            or pytest_source_receipt.get("frozen_case_dependency_version") != "unknown" \
            or pytest_source_receipt.get("assumptions_sha256") != hashlib.sha256(
                pytest_model_path.read_bytes()
            ).hexdigest():
        raise MappingEvidenceError("pytest model source receipt is missing or invalid")
    pre_registration = load_json(results / "pre-registration.json")
    if pre_registration.get("schema") != "mock-spec.python-observation-pre-registration/v1" \
            or pre_registration.get("expectations_sha256") != hashlib.sha256(expectations_path.read_bytes()).hexdigest() \
            or pre_registration.get("pytest_model_sha256") != hashlib.sha256(pytest_model_path.read_bytes()).hexdigest() \
            or pre_registration.get("input_manifest_sha256") != hashlib.sha256(
                (results / "input-manifest.json").read_bytes()
            ).hexdigest():
        raise MappingEvidenceError("pre-registration identity mismatch")
    rows = load_csv(results / "flask-flask-observation-flow.csv")
    demand_summary = load_demand_mechanism_summary(results)
    if demand_summary is None:
        verdict = load_json(results / "verdict.json")
        if verdict.get("schema") != "mock-spec.issue24-verdict/v2" or not verdict.get("gate_passed") \
                or verdict.get("model_evidence", {}).get("status") != "passed":
            raise MappingEvidenceError("bounded mechanism verdict is missing or not passed")
        pytest_evidence = evaluate_pytest_model_evidence(results)
        verdict_schema = verdict["schema"]
    else:
        verify_demand_receipts(results, demand_summary, {
            "flask-flask-observation-flow", "flask-flask-abort",
            "flask-exceptions", "flask-pytest-exception-flow",
        })
        pytest_evidence = demand_summary["pytest_capture_model"]
        verdict_schema = demand_summary["schema"]
    if pytest_evidence["status"] != "passed":
        raise MappingEvidenceError("pytest capture model mechanism evidence is blocked")
    if expectations.get("case_id") != manifest.get("case_id") or \
            expectations.get("snapshot_id") != manifest.get("snapshot_id"):
        raise MappingEvidenceError("expectation identity mismatch")

    extraction = manifest.get("target_extraction")
    if not isinstance(extraction, dict) or not isinstance(extraction.get("criteria"), list):
        raise MappingEvidenceError("missing criteria-based observation extraction")
    scope = extraction.get("target_scope", {})
    scope_locations = scope.get("locations", [])
    if scope.get("status") != "located" or len(scope_locations) != 1:
        raise MappingEvidenceError("selected test scope is not uniquely located")
    scope_start = scope_locations[0]["start_line"]
    scope_end = scope_locations[0]["end_line"]
    test_file = next(file for file in manifest["files"] if file["source_id"] == "test")

    expected_by_line = {item["line"]: item for item in expectations["observations"]}
    observations = []
    aliases = []
    criterion_ids: dict[int, str] = {}
    for index, criterion in enumerate(extraction["criteria"]):
        line = criterion["line"]
        expected = expected_by_line.get(line)
        if expected is None:
            raise MappingEvidenceError(f"unregistered extracted observation at line {line}")
        criterion_id = f"criterion:{index}"
        criterion_ids[line] = criterion_id
        observations.append({
            "observation_id": criterion_id,
            "location": {"source_id": "test", "start_line": line, "end_line": line},
            "kind": criterion["kind"], "origin": "explicit", "reason": criterion["reason"],
        })
        aliases.append({"observation_id": criterion_id, "expectation_id": expected["observation_id"],
                        "location": location(expected["logical_path"], line)})
    if set(expected_by_line) != set(criterion_ids):
        raise MappingEvidenceError("expected observations differ from criteria extraction")

    observation_rows = [row for row in rows if row.get("relation") == "observation"
                        and row.get("sourcePath") == test_file["logical_path"]
                        and row.get("sourceLine", "").isdigit()
                        and scope_start <= int(row["sourceLine"]) <= scope_end]
    for expected in expectations["observations"]:
        matching = [row for row in observation_rows if int(row["sourceLine"]) == expected["line"]]
        if expected["kind"] == "assertion":
            matching = [row for row in matching if row.get("guardLine") == str(expected["guard_line"])
                        and row.get("branch") == expected["guard_branch"]]
        if not matching:
            raise MappingEvidenceError(f"observation backend row missing at line {expected['line']}")

    product_paths = {file["logical_path"] for file in manifest["files"] if file["source_id"] != "test"}
    resolved = [row for row in rows if row.get("relation") == "resolved_test_product_call"
                and row.get("sourcePath") == test_file["logical_path"]
                and row.get("sourceLine", "").isdigit()
                and scope_start <= int(row["sourceLine"]) <= scope_end
                and row.get("targetPath") in product_paths]
    endpoints = {(row["sourcePath"], row["sourceLine"], row["targetPath"], row["targetLine"], row["targetEndLine"])
                 for row in resolved}
    if len(endpoints) != 1:
        raise MappingEvidenceError("resolved test-to-product call is not unique")
    source_path, source_line_s, target_path, target_line_s, target_end_line_s = next(iter(endpoints))
    source_line, target_line, target_end_line = int(source_line_s), int(target_line_s), int(target_end_line_s)
    endpoint_prefix = (source_path, source_line_s, target_path, target_line_s)

    relations = [relation(
        "call_binding", "certain", location(source_path, source_line),
        location(target_path, target_line), "CodeQL FunctionObject.getACall",
        "unique selected-scope test-to-visible-product endpoint",
    )]
    actual_formal = [row for row in rows if row.get("relation") == "actual_to_formal"
                     and (row.get("sourcePath"), row.get("sourceLine"), row.get("targetPath"),
                          row.get("targetLine")) == endpoint_prefix]
    if not actual_formal:
        raise MappingEvidenceError("actual-to-formal backend evidence is missing")
    for row in actual_formal:
        relations.append(relation(
            "value_flow", "analysis_may", location(source_path, source_line),
            {**location(target_path, target_line), "parameter_index": int(row["argumentIndex"])},
            "CodeQL Global data flow actual-to-formal", "matched resolved call context",
        ))

    for expected in expectations["observations"]:
        if expected["kind"] != "assertion":
            continue
        observation_id = criterion_ids[expected["line"]]
        matched_row = next(row for row in observation_rows
                           if int(row["sourceLine"]) == expected["line"]
                           and row.get("guardLine") == str(expected["guard_line"])
                           and row.get("branch") == expected["guard_branch"])
        relations.append(relation(
            "control_dependence", "analysis_may",
            location(matched_row["sourcePath"], int(matched_row["guardLine"])),
            location(matched_row["sourcePath"], int(matched_row["sourceLine"])),
            "CodeQL ConditionBlock.controls", f"backend control region: {matched_row['branch']} branch",
            [observation_id],
        ))

    flask_rows = load_csv(results / "flask-flask-abort.csv")
    for row in flask_rows:
        if row.get("relation") == "aborter_normal_path_reaches_fallback" \
                and row.get("col4") == target_path and row.get("col6") == target_path \
                and target_line <= int(row["col5"]) <= target_end_line \
                and target_line <= int(row["col7"]) <= target_end_line:
            relations.append(relation(
                "normal_return_dependence", "analysis_may",
                location(row["col4"], int(row["col5"])), location(row["col6"], int(row["col7"])),
                "CodeQL normal CFG reachability", "product-internal normal continuation",
            ))
    if not any(row["kind"] == "normal_return_dependence" for row in relations):
        raise MappingEvidenceError("bound product normal-continuation evidence is missing")
    exception_rows = load_csv(results / "flask-exceptions.csv")
    for row in exception_rows:
        if row.get("relation") == "exceptional_exit" and row.get("col3") == target_path \
                and target_line <= int(row["col4"]) <= target_end_line:
            relations.append(relation(
                "exception_dependence", "analysis_may",
                location(row["col3"], int(row["col4"])),
                {"scope": "product_function_exceptional_exit"},
                "CodeQL RaisingNode exceptional exit", "product-internal; not linked to pytest observation",
            ))

    pytest_rows = load_csv(results / "flask-pytest-exception-flow.csv")
    accepted_pytest_rows = [row for row in pytest_rows
                            if row.get("relation") == "captured_exception_may_reach_membership_observation"
                            and row.get("calleePath") == target_path
                            and row.get("calleeLine") == str(target_line)
                            and row.get("testCallPath") == source_path
                            and row.get("testCallLine") == str(source_line)
                            and row.get("withLine") == str(expected_by_line[102]["line"])
                            and row.get("observationLine", "").isdigit()
                            and int(row["observationLine"]) in criterion_ids
                            and row.get("sourceLine", "").isdigit()
                            and target_line <= int(row["sourceLine"]) <= target_end_line
                            and row.get("suppressedContinuationLine", "").isdigit()
                            and int(row["withLine"]) < int(row["suppressedContinuationLine"])
                            <= int(row["observationLine"])
                            and row.get("typeCompatibility") in {"unknown_may_match", "known_subtype_match"}]
    for observation_line in (120, 126):
        if not any(int(row["observationLine"]) == observation_line for row in accepted_pytest_rows):
            raise MappingEvidenceError(f"pytest capture path missing for observation {observation_line}")
    for row in accepted_pytest_rows:
        observation_id = criterion_ids[int(row["observationLine"])]
        relations.append(relation(
            "exception_dependence", "analysis_may",
            location(row["sourcePath"], int(row["sourceLine"])),
            location(row["testCallPath"], int(row["observationLine"])),
            "CodeQL pytest.raises exception capture model",
            f"same with/capture SSA; type={row['typeCompatibility']}; .value to str membership",
            [criterion_ids[102], observation_id],
            [
                "host pytest model version and source digests verified before analysis",
                "frozen case pytest dependency version is unknown",
            ],
        ))
    unknown_mappings: list[dict[str, Any]] = []
    unknown_value_constraints = [{
        "observation_id": criterion_ids[item["line"]], "status": "unknown_value",
        "reason": "influence recovered, but exact exception type/text and code-to-message construction are not modeled",
    } for item in expectations["observations"]]

    normal_relation = next(row for row in flask_rows
                           if row.get("relation") == "aborter_normal_path_reaches_fallback"
                           and row.get("col4") == target_path and row.get("col6") == target_path
                           and target_line <= int(row["col5"]) <= target_end_line
                           and target_line <= int(row["col7"]) <= target_end_line)
    expected_exception_lines = {int(normal_relation["col5"]), int(normal_relation["col7"])}
    actual_exception_lines = {
        relation_row["from"]["start_line"] for relation_row in relations
        if relation_row["kind"] == "exception_dependence"
    }
    if not expected_exception_lines <= actual_exception_lines:
        raise MappingEvidenceError("bound product exception evidence is missing")
    if demand_summary is None:
        effect_rows = load_csv(results / "probe-unknown-effect-flow.csv")
        exception_effect_rows = load_csv(results / "probe-unknown-exception-state.csv")
        positive_relations = sorted({row["relation"] for row in effect_rows
                                     if row.get("caseKind") in {"positive", "kill", "normal", "exception"}})
        negative_relations = sorted({row["relation"] for row in effect_rows
                                     if row.get("caseKind") == "negative"})
        exception_relations = sorted({row["relation"] for row in exception_effect_rows})
    else:
        positive_relations = demand_summary["relation_families"]["positive"]
        negative_relations = demand_summary["relation_families"]["negative"]
        exception_relations = demand_summary["relation_families"]["exception"]
    mechanism = {
        "status": "verified_bounded",
        "positive_relations": positive_relations,
        "negative_relations": negative_relations,
        "exception_relations": exception_relations,
        "scope": "mechanism probe only; not automatically attached to Flask observations",
        "verdict_schema": verdict_schema,
        "pytest_capture_model": pytest_evidence,
    }
    timings, peak_memory = parse_time_files(results, "flask-")
    mechanism_timings, mechanism_peak = parse_time_files(results, "probe-")
    expected_flask_timings = {
        "flask-db", "flask-control-flow", "flask-state-flow", "flask-exceptions",
        "flask-flask-abort", "flask-flask-observation-flow", "flask-pytest-exception-flow",
    }
    missing_flask_timings = sorted(expected_flask_timings - set(timings))
    expected_mechanism_timings = {
        "probe-db", "probe-local-flow", "probe-global-flow", "probe-control-flow",
        "probe-state-flow", "probe-effects", "probe-exceptions", "probe-reachability",
        "probe-unknown-effect-flow", "probe-unknown-exception-state", "probe-pytest-exception-flow",
    }
    missing_mechanism_timings = sorted(expected_mechanism_timings - set(mechanism_timings))
    mechanism["cost"] = {
        "timings_seconds": mechanism_timings,
        "total_seconds": round(sum(mechanism_timings.values()), 4),
        "peak_memory": mechanism_peak,
        "scope": "mechanism database and mechanism queries only",
        "timing_coverage": "complete" if not missing_mechanism_timings else "partial",
        "missing_timings": missing_mechanism_timings,
    }
    source_scope = [{
        "source_id": file["source_id"], "logical_path": file["logical_path"],
        "sha256": file["sha256"], "bytes": file["bytes"],
        "selection_role": "masked_test" if file["source_id"] == "test" else "candidate_product",
    } for file in manifest["files"]]
    coverage = {
        "call_binding": "verified_correct" if resolved else "not_evaluated",
        "value_flow": "verified_incomplete" if actual_formal else "not_evaluated",
        "control_dependence": "verified_incomplete",
        "normal_return_dependence": "verified_correct" if any(row["kind"] == "normal_return_dependence" for row in relations) else "not_evaluated",
        "exception_dependence": "verified_incomplete", "state_flow": "not_evaluated",
        "function_origin": "not_evaluated",
    }
    case_result = {
        "schema_version": RESULT_SCHEMA_VERSION,
        "identity": {key: manifest[key] for key in ("case_id", "dataset", "snapshot_id")} | {
            "language": "python", "masking_contract": manifest["masking_contract"],
            "masked_test_sha256": test_file["sha256"], "target_selector": manifest["target_selector"],
        },
        "source_scope": source_scope, "observations": observations,
        "parameterization": {"status": "not_expanded", "coverage": "selected_test_definition",
                             "reason": "parameter rows are visible but not enumerated as path-feasible executions"},
        "entry_point": {
            "test_selection": {"origin": "task_provided", "status": "unique",
                               "selector": manifest["target_selector"], "evidence": scope_locations},
            "mut_binding": {"origin": "automatic", "status": "unique", "candidates": [],
                            "evidence": [location(target_path, target_line)], "assumptions": []},
        },
        "relations": relations,
        "relation_coverage": [{"kind": kind, "status": coverage[kind]} for kind in sorted(coverage)],
        "analysis_boundaries": {
            "assumptions": ["selected visible Flask source scope only"],
            "unknown_effects": [{
                "boundary_id": f"product-call:{line}", "effect": "may_write_or_raise",
                "scope": scope_name, "provenance": "bounded unknown-effect model",
                "precision": "unresolved", "assumptions": [],
            } for line in sorted(actual_exception_lines)
              for scope_name in ("receiver", "direct_arguments", "return_alias",
                                 "global_or_escaped_state", "transitive_heap")],
            "unanalysed_regions": ([{"scope": item, "reason": "outside bounded model"}
                                    for item in expectations["unknown"]] + [{
                "scope": "observation_exact_value", "observation_id": row["observation_id"],
                "reason": row["reason"],
            } for row in unknown_value_constraints]),
        },
        "completion": {"status": "partial", "stage": "observation_mapping",
                       "failure_categories": ["observation_exact_value_unknown"], "error": None},
        "semantic_validation": {"status": "verified_incomplete", "reviewer": "pre_registered_source_oracle",
                                "evidence": aliases, "notes": "bounded exception influence recovered; exact observed values remain unknown"},
        "cost": {
            "timings_seconds": timings, "total_seconds": round(sum(timings.values()), 4),
            "total_scope": "Flask database creation and Flask-prefixed queries only; mechanism cost is separate",
            "timing_coverage": "complete" if not missing_flask_timings else "partial",
            "peak_memory": peak_memory, "cache_state": {"status": "unknown", "reason": "OS and CodeQL compilation caches not isolated"},
            "input_size": {"files": len(source_scope), "bytes": sum(row["bytes"] for row in source_scope)},
            "graph_size": {"nodes": None, "edges": None, "relation_count": len(relations),
                           "reason": "backend graph size is not exported by these queries"},
            "unavailable_phases": [
                {"phase": "input_admission", "reason": "adapter preparation is not separately timed"},
                {"phase": "criterion_extraction", "reason": "included in adapter process, not separately timed"},
                {"phase": "observation_mapping", "reason": "mapper and serialization are not separately timed"},
                {"phase": "path_feasibility", "reason": "not evaluated"},
                {"phase": "star_args_and_kwargs", "reason": "only positional argument 0 has matched actual/formal evidence"},
            ] + [{"phase": phase, "reason": "expected /usr/bin/time evidence is missing"}
                 for phase in missing_flask_timings],
        },
    }
    validate_result_contract(case_result)
    return {
        "schema": STUDY_SCHEMA, "case_result": case_result,
        "observation_aliases": aliases, "unknown_observation_mappings": unknown_mappings,
        "unknown_value_constraints": unknown_value_constraints,
        "mechanism_validation": mechanism, "pre_registered_expectations": expectations,
        "pytest_model": pytest_model, "pytest_model_source_receipt": pytest_source_receipt,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--expectations", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = build_study_result(args.results, args.expectations)
    with args.output.open("x", encoding="utf-8") as handle:
        json.dump(result, handle, ensure_ascii=False, indent=2, allow_nan=False)
        handle.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
