"""Bounded batch bridge for the independently accepted Java semantic backend.

Only the exact frozen Jib case is supported.  Other Java cases get an explicit
unsupported result; the generic candidate runner remains responsible for their
diagnostic attempt.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from typing import Any


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(HERE.parent))

from mock_spec.adapters.frozen_dataset import FrozenDataset
from result_contract import validate_result_contract

from jib_observation_adapter import adapt_jib_observations, validate_adapter_observations


SCHEMA = "mockspec.java-batch-semantic-backend/v1"
SUPPORTED = {
    "language": "java",
    "dataset": "java-bechmark-strong",
    "case_id": (
        "com.google.cloud.tools.jib.cli.jar.JarFilesTest#"
        "testToJibContainerBuilder_explodedStandard_basicInfo@69ae66df"
    ),
    "inventory_revision": "e8ed1a717304ae6437602a4da6389aae46d96f4a805bc140883d0a626742811f",
    "snapshot_id": "5b93995c794af0152854a01ddda2fe77948b5ab828b05282b00728f7729f64d4",
}
MUT_PATH = "src/main/java/com/google/cloud/tools/jib/cli/jar/JarFiles.java"
PROCESSOR_PATH = (
    "src/main/java/com/google/cloud/tools/jib/cli/jar/StandardExplodedProcessor.java"
)
EXPECTED_SOURCE_SHA256 = {
    "test": "19e800bf8967238ef3b7772278e476186e509051e5021067b1f7234de0f68ed0",
    "mut": "ff8ee22a4aa1bce7d61024a2d06fcb7983abc3e92751683cee598a92a0020140",
    "processor": "6dc801606c0e36dffa9ad447324524430cb3e676dde044320900f08eb61e50e5",
}
EXPECTED_SELECTOR = (
    "com.google.cloud.tools.jib.cli.jar.JarFilesTest#"
    "testToJibContainerBuilder_explodedStandard_basicInfo"
)
BASELINE = ROOT / "out/mockbench-study/issue25-jib-observations-all-none-20261111-r6"
RUNNER = HERE / "run_java_demand_analysis.py"


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _write_json(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def _identity_matches(entry: dict[str, Any]) -> bool:
    return all(entry.get(key) == value for key, value in SUPPORTED.items())


def unsupported_result(entry: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema": SCHEMA,
        "status": "unsupported",
        "support_status": "unsupported_input_domain",
        "identity": {
            key: entry.get(key)
            for key in ("language", "dataset", "case_id", "inventory_revision", "snapshot_id")
        },
        "failure_categories": ["java_semantic_backend_input_unsupported"],
        "reason": (
            "The accepted Java backend currently prepares bytecode, library summaries, "
            "and source-derived observations only for the exact JarFiles Jib case."
        ),
        "backend_attempted": False,
        "candidate_diagnostic_required": True,
    }


def _load_supported_case(entry: dict[str, Any], registry_root: Path) -> dict[str, Any]:
    dataset = FrozenDataset(
        registry_root,
        dataset=entry["dataset"],
        inventory_revision=entry["inventory_revision"],
    )
    with dataset.load_case(entry["case_id"]) as case:
        if (
            case.language != entry["language"]
            or case.snapshot_id != entry["snapshot_id"]
            or case.target_selector != EXPECTED_SELECTOR
            or case._masking_contract != "java-dual-region-v1"
        ):
            raise ValueError("frozen Jib case context differs from the accepted input domain")
        mut = case.read_source(MUT_PATH)
        processor = case.read_source(PROCESSOR_PATH)
        sources = {
            "test": case.masked_test.text,
            "mut": mut.text,
            "processor": processor.text,
        }
        source_sha256 = {key: _sha256_text(text) for key, text in sources.items()}
        if source_sha256 != EXPECTED_SOURCE_SHA256:
            raise ValueError("frozen Jib visible source identity changed")
        registration = json.loads(
            (BASELINE / "pre-registration.json").read_text(encoding="utf-8")
        )
        adapter = adapt_jib_observations(case.masked_test.text)
        validate_adapter_observations(adapter, registration["observations"])
        return {
            "target_selector": case.target_selector,
            "masking_contract": case._masking_contract,
            "masked_test_sha256": source_sha256["test"],
            "masked_test_logical_path": case.masked_test.logical_path,
            "masked_test_bytes": len(case.masked_test.text.encode("utf-8")),
            "mut_logical_path": mut.logical_path,
            "mut_bytes": len(mut.text.encode("utf-8")),
            "processor_logical_path": processor.logical_path,
            "processor_bytes": len(processor.text.encode("utf-8")),
            "source_sha256": source_sha256,
            "adapter": adapter,
        }


def _project_contract(
    native: dict[str, Any], entry: dict[str, Any], context: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    contract = copy.deepcopy(native)
    original_identity = copy.deepcopy(contract["identity"])
    original_source_scope = copy.deepcopy(contract["source_scope"])
    original_completion = copy.deepcopy(contract["completion"])
    contract["identity"] = {
        "case_id": entry["case_id"],
        "dataset": entry["dataset"],
        "language": entry["language"],
        "snapshot_id": entry["snapshot_id"],
        "masking_contract": context["masking_contract"],
        "masked_test_sha256": context["masked_test_sha256"],
        "target_selector": context["target_selector"],
    }
    contract["source_scope"] = [
        {
            "source_id": "test",
            "logical_path": context["masked_test_logical_path"],
            "sha256": context["source_sha256"]["test"],
            "bytes": context["masked_test_bytes"],
            "selection_role": "masked_test",
        },
        {
            "source_id": "mut",
            "logical_path": context["mut_logical_path"],
            "sha256": context["source_sha256"]["mut"],
            "bytes": context["mut_bytes"],
            "selection_role": "candidate_product",
        },
        {
            "source_id": "processor",
            "logical_path": context["processor_logical_path"],
            "sha256": context["source_sha256"]["processor"],
            "bytes": context["processor_bytes"],
            "selection_role": "supporting_visible_source",
        },
    ]
    contract["entry_point"]["test_selection"] = {
        "origin": "task_provided",
        "status": "unique",
        "selector": context["target_selector"],
        "evidence": [context["masked_test_sha256"]],
    }
    if contract["completion"].get("failure_categories") != [
        "original_observation_mapping_remains_diagnostic_override"
    ]:
        raise ValueError("native completion no longer has the registered adapter limitation")
    contract["completion"] = {
        **contract["completion"],
        "stage": "jib_observation_state_mapping_with_automatic_source_adapter",
        "failure_categories": [],
    }
    diagnostic_assumption = (
        "diagnostic observation mapping is manual; issue21 supplies automatic MUT binding"
    )
    assumptions = contract["analysis_boundaries"]["assumptions"]
    if assumptions != [diagnostic_assumption]:
        raise ValueError("native contract no longer has the registered manual-mapping assumption")
    contract["analysis_boundaries"]["assumptions"] = []

    adapter_criteria = context["adapter"]["criteria"]
    adapter_observations = [
        (
            item["observation_id"], item["start_line"], item["end_line"],
            item["expression"],
        )
        for item in adapter_criteria
    ]
    native_observations = [
        (
            item["observation_id"], item["location"]["start_line"],
            item["location"]["end_line"], item.get("expression"),
        )
        for item in contract["observations"]
    ]
    # The common contract omits expression. Compare identity/range separately and
    # preserve the exact expression list in the projection receipt.
    if [row[:3] for row in adapter_observations] != [row[:3] for row in native_observations]:
        raise ValueError("native contract observations differ from current masked test adapter")
    adapter_by_id = {item["observation_id"]: item for item in adapter_criteria}
    for observation in contract["observations"]:
        derived = adapter_by_id[observation["observation_id"]]
        observation["provenance"] = {
            "origin": "automatic_source_derived_observation_adapter",
            "backend": context["adapter"]["parse_provenance"]["backend"],
            "source_expression": derived["expression"],
            "sink": derived["sink"],
            "parse_provenance": derived["parse_provenance"],
            "expected_literals_used": context["adapter"]["parse_provenance"][
                "expected_literals_used"
            ],
            "target_executed": context["adapter"]["parse_provenance"]["target_executed"],
        }
    observation_ids = {row[0] for row in adapter_observations}
    for relation in contract["relations"]:
        if not set(relation["observation_ids"]) <= observation_ids:
            raise ValueError("native relation references a non-current observation")
    contract["cost"]["input_size"] = {
        "files": len(contract["source_scope"]),
        "bytes": sum(item["bytes"] for item in contract["source_scope"]),
    }
    validate_result_contract(contract)
    receipt = {
        "schema": "mockspec.java-case-context-projection/v1",
        "status": "verified",
        "original_identity": original_identity,
        "projected_identity": contract["identity"],
        "original_source_scope": original_source_scope,
        "projected_source_scope": contract["source_scope"],
        "original_completion": original_completion,
        "projected_completion": contract["completion"],
        "removed_analysis_boundary_assumption": diagnostic_assumption,
        "changed_fields": [
            "identity.dataset", "identity.masking_contract", "identity.target_selector",
            "source_scope.test", "source_scope.processor", "completion.stage",
            "completion.failure_categories",
            "analysis_boundaries.assumptions", "observations.provenance", "cost.input_size",
        ],
        "reason": (
            "The accepted backend used a descriptive dataset label and an unmasked "
            "test source record. The batch contract is rebound to the authenticated "
            "FrozenCase; semantic relations are retained only after all 19 current "
            "source-derived observation IDs and ranges match. The obsolete diagnostic "
            "adapter limitation is removed only because this automatic reconstruction "
            "and the registered observation table agree exactly."
        ),
        "adapter": {
            "status": context["adapter"]["status"],
            "masked_test_sha256": context["adapter"]["original_test_sha256"],
            "generated_entry_sha256": context["adapter"]["generated_entry_sha256"],
            "mut_call": context["adapter"]["mut_call"],
            "plan_binding": context["adapter"]["plan_binding"],
            "observation_count": len(adapter_criteria),
            "parse_provenance": context["adapter"]["parse_provenance"],
            "observations": [
                {"observation_id": row[0], "start_line": row[1],
                 "end_line": row[2], "expression": row[3]}
                for row in adapter_observations
            ],
        },
        "relations": {
            "count": len(contract["relations"]),
            "all_observation_references_current": True,
            "relation_endpoints_rewritten": False,
        },
    }
    return contract, receipt


def run_supported(entry: dict[str, Any], registry_root: Path, output: Path) -> dict[str, Any]:
    context = _load_supported_case(entry, registry_root)
    backend_output = output / "backend-run"
    cache = output / "fresh-cache"
    command = [
        sys.executable, str(RUNNER), "--baseline", str(BASELINE),
        "--cache", str(cache), "--output", str(backend_output),
        "--roots", "all-observations", "--max-units", "19",
        "--max-depth", "2", "--max-method-pdgs", "20000",
    ]
    completed = subprocess.run(command, capture_output=True, check=False, text=True)
    (output / "backend.stdout").write_text(completed.stdout, encoding="utf-8")
    (output / "backend.stderr").write_text(completed.stderr, encoding="utf-8")
    if completed.returncode != 0:
        raise RuntimeError(f"Jib demand backend exited {completed.returncode}")
    backend_result = json.loads((backend_output / "result.json").read_text(encoding="utf-8"))
    summary = json.loads((backend_output / "summary.json").read_text(encoding="utf-8"))
    if (
        backend_result.get("status") != "complete"
        or backend_result.get("target_executed") is not False
        or backend_result.get("cache", {}).get("executed") != 19
        or backend_result.get("cache", {}).get("hits") != 0
        or backend_result.get("equivalence", {}).get("full_semantic_summary_equivalent") is not True
    ):
        raise RuntimeError("fresh Jib demand run did not satisfy the accepted full-run gate")
    native_contract = summary["result_contract"]
    validate_result_contract(native_contract)
    contract, projection = _project_contract(native_contract, entry, context)
    _write_json(output / "native-result-contract.json", native_contract)
    _write_json(output / "case-context-projection.json", projection)
    return {
        "schema": SCHEMA,
        "status": "complete",
        "support_status": "supported_and_executed",
        "backend_attempted": True,
        "target_executed": False,
        "identity": contract["identity"],
        "result_contract": contract,
        "provenance": {
            "backend": "WALA 0-1-CFA demand CISDG Jib observation backend",
            "backend_run": str(backend_output),
            "baseline": str(BASELINE),
            "baseline_role": "registered input preparation and semantic equivalence oracle",
            "current_visible_sources": context["source_sha256"],
            "processor_source": {
                "logical_path": context["processor_logical_path"],
                "sha256": context["source_sha256"]["processor"],
            },
            "case_context_projection": "case-context-projection.json",
            "native_result_contract": "native-result-contract.json",
        },
        "cost": backend_result["cost"],
        "failure_categories": contract["completion"]["failure_categories"],
        "semantic_completion_status": contract["completion"]["status"],
        "limitations": backend_result["limitations"],
    }


def execute(entry: dict[str, Any], registry_root: Path, output: Path) -> dict[str, Any]:
    if not _identity_matches(entry):
        return unsupported_result(entry)
    return run_supported(entry, registry_root, output)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--entry", required=True, type=Path)
    parser.add_argument("--registry-root", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    entry = json.loads(args.entry.read_text(encoding="utf-8"))
    if not isinstance(entry, dict):
        raise ValueError("entry must be an object")
    try:
        result = execute(entry, args.registry_root, args.output)
    except Exception as exc:
        result = {
            "schema": SCHEMA,
            "status": "failed",
            "support_status": "supported_backend_failed",
            "backend_attempted": True,
            "target_executed": False,
            "identity": {
                key: entry.get(key) for key in
                ("language", "dataset", "case_id", "inventory_revision", "snapshot_id")
            },
            "failure_categories": ["java_semantic_backend_failure"],
            "error": {"type": type(exc).__name__, "message": str(exc)},
        }
    _write_json(args.output / "batch-result.json", result)
    print(json.dumps({"status": result["status"], "support_status": result["support_status"]}))
    return 0 if result["status"] in {"partial", "complete", "unsupported"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
