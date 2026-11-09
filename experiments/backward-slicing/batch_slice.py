"""Frozen MockBench test-goal backward candidate slicing (no target execution)."""
from __future__ import annotations

import argparse
import asyncio
from collections import Counter
import hashlib
import json
import math
import os
from pathlib import Path
import sys
import tempfile
import time

from criteria import extract_criteria
from source_selection import select_sources
from mock_spec.adapters.frozen_dataset import FrozenDataset
from mock_spec.analysis_process import communicate
from mock_spec.java_analysis import _backend_paths
from mock_spec.validation import decode_json

HERE = Path(__file__).resolve().parent
PROFILE = "test-goal-backward-candidates/v1"


def write(path: Path, value: dict) -> None:
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")


def evidence_summary(graph: dict) -> dict:
    """Describe graph reach, never certify dependency or Mock correctness."""
    nodes = {node["id"]: node for node in graph["nodes"]}
    incoming: dict[str, list[str]] = {}
    for edge in graph["edges"]:
        incoming.setdefault(edge["to"], []).append(edge["from"])
    per_seed = []
    for seed in graph["seeds"]:
        seen = {seed}
        queue = [seed]
        while queue:
            for prior in incoming.get(queue.pop(), []):
                if prior not in seen:
                    seen.add(prior)
                    queue.append(prior)
        selected = [nodes[key] for key in sorted(seen)]
        boundaries = [node["id"] for node in selected if any(node.get(flag) for flag in (
            "contains_unresolved_call", "contains_unknown_call", "contains_boundary_call"))]
        per_seed.append({
            "seed": seed, "node_ids": sorted(seen),
            "product_source_candidates": sorted({node["source_id"] for node in selected
                                                  if node["source_id"] != "test"}),
            "unknown_call_nodes": boundaries,
        })
    return {
        "nodes": len(nodes), "edges": len(graph["edges"]),
        "edge_kinds": dict(Counter(edge["kind"] for edge in graph["edges"])),
        "criteria": per_seed,
        "seed_bindings": graph.get("seed_bindings", []),
        "reaches_product_candidate": any(row["product_source_candidates"] for row in per_seed),
        "has_unknown_call": any(row["unknown_call_nodes"] for row in per_seed),
        "semantic_validation": "not_evaluated",
        "mock_boundary_identification": "unknown_calls_are_not_confirmed_mocks",
    }


async def backend(request: dict, language: str, work: str, java_command: list[str] | None,
                  seconds: float) -> dict:
    command = java_command if language == "java" else [sys.executable, str(HERE / "python_probe.py")]
    if command is None:
        raise RuntimeError("backend_unavailable: pinned Spoon/JDK or helper compilation")
    code, stdout, stderr = await communicate(
        command, input_bytes=json.dumps(request).encode(), timeout_seconds=seconds,
        cwd=work, max_output_bytes=12_000_000,
    )
    if code:
        detail = stderr.decode("utf-8", "replace")[-5000:]
        raise RuntimeError(f"backend_exit_{code}: {detail}")
    graph = decode_json(stdout.decode())
    if graph.get("status") != "ok":
        raise ValueError("backend did not return an ok graph")
    return graph


def failure_category(exc: Exception) -> str:
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError)):
        return "timeout"
    message = str(exc)
    if "limit exceeded" in message or isinstance(exc, OverflowError):
        return "resource_limit"
    if "backend_unavailable" in message:
        return "backend_unavailable"
    if "already defined" in message:
        return "duplicate_type_input"
    if "ambiguous" in message and "criterion" in message:
        return "criterion_binding_ambiguous"
    if "SyntaxError" in message or "syntax" in message.lower() or "SpoonException" in message:
        return "parse_or_model_error"
    return "analysis_error"


async def run_case(case, *, work: str, java_command, seconds: float,
                   max_files: int, max_bytes: int) -> dict:
    started = time.monotonic()
    result = {"status": "unknown", "failure_categories": [], "stage": "criteria"}
    try:
        criteria = extract_criteria(case.masked_test.text, case.language, case.target_selector)
        for index, item in enumerate(criteria["criteria"]):
            item["criterion_id"] = f"criterion:{index}"
        result["target_extraction"] = criteria
        if not criteria["criteria"]:
            result["failure_categories"].append("no_visible_criterion")
            return result
        result["stage"] = "source_selection"
        selection = select_sources(case, max_files=max_files, max_bytes=max_bytes)
        sources = selection.pop("sources")
        result["source_selection"] = selection
        result["sources"] = [{
            "source_id": source["source_id"], "logical_path": source["logical_path"],
            "sha256": hashlib.sha256(source["text"].encode()).hexdigest(),
            "bytes": len(source["text"].encode()),
        } for source in sources]
        if selection.get("truncated"):
            result["failure_categories"].append("source_budget")
        if selection.get("source_rejections"):
            result["failure_categories"].append("empty_support_source_omitted")
        if len(sources) == 1:
            result["failure_categories"].append("product_source_not_located")
        if all(item["confidence"] == "candidate" for item in criteria["criteria"]):
            result["failure_categories"].append("inferred_criterion_only")
        request = {
            "sources": sources,
            "seeds": [{"source_id": "test", **item} for item in criteria["criteria"]],
            "opaque_methods": selection.get("opaque_methods", []),
            "dependency_only": True,
        }
        result["stage"] = "backward_graph"
        graph = await backend(request, case.language, work, java_command, seconds)
        result["graph"] = graph
        result["evidence"] = evidence_summary(graph)
        if not result["evidence"]["reaches_product_candidate"]:
            result["failure_categories"].append("test_to_product_not_reached")
        if result["evidence"]["has_unknown_call"]:
            result["failure_categories"].append("unresolved_calls")
        result["failure_categories"].append("semantic_analysis_gap")
        result.update(status="partial", stage="complete")
    except Exception as exc:
        category = failure_category(exc)
        result.update(status="timeout" if category == "timeout" else "failed",
                      error={"type": type(exc).__name__, "message": str(exc)[:6000]})
        result["failure_categories"].append(category)
    finally:
        result["elapsed_seconds"] = round(time.monotonic() - started, 4)
    return result


def summarize(rows: list[dict]) -> dict:
    def counts(subset):
        return {
            "total": len(subset), "status": dict(Counter(row["status"] for row in subset)),
            "failure_categories": dict(Counter(category for row in subset
                                               for category in set(row["failure_categories"]))),
            "with_product_candidate": sum(row.get("evidence", {}).get("reaches_product_candidate", False)
                                          for row in subset),
        }
    return {"profile": PROFILE, "counts": counts(rows),
            "by_language": {lang: counts([r for r in rows if r["identity"]["language"] == lang])
                            for lang in ("java", "python")},
            "interpretation": "Partial means candidate graph obtained, not correct semantic slicing or Mock recovery. Categories overlap.",
            "semantic_validation": "not_evaluated"}


async def run(inventory: dict, output: Path, *, seconds: float = 30,
              max_files: int = 8, max_bytes: int = 200000,
              case_ids: list[str] | None = None) -> dict:
    if inventory.get("input_policy") != "frozen-agent":
        raise ValueError("requires frozen-agent inventory")
    if not math.isfinite(seconds) or seconds <= 0 or max_files < 1 or max_bytes < 1:
        raise ValueError("budgets must be positive and finite")
    entries = inventory["cases"]
    identities = [(row["dataset"], row["case_id"]) for row in entries]
    if len(identities) != len(set(identities)):
        raise ValueError("duplicate inventory identity")
    if case_ids and not set(case_ids) <= {row["case_id"] for row in entries}:
        raise ValueError("unknown selected case ID")
    output.mkdir(parents=True, exist_ok=False)
    write(output / "inventory.json", inventory)
    implementation = [HERE / name for name in (
        "batch_slice.py", "criteria.py", "source_selection.py", "survey_targets.py",
        "python_probe.py", "SliceProbe.java")]
    root = HERE.parents[1]
    implementation.extend(root / "src/mock_spec" / name for name in (
        "adapters/frozen_dataset.py", "validation.py", "analysis_process.py", "java_analysis.py"))
    manifest = {
        "profile": PROFILE, "configuration": {"backend_timeout_seconds": seconds,
            "max_files": max_files, "max_bytes": max_bytes, "case_ids": case_ids,
            "python_dependency_only": True, "java_heap_mb": 768},
        "implementation_sha256": {str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
                                   for path in implementation},
        "inventory_sha256": hashlib.sha256(json.dumps(inventory, sort_keys=True).encode()).hexdigest(),
        "scope": "Read frozen sources through Adapter; run trusted parsers only; no target execution, model calls or dataset copy.",
        "limitations": ["Candidate source graph, not SDG or feasible path analysis.",
            "No pointer/heap/exception-flow solver or reliable dynamic dispatch.",
            "No confirmed test-to-MUT binding or confirmed Mock boundary classification.",
            "Backend deadline excludes bounded adapter reads and source selection; peak memory not measured.",
            "Input summaries and graph snippets are evidence; full source files are not persisted."],
    }
    rows = []
    datasets = {}
    with tempfile.TemporaryDirectory(prefix="mockspec-batch-slice-") as work:
        paths = _backend_paths()
        java_command = None
        if paths is not None:
            jar, java, javac = paths
            try:
                code, _, stderr = await communicate(
                    [str(javac), "-cp", str(jar), "-d", work, str(HERE / "SliceProbe.java")],
                    input_bytes=None, timeout_seconds=60, cwd=work,
                )
                manifest["java_backend"] = {"compiled": code == 0,
                    "jar_sha256": hashlib.sha256(jar.read_bytes()).hexdigest(),
                    "diagnostic": stderr.decode("utf-8", "replace")[-2000:]}
                if code == 0:
                    java_command = [str(java), "-Xmx768m", "-cp", f"{work}{os.pathsep}{jar}", "SliceProbe"]
            except Exception as exc:
                manifest["java_backend"] = {"compiled": False, "diagnostic": str(exc)}
        else:
            manifest["java_backend"] = {"compiled": False, "diagnostic": "pinned backend unavailable"}
        write(output / "run-manifest.json", manifest)
        for index, entry in enumerate(entries, 1):
            identity = {key: entry[key] for key in ("case_id", "dataset", "language", "snapshot_id")}
            if case_ids is not None and entry["case_id"] not in case_ids:
                result = {"status": "not_selected", "failure_categories": []}
            else:
                try:
                    key = (entry["dataset"], entry["inventory_revision"])
                    if key not in datasets:
                        datasets[key] = FrozenDataset(Path(inventory["registry_root"]),
                                                      dataset=key[0], inventory_revision=key[1])
                    with datasets[key].load_case(entry["case_id"]) as case:
                        if case.snapshot_id != entry["snapshot_id"] or case.language != entry["language"]:
                            raise ValueError("inventory identity mismatch")
                        result = await run_case(case, work=work, java_command=java_command,
                                                seconds=seconds, max_files=max_files, max_bytes=max_bytes)
                except Exception as exc:
                    result = {"status": "failed", "stage": "admission",
                              "failure_categories": ["input_rejected"], "error": str(exc)}
            result["identity"] = identity
            filename = hashlib.sha256((entry["dataset"] + "\0" + entry["case_id"]).encode()).hexdigest()[:20] + ".json"
            write(output / filename, result)
            rows.append(result)
            if result["status"] != "not_selected":
                print(f"{index}/{len(entries)} {entry['case_id']} {result['status']}", flush=True)
    summary = summarize(rows)
    write(output / "summary.json", summary)
    write(output / "results.json", {"profile": PROFILE, "cases": rows})
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inventory", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--case-id", action="append")
    parser.add_argument("--seconds", type=float, default=30)
    parser.add_argument("--max-files", type=int, default=8)
    parser.add_argument("--max-bytes", type=int, default=200000)
    args = parser.parse_args()
    summary = asyncio.run(run(decode_json(args.inventory.read_text()), args.output,
                              seconds=args.seconds, max_files=args.max_files,
                              max_bytes=args.max_bytes, case_ids=args.case_id))
    print(json.dumps(summary, ensure_ascii=False, indent=2))
