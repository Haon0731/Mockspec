"""Project the bounded langflow backend study into the shared case contract."""
from __future__ import annotations

import math
from pathlib import Path
import sys
from typing import Any, Mapping, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from result_contract import (  # type: ignore[import-not-found]  # noqa: E402
    RELATION_KINDS,
    RESULT_SCHEMA_VERSION,
    validate_result_contract,
)


STUDY_SCHEMA = "mock-spec.langflow-backend-study/v1"
EXPECTATIONS_SCHEMA = "mock-spec.langflow-backend-expectations/v1"
RAW_KIND_MAP = {
    "product_function": "function_origin",
    "test_dynamic_call_syntax": "call_binding",
    "test_call_result_to_observation": "value_flow",
    "dynamic_global_identity_observation": "function_origin",
    "parameter_to_decrypt_argument": "value_flow",
    "parameter_to_return_value_use": "value_flow",
    "settings_result_to_decrypt_argument": "value_flow",
    "decrypt_result_to_downstream_use": "value_flow",
    "decrypt_exception_to_handler_return": "exception_dependence",
    "product_return": "normal_return_dependence",
    "product_global_reference": "function_origin",
    "guard_controls_product_return": "control_dependence",
    "product_dependency_call": "call_binding",
    "composed_product_return_to_observation": "normal_return_dependence",
    "composed_invalid_token_global_to_observation": "function_origin",
}
MODALITY_MAP = {
    "certain": "certain",
    "analysis_may": "analysis_may",
    "unknown_effect": "unknown_effect",
    "authenticated_external_bridge": "analysis_may",
}


class LangflowContractError(ValueError):
    pass


def _required_string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise LangflowContractError(f"{label} is missing")
    return value


def _validate_identity(manifest: Mapping[str, Any], expectations: Mapping[str, Any]) -> None:
    if expectations.get("schema") != EXPECTATIONS_SCHEMA:
        raise LangflowContractError("unsupported expectations schema")
    for key in ("case_id", "dataset", "snapshot_id", "target_selector"):
        if manifest.get(key) != expectations.get(key):
            raise LangflowContractError(f"{key} differs from expectations")
    if expectations.get("inventory_revision") is not None and manifest.get(
        "inventory_revision"
    ) != expectations.get("inventory_revision"):
        raise LangflowContractError("inventory_revision differs from expectations")
    files = manifest.get("files")
    if not isinstance(files, list):
        raise LangflowContractError("manifest files are missing")
    test_files = [row for row in files if isinstance(row, dict) and row.get("role") == "masked_test"]
    product_files = [
        row for row in files
        if isinstance(row, dict) and row.get("logical_path") == expectations.get("product_path")
    ]
    if len(test_files) != 1 or test_files[0].get("sha256") != expectations.get(
        "masked_test_sha256"
    ):
        raise LangflowContractError("masked test identity differs from expectations")
    if len(product_files) != 1 or product_files[0].get("sha256") != expectations.get(
        "product_sha256"
    ):
        raise LangflowContractError("product identity differs from expectations")


def _observations(
    manifest: Mapping[str, Any], expectations: Mapping[str, Any]
) -> list[dict[str, Any]]:
    expected = expectations.get("observations")
    if not isinstance(expected, list) or [row.get("id") for row in expected] != [
        "criterion:0", "criterion:1"
    ] or [row.get("line") for row in expected] != [106, 115]:
        raise LangflowContractError("expected observation identity is not the frozen two-criterion set")
    extraction = manifest.get("target_extraction")
    criteria = extraction.get("criteria") if isinstance(extraction, dict) else None
    if not isinstance(criteria, list) or [row.get("line") for row in criteria] != [106, 115]:
        raise LangflowContractError("manifest criteria differ from frozen observation positions")
    for index, criterion in enumerate(criteria):
        criterion_id = criterion.get("criterion_id")
        if criterion_id is not None and criterion_id != f"criterion:{index}":
            raise LangflowContractError("manifest criterion identity differs")
    test_file = next(row for row in manifest["files"] if row.get("role") == "masked_test")
    return [{
        "observation_id": row["id"],
        "location": {"source_id": test_file["source_id"], "start_line": row["line"],
                     "end_line": row["line"]},
        "kind": row["kind"], "origin": "explicit",
        "reason": "frozen explicit observation selected by extract_criteria",
    } for row in expected]


def _endpoint(value: Any, fallback_path: Any = None, fallback_line: Any = None) -> dict[str, Any]:
    if isinstance(value, dict):
        result = dict(value)
        path = result.pop("path", result.get("logical_path", fallback_path))
        line = result.pop("line", result.get("start_line", fallback_line))
    else:
        result, path, line = {}, fallback_path, fallback_line
    if isinstance(path, str):
        result["logical_path"] = path
    if isinstance(line, str) and line.isdigit():
        line = int(line)
    if isinstance(line, int) and not isinstance(line, bool):
        result["start_line"] = line
        result.setdefault("end_line", line)
    if not result:
        raise LangflowContractError("relation endpoint is missing")
    return result


def _typed_relations(
    study: Mapping[str, Any], observation_ids: set[str]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    rows = study.get("relations")
    if not isinstance(rows, list):
        raise LangflowContractError("study relations are missing")
    typed = []
    unmapped = []
    for row in rows:
        if not isinstance(row, dict):
            unmapped.append({"raw_relation": row, "reason": "relation is not an object"})
            continue
        raw_kind = row.get("kind", row.get("relation"))
        kind: str | None
        if raw_kind == "composed_dependency_to_observation":
            kind = ("exception_dependence" if "exception" in str(row.get("detail", "")).lower()
                    else "value_flow")
        else:
            kind = (raw_kind if raw_kind in RELATION_KINDS else RAW_KIND_MAP.get(raw_kind)) \
                if isinstance(raw_kind, str) else None
        raw_modality = row.get("modality")
        modality = MODALITY_MAP.get(raw_modality) if isinstance(raw_modality, str) else None
        if kind is None or modality is None:
            unmapped.append({
                "raw_relation": row,
                "reason": "unmapped relation kind" if kind is None else "unmapped relation modality",
            })
            continue
        references = row.get("observation_ids", [])
        if not isinstance(references, list) or any(item not in observation_ids for item in references):
            raise LangflowContractError("relation references an unknown observation")
        source = _endpoint(
            row.get("from"), row.get("sourcePath"), row.get("sourceLine")
        )
        target = _endpoint(
            row.get("to"), row.get("targetPath"), row.get("targetLine")
        )
        detail = row.get("detail")
        precision_parts = ["bounded langflow backend relation"]
        if isinstance(detail, str) and detail:
            precision_parts.append(detail)
        if raw_modality == "authenticated_external_bridge":
            precision_parts.append("authenticated_external_bridge projected as analysis_may")
        provenance = row.get("provenance")
        typed.append({
            "kind": kind, "modality": modality, "from": source, "to": target,
            "provenance": {
                "source": provenance or "langflow backend study",
                "raw_relation_kind": raw_kind,
                "raw_modality": raw_modality,
            },
            "precision": "; ".join(precision_parts),
            "observation_ids": list(references),
            "assumptions": list(row.get("assumptions", [])),
        })
    return typed, unmapped


def _unknown_effects(study: Mapping[str, Any]) -> list[dict[str, Any]]:
    values = study.get("unknown_effects", study.get("structured_unknowns", []))
    if values is None:
        return []
    if not isinstance(values, list):
        raise LangflowContractError("structured unknown effects must be an array")
    result = []
    for value in values:
        if not isinstance(value, dict) or not isinstance(value.get("boundary"), dict):
            raise LangflowContractError("structured unknown effect boundary is invalid")
        boundary = value["boundary"]
        path = _required_string(boundary.get("path"), "unknown boundary path")
        line = boundary.get("line")
        call = _required_string(boundary.get("call"), "unknown boundary call")
        if not isinstance(line, int) or isinstance(line, bool):
            raise LangflowContractError("unknown boundary line is invalid")
        result.append({
            "boundary_id": f"{path}:{line}:{call}",
            "effect": {
                "normal_return": value.get("normal_return"),
                "exception": value.get("exception"),
                "state_alias_scope": value.get("state_alias_scope"),
            },
            "scope": {"logical_path": path, "start_line": line, "call": call},
            "provenance": "structured unknown supplied by langflow backend study",
            "precision": "unresolved; copied without inferring concrete effects",
            "assumptions": [],
        })
    return result


def _cost(cost: Mapping[str, Any], files: Sequence[Mapping[str, Any]], count: int) -> dict[str, Any]:
    timings = cost.get("timings_seconds")
    phase_rows = [cost[key] for key in ("database", "query", "decode")
                  if isinstance(cost.get(key), dict)]
    if timings is None:
        timings = {}
        for key in ("database", "query", "decode"):
            value = cost.get(key)
            if isinstance(value, dict):
                value = value.get("seconds")
            if value is not None:
                timings[key] = value
    if not isinstance(timings, dict) or any(
        not isinstance(value, (int, float)) or isinstance(value, bool)
        or not math.isfinite(value) or value < 0
        for value in timings.values()
    ):
        raise LangflowContractError("cost timings are invalid")
    total = cost.get("total_seconds")
    if total is None and timings:
        total = sum(timings.values())
    peak = cost.get("peak_memory")
    if not isinstance(peak, dict):
        peaks = [row.get("peak_rss_bytes") for row in phase_rows
                 if isinstance(row.get("peak_rss_bytes"), int)
                 and not isinstance(row.get("peak_rss_bytes"), bool)]
        peak = ({"status": "measured_per_process_max", "bytes": max(peaks),
                 "reason": "maximum of phase process RSS values; not a simultaneous run peak"}
                if peaks else
                {"status": "unavailable", "bytes": None,
                 "reason": "peak memory was not supplied by the runner"})
    cache = cost.get("cache_state")
    if not isinstance(cache, dict):
        cache = {"status": "unknown", "reason": "cache isolation was not supplied"}
    unavailable = cost.get("unavailable_phases", [])
    if not isinstance(unavailable, list):
        raise LangflowContractError("cost unavailable_phases is invalid")
    if not timings and not unavailable:
        unavailable = [{"phase": "backend_total", "reason": "timing evidence was not supplied"}]
    return {
        "timings_seconds": dict(timings), "total_seconds": total,
        "total_scope": cost.get("total_scope", "bounded langflow backend bridge"),
        "peak_memory": peak, "cache_state": cache,
        "input_size": {"files": len(files), "bytes": sum(int(row.get("bytes", 0)) for row in files)},
        "graph_size": {"nodes": None, "edges": None, "relation_count": count,
                       "reason": "backend graph size is not exported"},
        "unavailable_phases": list(unavailable),
    }


def build_case_result(
    manifest: Mapping[str, Any], study: Mapping[str, Any],
    expectations: Mapping[str, Any], cost: Mapping[str, Any],
) -> dict[str, Any]:
    """Build and validate the shared case contract from one authenticated study."""
    if study.get("schema") != STUDY_SCHEMA:
        raise LangflowContractError("unsupported study schema")
    if study.get("status") != "verified_incomplete":
        raise LangflowContractError("study is not a verified-incomplete bounded result")
    _validate_identity(manifest, expectations)
    observations = _observations(manifest, expectations)
    study_identity = study.get("identity")
    if not isinstance(study_identity, dict) or any(
        study_identity.get(key) != manifest.get(key)
        for key in ("case_id", "dataset", "snapshot_id", "target_selector")
    ):
        raise LangflowContractError("study identity differs from admitted manifest")
    study_observations = study.get("observations")
    expected_observations = expectations["observations"]
    if not isinstance(study_observations, list) or [
        (row.get("id"), row.get("line"), row.get("kind")) for row in study_observations
    ] != [
        (row["id"], row["line"], row["kind"]) for row in expected_observations
    ]:
        raise LangflowContractError("study observations differ from frozen expectations")
    observation_ids = {row["observation_id"] for row in observations}
    relations, unmapped = _typed_relations(study, observation_ids)
    required_kinds = {
        "call_binding", "function_origin", "value_flow", "control_dependence",
        "normal_return_dependence", "exception_dependence",
    }
    missing_kinds = required_kinds - {row["kind"] for row in relations}
    covered_observations = {
        observation_id for row in relations for observation_id in row["observation_ids"]
    }
    missing_observations = observation_ids - covered_observations
    if missing_kinds or missing_observations:
        raise LangflowContractError(
            "required typed relation or observation coverage is missing: "
            f"kinds={sorted(missing_kinds)}, observations={sorted(missing_observations)}"
        )
    unknown_effects = _unknown_effects(study)
    files = manifest["files"]
    source_scope = [{
        "source_id": row["source_id"], "logical_path": row["logical_path"],
        "sha256": row["sha256"], "bytes": row["bytes"],
        "selection_role": row.get("role", "candidate_product"),
    } for row in files]
    coverage = {
        kind: ("verified_incomplete" if any(row["kind"] == kind for row in relations)
               else "not_evaluated")
        for kind in RELATION_KINDS
    }
    extraction = manifest["target_extraction"]
    target_scope = extraction.get("target_scope", {})
    binding = manifest.get("dynamic_loader_binding", {})
    contract = {
        "schema_version": RESULT_SCHEMA_VERSION,
        "identity": {
            "case_id": manifest["case_id"], "dataset": manifest["dataset"],
            "language": "python", "snapshot_id": manifest["snapshot_id"],
            "masking_contract": manifest.get("masking_contract"),
            "masked_test_sha256": expectations["masked_test_sha256"],
            "target_selector": manifest["target_selector"],
        },
        "source_scope": source_scope,
        "observations": observations,
        "parameterization": {
            "status": "not_expanded", "coverage": "selected test definition",
            "reason": "parameter rows and complete path feasibility were not evaluated",
        },
        "entry_point": {
            "test_selection": {
                "origin": "task_provided", "status": "unique",
                "selector": manifest["target_selector"],
                "evidence": target_scope.get("locations", []),
            },
            "mut_binding": {
                "origin": "automatic", "status": "unique" if binding.get("status") == "resolved"
                else "unresolved", "candidates": [], "evidence": [binding] if binding else [],
                "assumptions": [],
            },
        },
        "relations": relations,
        "relation_coverage": [
            {"kind": kind, "status": coverage[kind]} for kind in sorted(coverage)
        ],
        "analysis_boundaries": {
            "assumptions": [
                "authenticated frontend bridges are explicit inputs, not backend-native dynamic resolution"
            ],
            "unknown_effects": unknown_effects,
            "unanalysed_regions": [
                {"scope": "backend_relation", "reason": item["reason"],
                 "raw_relation": item["raw_relation"]} for item in unmapped
            ] + [
                {"scope": "pre_registered_unknown", "reason": item}
                for item in expectations.get("unknown", [])
            ] + [
                {"scope": "study_unknown", "reason": item}
                for item in study.get("unknowns", [])
            ],
        },
        "completion": {
            "status": "partial", "stage": "langflow_contract_projection",
            "failure_categories": ["bounded_unknown_effects", "path_feasibility_not_evaluated"],
            "error": None,
        },
        "semantic_validation": {
            "status": "verified_incomplete", "reviewer": "contract_projection",
            "evidence": (list(study.get("checks", []))
                         if isinstance(study.get("checks", []), list)
                         else [study.get("checks")]),
            "notes": "typed bounded evidence projected; independent review remains separate",
        },
        "cost": _cost(cost, files, len(relations)),
    }
    validate_result_contract(contract)
    return contract
