"""Run bounded WALA slices for one admitted frozen Java case.

The runner compiles only the masked test and admitted ``src/main`` sources.  If
the Java mask removed local declarations, those names become entry parameters;
no return value, exception, field write, or other Mock behaviour is supplied.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import signal
import shutil
import subprocess
import sys
import tempfile
import time
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(HERE.parent))

from criteria import extract_criteria  # noqa: E402
from result_contract import RELATION_KINDS, RESULT_SCHEMA_VERSION, validate_result_contract  # noqa: E402
from source_selection import _java_opaque, select_sources  # noqa: E402
from mock_spec.adapters.frozen_dataset import FrozenDataset  # noqa: E402

SCHEMA = "mockspec.generic-java-wala-backend/v1"
BACKEND_SOURCE = HERE / "backend/src/main/java/org/mockspec/research/WalaSliceProbe.java"
MAX_RELATIONS = 1000
DEFAULT_MAX_FILES = 8
DEFAULT_MAX_BYTES = 200_000
DEFAULT_COMPILE_MAX_FILES = 400
DEFAULT_COMPILE_MAX_BYTES = 5_000_000
JAVA_PACKAGE = re.compile(r"(?m)^\s*package\s+([\w.]+)\s*;")
JAVA_IMPORT = re.compile(r"(?m)^\s*import\s+(?:static\s+)?([\w.]+?)(?:\.\*)?\s*;")
JAVA_TYPE = re.compile(r"\b[A-Z][A-Za-z0-9_$]*\b")
MAVEN = Path.home() / ".m2/repository"
WALA_DEPENDENCIES = (
    "com/ibm/wala/com.ibm.wala.core/1.7.2/com.ibm.wala.core-1.7.2.jar",
    "com/ibm/wala/com.ibm.wala.shrike/1.7.2/com.ibm.wala.shrike-1.7.2.jar",
    "com/ibm/wala/com.ibm.wala.util/1.7.2/com.ibm.wala.util-1.7.2.jar",
    "org/json/json/20251224/json-20251224.jar",
    "org/jspecify/jspecify/1.0.0/jspecify-1.0.0.jar",
    "com/google/code/gson/gson/2.13.2/gson-2.13.2.jar",
    "com/google/errorprone/error_prone_annotations/2.41.0/error_prone_annotations-2.41.0.jar",
    "com/fasterxml/jackson/core/jackson-databind/2.20.0/jackson-databind-2.20.0.jar",
    "com/fasterxml/jackson/core/jackson-annotations/2.20/jackson-annotations-2.20.jar",
    "com/fasterxml/jackson/core/jackson-core/2.20.0/jackson-core-2.20.0.jar",
)


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _write(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n")


def _run(command: list[str], *, cwd: Path, timeout: int) -> tuple[subprocess.CompletedProcess[str], float]:
    started = time.monotonic()
    process = subprocess.Popen(command, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               text=True, start_new_session=True)
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        os.killpg(process.pid, signal.SIGKILL)
        stdout, stderr = process.communicate()
        raise subprocess.TimeoutExpired(command, timeout, output=stdout, stderr=stderr) from exc
    completed = subprocess.CompletedProcess(command, process.returncode, stdout, stderr)
    return completed, time.monotonic() - started


def _remaining(deadline: float, phase_limit: int) -> int:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("generic Java backend total deadline exhausted")
    return max(1, int(min(remaining, phase_limit)))


def _replace_entry(test: str, method: str, parameters: list[tuple[str, str]]) -> str:
    old = method + "()"
    if test.count(old) != 1:
        raise ValueError("selected zero-argument Java entry is not unique in the masked test")
    rendered = ", ".join(f"{type_name} {name}" for name, type_name in parameters)
    return test.replace(old, f"{method}({rendered})")


def _missing_variables(stderr: str, *, test_path: str | None = None,
                       start_line: int | None = None, end_line: int | None = None) -> list[str]:
    values: list[str] = []
    pattern = re.compile(
        r"(?ms)^([^\n]+):(\d+): error: cannot find symbol\n.*?"
        r"^\s*symbol:\s+variable\s+([A-Za-z_$][\w$]*)\s*$"
    )
    for path, line_text, name in pattern.findall(stderr):
        line = int(line_text)
        if test_path is not None and not path.endswith(test_path):
            continue
        if start_line is not None and line < start_line:
            continue
        if end_line is not None and line > end_line:
            continue
        if name not in values:
            values.append(name)
    return values


def _type_updates(stderr: str, missing: list[str]) -> dict[str, str]:
    """Recover only javac constraints whose caret identifies one missing name."""
    updates: dict[str, str] = {}
    pattern = re.compile(
        r"(?m)^.*?:(\d+): error: incompatible types: .*? cannot be converted to ([^\n]+)\n"
        r"([^\n]*)\n([ ]*)\^"
    )
    for match in pattern.finditer(stderr):
        required = match.group(2).strip()
        source_line, caret = match.group(3), len(match.group(4))
        candidates = []
        for name in missing:
            for occurrence in re.finditer(rf"\b{re.escape(name)}\b", source_line):
                if occurrence.start() <= caret <= occurrence.end():
                    candidates.append(name)
        if not candidates:
            present = [name for name in missing if re.search(rf"\b{re.escape(name)}\b", source_line)]
            if len(present) == 1:
                candidates = present
        if len(set(candidates)) == 1 and re.fullmatch(r"[A-Za-z_$][\w$]*(?:\[\])?", required):
            name = candidates[0]
            if name in updates and updates[name] != required:
                raise ValueError(f"conflicting visible type constraints for {name}")
            updates[name] = required
    return updates


def _compile(source_root: Path, classes: Path, sources: list[Path], classpath: str,
             *, timeout: int) -> tuple[subprocess.CompletedProcess[str], float]:
    if classes.exists():
        shutil.rmtree(classes)
    classes.mkdir()
    return _run(["javac", "-proc:none", "-g", "-parameters", "-encoding", "UTF-8", "-cp", classpath,
                 "-d", str(classes), *map(str, sources)], cwd=source_root, timeout=timeout)


def _prepare_harness(test_text: str, method: str, write_test, compile_once,
                     *, test_path: str | None = None, start_line: int | None = None,
                     end_line: int | None = None, max_rounds: int = 12,
                     record_attempts=None,
                     ) -> tuple[str, list[dict[str, str]], list[dict[str, Any]]]:
    """Compile raw input, then add only structurally constrained unknown parameters."""
    attempts: list[dict[str, Any]] = []
    write_test(test_text)
    result, seconds = compile_once()
    attempts.append({"kind": "raw", "exit_code": result.returncode, "seconds": seconds,
                     "stderr": result.stderr[-6000:]})
    if record_attempts is not None:
        record_attempts(attempts)
    if result.returncode == 0:
        return test_text, [], attempts
    missing = _missing_variables(result.stderr, test_path=test_path,
                                 start_line=start_line, end_line=end_line)
    if not missing:
        raise ValueError("masked Java input does not compile and no deleted variable is identifiable")
    types = {name: "Object" for name in missing}
    for round_index in range(max_rounds):
        parameters = [(name, types[name]) for name in missing]
        harness = _replace_entry(test_text, method, parameters)
        write_test(harness)
        result, seconds = compile_once()
        attempts.append({"kind": "structural_parameter_harness", "round": round_index,
                         "parameters": [{"name": name, "type": types[name]} for name in missing],
                         "exit_code": result.returncode, "seconds": seconds,
                         "stderr": result.stderr[-6000:]})
        if record_attempts is not None:
            record_attempts(attempts)
        if result.returncode == 0:
            return harness, attempts[-1]["parameters"], attempts
        updates = _type_updates(result.stderr, missing)
        changed = {name: required for name, required in updates.items() if types[name] != required}
        if not changed:
            raise ValueError("deleted Java declaration types are not uniquely constrained by visible usage")
        types.update(changed)
    raise ValueError("structural Java harness did not converge within its round budget")


def _java_code(text: str) -> str:
    return re.sub(
        r'//[^\n]*|/\*.*?\*/|""".*?"""|"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'',
        lambda match: "".join("\n" if char == "\n" else " " for char in match.group()),
        text, flags=re.DOTALL)


def _java_source_hints(text: str) -> set[str]:
    """Return direct import owners and same-package type references from visible code."""
    text = _java_code(text)
    hints = set()
    for imported in JAVA_IMPORT.findall(text):
        parts = imported.split(".")
        type_index = next((index for index, part in enumerate(parts)
                           if part and part[0].isupper()), None)
        if type_index is not None:
            hints.add(".".join(parts[:type_index + 1]))
    package = JAVA_PACKAGE.search(text)
    if package:
        hints.update(package.group(1) + "." + name.split("$", 1)[0]
                     for name in JAVA_TYPE.findall(text))
    return hints


def _expand_java_source_closure(case, selected: list[dict[str, Any]], *, max_files: int,
                                max_bytes: int) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Resolve a separately bounded fixed point of admitted visible source types."""
    paths_by_type: dict[str, list[str]] = {}
    paths_by_simple: dict[str, list[str]] = {}
    marker = "src/main/java/"
    for path in case.source_paths:
        if path.startswith(marker) and path.endswith(".java"):
            type_name = path[len(marker):-5].replace("/", ".")
            paths_by_type.setdefault(type_name, []).append(path)
            paths_by_simple.setdefault(type_name.rsplit(".", 1)[-1], []).append(path)
    rows = list(selected)
    selected_paths = {row["logical_path"] for row in rows}
    total_bytes = sum(len(row["text"].encode()) for row in rows)
    evidence: list[dict[str, Any]] = []
    cursor = 1
    while cursor < len(rows):
        source = rows[cursor]
        cursor += 1
        source_hints = _java_source_hints(source["text"])
        for hint in sorted(source_hints):
            candidates = paths_by_type.get(hint, [])
            if len(candidates) != 1:
                admitted = paths_by_simple.get(hint.rsplit(".", 1)[-1], [])
                exact_alternative = any(
                    other != hint
                    and other.rsplit(".", 1)[-1] == hint.rsplit(".", 1)[-1]
                    and len(paths_by_type.get(other, [])) == 1
                    for other in source_hints)
                if (candidates or admitted) and not exact_alternative:
                    evidence.append({"from_path": source["logical_path"], "type": hint,
                                     "candidates": candidates or admitted,
                                     "status": "ambiguous_or_package_mismatched_visible_type"})
                continue
            if candidates[0] in selected_paths:
                continue
            path = candidates[0]
            if len(rows) >= max_files:
                evidence.append({"from_path": source["logical_path"], "type": hint,
                                 "status": "file_budget"})
                continue
            admitted = case.read_source(path)
            size = len(admitted.text.encode())
            if total_bytes + size > max_bytes:
                evidence.append({"from_path": source["logical_path"], "type": hint,
                                 "path": path, "status": "byte_budget"})
                continue
            row = {"source_id": f"source:{len(rows)}", "logical_path": path,
                   "text": admitted.text, "start_line": admitted.start_line}
            rows.append(row)
            selected_paths.add(path)
            total_bytes += size
            evidence.append({"from_path": source["logical_path"], "type": hint,
                             "path": path, "status": "selected_unique_visible_type"})
    return rows, evidence


def _runtime_dependencies(snapshot: Path) -> tuple[list[Path], dict[str, Any]]:
    runtime = json.loads((snapshot / "control/runtime-lock.json").read_text())
    dependencies = []
    for item in runtime.get("dependency_hashes", []):
        if not isinstance(item, list) or len(item) != 2:
            raise ValueError("invalid reviewed dependency entry")
        relative = PurePosixPath(item[0])
        if len(relative.parts) != 2 or relative.parts[0] != "libs" or relative.suffix != ".jar":
            raise ValueError("reviewed dependency path escapes libs")
        actual = snapshot / "tree/repo" / relative
        if actual.is_symlink() or not actual.is_file():
            raise ValueError(f"reviewed dependency is unavailable: {relative}")
        dependencies.append(actual)
    if not dependencies:
        raise ValueError("reviewed runtime has no Java dependencies")
    return dependencies, runtime


def _entry_descriptor(classes: Path, entry_class: str, method: str) -> str:
    completed, _ = _run(["javap", "-classpath", str(classes), "-p", "-s", entry_class],
                        cwd=classes, timeout=30)
    if completed.returncode:
        raise RuntimeError("javap could not inspect the compiled analysis entry")
    lines = completed.stdout.splitlines()
    descriptors = []
    for index, line in enumerate(lines[:-1]):
        if re.search(rf"\b{re.escape(method)}\s*\(", line) and "descriptor:" in lines[index + 1]:
            descriptors.append(lines[index + 1].split("descriptor:", 1)[1].strip())
    if len(descriptors) != 1:
        raise ValueError("compiled Java entry descriptor is not unique")
    return method + descriptors[0]


def _assertion_fragment(test_text: str, line: int) -> str:
    source_line = test_text.splitlines()[line - 1]
    matches = re.findall(r"\b(assert[A-Za-z0-9_$]*)\s*\(", source_line)
    if len(matches) != 1:
        raise ValueError(f"observation line {line} does not contain one assertion invocation")
    return "." + matches[0] + "("


def _class_source_map(source_records: list[dict[str, Any]]) -> dict[str, str]:
    result = {}
    marker = "src/main/java/"
    for source in source_records:
        path = source["logical_path"]
        if marker in path and path.endswith(".java"):
            result[path.split(marker, 1)[1][:-5].replace("/", ".")] = path
    return result


def _source_for_signature(signature: str | None, classes: dict[str, str]) -> str | None:
    if not signature:
        return None
    for class_name in sorted(classes, key=len, reverse=True):
        if signature.startswith(class_name + ".") or signature.startswith(class_name + "$"):
            return classes[class_name]
    return None


def _endpoint(path: str, line: int | None, *, symbol: str | None = None) -> dict[str, Any]:
    value: dict[str, Any] = {"logical_path": path}
    if line is not None:
        value.update(start_line=line, end_line=line)
    if symbol:
        value["symbol"] = symbol
    return value


def _homogeneous_dependency_path(raw: dict[str, Any], start: int, end: int,
                                 allowed_labels: set[str]) -> list[dict[str, Any]] | None:
    outgoing: dict[int, list[dict[str, Any]]] = {}
    for edge in raw.get("slice_edges", []):
        labels = set(edge.get("labels", []))
        if labels and labels <= allowed_labels:
            outgoing.setdefault(int(edge["from"]), []).append(edge)
    queue: list[tuple[int, list[dict[str, Any]]]] = [(start, [])]
    seen = {start}
    while queue:
        current, path = queue.pop(0)
        if current == end:
            return path
        for edge in outgoing.get(current, []):
            target = int(edge["to"])
            if target not in seen:
                seen.add(target)
                queue.append((target, [*path, edge]))
    return None


def _typed_product_paths(raw: dict[str, Any], classes: dict[str, str],
                         opaque_by_path: dict[str, list[dict[str, Any]]]
                         ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    statements = raw.get("slice_statements", [])
    seed = raw.get("seed_index")
    if type(seed) is not int or not 0 <= seed < len(statements):
        return [], []
    results = []
    suppressed = []
    families = (("value_flow", {"DATA_DEP", "HEAP_DATA_DEP"}),
                ("control_dependence", {"CONTROL_DEP"}))
    for index, statement in enumerate(statements):
        path = _source_for_signature(statement.get("method"), classes)
        line = statement.get("line")
        if path is None or type(line) is not int or line < 1:
            continue
        for relation_kind, labels in families:
            dependency_path = _homogeneous_dependency_path(raw, index, seed, labels)
            if dependency_path:
                statement_indexes = [index, *(int(edge["to"]) for edge in dependency_path)]
                opaque_hits: list[dict[str, Any]] = []
                for statement_index in statement_indexes:
                    mapped = statements[statement_index]
                    mapped_path = _source_for_signature(mapped.get("method"), classes)
                    mapped_line = mapped.get("line")
                    if mapped_path is None or type(mapped_line) is not int:
                        continue
                    opaque_hits.extend({**opaque, "logical_path": mapped_path,
                                        "statement_index": statement_index}
                                       for opaque in opaque_by_path.get(mapped_path, [])
                                       if opaque["start_line"] <= mapped_line <= opaque["end_line"])
                if opaque_hits:
                    suppressed.append({"kind": relation_kind, "source_path": path,
                                       "source_line": line, "opaque_hits": opaque_hits})
                    continue
                results.append({"kind": relation_kind, "source_path": path,
                                "source_line": line, "source_statement": index,
                                "seed_statement": seed, "dependency_path": dependency_path})
    return results, suppressed


def _contract(entry: dict[str, Any], context: dict[str, Any], sources: list[dict[str, Any]],
              criteria: list[dict[str, Any]], analyses: list[dict[str, Any]], costs: dict[str, Any],
              harness_parameters: list[dict[str, str]], boundaries: list[dict[str, Any]],
              opaque_methods: list[dict[str, Any]]) -> tuple[dict, dict]:
    observations = [{"observation_id": item["criterion_id"],
                     "location": {"source_id": "test", "start_line": item["line"],
                                  "end_line": item["line"]},
                     "kind": item["kind"],
                     "origin": "explicit" if item["confidence"] == "explicit" else "candidate",
                     "reason": item["reason"]} for item in criteria]
    classes = _class_source_map(sources)
    source_paths = {item["source_id"]: item["logical_path"] for item in sources}
    opaque_by_path: dict[str, list[dict[str, Any]]] = {}
    for opaque in opaque_methods:
        path = source_paths.get(opaque["source_id"])
        if path is not None:
            opaque_by_path.setdefault(path, []).append(opaque)
    relations: list[dict[str, Any]] = []
    relation_keys: set[tuple] = set()
    candidates: dict[tuple[str, str], dict[str, Any]] = {}
    mapped_ids = set()
    graph_nodes = graph_edges = raw_cross_source = typed_cross_source = 0
    for analysis in analyses:
        observation_id, raw = analysis["observation_id"], analysis.get("raw")
        if raw is None:
            continue
        mapped_ids.add(observation_id)
        graph_nodes += int(raw.get("slice_statement_count", 0))
        graph_edges += int(raw.get("slice_edge_count", 0))
        observation = next(item for item in observations if item["observation_id"] == observation_id)
        observation_endpoint = _endpoint(context["test_path"], observation["location"]["start_line"])
        typed_paths, suppressed_paths = _typed_product_paths(raw, classes, opaque_by_path)
        boundaries.extend({"scope": observation_id,
                           "reason": "typed_path_crosses_opaque_placeholder",
                           "details": suppressed} for suppressed in suppressed_paths)
        for path in typed_paths:
            rkey = (path["kind"], path["source_path"], path["source_line"], observation_id)
            if rkey in relation_keys or len(relations) >= MAX_RELATIONS:
                continue
            relation_keys.add(rkey)
            typed_cross_source += 1
            relations.append({"kind": path["kind"], "modality": "analysis_may",
                              "from": _endpoint(path["source_path"], path["source_line"]),
                              "to": observation_endpoint,
                              "provenance": {"backend": "WALA 1.7.2 0-1-CFA + backward SDG",
                                             "source_statement": path["source_statement"],
                                             "seed_statement": path["seed_statement"],
                                             "dependency_path": path["dependency_path"]},
                              "precision": "homogeneous typed NO_HEAP SDG dependency path to exact observation seed",
                              "observation_ids": [observation_id],
                              "assumptions": ["context-insensitive predecessor traversal does not enforce matched calls"]})
        for statement in raw.get("slice_statements", []):
            product_path = _source_for_signature(statement.get("method"), classes)
            line = statement.get("line")
            if product_path and isinstance(line, int):
                # Slice membership proves influence under the configured SDG, but the
                # flattened slice does not identify whether the connecting edge is
                # data, heap, or control. Keep the membership as evidence instead of
                # assigning an unsound typed relation.
                raw_cross_source += 1
            if statement.get("method") == context["entry_signature"] and statement.get("declared_target"):
                target = statement["declared_target"]
                target_path = _source_for_signature(target, classes)
                if target_path and "<init>" not in target:
                    key = (target_path, target)
                    candidates[key] = {"logical_path": target_path, "symbol": target,
                                       "evidence": _endpoint(context["test_path"], line)}
                    rkey = ("call_binding", target, line, observation_id)
                    if rkey not in relation_keys and len(relations) < MAX_RELATIONS:
                        relation_keys.add(rkey)
                        relations.append({"kind": "call_binding", "modality": "analysis_may",
                                          "from": _endpoint(context["test_path"], line,
                                                            symbol=context["entry_signature"]),
                                          "to": _endpoint(target_path, None, symbol=target),
                                          "provenance": {"backend": "WALA 1.7.2 0-1-CFA + backward SDG",
                                                         "ssa_instruction": statement.get("instruction")},
                                          "precision": "entry SSA call retained in exact observation slice",
                                          "observation_ids": [observation_id], "assumptions": []})
    candidate_values = list(candidates.values())
    binding_status = "unique" if len(candidate_values) == 1 else "ambiguous" if candidate_values else "unresolved"
    missing = [item["observation_id"] for item in observations if item["observation_id"] not in mapped_ids]
    if len(relations) >= MAX_RELATIONS:
        boundaries.append({"scope": "relation_projection", "reason": "relation_budget",
                           "limit": MAX_RELATIONS})
    failures = ["semantic_validation_not_evaluated"]
    if missing:
        failures.append("observation_mapping_incomplete")
    if binding_status != "unique":
        failures.append("mut_binding_" + binding_status)
    unknown_effects = [{"boundary_id": "entry-parameter:" + item["name"],
                        "effect": "unknown_return_exception_and_reachable_state_effects",
                        "scope": {"entry_parameter": item}, "provenance": "structural parameter harness",
                        "precision": "unknown_effect", "assumptions": []}
                       for item in harness_parameters]
    unknown_effects.extend({
        "boundary_id": f"opaque:{item['source_id']}:{item['start_line']}:{item['name']}",
        "effect": "placeholder_method_body_does_not_provide_product_semantics",
        "scope": item, "provenance": "visible admitted source placeholder detection",
        "precision": "unknown_effect", "assumptions": [],
    } for item in opaque_methods)
    contract = {
        "schema_version": RESULT_SCHEMA_VERSION,
        "identity": {"case_id": entry["case_id"], "dataset": entry["dataset"], "language": "java",
                     "snapshot_id": entry["snapshot_id"], "masking_contract": context["masking_contract"],
                     "masked_test_sha256": context["masked_test_sha256"],
                     "target_selector": context["target_selector"]},
        "source_scope": [{"source_id": item["source_id"], "logical_path": item["logical_path"],
                          "sha256": item["sha256"], "bytes": item["bytes"],
                          "selection_role": "masked_test" if item["source_id"] == "test"
                          else "candidate_product"} for item in sources],
        "observations": observations,
        "parameterization": {"status": "not_expanded", "coverage": "selected_test_definition",
                             "reason": "parameter harness represents unknown masked declarations; no data rows expanded"},
        "entry_point": {"test_selection": {"origin": "task_provided", "status": "unique",
                                            "selector": context["target_selector"],
                                            "evidence": context["target_scope"]},
                        "mut_binding": {"origin": "automatic", "status": binding_status,
                                        "candidates": candidate_values,
                                        "evidence": [item["evidence"] for item in candidate_values],
                                        "assumptions": []}},
        "relations": relations,
        "relation_coverage": [{"kind": kind, "status": "not_evaluated"}
                              for kind in sorted(RELATION_KINDS)],
        "analysis_boundaries": {"assumptions": [
            "structural entry parameters have no configured Mock behaviour",
            "WALA reflection is disabled and opaque effects remain conservative boundaries",
            "generic Java traversal is context-insensitive may analysis"],
            "unknown_effects": unknown_effects, "unanalysed_regions": [*boundaries, {
                "scope": "product_slice_membership",
                "reason": "raw WALA slice membership retained without inventing data/control/state edge types"}, {
                "scope": "heap_and_matched_call_precision",
                "reason": "GENERIC_NO_HEAP omits heap and control dependencies; hash-backed predecessor traversal does not enforce matched call/return contexts"}]},
        "completion": {"status": "partial", "stage": "generic_wala_observation_mapping",
                       "failure_categories": failures, "error": None},
        "semantic_validation": {"status": "not_evaluated", "reviewer": None, "evidence": [],
                                "notes": "WALA relations are unreviewed analysis evidence, not benchmark gold"},
        "cost": {**costs, "graph_size": {"nodes": graph_nodes, "edges": graph_edges}},
    }
    validate_result_contract(contract)
    evidence = {"observations_total": len(observations), "observations_mapped": len(mapped_ids),
                "product_call_bindings": sum(item["kind"] == "call_binding" for item in relations),
                "product_slice_memberships": raw_cross_source,
                "cross_source_relations": typed_cross_source,
                "observation_linked_cross_source_relations": typed_cross_source,
                "missing_observations": missing, "mut_binding_status": binding_status}
    if missing:
        evidence["analysis_entry_status"] = "observation_mapping_incomplete"
    elif evidence["observation_linked_cross_source_relations"]:
        evidence["analysis_entry_status"] = "observation_linked_to_product"
    elif evidence["product_call_bindings"]:
        evidence["analysis_entry_status"] = "product_call_binding_only"
    elif mapped_ids:
        evidence["analysis_entry_status"] = "observations_mapped_only"
    else:
        evidence["analysis_entry_status"] = "no_observation_mapping"
    return contract, evidence


def execute(entry: dict[str, Any], registry_root: Path, output: Path, *, max_files: int = DEFAULT_MAX_FILES,
            max_bytes: int = DEFAULT_MAX_BYTES, compile_timeout: int = 120,
            analysis_timeout: int = 180, total_timeout: int = 1100,
            compile_max_files: int = DEFAULT_COMPILE_MAX_FILES,
            compile_max_bytes: int = DEFAULT_COMPILE_MAX_BYTES) -> dict[str, Any]:
    if output.exists():
        raise FileExistsError(f"output must not exist: {output}")
    if min(max_files, max_bytes, compile_timeout, analysis_timeout, total_timeout,
           compile_max_files, compile_max_bytes) < 1:
        raise ValueError("generic Java budgets and deadlines must be positive")
    output.mkdir(parents=True)
    started = time.monotonic()
    deadline = started + total_timeout
    entry_evidence: dict[str, Any] = {"input_prepared": False, "bytecode_compiled": False,
        "graph_created": False, "observations_total": 0, "observations_mapped": 0,
        "product_call_bindings": 0, "product_slice_memberships": 0, "cross_source_relations": 0,
        "observation_linked_cross_source_relations": 0, "missing_observations": [],
        "analysis_entry_status": "not_started", "current_stage": "admission"}
    try:
        if entry.get("language") != "java":
            raise ValueError("generic WALA backend requires a Java inventory entry")
        dataset = FrozenDataset(registry_root, dataset=entry["dataset"],
                                inventory_revision=entry["inventory_revision"])
        with dataset.load_case(entry["case_id"]) as case:
            if case.snapshot_id != entry["snapshot_id"] or case.language != "java":
                raise ValueError("frozen case identity differs from inventory entry")
            extraction = extract_criteria(case.masked_test.text, "java", case.target_selector)
            criteria = extraction["criteria"]
            if not criteria:
                raise ValueError("selected Java test has no visible observation")
            for index, criterion in enumerate(criteria):
                criterion["criterion_id"] = f"criterion:{index}"
            entry_evidence["observations_total"] = len(criteria)
            entry_evidence["missing_observations"] = [item["criterion_id"] for item in criteria]
            selection = select_sources(case, max_files=max_files, max_bytes=max_bytes)
            selected = selection.pop("sources")
            selected, closure_evidence = _expand_java_source_closure(
                case, selected, max_files=compile_max_files, max_bytes=compile_max_bytes)
            source_rows, total_bytes = [], 0
            for row in selected:
                if row["source_id"] != "test" and not row["logical_path"].startswith("src/main/"):
                    raise ValueError("Java source selection included a non-product support source")
                size = len(row["text"].encode())
                total_bytes += size
                source_rows.append({"source_id": row["source_id"], "logical_path": row["logical_path"],
                                    "text": row["text"], "sha256": _sha256(row["text"]), "bytes": size})
            opaque_methods = [opaque for row in source_rows
                              for opaque in _java_opaque(row["source_id"], row["text"])]
            context = {"target_selector": case.target_selector, "target_scope": extraction["target_scope"]["locations"],
                       "masking_contract": case._masking_contract,
                       "masked_test_sha256": _sha256(case.masked_test.text),
                       "test_path": case.masked_test.logical_path}
        entry_evidence["input_prepared"] = True
        source_records = [{key: row[key] for key in ("source_id", "logical_path", "sha256", "bytes")}
                          for row in source_rows]
        _write(output / "input-provenance.json", {
            "identity": {key: entry.get(key) for key in
                         ("dataset", "case_id", "language", "snapshot_id", "inventory_revision")},
            "masking_contract": context["masking_contract"],
            "masked_test_sha256": context["masked_test_sha256"],
            "target_selector": context["target_selector"],
            "target_scope": context["target_scope"],
            "criteria": criteria, "sources": source_records,
            "closure_evidence": closure_evidence, "opaque_methods": opaque_methods,
            "driver_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "probe_sha256": hashlib.sha256(BACKEND_SOURCE.read_bytes()).hexdigest(),
            "target_executed": False, "source_text_persisted": False,
        })
        snapshot = registry_root / "formal-snapshots" / entry["dataset"] / entry["snapshot_id"]
        dependencies, runtime = _runtime_dependencies(snapshot)
        wala_dependencies = [MAVEN / relative for relative in WALA_DEPENDENCIES]
        if any(not path.is_file() for path in wala_dependencies):
            raise FileNotFoundError("installed WALA backend dependency is absent")
        entry_class, method = context["target_selector"].rsplit("#", 1)
        entry_evidence["current_stage"] = "source_compilation"
        timings: dict[str, float] = {}
        with tempfile.TemporaryDirectory(prefix="mockspec-generic-wala-") as temporary:
            work = Path(temporary); source_root = work / "source"; source_root.mkdir()
            classes = work / "classes"; backend_classes = work / "backend-classes"; backend_classes.mkdir()
            paths = []
            for row in source_rows:
                target = source_root / PurePosixPath(row["logical_path"])
                target.parent.mkdir(parents=True, exist_ok=True); target.write_text(row["text"]); paths.append(target)
            test_path = paths[0]
            classpath = os.pathsep.join(map(str, dependencies))
            def write_test(text): test_path.write_text(text)
            def compile_once():
                return _compile(source_root, classes, paths, classpath,
                                timeout=_remaining(deadline, compile_timeout))
            harness, harness_parameters, attempts = _prepare_harness(
                source_rows[0]["text"], method, write_test, compile_once,
                test_path=context["test_path"],
                start_line=context["target_scope"][0]["start_line"],
                end_line=context["target_scope"][0]["end_line"],
                record_attempts=lambda value: _write(output / "compile-attempts.json", value))
            _write(output / "compile-attempts.json", attempts)
            timings["source_compilation"] = sum(item["seconds"] for item in attempts)
            entry_evidence["bytecode_compiled"] = True
            entry_evidence["current_stage"] = "backend_compilation"
            entry_selector = _entry_descriptor(classes, entry_class, method)
            context["entry_signature"] = entry_class + "." + entry_selector
            application = work / "application.jar"
            jar_result, jar_seconds = _run(["jar", "--create", "--file", str(application), "-C", str(classes), "."],
                                           cwd=work, timeout=_remaining(deadline, 30))
            timings["jar"] = jar_seconds
            if jar_result.returncode:
                raise RuntimeError("compiled application packaging failed")
            wala_cp = os.pathsep.join(map(str, wala_dependencies))
            backend_result, backend_seconds = _run(
                ["javac", "-proc:none", "-g", "-cp", wala_cp, "-d", str(backend_classes), str(BACKEND_SOURCE)],
                cwd=work, timeout=_remaining(deadline, compile_timeout))
            timings["backend_compilation"] = backend_seconds
            (output / "backend-compile.stderr").write_text(backend_result.stderr)
            if backend_result.returncode:
                raise RuntimeError("WALA probe compilation failed")
            entry_evidence["current_stage"] = "observation_analysis"
            analyses: list[dict[str, Any]] = []
            library_cp = os.pathsep.join(map(str, dependencies))
            analysis_seconds = 0.0
            for criterion_index, criterion in enumerate(criteria):
                if time.monotonic() >= deadline:
                    analyses.extend({"observation_id": pending["criterion_id"], "raw": None,
                                     "error": "generic Java backend total deadline exhausted before observation"}
                                    for pending in criteria[criterion_index:])
                    break
                try:
                    fragment = _assertion_fragment(harness, criterion["line"])
                    query = f"SOURCE_LINE:{criterion['line']}:-1:{fragment}"
                    command = ["java", "-Xmx2g", "-cp", str(backend_classes) + os.pathsep + wala_cp,
                               "org.mockspec.research.WalaSliceProbe", str(application), library_cp,
                               entry_class, entry_selector, query, "OPAQUE_SUMMARIES", "GENERIC_NO_HEAP"]
                    completed, seconds = _run(
                        command, cwd=work, timeout=_remaining(deadline, analysis_timeout))
                    analysis_seconds += seconds
                    (output / f"{criterion['criterion_id']}.stderr").write_text(completed.stderr)
                    if completed.returncode:
                        analyses.append({"observation_id": criterion["criterion_id"], "raw": None,
                                         "error": completed.stderr[-3000:],
                                         "graph_created": "WALA_STAGE call_graph_complete" in completed.stderr,
                                         "root_mapped": "WALA_STAGE sdg_initialized" in completed.stderr})
                    else:
                        raw = json.loads(completed.stdout)
                        _write(output / f"{criterion['criterion_id']}.json", raw)
                        analyses.append({"observation_id": criterion["criterion_id"], "raw": raw,
                                         "graph_created": True, "root_mapped": True})
                except subprocess.TimeoutExpired as exc:
                    stderr = exc.stderr or ""
                    if isinstance(stderr, bytes):
                        stderr = stderr.decode("utf-8", "replace")
                    (output / f"{criterion['criterion_id']}.stderr").write_text(stderr)
                    analyses.append({"observation_id": criterion["criterion_id"], "raw": None,
                                     "error": "WALA observation timeout\n" + stderr[-3000:],
                                     "graph_created": "WALA_STAGE call_graph_complete" in stderr,
                                     "root_mapped": "WALA_STAGE sdg_initialized" in stderr})
                except Exception as exc:
                    analyses.append({"observation_id": criterion["criterion_id"], "raw": None,
                                     "error": f"{type(exc).__name__}: {exc}"})
            timings["wala_analysis"] = analysis_seconds
        entry_evidence["graph_created"] = any(item.get("graph_created") for item in analyses)
        entry_evidence["observation_roots_mapped"] = sum(
            item.get("root_mapped") is True for item in analyses)
        boundaries = [{"scope": item["observation_id"], "reason": item["error"]}
                      for item in analyses if item["raw"] is None]
        boundaries.extend({"scope": "source_closure", "reason": item["status"], "details": item}
                          for item in closure_evidence
                          if item["status"] != "selected_unique_visible_type")
        for item in analyses:
            raw = item.get("raw")
            if raw and raw.get("traversal_truncated"):
                boundaries.append({"scope": item["observation_id"],
                                   "reason": "statement_budget_frontier",
                                   "frontier_count": raw.get("traversal_frontier_count")})
        costs = {"timings_seconds": timings, "total_seconds": time.monotonic() - started,
                 "total_scope": "adapter admission, bounded source compilation, bytecode packaging, WALA CG/SDG and mapping",
                 "peak_memory": {"status": "unavailable", "bytes": None,
                                 "reason": "runner enforces -Xmx2g but does not collect child peak RSS"},
                 "cache_state": {"status": "cold", "reason": "fresh source/classes/application and WALA process per observation"},
                 "input_size": {"files": len(source_rows), "bytes": total_bytes},
                 "unavailable_phases": [{"phase": "semantic_validation", "reason": "not performed"}]}
        contract, evidence = _contract(entry, context, source_records, criteria, analyses, costs,
                                       harness_parameters, boundaries, opaque_methods)
        entry_evidence.update(evidence)
        entry_evidence["current_stage"] = "mapped"
        result = {"schema": SCHEMA, "status": "complete", "backend": "generic_wala_java",
                  "identity": contract["identity"], "result_contract": contract,
                  "entry_evidence": entry_evidence,
                  "source_selection": {"origin": "FrozenCase admitted masked test plus src/main Java sources",
                                       "max_files": max_files, "max_bytes": max_bytes,
                                       "compile_closure_max_files": compile_max_files,
                                       "compile_closure_max_bytes": compile_max_bytes,
                                       "files": len(source_rows), "bytes": total_bytes,
                                       "diagnostics": selection.get("diagnostics", []),
                                       "closure_evidence": closure_evidence,
                                       "opaque_methods": opaque_methods,
                                       "truncated": selection.get("truncated", False)},
                  "structural_harness": {"used": bool(harness_parameters),
                                         "parameters": harness_parameters,
                                         "source_lines_preserved": True, "mock_behavior_added": False},
                  "provenance": {"wala": "1.7.2", "entry_class": entry_class,
                                 "entry_selector": entry_selector, "runtime_lock_digest": runtime["digest"],
                                 "driver_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                                 "probe_sha256": hashlib.sha256(BACKEND_SOURCE.read_bytes()).hexdigest(),
                                 "dependency_count": len(dependencies), "dependency_rehash": False,
                                 "target_executed": False, "selection": "no case allow-list"}}
    except Exception as exc:
        result = {"schema": SCHEMA, "status": "failed", "backend": "generic_wala_java",
                  "identity": {key: entry.get(key) for key in ("dataset", "case_id", "language", "snapshot_id")},
                  "result_contract": None, "error": {"type": type(exc).__name__, "message": str(exc)},
                  "entry_evidence": entry_evidence,
                  "provenance": {"target_executed": False, "dependency_rehash": False,
                                 "driver_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                                 "probe_sha256": hashlib.sha256(BACKEND_SOURCE.read_bytes()).hexdigest()},
                  "elapsed_seconds": time.monotonic() - started}
    _write(output / "batch-result.json", result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--entry", type=Path, required=True)
    parser.add_argument("--registry-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--codeql", type=Path)  # accepted for the shared generic-backend CLI
    parser.add_argument("--max-files", type=int, default=DEFAULT_MAX_FILES)
    parser.add_argument("--max-bytes", type=int, default=DEFAULT_MAX_BYTES)
    parser.add_argument("--compile-timeout", type=int, default=120)
    parser.add_argument("--analysis-timeout", type=int, default=180)
    parser.add_argument("--total-timeout", type=int, default=1100)
    parser.add_argument("--compile-max-files", type=int, default=DEFAULT_COMPILE_MAX_FILES)
    parser.add_argument("--compile-max-bytes", type=int, default=DEFAULT_COMPILE_MAX_BYTES)
    args = parser.parse_args()
    result = execute(json.loads(args.entry.read_text()), args.registry_root, args.output,
                     max_files=args.max_files, max_bytes=args.max_bytes,
                     compile_timeout=args.compile_timeout, analysis_timeout=args.analysis_timeout,
                     total_timeout=args.total_timeout, compile_max_files=args.compile_max_files,
                     compile_max_bytes=args.compile_max_bytes)
    print(json.dumps({"status": result["status"], "case_id": result["identity"].get("case_id"),
                      "entry_evidence": result["entry_evidence"]}, ensure_ascii=False))
    return 0 if result["status"] == "complete" else 1


if __name__ == "__main__":
    raise SystemExit(main())
