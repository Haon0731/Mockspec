#!/usr/bin/env python3
"""Project the reviewed Jib replay into the strict backward-slice clue contract."""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import sys
from pathlib import Path
from typing import Any


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(HERE.parent))

from mock_spec.adapters.frozen_dataset import FrozenDataset  # type: ignore[import-untyped]  # noqa: E402
from slice_contract import SliceClueContract, validate_source_quotes  # noqa: E402

from jib_observation_adapter import (  # noqa: E402
    adapt_jib_observations,
    validate_adapter_observations,
)


CASE_ID = (
    "com.google.cloud.tools.jib.cli.jar.JarFilesTest#"
    "testToJibContainerBuilder_explodedStandard_basicInfo@69ae66df"
)
DATASET = "java-bechmark-strong"
INVENTORY_REVISION = "e8ed1a717304ae6437602a4da6389aae46d96f4a805bc140883d0a626742811f"
SNAPSHOT_ID = "5b93995c794af0152854a01ddda2fe77948b5ab828b05282b00728f7729f64d4"
MUT_PATH = "src/main/java/com/google/cloud/tools/jib/cli/jar/JarFiles.java"
BASELINE = ROOT / "out/mockbench-study/issue25-jib-observations-all-none-20261111-r6"
PRIOR_PROVENANCE = (
    ROOT / "out/mockbench-study/entry-repair-20261111/java-targeted-r6/semantic/"
    "2a37d7a1edfad4e10a7d/input-provenance.json"
)
REVIEW = ROOT / "out/mockbench-study/entry-repair-20261111/verification/jib-route-review-r5.md"
EXPECTED_ARCHIVE_SHA256 = "120cc34fe59dd4dba8a32320c2287204e047136de0c50e88513b631a0d77392e"
EXPECTED_RAW_SHA256 = "46612e7f0e9e59e03ec0effb4217beae21c5f0b87d21e73c1db9c6d7d4fa5626"
EXPECTED_TEST_SHA256 = "19e800bf8967238ef3b7772278e476186e509051e5021067b1f7234de0f68ed0"
EXPECTED_MUT_SHA256 = "ff8ee22a4aa1bce7d61024a2d06fcb7983abc3e92751683cee598a92a0020140"


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_text(value: str) -> str:
    return _sha256_bytes(value.encode("utf-8"))


def _source_reference(source_id: str, text: str, start: int, end: int) -> dict[str, Any]:
    lines = text.splitlines()
    selected = lines[start - 1:end]
    if len(selected) != end - start + 1:
        raise AssertionError("source evidence range is outside frozen text")
    start_column = len(selected[0].encode()) - len(selected[0].lstrip().encode())
    end_column = len(selected[-1].encode())
    quote_lines = [selected[0].lstrip(), *selected[1:]]
    return {
        "source_id": source_id,
        "source_sha256": _sha256_text(text),
        "start_line": start,
        "start_column": start_column,
        "end_line": end,
        "end_column": end_column,
        "quote": "\n".join(quote_lines),
    }


def _authenticate_replay() -> tuple[dict[str, Any], str]:
    archive = BASELINE / "analysis.json.gz"
    archive_bytes = archive.read_bytes()
    if _sha256_bytes(archive_bytes) != EXPECTED_ARCHIVE_SHA256:
        raise AssertionError("reviewed Jib raw archive identity changed")
    raw_bytes = gzip.decompress(archive_bytes)
    if _sha256_bytes(raw_bytes) != EXPECTED_RAW_SHA256:
        raise AssertionError("reviewed Jib raw analysis identity changed")
    compression = json.loads((BASELINE / "analysis-compression.json").read_text())
    if (
        compression["archive_sha256"] != EXPECTED_ARCHIVE_SHA256
        or compression["original_sha256"] != EXPECTED_RAW_SHA256
        or compression["restored_sha256"] != EXPECTED_RAW_SHA256
    ):
        raise AssertionError("reviewed Jib compression receipt differs from raw replay")
    raw = json.loads(raw_bytes)
    if raw.get("target_executed") is not False or len(raw.get("observations", [])) != 19:
        raise AssertionError("reviewed Jib raw proof scope changed")
    base_image = raw["observations"][0]
    if base_image.get("criterion") != (
        "com.google.cloud.tools.jib.cli.jar.ObservationSink.baseImage(Ljava/lang/Object;)V"
    ):
        raise AssertionError("reviewed Jib base-image observation changed")
    statements = base_image.get("statements", [])
    required_fragments = (
        "JarFiles.toJibContainerBuilder",
        "JibContainerBuilder.toContainerBuildPlan",
        "ContainerBuildPlan.getBaseImage",
        "ContainerBuildPlan, baseImage",
    )
    joined = "\n".join(
        f"{row.get('method', '')} {row.get('statement', '')} {row.get('instruction', '')}"
        for row in statements
    )
    if any(fragment not in joined for fragment in required_fragments):
        raise AssertionError("reviewed Jib raw proof lost a required state/value witness")
    if base_image.get("opaque_expansion_truncated") is not False:
        raise AssertionError("reviewed Jib base-image replay is truncated")
    return raw, _sha256_bytes(REVIEW.read_bytes())


def build_contract(registry_root: Path) -> SliceClueContract:
    raw, review_sha256 = _authenticate_replay()
    dataset = FrozenDataset(
        registry_root, dataset=DATASET, inventory_revision=INVENTORY_REVISION,
    )
    with dataset.load_case(CASE_ID) as case:
        if case.snapshot_id != SNAPSHOT_ID or case.language != "java":
            raise AssertionError("frozen Jib case identity changed")
        test_text = case.masked_test.text
        test_path = case.masked_test.logical_path
        mut = case.read_source(MUT_PATH)
        mut_text = mut.text
        mut_path = mut.logical_path
    if _sha256_text(test_text) != EXPECTED_TEST_SHA256:
        raise AssertionError("frozen Jib masked test identity changed")
    if _sha256_text(mut_text) != EXPECTED_MUT_SHA256:
        raise AssertionError("frozen Jib MUT identity changed")

    prior = json.loads(PRIOR_PROVENANCE.read_text())
    prior_sources = {row["logical_path"]: row["sha256"] for row in prior["sources"]}
    if prior_sources.get(test_path) != EXPECTED_TEST_SHA256:
        raise AssertionError("prior reviewed manifest no longer binds the Jib masked test")
    if prior_sources.get(mut_path) != EXPECTED_MUT_SHA256:
        raise AssertionError("prior reviewed manifest no longer binds the Jib MUT")

    adapter = adapt_jib_observations(test_text)
    registration = json.loads((BASELINE / "pre-registration.json").read_text())
    validate_adapter_observations(adapter, registration["observations"])
    criteria = adapter["criteria"]
    if len(criteria) != 19:
        raise AssertionError("source-derived Jib observations changed")

    sources = [
        {"source_id": "test", "logical_path": test_path,
         "sha256": EXPECTED_TEST_SHA256, "start_line": 1,
         "end_line": len(test_text.splitlines())},
        {"source_id": "mut", "logical_path": mut_path,
         "sha256": EXPECTED_MUT_SHA256, "start_line": 1,
         "end_line": len(mut_text.splitlines())},
    ]
    evidence: list[dict[str, Any]] = []
    observations: list[dict[str, Any]] = []
    nodes: list[dict[str, Any]] = []
    unknowns: list[dict[str, Any]] = []
    for item in criteria:
        evidence_id = f"ev:source:{item['observation_id']}"
        evidence.append({
            "evidence_id": evidence_id,
            "kind": "source",
            "source": _source_reference(
                "test", test_text, item["start_line"], item["end_line"],
            ),
            "rule": None,
            "provenance": "FrozenDataset source-derived JUnit observation adapter",
        })
        observations.append({
            "observation_id": item["observation_id"],
            "kind": "value",
            "expression": item["expression"],
            "evidence_id": evidence_id,
        })
        nodes.append({
            "node_id": f"node:observation:{item['observation_id']}",
            "kind": "observation",
            "context_ids": ["context:jib-reviewed-replay"],
            "evidence_id": evidence_id,
            "relevance": "criterion",
        })
        unknown_id = f"unknown:{item['observation_id']}"
        description = (
            "Dependency responses and path feasibility remain unknown; the reviewed replay "
            "supports a conditional base-image influence clue but not the concrete value."
            if item["observation_id"] == "criterion:0" else
            "No source-linked relation for this observation passed the bounded reviewed-replay gate."
        )
        unknowns.append({
            "unknown_id": unknown_id,
            "kind": "unresolved_dependency_or_relation",
            "description": description,
            "affects_observation_ids": [item["observation_id"]],
            "context_ids": ["context:jib-reviewed-replay"],
            "evidence_ids": [evidence_id, "ev:rule:conservative-boundary"],
        })

    evidence.extend([
        {"evidence_id": "ev:source:mut-call", "kind": "source",
         "source": _source_reference("test", test_text, 62, 68), "rule": None,
         "provenance": "FrozenDataset exact test call binding"},
        {"evidence_id": "ev:source:plan-binding", "kind": "source",
         "source": _source_reference("test", test_text, 69, 69), "rule": None,
         "provenance": "FrozenDataset exact returned-builder observation binding"},
        {"evidence_id": "ev:source:base-image-branches", "kind": "source",
         "source": _source_reference("mut", mut_text, 59, 72), "rule": None,
         "provenance": "FrozenDataset visible base-image source alternatives"},
        {"evidence_id": "ev:source:return-builder", "kind": "source",
         "source": _source_reference("mut", mut_text, 93, 93), "rule": None,
         "provenance": "FrozenDataset visible MUT return"},
        {"evidence_id": "ev:binding:reviewed-raw-replay", "kind": "binding", "source": None,
         "rule": (
             f"reviewed_replay raw_sha256={EXPECTED_RAW_SHA256}; archive_sha256="
             f"{EXPECTED_ARCHIVE_SHA256}; exact JarFiles return, toContainerBuildPlan, "
             "baseImage field Mod/Ref, getter, and observation-sink witnesses are present"
         ),
         "provenance": "authenticated archived WALA raw proof; not a fresh generic-backend run"},
        {"evidence_id": "ev:rule:conservative-boundary", "kind": "rule", "source": None,
         "rule": "absence of an authenticated reviewed relation is reported as UNKNOWN",
         "provenance": "bounded frontend conservative publication rule"},
        {"evidence_id": "ev:rule:rejected-jib-routes", "kind": "rule", "source": None,
         "rule": (
             "archived Jib criterion:10 and criterion:15 generic routes lack exact matched "
             "call/return transition authentication and are not published as relations"
         ),
         "provenance": (
             f"reviewed route rejection sha256={review_sha256}; missing-evidence exclusion, "
             "not fresh proof of corrected chronology"
         )},
    ])
    nodes.extend([
        {"node_id": "node:base-image-source", "kind": "condition",
         "context_ids": ["context:jib-reviewed-replay"],
         "evidence_id": "ev:source:base-image-branches", "relevance": "value_source"},
        {"node_id": "node:return-builder", "kind": "return",
         "context_ids": ["context:jib-reviewed-replay"],
         "evidence_id": "ev:source:return-builder", "relevance": "call_boundary"},
    ])
    payload = {
        "schema_version": "mock-spec.slice-evidence/v1",
        "identity": {"case_id": CASE_ID, "dataset": DATASET, "language": "java",
                     "snapshot_id": SNAPSHOT_ID,
                     "entry_source": "diagnostic_override",
                     "analysis_version": "java-reviewed-replay-slice/v1"},
        "sources": sources,
        "contexts": [{
            "context_id": "context:jib-reviewed-replay",
            "label": "original Jib test path under reviewed archived WALA evidence",
            "input_facts": {"origin": "reviewed_replay", "fresh_generic_backend": False,
                            "target_executed": False, "observations": 19},
            "evidence_ids": ["ev:source:mut-call", "ev:source:plan-binding",
                             "ev:binding:reviewed-raw-replay"],
        }],
        "evidence": evidence,
        "observations": observations,
        "slice_nodes": nodes,
        "premises": [
            {"premise_id": "premise:normal-completion", "kind": "normal_completion",
             "expression": "JarFiles.toJibContainerBuilder and toContainerBuildPlan complete normally",
             "polarity": True, "context_ids": ["context:jib-reviewed-replay"],
             "evidence_ids": ["ev:source:mut-call", "ev:source:plan-binding",
                              "ev:binding:reviewed-raw-replay"], "status": "conditional"},
            {"premise_id": "premise:returned-object-identity", "kind": "object_identity",
             "expression": "the returned containerBuilder is the receiver of toContainerBuildPlan",
             "polarity": True, "context_ids": ["context:jib-reviewed-replay"],
             "evidence_ids": ["ev:source:mut-call", "ev:source:plan-binding",
                              "ev:source:return-builder", "ev:binding:reviewed-raw-replay"],
             "status": "conditional"},
        ],
        "relations": [{
            "relation_id": "relation:jib-base-image-state",
            "kind": "state", "from_node_id": "node:base-image-source",
            "to_node_id": "node:observation:criterion:0",
            "observation_ids": ["criterion:0"],
            "context_id": "context:jib-reviewed-replay",
            "premise_ids": ["premise:normal-completion", "premise:returned-object-identity"],
            "evidence_ids": ["ev:source:base-image-branches", "ev:source:return-builder",
                             "ev:source:mut-call", "ev:source:plan-binding",
                             "ev:binding:reviewed-raw-replay"],
            "status": "conditional",
        }],
        "unknowns": unknowns,
        "exclusions": [
            {"exclusion_id": "exclusion:jib-criterion-10-unmatched-routes",
             "kind": "unauthenticated_cross_context_routes",
             "description": (
                 "Nine archived generic routes to criterion:10 are excluded; their replay lacks "
                 "the exact transition metadata required for a relation."
             ), "evidence_ids": ["ev:source:criterion:10", "ev:rule:rejected-jib-routes"]},
            {"exclusion_id": "exclusion:jib-criterion-15-unmatched-routes",
             "kind": "unauthenticated_cross_context_routes",
             "description": (
                 "Nine archived generic routes to criterion:15 are excluded; their replay lacks "
                 "the exact transition metadata required for a relation."
             ), "evidence_ids": ["ev:source:criterion:15", "ev:rule:rejected-jib-routes"]},
        ],
        "coverage": {"status": "partial",
                     "declared_scope": (
                         "19 original Jib observations; one reviewed replay relation; all other "
                         "relations remain unknown"
                     ),
                     "included_context_ids": ["context:jib-reviewed-replay"],
                     "unknown_ids": [row["unknown_id"] for row in unknowns]},
        "cost": {"wall_seconds": None, "model_requests": 0, "target_executions": 0,
                 "note": "offline projection of authenticated frozen sources and reviewed raw replay"},
        "semantic_claim": "clues_only_not_semantic_proof",
    }
    contract = SliceClueContract.model_validate(payload)
    validate_source_quotes(contract, {"test": test_text, "mut": mut_text})
    # Keep the raw object live until after validation so a malformed replay cannot
    # be replaced by summary-only status flags.
    assert len(raw["observations"]) == len(contract.observations)
    return contract


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--registry-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    contract = build_contract(args.registry_root)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(contract.model_dump_json(indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
