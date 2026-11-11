"""Run a generic, bounded CodeQL observation analysis for one frozen Python case.

The adapter is driven by the admitted case identity, visible source selection,
selected test function and extracted observations.  It contains no benchmark
case allow-list and never executes the target or dynamic loader.
"""
from __future__ import annotations

import argparse
import ast
from collections import Counter
import csv
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(HERE.parent))

from criteria import _FunctionBodyVisitor, extract_criteria  # noqa: E402
from result_contract import RELATION_KINDS, RESULT_SCHEMA_VERSION, validate_result_contract  # noqa: E402
from source_selection import select_sources  # noqa: E402
from mock_spec.adapters.frozen_dataset import FrozenDataset  # noqa: E402

SCHEMA = "mockspec.generic-python-backend/v1"
QUERY = HERE / "generic-observation-flow.ql"
QLPACK = HERE / "qlpack.yml"
DRIVER_ENV = {"LANG": "C.UTF-8", "LC_ALL": "C.UTF-8", "PATH": os.defpath}


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n")


def run_tool(command: list[str], *, cwd: Path, stdout: Path, stderr: Path,
             timeout: int = 180) -> dict[str, Any]:
    started = time.monotonic()
    try:
        with stdout.open("wb") as out, stderr.open("wb") as err:
            process = subprocess.Popen(command, cwd=cwd, env=DRIVER_ENV, stdout=out, stderr=err,
                                       start_new_session=True)
            try:
                code = process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
                raise
        return {"status": "complete", "exit_code": code,
                "seconds": time.monotonic() - started}
    except subprocess.TimeoutExpired:
        return {"status": "timeout", "exit_code": None, "seconds": time.monotonic() - started,
                "timeout_seconds": timeout}


def write_external_column(path: Path, values: list[str | int]) -> None:
    with path.open("w", newline="") as stream:
        writer = csv.writer(stream)
        for value in values:
            writer.writerow([value])


def endpoint(path: str, line: int, end_line: int) -> dict[str, Any]:
    return {"logical_path": path, "start_line": line, "end_line": max(line, end_line)}


def selector_locations(text: str, criteria: list[dict[str, Any]]) -> list[list[Any]]:
    """Resolve each unambiguous original root to an exact AST expression location."""
    nodes = list(ast.walk(ast.parse(text)))
    counts = Counter(item["line"] for item in criteria)
    facts = []
    for item in criteria:
        if counts[item["line"]] != 1:
            continue
        candidates = []
        for node in nodes:
            if getattr(node, "lineno", None) != item["line"]:
                continue
            if isinstance(node, ast.Assert) and not item.get("call"):
                candidates.append(node.test)
            elif isinstance(node, ast.Call) and item.get("call"):
                name = node.func.id if isinstance(node.func, ast.Name) else (
                    node.func.attr if isinstance(node.func, ast.Attribute) else None)
                if name == item["call"]:
                    candidates.append(node)
        if len(candidates) == 1:
            node = candidates[0]
            # Python AST offsets count UTF-8 bytes; CodeQL columns count characters.
            prefix = text.splitlines()[node.lineno - 1].encode()[:node.col_offset].decode()
            facts.append([item["line"], node.lineno, len(prefix) + 1,
                          "call" if item.get("call") else "assert"])
    return facts


def loader_facts(sources: list[dict[str, Any]], selection: dict[str, Any],
                 scope: dict[str, Any]) -> list[list[Any]]:
    """Qualify existing authenticated loader evidence at unique selected-test calls."""
    test = next(source for source in sources if source["source_id"] == "test")
    tree = ast.parse(test["text"])
    targets = [node for node in ast.walk(tree)
               if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
               and node.lineno == scope["start_line"] and node.name == scope["symbol"]]
    if len(targets) != 1:
        return []
    target = targets[0]
    visitor = _FunctionBodyVisitor()
    for statement in target.body:
        visitor.visit(statement)
    target_nodes = visitor.nodes
    facts = []
    for binding in selection.get("dynamic_loader_bindings", []):
        if binding["status"] != "resolved":
            continue
        loader_name = binding.get("loader")
        loaders = [node for node in tree.body if isinstance(node, ast.FunctionDef)
                   and node.name == loader_name and not node.decorator_list]
        if len(loaders) != 1 or any(
                isinstance(node, ast.Name) and node.id == loader_name
                and isinstance(node.ctx, (ast.Store, ast.Del)) for node in ast.walk(tree)):
            continue
        definition = binding["function_definition"]
        product = next((source for source in sources
                        if source["source_id"] == definition["source_id"]
                        and source["logical_path"] == definition["logical_path"]), None)
        if product is None:
            continue
        functions = [node for node in ast.parse(product["text"]).body
                     if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                     and node.name == definition["name"]
                     and node.lineno == definition["start_line"]
                     and node.end_lineno == definition["end_line"]]
        if (len(functions) != 1 or functions[0].decorator_list
                or isinstance(functions[0], ast.AsyncFunctionDef)):
            continue
        # Existing loader recovery scans the module. Only accept names initialized
        # inside this target, never a same-name binding in another test.
        bound: set[str] = set()
        for item in sorted(binding["test_bindings"], key=lambda row: row["line"]):
            stores = [node for node in target_nodes if isinstance(node, ast.Name)
                      and isinstance(node.ctx, (ast.Store, ast.Del)) and node.id == item["name"]]
            if len(stores) != 1 or stores[0].lineno != item["line"]:
                continue
            assignments = [node for node in target_nodes
                           if isinstance(node, (ast.Assign, ast.AnnAssign)) and node.lineno == item["line"]
                           and stores[0] in ast.walk(node)]
            if len(assignments) != 1:
                continue
            assignment = assignments[0]
            targets_at_line = assignment.targets if isinstance(assignment, ast.Assign) else [assignment.target]
            if len(targets_at_line) != 1:
                continue
            target_at_line = targets_at_line[0]
            rhs = assignment.value
            if isinstance(rhs, ast.Name) and rhs.id in bound and isinstance(target_at_line, ast.Name):
                bound.add(item["name"])
            elif isinstance(rhs, ast.Call) and isinstance(rhs.func, ast.Name) and rhs.func.id == loader_name:
                slots = list(target_at_line.elts) if isinstance(target_at_line, (ast.Tuple, ast.List)) else [target_at_line]
                index = binding.get("return_index", 0)
                if index < len(slots) and isinstance(slots[index], ast.Name) and slots[index].id == item["name"]:
                    bound.add(item["name"])
        allowed_stores = {(item["name"], item["line"]) for item in binding["test_bindings"]}
        if any(isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del))
               and node.id in bound and (node.id, node.lineno) not in allowed_stores
               for node in target_nodes):
            continue
        for call in binding["call_sites"]:
            if call["name"] not in bound:
                continue
            matches = [node for node in target_nodes if isinstance(node, ast.Call)
                       and isinstance(node.func, ast.Name) and node.func.id == call["name"]
                       and node.lineno == call["line"]]
            if len(matches) == 1:
                facts.append([call["line"], call["name"], definition["logical_path"],
                              definition["start_line"], definition["name"], definition["end_line"]])
    counts = Counter((row[0], row[1]) for row in facts)
    return [row for row in facts if counts[(row[0], row[1])] == 1]


def summarize_entry_evidence(criteria: list[dict[str, Any]], rows: list[dict[str, str]]) -> dict[str, Any]:
    mapped_counts = Counter(int(row["observationLine"]) for row in rows
                            if row["relation"] == "observation")
    criterion_counts = Counter(item["line"] for item in criteria)
    mapped_lines = {line for line, count in mapped_counts.items()
                    if count == 1 and criterion_counts[line] == 1}
    observations_by_line: dict[int, list[str]] = {}
    for criterion in criteria:
        observations_by_line.setdefault(criterion["line"], []).append(criterion["criterion_id"])
    product_call_bindings = sum(row["relation"] in {
        "resolved_test_product_call", "visible_loader_product_call"} for row in rows)
    cross_source_relations = sum(
        row["relation"] != "observation" and row["sourcePath"] != row["targetPath"]
        for row in rows
    )
    observation_linked = sum(
        row["relation"] in {"resolved_product_result_to_observation", "loader_product_result_to_observation"}
        and row["sourcePath"] != row["targetPath"]
        and int(row["observationLine"]) in mapped_lines
        for row in rows
    )
    observations_mapped = sum(len(ids) for line, ids in observations_by_line.items()
                              if line in mapped_lines)
    if observation_linked:
        entry_status = "observation_linked_to_product"
    elif product_call_bindings:
        entry_status = "product_call_binding_only"
    elif observations_mapped:
        entry_status = "observations_mapped_only"
    else:
        entry_status = "no_observation_mapping"
    return {
        "observations_mapped": observations_mapped,
        "product_call_bindings": product_call_bindings,
        "cross_source_relations": cross_source_relations,
        "observation_linked_cross_source_relations": observation_linked,
        "missing_observations": [criterion["criterion_id"] for criterion in criteria
                                 if criterion["line"] not in mapped_lines],
        "ambiguous_observation_lines": sorted({line for line, ids in observations_by_line.items()
                                               if len(ids) > 1 or mapped_counts[line] > 1}),
        "analysis_entry_status": entry_status,
    }


def build_contract(entry: dict[str, Any], context: dict[str, Any], sources: list[dict[str, Any]],
                   criteria: list[dict[str, Any]], rows: list[dict[str, str]],
                   costs: dict[str, Any], boundaries: list[dict[str, Any]]) -> dict[str, Any]:
    observations = [{
        "observation_id": criterion["criterion_id"],
        "location": {"source_id": "test", "start_line": criterion["line"],
                     "end_line": criterion["line"]},
        "kind": criterion["kind"],
        "origin": "explicit" if criterion["confidence"] == "explicit" else "candidate",
        "reason": criterion["reason"],
    } for criterion in criteria]
    evidence = summarize_entry_evidence(criteria, rows)
    observation_ids: dict[int, list[str]] = {}
    for observation in observations:
        if observation["observation_id"] in evidence["missing_observations"]:
            continue
        observation_ids.setdefault(observation["location"]["start_line"], []).append(
            observation["observation_id"]
        )
    relations: list[dict[str, Any]] = []
    candidates: dict[tuple[str, int, str], dict[str, Any]] = {}
    unknown_effects: dict[tuple[str, int], dict[str, Any]] = {}
    for row in rows:
        kind = row["relation"]
        source_line, source_end = int(row["sourceLine"]), int(row["sourceEndLine"])
        target_line, target_end = int(row["targetLine"]), int(row["targetEndLine"])
        refs = list(observation_ids.get(int(row["observationLine"]), []))
        common = {
            "from": endpoint(row["sourcePath"], source_line, source_end),
            "to": endpoint(row["targetPath"], target_line, target_end),
            "provenance": {"backend": "CodeQL 2.27.4", "query_relation": kind,
                           "query_detail": row["detail"],
                           "query_sha256": sha256_bytes(QUERY.read_bytes())},
            "precision": "generic bounded CodeQL relation; semantic correctness not independently reviewed",
            "observation_ids": refs, "assumptions": [],
        }
        if kind in {"resolved_test_product_call", "visible_loader_product_call"}:
            relations.append({"kind": "call_binding", "modality": "analysis_may", **common})
            key = (row["targetPath"], target_line, row["detail"])
            candidates[key] = {"logical_path": key[0], "start_line": key[1], "symbol": key[2],
                               "evidence": common["from"]}
            unknown_effects[(row["sourcePath"], source_line)] = {
                "boundary_id": f"call:{row['sourcePath']}:{source_line}",
                "effect": "return_exception_and_reachable_state_effects_not_fully_modelled",
                "scope": common["from"], "provenance": (
                    "authenticated visible-loader frontend" if kind == "visible_loader_product_call"
                    else "generic CodeQL resolved call"),
                "precision": "unknown_effect", "assumptions": [],
            }
        elif kind == "actual_to_formal":
            relations.append({"kind": "value_flow", "modality": "analysis_may", **common})
        elif kind in {"call_result_to_observation", "resolved_product_result_to_observation",
                      "loader_product_result_to_observation"}:
            relations.append({"kind": "value_flow", "modality": "analysis_may", **common})
        elif kind == "guard_controls_observation":
            relations.append({"kind": "control_dependence", "modality": "analysis_may", **common})
    candidate_values = list(candidates.values())
    binding_status = "unique" if len(candidate_values) == 1 else "ambiguous" if candidate_values else "unresolved"
    coverage = {kind: "not_evaluated" for kind in RELATION_KINDS}
    failure_categories = ["semantic_validation_not_evaluated",
                          "generic_interprocedural_effects_incomplete"]
    if not candidate_values:
        failure_categories.append("mut_binding_unresolved")
    if evidence["missing_observations"]:
        failure_categories.append("observation_mapping_incomplete")
    if evidence["ambiguous_observation_lines"]:
        failure_categories.append("observation_mapping_ambiguous")
    for relation in relations:
        origin = relation["provenance"]["query_relation"]
        if origin in {"visible_loader_product_call", "loader_product_result_to_observation"}:
            relation["provenance"]["binding_origin"] = "authenticated_visible_loader_frontend"
            relation["provenance"]["selector_facts_sha256"] = context.get("selector_facts_sha256")
            relation["assumptions"] = ["visible loader namespace and runtime imports retain their shown bindings"]
        if origin in {"resolved_product_result_to_observation", "loader_product_result_to_observation"}:
            relation["precision"] = "composed function binding and CodeQL test-local result flow; function span is provenance scope, not an internal return-site edge"
    contract = {
        "schema_version": RESULT_SCHEMA_VERSION,
        "identity": {"case_id": entry["case_id"], "dataset": entry["dataset"],
                     "language": "python", "snapshot_id": entry["snapshot_id"],
                     "masking_contract": context["masking_contract"],
                     "masked_test_sha256": context["masked_test_sha256"],
                     "target_selector": context["target_selector"]},
        "source_scope": [{"source_id": source["source_id"], "logical_path": source["logical_path"],
                          "sha256": source["sha256"], "bytes": source["bytes"],
                          "selection_role": "masked_test" if source["source_id"] == "test"
                          else "candidate_product"} for source in sources],
        "observations": observations,
        "parameterization": {"status": "not_expanded", "coverage": "selected_test_definition",
                             "reason": "generic backend does not enumerate parameter rows"},
        "entry_point": {
            "test_selection": {"origin": "task_provided", "status": "unique",
                               "selector": context["target_selector"], "evidence": context["target_scope"]},
            "mut_binding": {"origin": "automatic", "status": binding_status,
                            "candidates": candidate_values,
                            "evidence": [row["evidence"] for row in candidate_values], "assumptions": []},
        },
        "relations": relations,
        "relation_coverage": [{"kind": kind, "status": coverage[kind]} for kind in sorted(coverage)],
        "analysis_boundaries": {
            "assumptions": ["selected source paths are candidates, not proof of full runtime module resolution"],
            "unknown_effects": list(unknown_effects.values()), "unanalysed_regions": boundaries,
        },
        "completion": {"status": "partial", "stage": "generic_codeql_mapping",
                       "failure_categories": failure_categories, "error": None},
        "semantic_validation": {"status": "not_evaluated", "reviewer": None, "evidence": [],
                                "notes": "relations are backend candidates; no case-specific gold or review used"},
        "cost": costs,
    }
    validate_result_contract(contract)
    return contract


def execute(entry: dict[str, Any], registry_root: Path, output: Path, codeql: Path,
            *, max_files: int = 8, max_bytes: int = 200_000,
            database_timeout: int = 180, query_timeout: int = 360) -> dict[str, Any]:
    output = output.resolve()
    if output.exists():
        raise FileExistsError(f"output must not exist: {output}")
    output.mkdir(parents=True)
    started = time.monotonic()
    entry_evidence: dict[str, Any] = {
        "input_prepared": False, "database_created": False,
        "observations_total": 0, "observations_mapped": 0,
        "product_call_bindings": 0, "cross_source_relations": 0,
        "observation_linked_cross_source_relations": 0,
        "missing_observations": [], "ambiguous_observation_lines": [],
        "analysis_entry_status": "not_started",
        "current_stage": "admission", "tool_runs": {},
    }
    try:
        if entry.get("language") != "python":
            raise ValueError("generic Python backend requires a Python inventory entry")
        if not codeql.is_file() or not os.access(codeql, os.X_OK):
            raise ValueError("CodeQL path must be an executable file")
        dataset = FrozenDataset(registry_root, dataset=entry["dataset"],
                                inventory_revision=entry["inventory_revision"])
        with dataset.load_case(entry["case_id"]) as case:
            if case.snapshot_id != entry["snapshot_id"] or case.language != "python":
                raise ValueError("frozen case identity differs from inventory entry")
            extraction = extract_criteria(case.masked_test.text, case.language, case.target_selector)
            criteria = extraction["criteria"]
            if not criteria:
                raise ValueError("selected test has no visible observation candidate")
            for index, criterion in enumerate(criteria):
                criterion["criterion_id"] = f"criterion:{index}"
            entry_evidence["observations_total"] = len(criteria)
            entry_evidence["missing_observations"] = [item["criterion_id"] for item in criteria]
            selection = select_sources(case, max_files=max_files, max_bytes=max_bytes)
            selected = selection.pop("sources")
            context = {"target_selector": case.target_selector,
                       "target_scope": extraction["target_scope"]["locations"],
                       "masking_contract": case._masking_contract,
                       "masked_test_sha256": sha256_bytes(case.masked_test.text.encode())}
        sources = [{"source_id": row["source_id"], "logical_path": row["logical_path"],
                    "sha256": sha256_bytes(row["text"].encode()),
                    "bytes": len(row["text"].encode()), "text": row["text"]} for row in selected]
        write_json(output / "input-provenance.json", {
            "identity": {key: entry[key] for key in ("case_id", "dataset", "language", "snapshot_id")},
            "input_context": context, "criteria": criteria,
            "sources": [{key: row[key] for key in ("source_id", "logical_path", "sha256", "bytes")}
                        for row in sources],
            "implementation_sha256": {str(path.relative_to(ROOT)): sha256_bytes(path.read_bytes())
                                      for path in (Path(__file__), QUERY, HERE.parent / "source_selection.py",
                                                   HERE.parent / "criteria.py")},
        })
        test_source = next(row for row in selected if row["source_id"] == "test")
        entry_evidence["input_prepared"] = True
        entry_evidence["current_stage"] = "database_create"
        with tempfile.TemporaryDirectory(prefix="mockspec-generic-python-") as temporary:
            work = Path(temporary)
            input_root, query_root = work / "input", work / "query"
            input_root.mkdir(); query_root.mkdir()
            for source in sources:
                destination = input_root / source["logical_path"]
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_text(source["text"])
            shutil.copy2(QLPACK, query_root / "qlpack.yml")
            query_text = QUERY.read_text()
            selector_facts = {
                "test_path": test_source["logical_path"],
                "test_function": case.target_selector.rsplit("::", 1)[-1],
                "observations": [{"observation_id": criterion["criterion_id"],
                                  "line": criterion["line"]} for criterion in criteria],
                "observation_expressions": selector_locations(selected[0]["text"], criteria),
                "visible_loader_bindings": loader_facts(selected, selection, context["target_scope"][0]),
            }
            write_json(output / "selector-facts.json", selector_facts)
            context["selector_facts_sha256"] = sha256_bytes((output / "selector-facts.json").read_bytes())
            fact_files = {
                "selectedTestPathFact": output / "selector-test-path.csv",
                "selectedTestFunctionFact": output / "selector-test-function.csv",
                "selectedObservationLineFact": output / "selector-observation-lines.csv",
                "selectedObservationExprFact": output / "selector-observation-expressions.csv",
                "visibleLoaderBindingFact": output / "selector-loader-bindings.csv",
            }
            write_external_column(fact_files["selectedTestPathFact"], [selector_facts["test_path"]])
            write_external_column(fact_files["selectedTestFunctionFact"],
                                  [selector_facts["test_function"]])
            write_external_column(fact_files["selectedObservationLineFact"],
                                  sorted({row["line"] for row in selector_facts["observations"]}))
            for key, predicate in (("observation_expressions", "selectedObservationExprFact"),
                                   ("visible_loader_bindings", "visibleLoaderBindingFact")):
                with fact_files[predicate].open("w", newline="") as stream:
                    csv.writer(stream).writerows(selector_facts[key])
            query = query_root / QUERY.name
            query.write_text(query_text)
            version = json.loads(subprocess.check_output(
                [str(codeql), "version", "--format=json"], text=True, env=DRIVER_ENV, timeout=30))
            database, bqrs = work / "database", work / "result.bqrs"
            db_cost = run_tool([str(codeql), "database", "create", str(database), "--language=python",
                                f"--source-root={input_root}", "--threads=2", "--ram=4096", "--overwrite"],
                               cwd=work, stdout=output / "database.stdout", stderr=output / "database.stderr",
                               timeout=database_timeout)
            entry_evidence["tool_runs"]["database_create"] = db_cost
            if db_cost["status"] != "complete" or db_cost["exit_code"]:
                raise RuntimeError("CodeQL database creation failed")
            entry_evidence["database_created"] = True
            entry_evidence["current_stage"] = "query"
            query_cost = run_tool([str(codeql), "query", "run", f"--database={database}", "--threads=2",
                                   "--ram=4096", f"--output={bqrs}",
                                   *[f"--external={name}={path}" for name, path in fact_files.items()],
                                   str(query)], cwd=work,
                                  stdout=output / "query.stdout", stderr=output / "query.stderr",
                                  timeout=query_timeout)
            entry_evidence["tool_runs"]["query"] = query_cost
            if query_cost["status"] != "complete" or query_cost["exit_code"]:
                raise RuntimeError("generic CodeQL query failed")
            entry_evidence["current_stage"] = "decode"
            csv_path = output / "relations.csv"
            decode_cost = run_tool([str(codeql), "bqrs", "decode", "--format=csv",
                                    f"--output={csv_path}", str(bqrs)], cwd=work,
                                   stdout=output / "decode.stdout", stderr=output / "decode.stderr")
            entry_evidence["tool_runs"]["decode"] = decode_cost
            if decode_cost["status"] != "complete" or decode_cost["exit_code"]:
                raise RuntimeError("generic CodeQL result decode failed")
            with csv_path.open(newline="") as stream:
                rows = list(csv.DictReader(stream))
        entry_evidence.update(summarize_entry_evidence(criteria, rows))
        entry_evidence["current_stage"] = "mapped"
        source_records = [{key: row[key] for key in ("source_id", "logical_path", "sha256", "bytes")}
                          for row in sources]
        total = time.monotonic() - started
        costs = {"timings_seconds": {"database": db_cost["seconds"], "query": query_cost["seconds"],
                                     "decode": decode_cost["seconds"]}, "total_seconds": total,
                 "total_scope": "frozen admission, bounded selection, CodeQL database/query/decode and mapping",
                 "peak_memory": {"status": "unavailable", "bytes": None,
                                 "reason": "generic runner does not yet collect per-process peak RSS"},
                 "cache_state": {"status": "cold", "reason": "fresh temporary database; OS/tool distribution caches not isolated"},
                 "input_size": {"files": len(sources), "bytes": sum(row["bytes"] for row in sources)},
                 "graph_size": {"nodes": None, "edges": len(rows)}, "unavailable_phases": []}
        boundaries = [{"scope": "source_selection", "reason": value}
                      for value in selection["diagnostics"]]
        boundaries.extend([
            {"scope": "path_feasibility", "reason": "not evaluated"},
            {"scope": "interprocedural_return_exception_state", "reason": "generic query is incomplete"},
        ])
        contract = build_contract(entry, context, source_records, criteria, rows, costs, boundaries)
        result = {"schema": SCHEMA, "status": "complete", "backend": "generic_codeql_python",
                  "identity": contract["identity"], "result_contract": contract,
                  "source_selection": selection, "raw_relation_count": len(rows),
                  "entry_evidence": entry_evidence,
                  "provenance": {"query_template_sha256": sha256_bytes(QUERY.read_bytes()),
                                 "rendered_query_sha256": sha256_bytes(query_text.encode()),
                                 "selector_facts_sha256": sha256_bytes(
                                     (output / "selector-facts.json").read_bytes()),
                                 "codeql_version": version["version"], "driver_environment": DRIVER_ENV,
                                 "target_executed": False, "loader_executed": False,
                                 "selection": "case identity + visible imports/paths + extracted observations; no case allow-list"}}
    except Exception as exc:
        failed_run = entry_evidence["tool_runs"].get(entry_evidence["current_stage"], {})
        result = {"schema": SCHEMA, "status": "failed", "backend": "generic_codeql_python",
                  "identity": {key: entry.get(key) for key in ("dataset", "case_id", "language", "snapshot_id")},
                  "result_contract": None,
                  "error": {"type": type(exc).__name__, "message": str(exc),
                            "stage": entry_evidence["current_stage"],
                            "classification": "timeout" if failed_run.get("status") == "timeout"
                            else "tool_failure" if failed_run else "pre_tool_failure"},
                  "entry_evidence": entry_evidence,
                  "provenance": {"target_executed": False, "loader_executed": False},
                  "elapsed_seconds": time.monotonic() - started}
    write_json(output / "batch-result.json", result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--entry", type=Path, required=True)
    parser.add_argument("--registry-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--codeql", type=Path, required=True)
    parser.add_argument("--max-files", type=int, default=8)
    parser.add_argument("--max-bytes", type=int, default=200_000)
    parser.add_argument("--database-timeout", type=int, default=180)
    parser.add_argument("--query-timeout", type=int, default=360)
    args = parser.parse_args()
    entry = json.loads(args.entry.read_text())
    result = execute(entry, args.registry_root, args.output, args.codeql,
                     max_files=args.max_files, max_bytes=args.max_bytes,
                     database_timeout=args.database_timeout, query_timeout=args.query_timeout)
    print(json.dumps({"status": result["status"], "case_id": entry.get("case_id"),
                      "relations": len((result.get("result_contract") or {}).get("relations", []))}))
    return 0 if result["status"] == "complete" else 1


if __name__ == "__main__":
    raise SystemExit(main())
