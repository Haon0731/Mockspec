"""Strict validator for the isolated Scenario Contract research draft.

The draft is intentionally independent from ``mock_spec.scenario``.  SMT-LIB
is stored as opaque metadata here; parsing and solving belong to the PoC host.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
from typing import Any, Iterable, Mapping

from inputs import ReviewedInput, load_java, load_python
from mock_spec.validation import decode_json


SCHEMA_VERSION = "mock-spec.scenario-contract-research/v0"
_ID = re.compile(r"[A-Za-z][A-Za-z0-9_.:-]*\Z")
class DraftValidationError(ValueError):
    pass


def _object(value: object, path: str) -> Mapping[str, Any]:
    if type(value) is not dict:
        raise DraftValidationError(f"{path} must be an object")
    return value


def _array(value: object, path: str) -> list[Any]:
    if type(value) is not list:
        raise DraftValidationError(f"{path} must be an array")
    return value


def _string(value: object, path: str, *, allow_empty: bool = False) -> str:
    if type(value) is not str or (not allow_empty and not value.strip()):
        raise DraftValidationError(f"{path} must be a non-empty string")
    return value


def _boolean(value: object, path: str) -> bool:
    if type(value) is not bool:
        raise DraftValidationError(f"{path} must be a boolean")
    return value


def _strict(value: object, fields: Iterable[str], path: str) -> Mapping[str, Any]:
    obj = _object(value, path)
    expected = set(fields)
    unknown = sorted(set(obj) - expected)
    missing = sorted(expected - set(obj))
    if unknown:
        raise DraftValidationError(f"{path} has unknown fields: {', '.join(unknown)}")
    if missing:
        raise DraftValidationError(f"{path} is missing fields: {', '.join(missing)}")
    return obj


def _id(value: object, path: str) -> str:
    parsed = _string(value, path)
    if _ID.fullmatch(parsed) is None:
        raise DraftValidationError(f"{path} has invalid identifier syntax")
    return parsed


def _strings(value: object, path: str) -> list[str]:
    return [_string(item, f"{path}[{index}]") for index, item in enumerate(_array(value, path))]


def _records(value: object, fields: Iterable[str], path: str) -> list[Mapping[str, Any]]:
    return [
        _strict(item, fields, f"{path}[{index}]")
        for index, item in enumerate(_array(value, path))
    ]


def _index(records: list[Mapping[str, Any]], path: str) -> dict[str, Mapping[str, Any]]:
    result: dict[str, Mapping[str, Any]] = {}
    for index, record in enumerate(records):
        key = _id(record["id"], f"{path}[{index}].id")
        if key in result:
            raise DraftValidationError(f"duplicate {path} id: {key}")
        result[key] = record
    return result


def _refs(values: object, known: set[str], path: str) -> list[str]:
    refs = _strings(values, path)
    missing = sorted(set(refs) - known)
    if missing:
        raise DraftValidationError(f"{path} has unknown references: {', '.join(missing)}")
    return refs


def _condition(
    value: object,
    *,
    formulas: set[str],
    unknowns: set[str],
    path: str,
) -> None:
    condition = _strict(value, {"status", "formula_ref", "unknown_ref"}, path)
    status = _string(condition["status"], f"{path}.status")
    formula_ref = condition["formula_ref"]
    unknown_ref = condition["unknown_ref"]
    if status == "explicit_unconditional":
        if formula_ref != "true" or unknown_ref is not None:
            raise DraftValidationError(
                f"{path} explicit_unconditional must use formula_ref='true' and unknown_ref=null"
            )
    elif status == "scoped":
        if type(formula_ref) is not str or formula_ref not in formulas or unknown_ref is not None:
            raise DraftValidationError(
                f"{path} scoped condition must reference one formula and no unknown"
            )
    elif status == "unknown":
        if formula_ref is not None or type(unknown_ref) is not str or unknown_ref not in unknowns:
            raise DraftValidationError(
                f"{path} unknown condition must reference one known unknown and no formula"
            )
    else:
        raise DraftValidationError(
            f"{path}.status must be explicit_unconditional, scoped, or unknown"
        )


def validate_contract(
    contract: object,
    reviewed: ReviewedInput | None = None,
    *,
    expected_oracle_ids: Iterable[str] | None = None,
    expected_parameter_rows: Iterable[str] | None = None,
) -> None:
    root = _strict(
        contract,
        {
            "schema_version", "identity", "coverage", "sources", "evidence",
            "oracles", "symbols", "bindings", "formulas", "scenarios",
            "response_rules", "runtime_expectations", "verification_candidates",
            "assumptions", "unknowns", "checks", "consumption",
        },
        "contract",
    )
    if root["schema_version"] != SCHEMA_VERSION:
        raise DraftValidationError("unsupported schema_version")

    identity = _strict(
        root["identity"],
        {"case_id", "language", "authoring_mode", "input_policy_id", "source_hash_algorithm"},
        "identity",
    )
    language = _string(identity["language"], "identity.language")
    if language not in {"java", "python"}:
        raise DraftValidationError("identity.language must be java or python")
    if identity["authoring_mode"] != "handcrafted_from_visible_input":
        raise DraftValidationError("identity.authoring_mode must disclose handcrafted origin")
    if identity["source_hash_algorithm"] != "sha256-utf8-visible-slice":
        raise DraftValidationError("unexpected source hash algorithm")

    coverage = _strict(
        root["coverage"],
        {"scope", "formalized_oracle_refs", "unformalized_oracle_refs", "whole_test_verified"},
        "coverage",
    )
    _string(coverage["scope"], "coverage.scope")
    if _boolean(coverage["whole_test_verified"], "coverage.whole_test_verified"):
        raise DraftValidationError("research examples must not claim whole_test_verified")

    source_records = _records(
        root["sources"],
        {"id", "logical_path", "start_line", "end_line", "sha256"},
        "sources",
    )
    sources = _index(source_records, "sources")
    for index, source in enumerate(source_records):
        _string(source["logical_path"], f"sources[{index}].logical_path")
        if type(source["start_line"]) is not int or type(source["end_line"]) is not int:
            raise DraftValidationError(f"sources[{index}] line bounds must be integers")
        if source["start_line"] < 1 or source["end_line"] < source["start_line"]:
            raise DraftValidationError(f"sources[{index}] line bounds are invalid")
        digest = _string(source["sha256"], f"sources[{index}].sha256")
        if re.fullmatch(r"[0-9a-f]{64}", digest) is None:
            raise DraftValidationError(f"sources[{index}].sha256 must be lowercase SHA-256")

    evidence_records = _records(
        root["evidence"],
        {"id", "source_id", "start_line", "end_line", "quote", "supports", "semantic_limit"},
        "evidence",
    )
    evidence = _index(evidence_records, "evidence")
    for index, item in enumerate(evidence_records):
        source_id = _id(item["source_id"], f"evidence[{index}].source_id")
        if source_id not in sources:
            raise DraftValidationError(f"evidence[{index}].source_id is unknown")
        if type(item["start_line"]) is not int or type(item["end_line"]) is not int:
            raise DraftValidationError(f"evidence[{index}] line bounds must be integers")
        if item["start_line"] < 1 or item["end_line"] < item["start_line"]:
            raise DraftValidationError(f"evidence[{index}] line bounds are invalid")
        _string(item["quote"], f"evidence[{index}].quote")
        _string(item["supports"], f"evidence[{index}].supports")
        _string(item["semantic_limit"], f"evidence[{index}].semantic_limit")

    oracle_records = _records(
        root["oracles"],
        {"id", "parameter_row", "observation", "expected", "evidence_refs", "formalization_status"},
        "oracles",
    )
    oracles = _index(oracle_records, "oracles")
    for index, item in enumerate(oracle_records):
        if item["parameter_row"] is not None:
            _string(item["parameter_row"], f"oracles[{index}].parameter_row")
        _string(item["observation"], f"oracles[{index}].observation")
        _string(item["expected"], f"oracles[{index}].expected")
        _refs(item["evidence_refs"], set(evidence), f"oracles[{index}].evidence_refs")
        if item["formalization_status"] not in {"formalized", "not_formalized"}:
            raise DraftValidationError(f"oracles[{index}].formalization_status is invalid")
    _refs(coverage["formalized_oracle_refs"], set(oracles), "coverage.formalized_oracle_refs")
    _refs(coverage["unformalized_oracle_refs"], set(oracles), "coverage.unformalized_oracle_refs")
    covered_oracles = set(coverage["formalized_oracle_refs"]) | set(coverage["unformalized_oracle_refs"])
    if covered_oracles != set(oracles):
        raise DraftValidationError("coverage must classify every oracle exactly as formalized or unformalized")
    if set(coverage["formalized_oracle_refs"]) & set(coverage["unformalized_oracle_refs"]):
        raise DraftValidationError("an oracle cannot be both formalized and unformalized")
    if expected_oracle_ids is not None and set(expected_oracle_ids) != set(oracles):
        raise DraftValidationError("oracle inventory differs from the host-provided expected inventory")
    actual_rows = {
        item["parameter_row"] for item in oracle_records if item["parameter_row"] is not None
    }
    if expected_parameter_rows is not None and set(expected_parameter_rows) != actual_rows:
        raise DraftValidationError("parameter-row inventory differs from the host-provided expected inventory")

    symbol_records = _records(
        root["symbols"],
        {"id", "sort", "role", "controllable", "binding_ref", "model_symbol", "evidence_refs"},
        "symbols",
    )
    symbols = _index(symbol_records, "symbols")
    binding_records = _records(
        root["bindings"],
        {"id", "receiver", "method", "arguments", "call_ordinal", "source_id", "start_line", "end_line", "resolution_status"},
        "bindings",
    )
    bindings = _index(binding_records, "bindings")
    for index, item in enumerate(binding_records):
        _string(item["receiver"], f"bindings[{index}].receiver")
        _string(item["method"], f"bindings[{index}].method")
        _strings(item["arguments"], f"bindings[{index}].arguments")
        if item["call_ordinal"] is not None and type(item["call_ordinal"]) is not int:
            raise DraftValidationError(f"bindings[{index}].call_ordinal must be integer or null")
        if item["source_id"] not in sources:
            raise DraftValidationError(f"bindings[{index}].source_id is unknown")
        if (
            type(item["start_line"]) is not int
            or type(item["end_line"]) is not int
            or item["start_line"] < 1
            or item["end_line"] < item["start_line"]
        ):
            raise DraftValidationError(f"bindings[{index}] line bounds are invalid")
        if item["resolution_status"] not in {"resolved", "partial", "unresolved"}:
            raise DraftValidationError(f"bindings[{index}].resolution_status is invalid")
    for index, item in enumerate(symbol_records):
        _string(item["sort"], f"symbols[{index}].sort")
        _string(item["role"], f"symbols[{index}].role")
        _boolean(item["controllable"], f"symbols[{index}].controllable")
        if item["binding_ref"] is not None and item["binding_ref"] not in bindings:
            raise DraftValidationError(f"symbols[{index}].binding_ref is unknown")
        if item["model_symbol"] is not None:
            _string(item["model_symbol"], f"symbols[{index}].model_symbol")
        _refs(item["evidence_refs"], set(evidence), f"symbols[{index}].evidence_refs")

    formula_records = _records(
        root["formulas"],
        {"id", "format", "text", "role", "symbol_refs", "evidence_refs", "support_status"},
        "formulas",
    )
    formulas = _index(formula_records, "formulas")
    for index, item in enumerate(formula_records):
        if item["format"] != "smtlib2":
            raise DraftValidationError(f"formulas[{index}].format must be smtlib2")
        _string(item["text"], f"formulas[{index}].text")
        _string(item["role"], f"formulas[{index}].role")
        _refs(item["symbol_refs"], set(symbols), f"formulas[{index}].symbol_refs")
        _refs(item["evidence_refs"], set(evidence), f"formulas[{index}].evidence_refs")
        if item["support_status"] not in {"modeled", "descriptive_unsupported"}:
            raise DraftValidationError(f"formulas[{index}].support_status is invalid")

    assumption_records = _records(
        root["assumptions"],
        {"id", "statement", "scope", "evidence_refs", "blocks_semantic_proof"},
        "assumptions",
    )
    assumptions = _index(assumption_records, "assumptions")
    unknown_records = _records(
        root["unknowns"],
        {"id", "statement", "impact", "evidence_refs", "blocks_consumption"},
        "unknowns",
    )
    unknowns = _index(unknown_records, "unknowns")
    for path, records in (("assumptions", assumption_records), ("unknowns", unknown_records)):
        for index, item in enumerate(records):
            _string(item["statement"], f"{path}[{index}].statement")
            _refs(item["evidence_refs"], set(evidence), f"{path}[{index}].evidence_refs")
            if path == "assumptions":
                _string(item["scope"], f"{path}[{index}].scope")
                _boolean(item["blocks_semantic_proof"], f"{path}[{index}].blocks_semantic_proof")
            else:
                _string(item["impact"], f"{path}[{index}].impact")
                _boolean(item["blocks_consumption"], f"{path}[{index}].blocks_consumption")

    scenario_records = _records(
        root["scenarios"],
        {"id", "title", "alternative_group", "condition", "oracle_refs", "assumption_refs", "unknown_refs", "status"},
        "scenarios",
    )
    scenarios = _index(scenario_records, "scenarios")
    for index, item in enumerate(scenario_records):
        _string(item["title"], f"scenarios[{index}].title")
        _string(item["alternative_group"], f"scenarios[{index}].alternative_group")
        _condition(item["condition"], formulas=set(formulas), unknowns=set(unknowns), path=f"scenarios[{index}].condition")
        _refs(item["oracle_refs"], set(oracles), f"scenarios[{index}].oracle_refs")
        _refs(item["assumption_refs"], set(assumptions), f"scenarios[{index}].assumption_refs")
        _refs(item["unknown_refs"], set(unknowns), f"scenarios[{index}].unknown_refs")
        if item["status"] not in {"candidate", "partial", "blocked"}:
            raise DraftValidationError(f"scenarios[{index}].status is invalid")

    rule_records = _records(
        root["response_rules"],
        {"id", "scenario_refs", "condition", "call_binding", "reaction", "logical_role", "oracle_refs", "formula_refs", "evidence_refs", "check_refs", "whole_test_verified"},
        "response_rules",
    )
    rules = _index(rule_records, "response_rules")
    for index, item in enumerate(rule_records):
        _refs(item["scenario_refs"], set(scenarios), f"response_rules[{index}].scenario_refs")
        _condition(item["condition"], formulas=set(formulas), unknowns=set(unknowns), path=f"response_rules[{index}].condition")
        if item["call_binding"] not in bindings:
            raise DraftValidationError(f"response_rules[{index}].call_binding is unknown")
        reaction = _strict(item["reaction"], {"kind", "value", "constraint_refs"}, f"response_rules[{index}].reaction")
        if reaction["kind"] not in {"return", "throw", "sequence"}:
            raise DraftValidationError(f"response_rules[{index}].reaction.kind is invalid")
        if reaction["value"] is not None:
            _string(reaction["value"], f"response_rules[{index}].reaction.value")
        _refs(reaction["constraint_refs"], set(formulas), f"response_rules[{index}].reaction.constraint_refs")
        if item["logical_role"] not in {"scenario_local_obligation", "implementation_choice"}:
            raise DraftValidationError(f"response_rules[{index}].logical_role is invalid")
        _refs(item["oracle_refs"], set(oracles), f"response_rules[{index}].oracle_refs")
        _refs(item["formula_refs"], set(formulas), f"response_rules[{index}].formula_refs")
        _refs(item["evidence_refs"], set(evidence), f"response_rules[{index}].evidence_refs")
        _strings(item["check_refs"], f"response_rules[{index}].check_refs")
        if _boolean(item["whole_test_verified"], f"response_rules[{index}].whole_test_verified"):
            raise DraftValidationError("response rule cannot claim whole-test verification")
    if expected_parameter_rows is not None:
        for row in set(expected_parameter_rows):
            row_oracles = {
                item["id"] for item in oracle_records if item["parameter_row"] == row
            }
            for oracle_id in row_oracles:
                applicable_scenarios = {
                    item["id"]
                    for item in scenario_records
                    if oracle_id in item["oracle_refs"]
                }
                has_rule = any(
                    oracle_id in rule["oracle_refs"]
                    and bool(applicable_scenarios & set(rule["scenario_refs"]))
                    for rule in rule_records
                )
                if not applicable_scenarios or not has_rule:
                    raise DraftValidationError(
                        f"parameter row {row} oracle {oracle_id} has no applicable scenario response rule"
                    )

    expectation_records = _records(
        root["runtime_expectations"],
        {"id", "scenario_refs", "condition", "binding_ref", "expectation", "evidence_refs", "logical_role"},
        "runtime_expectations",
    )
    expectations = _index(expectation_records, "runtime_expectations")
    for index, item in enumerate(expectation_records):
        _refs(item["scenario_refs"], set(scenarios), f"runtime_expectations[{index}].scenario_refs")
        _condition(item["condition"], formulas=set(formulas), unknowns=set(unknowns), path=f"runtime_expectations[{index}].condition")
        if item["binding_ref"] not in bindings:
            raise DraftValidationError(f"runtime_expectations[{index}].binding_ref is unknown")
        _string(item["expectation"], f"runtime_expectations[{index}].expectation")
        _refs(item["evidence_refs"], set(evidence), f"runtime_expectations[{index}].evidence_refs")
        if item["logical_role"] != "runtime_expectation":
            raise DraftValidationError(f"runtime_expectations[{index}] has invalid logical_role")

    candidate_records = _records(
        root["verification_candidates"],
        {"id", "scenario_refs", "condition", "expectation_ref", "proposal", "behavior_protected", "evidence_refs", "status", "whole_test_verified"},
        "verification_candidates",
    )
    candidates = _index(candidate_records, "verification_candidates")
    for index, item in enumerate(candidate_records):
        _refs(item["scenario_refs"], set(scenarios), f"verification_candidates[{index}].scenario_refs")
        _condition(item["condition"], formulas=set(formulas), unknowns=set(unknowns), path=f"verification_candidates[{index}].condition")
        if item["expectation_ref"] not in expectations:
            raise DraftValidationError(f"verification_candidates[{index}].expectation_ref is unknown")
        _string(item["proposal"], f"verification_candidates[{index}].proposal")
        _string(item["behavior_protected"], f"verification_candidates[{index}].behavior_protected")
        _refs(item["evidence_refs"], set(evidence), f"verification_candidates[{index}].evidence_refs")
        if item["status"] != "candidate_only":
            raise DraftValidationError("citations/runtime expectations cannot establish a verify obligation")
        if _boolean(item["whole_test_verified"], f"verification_candidates[{index}].whole_test_verified"):
            raise DraftValidationError("verification candidate cannot claim whole-test verification")

    check_records = _records(
        root["checks"],
        {"id", "kind", "formula_refs", "scenario_refs", "result", "performed_by", "details", "semantic_claim"},
        "checks",
    )
    checks = _index(check_records, "checks")
    for index, item in enumerate(check_records):
        _string(item["kind"], f"checks[{index}].kind")
        _refs(item["formula_refs"], set(formulas), f"checks[{index}].formula_refs")
        _refs(item["scenario_refs"], set(scenarios), f"checks[{index}].scenario_refs")
        if item["result"] != "not_run":
            raise DraftValidationError(
                "contract checks are proposals only; actual results belong to the host PoC sidecar"
            )
        performed_by = _string(item["performed_by"], f"checks[{index}].performed_by")
        if performed_by != "not_run":
            raise DraftValidationError("an unexecuted contract check must use performed_by='not_run'")
        _string(item["details"], f"checks[{index}].details")
        semantic_claim = _boolean(item["semantic_claim"], f"checks[{index}].semantic_claim")
        if semantic_claim:
            raise DraftValidationError("an incoming contract cannot self-report a semantic proof")
    for index, item in enumerate(rule_records):
        _refs(item["check_refs"], set(checks), f"response_rules[{index}].check_refs")

    consumption = _strict(
        root["consumption"],
        {"selection_mode", "selectable_scenarios", "mutual_exclusion_groups", "stub_guidance", "runtime_vs_verification", "blocking_unknown_refs", "partial_unknown_refs", "notes"},
        "consumption",
    )
    if consumption["selection_mode"] != "human_review_only":
        raise DraftValidationError("research contracts must use selection_mode='human_review_only'")
    _refs(consumption["selectable_scenarios"], set(scenarios), "consumption.selectable_scenarios")
    groups = _object(consumption["mutual_exclusion_groups"], "consumption.mutual_exclusion_groups")
    for name, refs in groups.items():
        _string(name, "consumption.mutual_exclusion_groups key")
        _refs(refs, set(scenarios), f"consumption.mutual_exclusion_groups.{name}")
    _strings(consumption["stub_guidance"], "consumption.stub_guidance")
    _strings(consumption["runtime_vs_verification"], "consumption.runtime_vs_verification")
    blocking_unknowns = _refs(consumption["blocking_unknown_refs"], set(unknowns), "consumption.blocking_unknown_refs")
    partial_unknowns = _refs(consumption["partial_unknown_refs"], set(unknowns), "consumption.partial_unknown_refs")
    if set(blocking_unknowns) & set(partial_unknowns):
        raise DraftValidationError("an unknown cannot be both blocking and partial")
    for unknown_id in blocking_unknowns:
        if not unknowns[unknown_id]["blocks_consumption"]:
            raise DraftValidationError("blocking_unknown_refs must reference blocking unknowns")
    for unknown_id in partial_unknowns:
        if unknowns[unknown_id]["blocks_consumption"]:
            raise DraftValidationError("partial_unknown_refs cannot reference blocking unknowns")
    _strings(consumption["notes"], "consumption.notes")

    if reviewed is not None:
        if identity["case_id"] != reviewed.task.case_id or language != reviewed.task.language:
            raise DraftValidationError("contract identity does not match reviewed input")
        if identity["input_policy_id"] != reviewed.task.input_policy.policy_id:
            raise DraftValidationError("contract input policy does not match reviewed input")
        if set(sources) != set(reviewed.sources.ids()):
            raise DraftValidationError("contract source ids differ from reviewed SourceStore")
        for source_id, record in sources.items():
            source = reviewed.sources.get(source_id)
            if (
                record["logical_path"] != source.logical_path
                or record["start_line"] != source.start_line
                or record["end_line"] != source.end_line
                or record["sha256"] != reviewed.source_sha256[source_id]
            ):
                raise DraftValidationError(f"source metadata differs for {source_id}")
        for item in evidence_records:
            source = reviewed.sources.get(item["source_id"])
            start, end = item["start_line"], item["end_line"]
            if not source.start_line <= start <= end <= source.end_line:
                raise DraftValidationError(f"evidence {item['id']} is outside its reviewed source")
            lines = source.text.splitlines()
            actual = "\n".join(lines[start - source.start_line : end - source.start_line + 1])
            if item["quote"] != actual:
                raise DraftValidationError(f"evidence {item['id']} quote does not match reviewed source")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("contracts", nargs="+", type=Path)
    args = parser.parse_args()
    loaded: dict[str, ReviewedInput] = {}
    for path in args.contracts:
        contract = decode_json(path.read_text(encoding="utf-8"))
        language = _object(contract, "contract").get("identity", {}).get("language")
        if language not in loaded:
            loaded[language] = load_java() if language == "java" else load_python()
        validate_contract(contract, loaded[language])
        print(f"valid: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
