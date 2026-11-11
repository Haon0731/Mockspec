"""Frozen MockBench test-goal backward candidate slicing (no target execution)."""
from __future__ import annotations

import argparse
import asyncio
from collections import Counter, deque
import copy
import hashlib
import json
import math
import os
from pathlib import Path
import sys
import tempfile
import time

from criteria import extract_criteria
from result_contract import (RESULT_SCHEMA_VERSION, attach_result_contract, contract_digest,
                             validate_result_contract)
from source_selection import (PYTHON_FOCAL_FILE_BYTES, PYTHON_LOADER_SUPPORT_FILE_BYTES,
                              select_sources)
from mock_spec.adapters.frozen_dataset import FrozenDataset
from mock_spec.analysis_process import communicate
from mock_spec.java_analysis import _backend_paths
from mock_spec.validation import decode_json

HERE = Path(__file__).resolve().parent
PROFILE = "test-goal-backward-candidates/v1"
REPAIRED_PROFILE = "test-goal-backward-repaired-study/v1"


def verify_formal_inputs(inventory: dict, gate: Path, baseline: Path) -> dict:
    """Bind the formal run to the accepted gate and unchanged r3 denominator."""
    root = HERE.parents[1]
    acceptance = json.loads(gate.read_text())
    if acceptance.get("issue") != 30 or acceptance.get("status") != "passed":
        raise ValueError("formal run requires the passed issue30 gate")
    if hashlib.sha256((gate.parent / "pre-registration.json").read_bytes()).hexdigest() != acceptance["pre_registration_sha256"]:
        raise ValueError("formal gate pre-registration changed")
    for check in acceptance["checks"]:
        if check["status"] != "passed_bounded":
            raise ValueError("formal gate has an unpassed check")
        for artifact in check["evidence"]:
            if hashlib.sha256((root / artifact["path"]).read_bytes()).hexdigest() != artifact["sha256"]:
                raise ValueError("formal gate evidence changed")
    previous = json.loads(baseline.read_text())
    keys = ("dataset", "case_id", "snapshot_id", "language")
    before = {tuple(row["identity"][key] for key in keys) for row in previous["cases"]}
    after = {tuple(row[key] for key in keys) for row in inventory["cases"]}
    if len(previous["cases"]) != 165 or len(inventory["cases"]) != 165 or before != after or len(after) != 165:
        raise ValueError("formal run must retain all original165 frozen identities")
    if Counter(row["language"] for row in inventory["cases"]) != {"java": 59, "python": 106}:
        raise ValueError("formal run must retain Java59/Python106")
    input_ledger = []
    for row in previous["cases"]:
        test: dict = next((source for source in row.get("sources", []) if source["source_id"] == "test"), {})
        input_ledger.append({**row["identity"], "masked_test_sha256": test.get("sha256"),
                             "target_selector": row.get("target_extraction", {}).get("target_scope", {}).get("selector"),
                             "masking_contract": "not_recorded_in_r3"})
    return {"gate": {"path": str(gate), "sha256": hashlib.sha256(gate.read_bytes()).hexdigest()},
            "baseline": {"path": str(baseline), "sha256": hashlib.sha256(baseline.read_bytes()).hexdigest()},
            "baseline_input_identity": input_ledger}


def merge_semantic_result(result: dict, semantic: dict, identity: dict) -> None:
    """Keep candidate diagnostics separate from the mature backend's claims."""
    result["candidate_diagnostics"] = {
        "status": result["status"], "failure_categories": list(result["failure_categories"]),
        "result_contract": copy.deepcopy(result["result_contract"]),
        **{key: copy.deepcopy(result.get(key)) for key in ("stage", "error", "elapsed_seconds", "timings_seconds")},
        "graph_and_evidence": "top-level graph/evidence/sources remain candidate diagnostics only",
    }
    result["semantic_backend"] = {key: value for key, value in semantic.items() if key != "result_contract"}
    contract = semantic.get("result_contract")
    if contract is not None:
        validate_result_contract(contract)
        for key in ("dataset", "case_id", "snapshot_id", "language"):
            if contract["identity"][key] != identity[key]:
                raise ValueError(f"semantic backend identity mismatch: {key}")
        for key in ("masking_contract", "masked_test_sha256", "target_selector"):
            expected = result["result_contract"]["identity"].get(key)
            if expected is not None and contract["identity"].get(key) != expected:
                raise ValueError(f"semantic backend input mismatch: {key}")
        result["result_contract"] = contract
        result["status"] = contract["completion"]["status"]
        result["stage"] = contract["completion"]["stage"]
        result["failure_categories"] = list(contract["completion"]["failure_categories"])
        result["error"] = contract["completion"].get("error")
    else:
        status = semantic.get("status", "failed")
        if status not in {"unsupported", "failed", "timeout", "budget_exceeded", "blocked"}:
            raise ValueError("semantic backend success requires a validated case contract")
        category = "semantic_backend_unsupported" if status == "unsupported" else "semantic_backend_" + status
        result["failure_categories"].append(category)
        if result["status"] not in {"failed", "timeout"}:
            result["status"] = status
        result["stage"] = "semantic_backend"
        attach_result_contract(result, identity)
        result["result_contract"]["analysis_boundaries"]["unanalysed_regions"].append({
            "scope": "mature_backend_support_domain",
            "reason": semantic.get("reason") or semantic.get("error", {}).get("message") or category,
            "details": semantic.get("limitations", []),
        })
    validate_result_contract(result["result_contract"])


async def run_semantic_backend(entry: dict, result: dict, *, registry_root: str,
                               output: Path, codeql: Path | None, seconds: float,
                               backend_mode: str = "registered") -> dict:
    if backend_mode not in {"registered", "generic"}:
        raise ValueError("unknown semantic backend mode")
    token = hashlib.sha256((entry["dataset"] + "\0" + entry["case_id"]).encode()).hexdigest()[:20]
    request = output / (token + "-request.json")
    write(request, {**entry, **result.get("input_context", {}),
                    "input_context": result.get("input_context", {}), "sources": result.get("sources", [])})
    pilot = "wala-pilot" if entry["language"] == "java" else "python-backend-pilot"
    destination = output / token
    driver = "generic_backend.py" if backend_mode == "generic" else "batch_semantic_backend.py"
    command = [sys.executable, str(HERE / pilot / driver),
               "--entry", str(request), "--registry-root", registry_root, "--output", str(destination)]
    if (entry["language"] == "python" or backend_mode == "generic") and codeql is not None:
        command += ["--codeql", str(codeql)]
    started = time.monotonic()
    try:
        code, stdout, stderr = await communicate(command, input_bytes=None, cwd=str(HERE.parents[1]),
                                                timeout_seconds=seconds, max_output_bytes=2_000_000)
        (output / (token + ".stdout")).write_bytes(stdout)
        (output / (token + ".stderr")).write_bytes(stderr)
        artifact = destination / "batch-result.json"
        if code and not artifact.is_file():
            raise RuntimeError(f"semantic backend exit {code}: {stderr.decode('utf-8', 'replace')[-3000:]}")
        value = json.loads(artifact.read_text())
        if not isinstance(value, dict):
            raise ValueError("semantic backend result must be an object")
        if code and value.get("status") not in {"failed", "timeout", "blocked", "budget_exceeded"}:
            raise ValueError("nonzero backend exit contradicts its terminal result")
        value["driver_exit_code"] = code
    except Exception as exc:
        value = {"status": "timeout" if isinstance(exc, (TimeoutError, asyncio.TimeoutError)) else "failed",
                 "reason": str(exc), "error_type": type(exc).__name__}
    value["batch_dispatch_seconds"] = round(time.monotonic() - started, 6)
    value["batch_dispatch_scope"] = "bridge process including support check, adapter and backend; excludes candidate diagnostics"
    return value


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
    if not isinstance(graph, dict) or graph.get("status") != "ok":
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
    phase_started = started
    timings: dict[str, float] = {}
    result: dict = {
        "status": "unknown", "failure_categories": [], "stage": "criteria",
        "input_context": {
            "target_selector": case.target_selector,
            "masked_test_logical_path": getattr(case.masked_test, "logical_path", None),
            "masked_test_sha256": hashlib.sha256(case.masked_test.text.encode()).hexdigest(),
            "masking_contract": getattr(case, "_masking_contract", None),
        },
    }
    try:
        criteria = extract_criteria(case.masked_test.text, case.language, case.target_selector)
        timings["criterion_extraction"] = round(time.monotonic() - phase_started, 6)
        for index, item in enumerate(criteria["criteria"]):
            item["criterion_id"] = f"criterion:{index}"
        result["target_extraction"] = criteria
        if not criteria["criteria"]:
            result["failure_categories"].append("no_visible_criterion")
            return result
        result["stage"] = "source_selection"
        phase_started = time.monotonic()
        selection = select_sources(case, max_files=max_files, max_bytes=max_bytes)
        timings["source_selection"] = round(time.monotonic() - phase_started, 6)
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
        phase_started = time.monotonic()
        graph = await backend(request, case.language, work, java_command, seconds)
        timings["backend_total"] = round(time.monotonic() - phase_started, 6)
        result["graph"] = graph
        phase_started = time.monotonic()
        result["evidence"] = evidence_summary(graph)
        timings["evidence_summary"] = round(time.monotonic() - phase_started, 6)
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
        result["timings_seconds"] = timings
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
    return {"profile": PROFILE, "result_schema_version": RESULT_SCHEMA_VERSION,
            "counts": counts(rows),
            "by_language": {lang: counts([r for r in rows if r["identity"]["language"] == lang])
                            for lang in ("java", "python")},
            "interpretation": "Partial means candidate graph obtained, not correct semantic slicing or Mock recovery. Categories overlap.",
            "semantic_validation": "not_evaluated"}


def summarize_generic_entry(rows: list[dict]) -> dict:
    """Separate execution, root mapping and cross-source influence evidence."""
    def counts(subset: list[dict]) -> dict:
        stages: Counter = Counter()
        for row in subset:
            backend = row.get("semantic_backend", {})
            evidence = backend.get("entry_evidence", {})
            contract = row.get("result_contract", {})
            stages["attempted"] += 1
            stages["input_prepared"] += evidence.get("input_prepared") is True
            stages["database_or_graph_created"] += (
                evidence.get("database_created") is True or evidence.get("graph_created") is True)
            stages["backend_completed"] += backend.get("status") == "complete"
            total = evidence.get("observations_total", 0)
            stages["all_observations_mapped"] += (
                total > 0 and evidence.get("observations_mapped") == total
                and not evidence.get("ambiguous_observation_lines"))
            stages["product_call_binding_present"] += evidence.get("product_call_bindings", 0) > 0
            # A resolved call elsewhere in the test does not establish influence
            # on any observation. Require an observation-linked cross-source edge.
            stages["observation_linked_cross_source_relation"] += any(
                relation.get("observation_ids")
                and relation.get("kind") in {"value_flow", "control_dependence", "state_flow",
                                              "normal_return_dependence", "exception_dependence"}
                and relation.get("from", {}).get("logical_path")
                and relation.get("to", {}).get("logical_path")
                and relation["from"]["logical_path"] != relation["to"]["logical_path"]
                for relation in contract.get("relations", []))
        return dict(stages)

    return {"counts": counts(rows),
            "by_language": {language: counts([row for row in rows
                                               if row["identity"]["language"] == language])
                            for language in ("java", "python")},
            "interpretation": "Stage evidence only. Observation-linked relations remain unverified; these are not semantic success counts."}


async def run(inventory: dict, output: Path, *, seconds: float = 30,
              max_files: int = 8, max_bytes: int = 200000,
              case_ids: list[str] | None = None, workers: int = 1,
              semantic_gate: Path | None = None, baseline: Path | None = None,
              codeql: Path | None = None, semantic_seconds: float = 1200,
              backend_mode: str = "registered") -> dict:
    batch_started = time.monotonic()
    if inventory.get("input_policy") != "frozen-agent":
        raise ValueError("requires frozen-agent inventory")
    if not math.isfinite(seconds) or seconds <= 0 or max_files < 1 or max_bytes < 1:
        raise ValueError("budgets must be positive and finite")
    if type(workers) is not int or workers < 1:
        raise ValueError("workers must be a positive integer")
    if backend_mode not in {"registered", "generic"}:
        raise ValueError("unknown semantic backend mode")
    if backend_mode == "generic" and semantic_gate is None:
        raise ValueError("generic backend execution requires explicit semantic study registration")
    entries = inventory["cases"]
    identities = [(row["dataset"], row["case_id"]) for row in entries]
    if len(identities) != len(set(identities)):
        raise ValueError("duplicate inventory identity")
    if case_ids and not set(case_ids) <= {row["case_id"] for row in entries}:
        raise ValueError("unknown selected case ID")
    formal = None
    if semantic_gate is not None:
        if baseline is None or case_ids is not None:
            raise ValueError("formal semantic run requires r3 baseline and no case filter")
        if codeql is None or not codeql.is_file() or not os.access(codeql, os.X_OK):
            raise ValueError("formal semantic run requires the executable CodeQL file, not its installation directory")
        if not math.isfinite(semantic_seconds) or semantic_seconds <= 0:
            raise ValueError("semantic deadline must be positive and finite")
        formal = verify_formal_inputs(inventory, semantic_gate, baseline)
    output.mkdir(parents=True, exist_ok=False)
    output = output.resolve()
    if formal is not None:
        (output / "semantic").mkdir()
    write(output / "inventory.json", inventory)
    implementation = [HERE / name for name in (
        "batch_slice.py", "criteria.py", "source_selection.py", "survey_targets.py",
        "python_probe.py", "SliceProbe.java", "result_contract.py", "compare_runs.py")]
    root = HERE.parents[1]
    implementation.extend(root / "src/mock_spec" / name for name in (
        "adapters/frozen_dataset.py", "validation.py", "analysis_process.py", "java_analysis.py"))
    if formal is not None:
        for pilot in ("wala-pilot", "python-backend-pilot"):
            implementation.extend(path for path in (HERE / pilot).rglob("*")
                                  if path.is_file() and path.suffix in {".py", ".java", ".ql", ".json", ".xml", ".yml"}
                                  and "target" not in path.parts and "__pycache__" not in path.parts)
    manifest: dict = {
        "profile": REPAIRED_PROFILE if formal else PROFILE, "result_schema_version": RESULT_SCHEMA_VERSION,
        "configuration": {"backend_timeout_seconds": seconds,
            "max_files": max_files, "max_bytes": max_bytes, "case_ids": case_ids,
            "python_focal_file_bytes": PYTHON_FOCAL_FILE_BYTES,
            "python_loader_support_file_bytes": PYTHON_LOADER_SUPPORT_FILE_BYTES,
            "dispatch_order": "language_round_robin_results_in_inventory_order",
            "python_dependency_only": True, "java_heap_mb": 768, "workers": workers},
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
    if formal is not None:
        manifest["formal_registration"] = formal
        manifest["configuration"].update(semantic_timeout_seconds=semantic_seconds,
                                          codeql=str(codeql), semantic_cache="new per supported case",
                                          semantic_workers=2, java_semantic_workers=1,
                                          backend_mode=backend_mode)
        manifest["semantic_support_scope"] = {
            "java": "authenticated Jib JarFiles observation adapter and prepared application only",
            "python": "authenticated flask_308 and langflow_189 adapters only",
            "other_cases": "candidate diagnostics plus explicit mature-backend support rejection; no semantic success claim",
        }
        if backend_mode == "generic":
            manifest["semantic_support_scope"] = {
                "selection": "language-specific generic adapters; no case-ID allowlist or silent registered-case fallback",
                "entry_evidence": "input, database/graph, observation mapping and cross-source relations reported separately",
                "validation": "new relations remain unverified unless independently reviewed",
            }
        manifest["runtime"] = {"python": sys.version, "executable": sys.executable,
                               "codeql_executable_sha256": hashlib.sha256(codeql.read_bytes()).hexdigest()
                               if codeql is not None and codeql.is_file() else None}
        write(output / "pre-registration.json", manifest)
    datasets = {}
    with tempfile.TemporaryDirectory(prefix="mockspec-batch-slice-") as work:
        paths = _backend_paths()
        java_command = None
        if paths is not None:
            jar, java, javac = paths
            try:
                code, _, stderr = await communicate(
                    [str(javac), "-proc:none", "-cp", str(jar), "-d", work, str(HERE / "SliceProbe.java")],
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
        semaphore = asyncio.Semaphore(workers)
        semantic_semaphore = asyncio.Semaphore(2)
        language_semaphores = {"java": asyncio.Semaphore(1), "python": asyncio.Semaphore(2)}

        async def analyse_entry(index: int, entry: dict) -> dict:
            async with semaphore:
                return await analyse_entry_with_slot(index, entry)

        async def analyse_entry_with_slot(index: int, entry: dict) -> dict:
            case_started = time.monotonic()
            identity = {key: entry[key] for key in ("case_id", "dataset", "language", "snapshot_id")}
            result: dict
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
            attach_result_contract(result, identity)
            if formal is not None:
                prior = next(row for row in formal["baseline_input_identity"]
                             if (row["dataset"], row["case_id"]) == (entry["dataset"], entry["case_id"]))
                mismatch = [key for key in ("masked_test_sha256", "target_selector")
                            if prior[key] is not None and result["result_contract"]["identity"][key] is not None
                            and prior[key] != result["result_contract"]["identity"][key]]
                if mismatch or "input_rejected" in result["failure_categories"]:
                    semantic = {"status": "failed", "reason": "frozen input identity/admission rejected",
                                "identity_mismatches": mismatch}
                else:
                    # Acquire the language slot first so queued Java cases do not
                    # occupy both shared slots and prevent Python from progressing.
                    async with language_semaphores[entry["language"]], semantic_semaphore:
                        semantic = await run_semantic_backend(
                            entry, result, registry_root=inventory["registry_root"],
                            output=output / "semantic", codeql=codeql, seconds=semantic_seconds,
                            backend_mode=backend_mode)
                try:
                    merge_semantic_result(result, semantic, identity)
                except Exception as exc:
                    # Preserve the rejected producer output; never let one malformed
                    # semantic result discard later cases or replace frozen identity.
                    write(output / "semantic" / (str(index) + "-rejected-result.json"), semantic)
                    if "candidate_diagnostics" in result:
                        result["result_contract"] = result["candidate_diagnostics"]["result_contract"]
                        result["status"] = result["candidate_diagnostics"]["status"]
                        result["failure_categories"] = result["candidate_diagnostics"]["failure_categories"]
                    merge_semantic_result(result, {"status": "failed", "reason": str(exc),
                                                  "error_type": type(exc).__name__}, identity)
                result["batch_case_cost"] = {"total_seconds": round(time.monotonic() - case_started, 6),
                                             "scope": "admission, candidate diagnostic, semantic-slot wait, dispatch and mapping; excludes case queue, shared compile and serialization"}
            result["result_contract_sha256"] = contract_digest(result["result_contract"])
            filename = hashlib.sha256((entry["dataset"] + "\0" + entry["case_id"]).encode()).hexdigest()[:20] + ".json"
            write(output / filename, result)
            if result["status"] != "not_selected":
                print(f"{index}/{len(entries)} {entry['case_id']} {result['status']}", flush=True)
            return result

        # Interleave languages so waiting Java cases cannot occupy every outer
        # worker while Python is runnable. Restore frozen order after dispatch.
        queues: dict[str, deque] = {}
        for index, entry in enumerate(entries, 1):
            queues.setdefault(entry["language"], deque()).append((index, entry))
        scheduled = []
        while any(queues.values()):
            for queue in queues.values():
                if queue:
                    scheduled.append(queue.popleft())
        completed = await asyncio.gather(*(analyse_entry(index, entry)
                                           for index, entry in scheduled))
        rows = [row for _, row in sorted(zip((index for index, _ in scheduled), completed))]
    summary = summarize(rows)
    if formal is not None:
        summary.update(profile=REPAIRED_PROFILE, semantic_validation="per_case",
                       interpretation="All165 inputs attempted. Candidate diagnostics are retained separately; mature backend support is bounded to three registered cases.")
        if backend_mode == "generic":
            summary["interpretation"] = "All165 inputs attempted through generic adapters. Backend execution, observation mapping and cross-source influence remain separate from semantic correctness."
            summary["generic_entry"] = summarize_generic_entry(rows)
        summary["semantic_backend_status"] = dict(Counter(row["semantic_backend"]["status"] for row in rows))
        summary["semantic_validation_counts"] = dict(Counter(row["result_contract"]["semantic_validation"]["status"] for row in rows))
        summary["candidate_status"] = dict(Counter(row["candidate_diagnostics"]["status"] for row in rows))
        summary["formal_registration"] = formal
        summary["batch_total_seconds"] = round(time.monotonic() - batch_started, 6)
        summary["batch_total_scope"] = "registration, shared compile, all case processing and per-case writes; excludes final summary/results serialization"
    write(output / "summary.json", summary)
    write(output / "results.json", {"profile": REPAIRED_PROFILE if formal else PROFILE,
                                      "result_schema_version": RESULT_SCHEMA_VERSION,
                                      "cases": rows})
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inventory", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--case-id", action="append")
    parser.add_argument("--seconds", type=float, default=30)
    parser.add_argument("--max-files", type=int, default=8)
    parser.add_argument("--max-bytes", type=int, default=200000)
    parser.add_argument("--workers", type=int, default=1,
                        help="parallel case parsers; each Java case may use its own 768 MiB heap")
    parser.add_argument("--semantic-gate", type=Path, help="passed issue30 acceptance; enables bounded mature backend dispatch")
    parser.add_argument("--baseline", type=Path, help="immutable r3 results required for the formal165 run")
    parser.add_argument("--codeql", type=Path)
    parser.add_argument("--semantic-seconds", type=float, default=1200)
    parser.add_argument("--backend-mode", choices=("registered", "generic"), default="registered")
    args = parser.parse_args()
    inventory = decode_json(args.inventory.read_text())
    if not isinstance(inventory, dict):
        parser.error("inventory must be a JSON object")
    summary = asyncio.run(run(inventory, args.output,
                              seconds=args.seconds, max_files=args.max_files,
                              max_bytes=args.max_bytes, case_ids=args.case_id, workers=args.workers,
                              semantic_gate=args.semantic_gate, baseline=args.baseline,
                              codeql=args.codeql, semantic_seconds=args.semantic_seconds,
                              backend_mode=args.backend_mode))
    print(json.dumps(summary, ensure_ascii=False, indent=2))
