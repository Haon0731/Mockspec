#!/usr/bin/env python3
"""Summarize final generic-cohort Java intake without reading raw criteria.

The preferred input is the post-run ``results.json``.  While a later-language
phase is still active, the explicit Java fallback requires exactly the expected
number of readable terminal Java records and an identity-matched
``semantic/*/batch-result.json`` for every one.  The script refuses to replace
an existing verification directory.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
from typing import Any


SUSPICIOUS_PLACEHOLDER_NAMES = {
    "do", "else", "for", "if", "switch", "synchronized", "try", "while",
}


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _counter(counter: Counter[str]) -> dict[str, int]:
    return dict(sorted(counter.items()))


def _table_value(value: object) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


def _mapping_gap(reason: object) -> str | None:
    if not isinstance(reason, str):
        return "<missing reason>"
    if reason in {
        "statement_budget_frontier",
        "typed_path_crosses_opaque_placeholder",
    }:
        return None
    if reason.startswith("raw WALA slice membership retained"):
        return None
    if reason.startswith("GENERIC_NO_HEAP omits heap and control dependencies"):
        return None
    if reason.startswith("ValueError: observation line"):
        return "observation line does not contain one assertion invocation"
    if "ArrayClass.getAllInstanceFields" in reason:
        return "WALA ArrayClass.getAllInstanceFields UnimplementedError during opaque summaries"
    return reason.splitlines()[0]


def _batch_results(run_dir: Path) -> dict[str, tuple[Path, dict[str, Any]]]:
    by_case: dict[str, tuple[Path, dict[str, Any]]] = {}
    for path in sorted((run_dir / "semantic").glob("*/batch-result.json")):
        value = _load(path)
        identity = value.get("identity")
        if not isinstance(identity, dict) or identity.get("language") != "java":
            continue
        case_id = identity.get("case_id")
        if not isinstance(case_id, str) or not case_id:
            raise ValueError(f"Java batch result lacks case_id: {path}")
        if case_id in by_case:
            raise ValueError(f"duplicate Java batch result: {case_id}")
        by_case[case_id] = (path, value)
    return by_case


def _case_row(case: dict[str, Any], batch_path: Path,
              batch: dict[str, Any]) -> dict[str, Any]:
    identity = case["identity"]
    case_id = identity["case_id"]
    if batch.get("identity", {}).get("snapshot_id") != identity.get("snapshot_id"):
        raise ValueError(f"snapshot mismatch between results and batch result: {case_id}")
    evidence = batch.get("entry_evidence") or {}
    contract = batch.get("result_contract")
    relations = contract.get("relations", []) if isinstance(contract, dict) else []
    boundaries = (
        contract.get("analysis_boundaries", {}) if isinstance(contract, dict) else {}
    )
    unknowns = boundaries.get("unknown_effects", []) or []
    unanalysed = boundaries.get("unanalysed_regions", []) or []
    frontier_rows = [
        item for item in unanalysed
        if item.get("reason") == "statement_budget_frontier"
    ]
    frontier_observations = {
        item.get("scope") for item in frontier_rows if isinstance(item.get("scope"), str)
    }
    relation_kinds = Counter(
        relation.get("kind", "<missing>") for relation in relations
    )
    frontier_relation_kinds: Counter[str] = Counter()
    for relation in relations:
        observation_ids = relation.get("observation_ids") or []
        if set(observation_ids) & frontier_observations:
            frontier_relation_kinds[relation.get("kind", "<missing>")] += 1
    opaque = [
        item for item in unknowns
        if item.get("provenance") == "visible admitted source placeholder detection"
    ]
    suspicious_opaque = [
        item for item in opaque
        if (item.get("scope") or {}).get("name") in SUSPICIOUS_PLACEHOLDER_NAMES
    ]
    opaque_suppressions = sum(
        item.get("reason") == "typed_path_crosses_opaque_placeholder"
        for item in unanalysed
    )
    mapping_gap_counts = Counter(
        gap for gap in (_mapping_gap(item.get("reason")) for item in unanalysed)
        if gap is not None
    )
    source_selection = batch.get("source_selection") or {}
    semantic = case.get("semantic_backend") or {}
    error = batch.get("error") or semantic.get("error")
    completion = contract.get("completion", {}) if isinstance(contract, dict) else {}
    return {
        "case_id": case_id,
        "snapshot_id": identity.get("snapshot_id"),
        "top_level_status": case.get("status"),
        "top_level_stage": case.get("stage"),
        "failure_categories": case.get("failure_categories", []),
        "backend_status": batch.get("status"),
        "backend_current_stage": evidence.get("current_stage"),
        "analysis_entry_status": evidence.get("analysis_entry_status"),
        "input_prepared": evidence.get("input_prepared", False),
        "bytecode_compiled": evidence.get("bytecode_compiled", False),
        "graph_created": evidence.get("graph_created", False),
        "observations_total": evidence.get("observations_total", 0),
        "observations_mapped": evidence.get("observations_mapped", 0),
        "missing_observations": evidence.get("missing_observations", []),
        "product_call_bindings": evidence.get("product_call_bindings", 0),
        "product_slice_memberships": evidence.get("product_slice_memberships", 0),
        "relation_kinds": _counter(relation_kinds),
        "frontier_observations": sorted(frontier_observations),
        "frontier_observation_count": len(frontier_observations),
        "frontier_statement_count": sum(
            int(item.get("frontier_count", 0)) for item in frontier_rows
        ),
        "frontier_relation_kinds": _counter(frontier_relation_kinds),
        "source_selection_diagnostics": source_selection.get("diagnostics", []),
        "opaque_boundary_count": len(opaque),
        "opaque_suppression_count": opaque_suppressions,
        "mapping_gap_counts": _counter(mapping_gap_counts),
        "suspicious_opaque_boundaries": [
            {
                "boundary_id": item.get("boundary_id"),
                "scope": item.get("scope"),
            }
            for item in suspicious_opaque
        ],
        "completion": completion,
        "semantic_validation": (
            contract.get("semantic_validation") if isinstance(contract, dict) else None
        ),
        "error": error,
        "batch_result": {
            "path": batch_path.as_posix(),
            "sha256": _digest(batch_path),
        },
    }


def _terminal_java_cases(run_dir: Path, expected_java: int) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    cases: list[tuple[Path, dict[str, Any]]] = []
    for path in sorted(run_dir.glob("*.json")):
        if path.name in {"pre-registration.json", "run-manifest.json", "results.json"}:
            continue
        try:
            value = _load(path)
        except json.JSONDecodeError:
            # The non-Java phase may be writing another top-level case concurrently.
            continue
        if value.get("identity", {}).get("language") == "java":
            cases.append((path, value))
    if len(cases) != expected_java:
        raise ValueError(
            f"terminal Java fallback requires exactly {expected_java} readable Java records; "
            f"found {len(cases)}"
        )
    files = [{"path": path.as_posix(), "sha256": _digest(path)} for path, _ in cases]
    aggregate = hashlib.sha256(
        json.dumps(files, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return [case for _, case in cases], {
        "source_mode": "exact_terminal_java_fallback",
        "java_case_files": files,
        "java_case_files_digest": aggregate,
        "cohort_cases": None,
    }


def _summarize(run_dir: Path, expected_total: int, expected_java: int) -> dict[str, Any]:
    results_path = run_dir / "results.json"
    if results_path.is_file():
        results = _load(results_path)
        cases = results.get("cases")
        if not isinstance(cases, list) or len(cases) != expected_total:
            raise ValueError(
                f"final results must contain exactly {expected_total} cases; "
                f"found {len(cases) if isinstance(cases, list) else 'non-list'}"
            )
        source_provenance = {
            "source_mode": "final_results",
            "results_path": results_path.as_posix(),
            "results_sha256": _digest(results_path),
            "cohort_cases": len(cases),
        }
    else:
        cases, source_provenance = _terminal_java_cases(run_dir, expected_java)
    identities = [case.get("identity", {}) for case in cases]
    case_ids = [identity.get("case_id") for identity in identities]
    if any(not isinstance(case_id, str) or not case_id for case_id in case_ids):
        raise ValueError("final results contain a case without a valid case_id")
    if len(set(case_ids)) != len(case_ids):
        raise ValueError("final results contain duplicate case identities")
    java_cases = [
        case for case in cases if case.get("identity", {}).get("language") == "java"
    ]
    if len(java_cases) != expected_java:
        raise ValueError(
            f"expected exactly {expected_java} final Java cases; found {len(java_cases)}"
        )
    batches = _batch_results(run_dir)
    java_ids = {case["identity"]["case_id"] for case in java_cases}
    if set(batches) != java_ids:
        missing = sorted(java_ids - set(batches))
        extra = sorted(set(batches) - java_ids)
        raise ValueError(f"Java batch/result mismatch; missing={missing}, extra={extra}")
    rows = [
        _case_row(case, *batches[case["identity"]["case_id"]])
        for case in java_cases
    ]
    rows.sort(key=lambda row: row["case_id"])

    failure_groups: dict[tuple[str, str, str], list[str]] = defaultdict(list)
    for row in rows:
        error = row["error"]
        if isinstance(error, dict):
            key = (
                str(row["backend_current_stage"]),
                str(error.get("type", "<missing>")),
                str(error.get("message", "<missing>")),
            )
            failure_groups[key].append(row["case_id"])
    concrete_failures = [
        {
            "stage": stage,
            "type": error_type,
            "message": message,
            "count": len(ids),
            "examples": sorted(ids)[:5],
        }
        for (stage, error_type, message), ids in sorted(
            failure_groups.items(), key=lambda item: (-len(item[1]), item[0])
        )
    ]
    mapping_gap_cases: dict[str, list[str]] = defaultdict(list)
    mapping_gap_counts: Counter[str] = Counter()
    for row in rows:
        for reason, count in row["mapping_gap_counts"].items():
            mapping_gap_counts[reason] += count
            mapping_gap_cases[reason].append(row["case_id"])
    concrete_mapping_gaps = [
        {
            "reason": reason,
            "observation_count": count,
            "case_count": len(set(mapping_gap_cases[reason])),
            "examples": sorted(set(mapping_gap_cases[reason]))[:5],
        }
        for reason, count in sorted(
            mapping_gap_counts.items(), key=lambda item: (-item[1], item[0])
        )
    ]

    relation_kinds: Counter[str] = Counter()
    frontier_relation_kinds: Counter[str] = Counter()
    for row in rows:
        relation_kinds.update(row["relation_kinds"])
        frontier_relation_kinds.update(row["frontier_relation_kinds"])
    suspicious_cases = [
        row["case_id"] for row in rows if row["suspicious_opaque_boundaries"]
    ]
    return {
        "schema": "mockspec.generic-java-final-taxonomy/v1",
        "scope": "final Java generic-backend stage and evidence taxonomy",
        "run": {
            "directory": run_dir.as_posix(),
            **source_provenance,
            "java_cases": len(rows),
            "java_batch_results": len(batches),
        },
        "counts": {
            "top_level_status": _counter(Counter(str(r["top_level_status"]) for r in rows)),
            "top_level_stage": _counter(Counter(str(r["top_level_stage"]) for r in rows)),
            "backend_status": _counter(Counter(str(r["backend_status"]) for r in rows)),
            "backend_current_stage": _counter(
                Counter(str(r["backend_current_stage"]) for r in rows)
            ),
            "analysis_entry_status": _counter(
                Counter(str(r["analysis_entry_status"]) for r in rows)
            ),
            "failure_categories": _counter(
                Counter(category for r in rows for category in r["failure_categories"])
            ),
            "observations_total": sum(int(r["observations_total"]) for r in rows),
            "observations_mapped": sum(int(r["observations_mapped"]) for r in rows),
            "cases_input_prepared": sum(bool(r["input_prepared"]) for r in rows),
            "cases_bytecode_compiled": sum(bool(r["bytecode_compiled"]) for r in rows),
            "cases_graph_created": sum(bool(r["graph_created"]) for r in rows),
            "cases_all_roots_mapped": sum(
                int(r["observations_total"]) > 0
                and r["observations_mapped"] == r["observations_total"]
                for r in rows
            ),
            "cases_with_call_binding": sum(
                int(r["relation_kinds"].get("call_binding", 0)) > 0 for r in rows
            ),
            "cases_with_typed_influence": sum(
                any(
                    int(count) > 0 and kind != "call_binding"
                    for kind, count in r["relation_kinds"].items()
                )
                for r in rows
            ),
            "frontier_observations": sum(r["frontier_observation_count"] for r in rows),
            "cases_with_frontier": sum(r["frontier_observation_count"] > 0 for r in rows),
            "relation_kinds": _counter(relation_kinds),
            "frontier_relation_kinds": _counter(frontier_relation_kinds),
            "cases_with_opaque_boundaries": sum(r["opaque_boundary_count"] > 0 for r in rows),
            "opaque_boundaries": sum(r["opaque_boundary_count"] for r in rows),
            "opaque_suppressions": sum(r["opaque_suppression_count"] for r in rows),
            "cases_with_suspicious_opaque_names": len(suspicious_cases),
        },
        "opaque_boundary_precision_caveat": {
            "policy": "known placeholder spans remain unknown and suppress typed paths",
            "detector_scope": "narrow visible single-throw syntax",
            "suspicious_names": sorted(SUSPICIOUS_PLACEHOLDER_NAMES),
            "cases_with_suspicious_detected_names": suspicious_cases,
            "interpretation": "keyword-like detected names require source review; do not treat them as reviewed method identities",
        },
        "concrete_failures": concrete_failures,
        "concrete_mapping_gaps": concrete_mapping_gaps,
        "cases": rows,
        "semantic_claim": "All new relations remain analysis evidence unless their contract says otherwise; counts do not establish semantic correctness or complete slicing.",
    }


def _markdown(report: dict[str, Any]) -> str:
    counts = report["counts"]
    lines = [
        "# Final Java generic-backend taxonomy",
        "",
        f"Run: `{report['run']['directory']}`  ",
        f"Input mode: `{report['run']['source_mode']}`  ",
        f"Input digest: `{report['run'].get('results_sha256', report['run'].get('java_case_files_digest'))}`  ",
        f"Cohort: {report['run']['cohort_cases'] if report['run']['cohort_cases'] is not None else 'still active'}; Java: {report['run']['java_cases']} terminal cases.",
        "",
        "This is a stage/evidence taxonomy. Backend completion and relation counts do not establish semantic correctness or complete slicing.",
        "",
        "## Aggregate counts",
        "",
        f"- Top-level status: `{json.dumps(counts['top_level_status'], sort_keys=True)}`",
        f"- Backend status: `{json.dumps(counts['backend_status'], sort_keys=True)}`",
        f"- Analysis entry: `{json.dumps(counts['analysis_entry_status'], sort_keys=True)}`",
        f"- Observations: {counts['observations_mapped']}/{counts['observations_total']} mapped; {counts['frontier_observations']} mapped observations record a statement frontier across {counts['cases_with_frontier']} cases.",
        f"- Case stages: {counts['cases_input_prepared']} input-prepared, {counts['cases_bytecode_compiled']} bytecode-compiled, {counts['cases_graph_created']} graph-created, {counts['cases_all_roots_mapped']} all-roots-mapped, {counts['cases_with_call_binding']} with call binding, {counts['cases_with_typed_influence']} with typed influence.",
        f"- Relation kinds: `{json.dumps(counts['relation_kinds'], sort_keys=True)}`",
        f"- Relations linked to frontier observations: `{json.dumps(counts['frontier_relation_kinds'], sort_keys=True)}`",
        f"- Opaque boundaries: {counts['opaque_boundaries']} across {counts['cases_with_opaque_boundaries']} cases; {counts['opaque_suppressions']} typed paths suppressed.",
        "",
        "## Concrete terminal failures",
        "",
        "| Stage | Type | Count | Message | Examples |",
        "| --- | --- | ---: | --- | --- |",
    ]
    for failure in report["concrete_failures"]:
        lines.append(
            "| " + " | ".join(
                _table_value(value) for value in (
                    failure["stage"], failure["type"], failure["count"],
                    failure["message"], ", ".join(failure["examples"]),
                )
            ) + " |"
        )
    if not report["concrete_failures"]:
        lines.append("| — | — | 0 | No terminal backend errors | — |")
    lines.extend([
        "",
        "## Concrete observation-mapping gaps",
        "",
        "| Reason | Observations | Cases | Examples |",
        "| --- | ---: | ---: | --- |",
    ])
    for gap in report["concrete_mapping_gaps"]:
        lines.append(
            "| " + " | ".join(
                _table_value(value) for value in (
                    gap["reason"], gap["observation_count"], gap["case_count"],
                    ", ".join(gap["examples"]),
                )
            ) + " |"
        )
    if not report["concrete_mapping_gaps"]:
        lines.append("| — | 0 | 0 | No additional observation-mapping gaps |")
    caveat = report["opaque_boundary_precision_caveat"]
    lines.extend([
        "",
        "## Opaque-boundary precision",
        "",
        f"Known placeholder spans suppress typed paths and remain unknown. Detection is limited to {caveat['detector_scope']}. Keyword-like detected names require source review; affected cases: {len(caveat['cases_with_suspicious_detected_names'])}.",
        "",
        "## Per-case root mapping and evidence",
        "",
        "| Case | Backend/stage | Roots | Entry status | Relations | Frontier roots | Opaque/suppressed | Error |",
        "| --- | --- | ---: | --- | --- | ---: | ---: | --- |",
    ])
    for row in report["cases"]:
        error = row["error"] or {}
        lines.append(
            "| " + " | ".join(
                _table_value(value) for value in (
                    row["case_id"],
                    f"{row['backend_status']}/{row['backend_current_stage']}",
                    f"{row['observations_mapped']}/{row['observations_total']}",
                    row["analysis_entry_status"],
                    json.dumps(row["relation_kinds"], sort_keys=True),
                    row["frontier_observation_count"],
                    f"{row['opaque_boundary_count']}/{row['opaque_suppression_count']}",
                    error.get("message", ""),
                )
            ) + " |"
        )
    lines.extend(["", f"Semantic claim boundary: {report['semantic_claim']}", ""])
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True,
                        help="must be a new directory")
    parser.add_argument("--expected-total", type=int, default=165)
    parser.add_argument("--expected-java", type=int, default=59)
    args = parser.parse_args()
    if min(args.expected_total, args.expected_java) < 1:
        parser.error("expected cohort sizes must be positive")
    if args.output_dir.exists():
        raise FileExistsError(f"output directory must not exist: {args.output_dir}")
    report = _summarize(args.run_dir, args.expected_total, args.expected_java)
    markdown = _markdown(report)
    args.output_dir.mkdir(parents=True)
    (args.output_dir / "java-taxonomy.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    )
    (args.output_dir / "java-taxonomy.md").write_text(markdown)


if __name__ == "__main__":
    main()
