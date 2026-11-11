"""Validate and map CodeQL facts composed with the authenticated dynamic-loader bridge."""
from __future__ import annotations

import csv
import hashlib
from pathlib import Path
from typing import Any


ROW_FIELDS = ("relation", "sourcePath", "sourceLine", "targetPath", "targetLine", "detail", "modality")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as stream:
        return list(csv.DictReader(stream))


def _row(relation: str, source_path: str, source_line: int, target_path: str,
         target_line: int, detail: str, modality: str) -> tuple[str, ...]:
    return (relation, source_path, str(source_line), target_path, str(target_line), detail, modality)


def expected_backend_rows(test_path: str, product_path: str,
                          function_name: str) -> set[tuple[str, ...]]:
    rows = {
        _row("product_function", product_path, 94, product_path, 118, function_name, "certain"),
        _row("test_dynamic_call_syntax", test_path, 104, product_path, 94,
             "frontend_bridge_required", "authenticated_external_bridge"),
        _row("test_call_result_to_observation", test_path, 104, test_path, 106,
             "criterion:0", "analysis_may"),
        _row("parameter_to_decrypt_argument", product_path, 94, product_path, 109,
             "argument:0", "analysis_may"),
        _row("parameter_to_return_value_use", product_path, 94, product_path, 115,
             "return_comparison", "analysis_may"),
        _row("product_global_reference", product_path, 116, product_path, 94,
             "InvalidToken", "analysis_may"),
        _row("dynamic_global_identity_observation", test_path, 115, product_path, 94,
             "frontend_namespace_bridge_required", "authenticated_external_bridge"),
        _row("product_dependency_call", product_path, 106, product_path, 94,
             "get_settings_service", "unknown_effect"),
        _row("product_dependency_call", product_path, 109, product_path, 94,
             "decrypt_api_key", "unknown_effect"),
        _row("settings_result_to_decrypt_argument", product_path, 106, product_path, 109,
             "argument:1", "analysis_may"),
        _row("decrypt_result_to_downstream_use", product_path, 109, product_path, 111,
             "guard", "analysis_may"),
        _row("decrypt_result_to_downstream_use", product_path, 109, product_path, 115,
             "return_comparison", "analysis_may"),
        _row("decrypt_exception_to_handler_return", product_path, 109, product_path, 118,
             "caught_path_candidate", "analysis_may"),
    }
    rows.update(_row("product_return", product_path, line, product_path, 94,
                     function_name, "analysis_may") for line in (104, 112, 115, 118))
    rows.update(_row("guard_controls_product_return", product_path, source, product_path, target,
                     truth, "analysis_may") for source, target, truth in (
                         (103, 104, "true"), (103, 112, "false"), (103, 115, "false"),
                         (103, 118, "false"), (111, 112, "true"), (111, 115, "false"),
                     ))
    return rows


def _validate_frontend_proof(manifest: dict[str, Any], expectations: dict[str, Any]) -> None:
    proof = manifest.get("invalid_token_identity_binding")
    registered = expectations.get("invalid_token_identity")
    if not isinstance(proof, dict) or not isinstance(registered, dict) or proof != registered:
        raise ValueError("authenticated InvalidToken identity provenance differs from registration")


def _structured_unknowns(test_path: str, product_path: str, function_name: str) -> list[dict[str, Any]]:
    return [
        {
            "boundary": {"path": product_path, "line": 106, "call": "get_settings_service"},
            "normal_return": "may supply decrypt_api_key argument 1 and thereby influence criterion:0",
            "exception": "may prevent the product call from returning and criterion:0 from being reached",
            "state_alias_scope": "returned service object and externally reachable state are unknown",
        },
        {
            "boundary": {"path": product_path, "line": 109, "call": "auth_utils.decrypt_api_key"},
            "normal_return": "may influence guard line 111 and comparison return line 115",
            "exception": (
                "caught listed exceptions may select handler return line 118; other exceptions may prevent "
                "the product call from returning"
            ),
            "state_alias_scope": "reachable argument, settings-service, and auth-utils state effects are unknown",
        },
        {
            "boundary": {"path": test_path, "line": 104, "call": function_name},
            "normal_return": "may flow to criterion:0 at line 106",
            "exception": "may prevent criteria at lines 106 and 115 from being reached",
            "state_alias_scope": "dynamic callable effects beyond visible product facts are unknown",
        },
    ]


def map_result(manifest: dict[str, Any], expectations: dict[str, Any],
               rows: list[dict[str, str]]) -> dict[str, Any]:
    binding = manifest["dynamic_loader_binding"]
    if binding["path"] != expectations["product_path"] or binding["function_key"] != expectations["function"]["name"]:
        raise ValueError("authenticated loader origin differs from registered root")
    if binding["call_sites"] != [{"name": expectations["function"]["name"],
                                   "line": expectations["loader"]["call_line"]}]:
        raise ValueError("authenticated loader call was rebound or is ambiguous")
    _validate_frontend_proof(manifest, expectations)
    test_files = [item for item in manifest["files"] if item.get("role") in (None, "masked_test")]
    if len(test_files) != 1:
        raise ValueError("masked test path is missing or ambiguous")
    test_path = test_files[0]["logical_path"]
    expected_rows = expected_backend_rows(
        test_path, expectations["product_path"], expectations["function"]["name"]
    )
    actual_rows: list[tuple[str, ...]] = []
    for row in rows:
        if set(row) != set(ROW_FIELDS) or any(row[field] is None for field in ROW_FIELDS):
            raise ValueError("backend row schema differs from registration")
        actual_rows.append(tuple(row[field] for field in ROW_FIELDS))
    if len(actual_rows) != len(set(actual_rows)):
        raise ValueError("backend returned duplicate evidence rows")
    actual_set = set(actual_rows)
    if actual_set != expected_rows:
        raise ValueError(
            f"backend evidence contract differs; missing={sorted(expected_rows - actual_set)}; "
            f"unexpected={sorted(actual_set - expected_rows)}"
        )

    raw_relations = [{field: row[field] for field in ROW_FIELDS} for row in rows]
    relations: list[dict[str, Any]] = [
        {
            **row,
            "observation_ids": [row["detail"]] if row["detail"].startswith("criterion:") else [],
            "provenance": (
                "authenticated_loader_bridge_plus_exact_codeql_endpoint"
                if row["modality"] == "authenticated_external_bridge" else "CodeQL 2.27.4"
            ),
            "assumptions": (["CodeQL did not resolve the exec-created callable"]
                            if row["modality"] == "authenticated_external_bridge" else []),
        }
        for row in raw_relations
    ]
    for line in (104, 112, 115, 118):
        relations.append({
            "kind": "normal_return_dependence", "modality": "analysis_may",
            "from": {"path": expectations["product_path"], "line": line},
            "to": {"path": test_path, "line": 106}, "detail": "criterion:0",
            "observation_ids": ["criterion:0"],
            "provenance": "CodeQL return + authenticated loader callable + CodeQL test result local flow",
            "assumptions": ["the authenticated exec-created callable is invoked at test line 104"],
        })
    for kind, line, detail, evidence in (
        ("value_flow", 106, "get_settings_service normal result may influence criterion:0",
         "CodeQL flow 106->109 plus downstream decrypt flows and product return composition"),
        ("value_flow", 109, "decrypt_api_key normal result may influence criterion:0",
         "CodeQL flows 109->111/115 plus product return composition"),
        ("exception_dependence", 109, "decrypt_api_key caught exception may influence criterion:0",
         "CodeQL exceptional successor candidate 109->118 plus product return composition"),
    ):
        relations.append({
            "kind": kind, "modality": "analysis_may",
            "from": {"path": expectations["product_path"], "line": line},
            "to": {"path": test_path, "line": 106}, "detail": detail,
            "observation_ids": ["criterion:0"], "provenance": evidence,
            "assumptions": [
                "the relevant normal or caught-exception path reaches a product return",
                "external values, exceptions, and state effects remain unknown",
            ],
        })
    relations.append({
        "kind": "value_flow", "modality": "analysis_may",
        "from": {"path": expectations["product_path"], "line": 116},
        "to": {"path": test_path, "line": 115}, "detail": "criterion:1",
        "observation_ids": ["criterion:1"],
        "provenance": (
            "CodeQL global reference + exact loader namespace/tuple/destructuring proof + "
            "exact CodeQL identity-expression endpoint"
        ),
        "assumptions": [
            "no unsupported runtime mutation occurs outside authenticated visible intervals",
            "this is a may-influence chain, not proof of the runtime identity outcome",
        ],
    })
    return {
        "schema": "mock-spec.langflow-backend-study/v1", "status": "verified_incomplete",
        "identity": {key: manifest[key] for key in
                     ("case_id", "dataset", "snapshot_id", "target_selector", "masking_contract")},
        "entry_point": {
            "function_origin": {"origin": "automatic_visible_loader", "status": "unique",
                                "evidence": [binding]},
            "invalid_token_identity_origin": manifest["invalid_token_identity_binding"],
            "backend_resolution": (
                "authenticated frontend origin plus exact CodeQL endpoints; CodeQL did not resolve exec"
            ),
        },
        "observations": expectations["observations"], "relations": relations,
        "raw_backend_relations": raw_relations,
        "unknown_effects": _structured_unknowns(
            test_path, expectations["product_path"], expectations["function"]["name"]
        ),
        "unknowns": expectations["unknown"] + [
            "The bridge establishes conditional may-influence chains, not concrete runtime values.",
            "CodeQL return/control facts do not establish complete path feasibility.",
        ],
        "checks": {
            "backend_evidence_rows_exact": len(expected_rows), "criterion0_result_flow": True,
            "criterion0_dependency_paths": ["106->109", "109->111/115", "109~exception->118"],
            "criterion1_exact_frontend_and_backend_expression_composed": True,
        },
    }
