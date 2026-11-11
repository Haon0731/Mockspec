#!/usr/bin/env python3
"""Independent, read-only audit of the issue-32 generic full cohort."""

from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import sys
from typing import Any


def find_repo_root(script: Path) -> Path:
    for candidate in script.resolve().parents:
        if (candidate / "experiments/backward-slicing/result_contract.py").is_file():
            return candidate
    raise RuntimeError(f"cannot locate Mockspec repository root from {script}")


ROOT = find_repo_root(Path(__file__))
sys.path.insert(0, str(ROOT / "experiments" / "backward-slicing"))

from result_contract import (  # type: ignore[import-not-found]  # noqa: E402
    RELATION_KINDS,
    contract_digest,
    validate_result_contract,
)

ORIGINAL = ROOT / "out/mockbench-study/backward-slice-full-20261109-r3/results.json"
FORMAL = ROOT / "out/mockbench-study/backward-slice-formal165-20261111-r3/results.json"
TARGETED = {
    "python": (
        ROOT / "out/mockbench-study/generic-entry-20261111-r1/python-final-r4/targeted-results.json",
    ),
    "java": (
        ROOT / "out/mockbench-study/generic-entry-20261111-r1/java-zipkin-direct-r6/targeted-results.json",
        ROOT / "out/mockbench-study/generic-entry-20261111-r1/java-zipkin-direct-r7/targeted-results.json",
    ),
}
ORIGINAL_MATURE = {
    ("java-bechmark-strong", "com.google.cloud.tools.jib.cli.jar.JarFilesTest#testToJibContainerBuilder_explodedStandard_basicInfo@69ae66df"),
    ("python-benchmark-new", "flask_308"),
    ("python-benchmark-new", "langflow_189"),
}
TYPED_KINDS = {
    "value_flow",
    "control_dependence",
    "state_flow",
    "normal_return_dependence",
    "exception_dependence",
}


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise ValueError(f"{path}: expected JSON object")
    return value


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def identity(row: dict[str, Any]) -> tuple[str, str]:
    item = row["identity"]
    return item["dataset"], item["case_id"]


def facts(row: dict[str, Any]) -> dict[str, Any]:
    contract = row.get("result_contract", {})
    contract_identity = contract.get("identity", {})
    context = row.get("input_context", {})
    target = row.get("target_extraction", {})
    mask = contract_identity.get("masked_test_sha256") or context.get("masked_test_sha256")
    if mask is None:
        mask = next(
            (source.get("sha256") for source in row.get("sources", [])
             if source.get("source_id") == "test"),
            None,
        )
    selector = (
        contract_identity.get("target_selector")
        or context.get("target_selector")
        or target.get("target_scope", {}).get("selector")
    )
    masking_contract = contract_identity.get("masking_contract") or context.get("masking_contract")
    observations = []
    if contract.get("observations") is not None:
        for observation in contract.get("observations", []):
            location = observation.get("location", {})
            observations.append((
                observation.get("observation_id"),
                location.get("start_line"),
                location.get("end_line"),
                observation.get("kind"),
            ))
    else:
        for index, criterion in enumerate(target.get("criteria", [])):
            observations.append((
                criterion.get("criterion_id", f"criterion:{index}"),
                criterion.get("line"),
                criterion.get("line"),
                criterion.get("kind"),
            ))
    item = row["identity"]
    return {
        "identity": (item.get("dataset"), item.get("case_id"), item.get("language"),
                     item.get("snapshot_id")),
        "mask": mask,
        "selector": selector,
        "masking_contract": masking_contract,
        "observations": observations,
        "observation_seeds": [(item[0], item[1]) for item in observations],
    }


def load_index(path: Path) -> dict[tuple[str, str], dict[str, Any]]:
    payload = read_json(path)
    rows = payload.get("cases", [])
    index = {identity(row): facts(row) for row in rows}
    if len(index) != len(rows):
        raise ValueError(f"{path}: duplicate dataset/case identity")
    return index


def load_rows(path: Path) -> list[dict[str, Any]]:
    rows = read_json(path).get("cases", [])
    if not isinstance(rows, list):
        raise ValueError(f"{path}: cases must be an array")
    return rows


def linked_cross_source(contract: dict[str, Any]) -> bool:
    return any(
        relation.get("observation_ids")
        and relation.get("kind") in TYPED_KINDS
        and relation.get("from", {}).get("logical_path")
        and relation.get("to", {}).get("logical_path")
        and relation["from"]["logical_path"] != relation["to"]["logical_path"]
        for relation in contract.get("relations", [])
    )


def stage_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for row in rows:
        backend = row.get("semantic_backend", {})
        evidence = backend.get("entry_evidence", {})
        counts["attempted"] += 1
        counts["input_prepared"] += evidence.get("input_prepared") is True
        counts["database_or_graph_created"] += (
            evidence.get("database_created") is True or evidence.get("graph_created") is True
        )
        counts["database_created"] += evidence.get("database_created") is True
        counts["graph_created"] += evidence.get("graph_created") is True
        counts["backend_completed"] += backend.get("status") == "complete"
        total = evidence.get("observations_total", 0)
        counts["all_observations_mapped"] += (
            total > 0
            and evidence.get("observations_mapped") == total
            and not evidence.get("ambiguous_observation_lines")
        )
        counts["product_call_binding_present"] += evidence.get("product_call_bindings", 0) > 0
        counts["observation_linked_cross_source_relation"] += linked_cross_source(
            row.get("result_contract", {})
        )
    return dict(counts)


def add_error(errors: list[dict[str, Any]], check: str, key: Any, detail: Any) -> None:
    errors.append({"check": check, "case": key, "detail": detail})


def contract_shape_errors(row: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    contract = row.get("result_contract", {})
    try:
        validate_result_contract(contract)
    except Exception as exc:
        errors.append(f"contract: {type(exc).__name__}: {exc}")
    try:
        if contract_digest(contract) != row.get("result_contract_sha256"):
            errors.append("contract digest mismatch")
    except (TypeError, ValueError) as exc:
        errors.append(f"contract digest failed: {type(exc).__name__}: {exc}")
    coverage = contract.get("relation_coverage", [])
    if not isinstance(coverage, list):
        coverage = []
    coverage_by_kind = {
        item.get("kind"): item.get("status") for item in coverage if isinstance(item, dict)
    }
    if set(coverage_by_kind) != set(RELATION_KINDS):
        errors.append(f"coverage kinds: {sorted(str(key) for key in coverage_by_kind)}")
    if any(coverage_by_kind.get(kind) != "not_evaluated" for kind in RELATION_KINDS):
        errors.append(f"coverage statuses: {coverage_by_kind}")
    if contract.get("semantic_validation", {}).get("status") != "not_evaluated":
        errors.append("semantic validation is not not_evaluated")
    boundaries = contract.get("analysis_boundaries", {})
    if not isinstance(boundaries.get("unknown_effects"), list):
        errors.append("analysis_boundaries.unknown_effects is not an array")
    if not isinstance(boundaries.get("unanalysed_regions"), list):
        errors.append("analysis_boundaries.unanalysed_regions is not an array")
    return errors


def self_check_records(run: Path) -> dict[str, Any]:
    checked = 0
    by_language: Counter[str] = Counter()
    by_status: Counter[str] = Counter()
    errors: list[dict[str, Any]] = []
    for path in sorted(run.glob("[0-9a-f]*.json")):
        row = read_json(path)
        if "identity" not in row or "result_contract" not in row:
            continue
        checked += 1
        by_language[row["identity"].get("language", "missing")] += 1
        by_status[row.get("semantic_backend", {}).get("status", "missing")] += 1
        for detail in contract_shape_errors(row):
            errors.append({"record": path.name, "detail": detail})
    return {
        "checked": checked,
        "by_language": dict(by_language),
        "semantic_status": dict(by_status),
        "errors": errors,
    }


def audit(run: Path) -> dict[str, Any]:
    results_path = run / "results.json"
    if not results_path.is_file():
        raise FileNotFoundError(f"cohort is not complete: {results_path} is absent")

    original = load_index(ORIGINAL)
    formal = load_index(FORMAL)
    payload = read_json(results_path)
    rows = payload.get("cases", [])
    current = {identity(row): facts(row) for row in rows}
    errors: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []

    if len(current) != len(rows):
        add_error(errors, "unique_current_identities", None, {"rows": len(rows), "unique": len(current)})
    for name, baseline in (("original_r3", original), ("formal_r3", formal)):
        if set(current) != set(baseline):
            add_error(errors, f"identity_set_matches_{name}", None, {
                "missing": sorted(set(baseline) - set(current)),
                "extra": sorted(set(current) - set(baseline)),
            })
    languages = Counter(row["identity"].get("language") for row in rows)
    if languages != Counter({"java": 59, "python": 106}):
        add_error(errors, "language_cohort", None, dict(languages))

    for key, actual in current.items():
        for name, baseline in (("original_r3", original), ("formal_r3", formal)):
            expected = baseline.get(key)
            if expected is None:
                continue
            for field in ("identity", "mask", "selector", "observation_seeds"):
                if actual[field] != expected[field]:
                    add_error(errors, f"{field}_matches_{name}", key, {
                        "expected": expected[field], "actual": actual[field],
                    })
        expected_original = original.get(key)
        if expected_original is not None and actual["observations"] != expected_original["observations"]:
            add_error(errors, "observations_match_original_r3", key, {
                "expected": expected_original["observations"],
                "actual": actual["observations"],
            })
        expected_formal = formal.get(key)
        if (
            expected_formal is not None
            and actual["observation_seeds"] == expected_formal["observation_seeds"]
            and actual["observations"] != expected_formal["observations"]
        ):
            warnings.append({
                "check": "formal_specialized_observation_metadata_not_carried",
                "case": key,
                "detail": {
                    "retained_seed_ids_and_start_lines": actual["observation_seeds"],
                    "current_original_r3_metadata": actual["observations"],
                    "prior_formal_specialized_metadata": expected_formal["observations"],
                    "interpretation": "Formal-only kind/end-span enrichment is nonsemantic bridge metadata, not an original frozen root loss.",
                },
            })
        expected_masking = formal.get(key, {}).get("masking_contract")
        if actual["masking_contract"] != expected_masking:
            add_error(errors, "masking_contract_matches_formal_r3", key, {
                "expected": expected_masking, "actual": actual["masking_contract"],
            })

    registration = read_json(run / "pre-registration.json")
    manifest = read_json(run / "run-manifest.json")
    inventory = read_json(run / "inventory.json")
    inventory_hash = hashlib.sha256(
        json.dumps(inventory, sort_keys=True).encode()
    ).hexdigest()
    if inventory_hash != registration.get("inventory_sha256"):
        add_error(errors, "inventory_hash_matches_registration", None, {
            "registered": registration.get("inventory_sha256"), "actual": inventory_hash,
        })
    registered_hashes = registration.get("implementation_sha256", {})
    if manifest.get("implementation_sha256") != registered_hashes:
        add_error(errors, "runtime_manifest_hashes_match_registration", None, {
            "registered": registered_hashes, "runtime": manifest.get("implementation_sha256"),
        })
    implementation_hashes: dict[str, Any] = {}
    for relative, expected in registered_hashes.items():
        path = ROOT / relative
        actual_hash = sha256_file(path) if path.is_file() else None
        implementation_hashes[relative] = {"registered": expected, "current": actual_hash,
                                           "matches": actual_hash == expected}
        if actual_hash != expected:
            add_error(errors, "registered_implementation_unchanged", relative, {
                "expected": expected, "actual": actual_hash,
            })
    if registration.get("configuration", {}).get("backend_mode") != "generic":
        add_error(errors, "generic_backend_registered", None,
                  registration.get("configuration", {}).get("backend_mode"))
    scope = registration.get("semantic_support_scope", {})
    selection = scope.get("selection")
    if not isinstance(selection, str) or (
        "no case-ID allowlist or silent registered-case fallback" not in selection
    ):
        add_error(errors, "no_registered_fallback", None, selection)

    status_counts: Counter[str] = Counter()
    backend_counts: Counter[str] = Counter()
    failure_counts: Counter[str] = Counter()
    unknown_effect_cases = 0
    ambiguous_cases = 0
    missing_observation_cases = 0
    per_case_records = 0
    generic_request_records = 0
    for row in rows:
        key = identity(row)
        contract = row.get("result_contract", {})
        for detail in contract_shape_errors(row):
            add_error(errors, "result_contract_integrity", key, detail)

        token = hashlib.sha256((key[0] + "\0" + key[1]).encode()).hexdigest()[:20]
        record = run / f"{token}.json"
        if record.is_file():
            per_case_records += 1
            recorded = read_json(record)
            if recorded.get("result_contract_sha256") != row.get("result_contract_sha256"):
                add_error(errors, "per_case_record_digest_matches_results", key, {
                    "record": recorded.get("result_contract_sha256"),
                    "results": row.get("result_contract_sha256"),
                })
        else:
            add_error(errors, "per_case_record_present", key, str(record))
        request = run / "semantic" / f"{token}-request.json"
        if request.is_file():
            generic_request_records += 1
        else:
            add_error(errors, "generic_dispatch_request_present", key, str(request))

        backend = row.get("semantic_backend", {})
        backend_status = backend.get("status")
        backend_name = backend.get("backend")
        status_counts[backend_status or "missing"] += 1
        backend_counts[backend_name or "missing"] += 1
        if backend_status == "unsupported":
            add_error(errors, "generic_path_not_registered_unsupported", key, backend)
        expected_backend = "generic_codeql_python" if row["identity"]["language"] == "python" else "generic_wala_java"
        if backend_name is not None and backend_name != expected_backend:
            add_error(errors, "language_generic_backend", key,
                      {"expected": expected_backend, "actual": backend_name})
        failure_counts.update(set(contract.get("completion", {}).get("failure_categories", [])))
        unknown_effects = contract.get("analysis_boundaries", {}).get("unknown_effects", [])
        unknown_effect_cases += bool(unknown_effects)
        evidence = backend.get("entry_evidence", {})
        ambiguous_cases += bool(evidence.get("ambiguous_observation_lines"))
        missing_observation_cases += bool(
            evidence.get("missing_observations") or evidence.get("missing_observation_ids")
        )
        evidence_count = evidence.get("observation_linked_cross_source_relations")
        if evidence_count is not None and (evidence_count > 0) != linked_cross_source(contract):
            add_error(errors, "entry_evidence_agrees_with_typed_contract_relation", key, {
                "entry_evidence": evidence_count,
                "contract_has_typed_cross_source": linked_cross_source(contract),
            })

    summary = read_json(run / "summary.json")
    total_counts = stage_counts(rows)
    by_language = {
        language: stage_counts([row for row in rows if row["identity"]["language"] == language])
        for language in ("java", "python")
    }
    reported = summary.get("generic_entry", {})
    expected_reported = {key: value for key, value in total_counts.items()
                         if key not in {"database_created", "graph_created"}}
    if reported.get("counts") != expected_reported:
        add_error(errors, "summary_generic_entry_counts", None, {
            "reported": reported.get("counts"), "independent": expected_reported,
        })
    for language in ("java", "python"):
        expected_language = {key: value for key, value in by_language[language].items()
                             if key not in {"database_created", "graph_created"}}
        if reported.get("by_language", {}).get(language) != expected_language:
            add_error(errors, "summary_generic_entry_language_counts", language, {
                "reported": reported.get("by_language", {}).get(language),
                "independent": expected_language,
            })

    targeted_qualifying: dict[str, set[tuple[str, str]]] = {}
    targeted_linkage: dict[str, Any] = {}
    for language, paths in TARGETED.items():
        targeted_rows = [row for path in paths for row in load_rows(path)]
        qualifying = {
            identity(row) for row in targeted_rows
            if identity(row) not in ORIGINAL_MATURE
            and row.get("identity", {}).get("language") == language
            and linked_cross_source(row.get("result_contract", {}))
        }
        targeted_qualifying[language] = qualifying
        targeted_linkage[language] = {
            "artifacts": [{"path": str(path.relative_to(ROOT)), "sha256": sha256_file(path)}
                          for path in paths],
            "qualifying_non_original_cases": sorted(qualifying),
        }
    current_qualifying = {
        language: {
            identity(row) for row in rows
            if identity(row) not in ORIGINAL_MATURE
            and row.get("identity", {}).get("language") == language
            and linked_cross_source(row.get("result_contract", {}))
        }
        for language in ("python", "java")
    }
    for language, minimum in (("python", 3), ("java", 2)):
        missing_targeted = targeted_qualifying[language] - current_qualifying[language]
        if missing_targeted:
            add_error(errors, "targeted_gate_cases_reproduced_in_full_cohort", language,
                      sorted(missing_targeted))
        if len(targeted_qualifying[language]) < minimum:
            add_error(errors, "targeted_gate_has_required_non_original_cases", language, {
                "minimum": minimum, "actual": sorted(targeted_qualifying[language]),
            })

    semantic_claim = {
        "status": "not_evaluated",
        "verified_cases": 0,
        "statement": "Typed relations are intake evidence only; this audit does not establish semantic correctness, Mock recovery, execution, or scoring.",
    }
    intake_gate = (
        not errors
        and total_counts.get("attempted") == 165
        and len(current_qualifying["python"]) >= 3
        and len(current_qualifying["java"]) >= 2
        and targeted_qualifying["python"] <= current_qualifying["python"]
        and targeted_qualifying["java"] <= current_qualifying["java"]
    )
    if len(current_qualifying["python"]) < 3:
        warnings.append({"check": "python_non_original_intake_threshold",
                         "detail": sorted(current_qualifying["python"])})
    if len(current_qualifying["java"]) < 2:
        warnings.append({"check": "java_non_jib_intake_threshold",
                         "detail": sorted(current_qualifying["java"])})

    return {
        "audit_schema": "mockspec.issue32-full-cohort-audit/v1",
        "audit_implementation": {
            "path": str(Path(__file__).resolve().relative_to(ROOT)),
            "sha256": sha256_file(Path(__file__).resolve()),
        },
        "run": str(run.relative_to(ROOT)),
        "cohort": {"total": len(rows), "by_language": dict(languages)},
        "intake_acceptance": {
            "status": "passed" if intake_gate else "failed",
            "criteria": "all165 generic attempts plus >=3 Python and >=2 Java cases with typed observation-linked cross-source relations, with preservation and integrity checks passing",
            "independent_stage_counts": total_counts,
            "by_language": by_language,
            "non_original_qualifying_cases": {
                language: sorted(keys) for language, keys in current_qualifying.items()
            },
            "targeted_gate_linkage": targeted_linkage,
        },
        "semantic_correctness": semantic_claim,
        "integrity": {
            "errors": errors,
            "warnings": warnings,
            "implementation_hashes": implementation_hashes,
            "per_case_records": per_case_records,
            "generic_request_records": generic_request_records,
        },
        "outcomes": {
            "semantic_backend_status": dict(status_counts),
            "semantic_backend": dict(backend_counts),
            "failure_categories": dict(failure_counts),
            "unknown_effect_cases": unknown_effect_cases,
            "ambiguous_observation_cases": ambiguous_cases,
            "missing_observation_cases": missing_observation_cases,
            "budget_or_timeout_cases": status_counts["budget_exceeded"] + status_counts["timeout"],
        },
        "scope_limits": {
            "python": "Static CodeQL intake, including narrowly qualified visible dynamic-loader facts; only typed observation-linked cross-source relations count.",
            "java": "Static WALA graph/SSA/slice intake with source provenance; only typed observation-linked cross-source relations count.",
            "shared": "No target tests, loaders, Mock generation/scoring, hidden answers, or semantic validation are covered.",
        },
    }


def markdown(report: dict[str, Any]) -> str:
    intake = report["intake_acceptance"]
    integrity = report["integrity"]
    stages = intake["independent_stage_counts"]
    lines = [
        "# Issue #32 independent full-cohort audit",
        "",
        f"- Intake acceptance: **{intake['status']}**",
        f"- Cohort: {report['cohort']['total']} cases ({report['cohort']['by_language']})",
        f"- Integrity errors: {len(integrity['errors'])}; warnings: {len(integrity['warnings'])}",
        f"- Independent stages: `{json.dumps(stages, sort_keys=True)}`",
        f"- By language: `{json.dumps(intake['by_language'], sort_keys=True)}`",
        "- Semantic correctness: **not_evaluated** (zero verified cases)",
        "",
        "## Outcomes and retained gaps",
        "",
        f"`{json.dumps(report['outcomes'], sort_keys=True)}`",
        "",
        "Typed relations are evidence of generic analysis intake, not proof of correct slicing, Mock recovery, generation, execution, or scoring. All relation coverage and semantic validation must remain `not_evaluated`.",
        "",
        "## Integrity findings",
        "",
    ]
    if integrity["errors"]:
        lines.extend(f"- ERROR `{json.dumps(item, sort_keys=True)}`" for item in integrity["errors"])
    else:
        lines.append("- No integrity errors.")
    lines.extend(f"- WARNING `{json.dumps(item, sort_keys=True)}`" for item in integrity["warnings"])
    lines.extend(["", "## Scope limits", ""])
    lines.extend(f"- {language}: {text}" for language, text in report["scope_limits"].items())
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--self-check-only", action="store_true")
    args = parser.parse_args()
    run = args.run.resolve()
    if args.self_check_only:
        report = self_check_records(run)
        print(json.dumps(report, indent=2, sort_keys=True))
        return 0 if report["checked"] and not report["errors"] else 1
    if args.output is None:
        parser.error("--output is required unless --self-check-only is used")
    try:
        report = audit(run)
    except FileNotFoundError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "audit.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    (args.output / "audit.md").write_text(markdown(report))
    print(json.dumps({
        "status": report["intake_acceptance"]["status"],
        "errors": len(report["integrity"]["errors"]),
        "output": str(args.output),
    }, sort_keys=True))
    return 0 if not report["integrity"]["errors"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
