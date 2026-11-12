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
EFFECT_ANALYZER_SOURCE = HERE / "backend/src/main/java/org/mockspec/research/ObservationEffectAnalyzer.java"
TYPE_CONSTRAINT_SOURCE = HERE / "backend/src/main/java/org/mockspec/research/JavaTypeConstraints.java"
TYPE_CONSTRAINT_CLASS = "org.mockspec.research.JavaTypeConstraints"
MAX_RELATIONS = 1000
MAX_UNKNOWN_EFFECTS = 1000
MAX_RELATION_PATH_EDGES = 5_000
MAX_OBSERVATION_PROJECTED_EDGES = 50_000
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


def _read_root_checkpoints(
        directory: Path, queries: list[str],
        manifest: list[dict[str, Any]] | None = None) -> dict[str, dict[str, Any]]:
    expected = set(queries)
    if len(expected) != len(queries):
        raise ValueError("checkpoint query identities must be unique")
    returned: dict[str, dict[str, Any]] = {}
    indexes: set[int] = set()
    manifest_by_query: dict[str, dict[str, Any]] | None = None
    if manifest is not None:
        manifest_by_query = {}
        for item in manifest:
            if not isinstance(item, dict):
                raise ValueError("WALA root manifest contains a malformed entry")
            index = item.get("root_index")
            query = item.get("criterion_query")
            expected_file = f"root-{index:05d}.json" if type(index) is int else None
            if (type(index) is not int or not 0 <= index < len(queries)
                    or queries[index] != query or item.get("checkpoint_file") != expected_file
                    or query in manifest_by_query):
                raise ValueError("WALA root manifest duplicated or substituted a criterion")
            manifest_by_query[query] = item
        if set(manifest_by_query) != expected:
            raise ValueError("WALA root manifest omitted a criterion")
    for path in sorted(directory.glob("root-*.json")):
        checkpoint = json.loads(path.read_text())
        if checkpoint.get("schema_version") != "mockspec.wala-root-checkpoint/v1":
            raise ValueError(f"WALA root checkpoint schema differs: {path.name}")
        index = checkpoint.get("root_index")
        query = checkpoint.get("criterion_query")
        root = checkpoint.get("root")
        if type(index) is not int or not 0 <= index < len(queries) or queries[index] != query:
            raise ValueError(f"WALA root checkpoint index/query identity differs: {path.name}")
        if query not in expected or not isinstance(root, dict):
            raise ValueError(f"WALA root checkpoint is malformed or substituted: {path.name}")
        if index in indexes or query in returned:
            raise ValueError(f"duplicate WALA root checkpoint: {path.name}")
        if manifest_by_query is not None:
            item = manifest_by_query[query]
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            expected_status = "error" if root.get("error") else "complete"
            if item.get("checkpoint_sha256") != digest or item.get("status") != expected_status:
                raise ValueError(f"WALA root checkpoint hash/status differs: {path.name}")
        indexes.add(index)
        returned[query] = root
    if manifest_by_query is not None and set(returned) != expected:
        raise ValueError("WALA root checkpoints omitted a manifested criterion")
    return returned


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_checkpoint_root(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as stream:
        checkpoint = json.load(stream)
    root = checkpoint.get("root")
    if not isinstance(root, dict):
        raise ValueError(f"WALA root checkpoint lacks a root object: {path.name}")
    return root


def _validated_root_checkpoint_paths(
        directory: Path, queries: list[str],
        manifest: list[dict[str, Any]] | None = None) -> dict[str, Path]:
    # Reuse the strict identity/status/hash validation without retaining its full roots.
    expected = set(queries)
    manifest_by_query = ({item["criterion_query"]: item for item in manifest}
                         if manifest is not None else None)
    paths: dict[str, Path] = {}
    for path in sorted(directory.glob("root-*.json")):
        with path.open(encoding="utf-8") as stream:
            checkpoint = json.load(stream)
        if checkpoint.get("schema_version") != "mockspec.wala-root-checkpoint/v1":
            raise ValueError(f"WALA root checkpoint schema differs: {path.name}")
        index = checkpoint.get("root_index")
        query = checkpoint.get("criterion_query")
        root = checkpoint.get("root")
        if (type(index) is not int or not 0 <= index < len(queries) or queries[index] != query
                or query not in expected or query in paths or not isinstance(root, dict)):
            raise ValueError(f"WALA root checkpoint identity differs or duplicates: {path.name}")
        if manifest_by_query is not None:
            item = manifest_by_query.get(query)
            expected_name = f"root-{index:05d}.json"
            expected_status = "error" if root.get("error") else "complete"
            if (item is None or item.get("root_index") != index
                    or item.get("checkpoint_file") != expected_name
                    or item.get("status") != expected_status
                    or item.get("checkpoint_sha256") != _sha256_file(path)):
                raise ValueError(f"WALA root manifest differs from checkpoint: {path.name}")
        paths[query] = path
        del checkpoint, root
    if manifest_by_query is not None:
        assert manifest is not None
        if len(manifest_by_query) != len(manifest) or set(manifest_by_query) != expected:
            raise ValueError("WALA root manifest duplicated, omitted or substituted a criterion")
        if set(paths) != expected:
            raise ValueError("WALA root checkpoints omitted a manifested criterion")
    return paths


def _analysis_raw(analysis: dict[str, Any]) -> dict[str, Any] | None:
    raw = analysis.get("raw")
    base: dict[str, Any] | None
    if isinstance(raw, dict):
        base = raw
    else:
        raw_path = analysis.get("raw_path")
        base = _load_checkpoint_root(Path(raw_path)) if isinstance(raw_path, str) else None
    if base is None:
        return None
    effect_path = analysis.get("effect_raw_path")
    effect_error = analysis.get("effect_error")
    if isinstance(effect_path, str):
        effect = _load_checkpoint_root(Path(effect_path))
        if effect.get("criterion", {}).get("query") != analysis.get("checkpoint_query"):
            raise ValueError("WALA effect checkpoint query differs from authenticated base root")
        base = dict(base)
        base["observation_effect_evidence"] = effect.get("observation_effect_evidence", [])
        base["observation_effect_demand"] = effect.get("observation_effect_demand", {})
        base["observation_effect_stage"] = {
            "status": "complete", "checkpoint_sha256": analysis.get("effect_checkpoint_sha256"),
            "cost_seconds": analysis.get("effect_seconds")}
    elif isinstance(effect_error, str):
        base = dict(base)
        base["observation_effect_error"] = effect_error
        base["observation_effect_stage"] = {
            "status": analysis.get("effect_status", "failed"),
            "cost_seconds": analysis.get("effect_seconds"),
            "failed_authenticated_checkpoint_sha256": analysis.get(
                "effect_failed_checkpoint_sha256")}
    return base


def _attach_effect_checkpoint(
        analysis: dict[str, Any], path: Path, expected_query: str) -> tuple[str, str | None]:
    checkpoint = json.loads(path.read_text())
    root = checkpoint.get("root")
    if checkpoint.get("criterion_query") != expected_query or not isinstance(root, dict):
        raise ValueError("WALA effect checkpoint query differs from requested effect root")
    error = root.get("error")
    if error:
        analysis.pop("effect_raw_path", None)
        analysis.pop("effect_checkpoint_sha256", None)
        analysis["effect_failed_checkpoint_sha256"] = _sha256_file(path)
        return "failed", f"WALA effect root error: {error}"
    if root.get("criterion", {}).get("query") != expected_query:
        raise ValueError("WALA effect root query differs from requested effect root")
    analysis["effect_raw_path"] = str(path)
    analysis["effect_checkpoint_sha256"] = _sha256_file(path)
    return "complete", None


def _demanded_effect_roots(
        queries: list[str], query_by_text: dict[str, dict[str, Any]],
        analyses: list[dict[str, Any]], checkpoint_paths: dict[str, Path]
        ) -> list[tuple[str, dict[str, Any], dict[str, Any]]]:
    if len(checkpoint_paths) != len(queries) or not all(
            item.get("root_mapped") for item in analyses):
        return []
    analyses_by_observation = {item["observation_id"]: item for item in analyses}
    demanded = []
    for query, criterion in query_by_text.items():
        analysis = analyses_by_observation[criterion["criterion_id"]]
        raw = _analysis_raw(analysis)
        if raw and raw.get("observation_effect_demand", {}).get(
                "status") == "demanded_instance_field":
            demanded.append((query, criterion, analysis))
        del raw
    return demanded


def _analysis_boundaries(analyses: list[dict[str, Any]],
                         closure_evidence: list[dict[str, Any]]) -> list[dict[str, Any]]:
    boundaries = [{"scope": item["observation_id"], "reason": item["error"]}
                  for item in analyses if item.get("raw") is None and not item.get("raw_path")]
    boundaries.extend({"scope": "source_closure", "reason": item["status"], "details": item}
                      for item in closure_evidence
                      if item["status"] != "selected_unique_visible_type")
    boundaries.extend({"scope": item["observation_id"],
                       "reason": "statement_budget_frontier",
                       "frontier_count": item.get("traversal_frontier_count")}
                      for item in analyses if item.get("traversal_truncated"))
    return boundaries


def _recover_checkpoint_analyses(
        queries: list[str], query_by_text: dict[str, dict[str, Any]],
        completed_roots: dict[str, dict[str, Any]], *, missing_error: str,
        graph_created: bool) -> list[dict[str, Any]]:
    analyses: list[dict[str, Any]] = []
    for root_index, query in enumerate(queries):
        criterion = query_by_text[query]
        root = completed_roots.get(query)
        if root is not None and not root.get("error"):
            analyses.append({"observation_id": criterion["criterion_id"], "raw": root,
                             "graph_created": True, "root_mapped": True,
                             "checkpoint_recovered": True})
        else:
            analyses.append({"observation_id": criterion["criterion_id"], "raw": None,
                             "error": root.get("error") if root else missing_error,
                             "graph_created": graph_created, "root_mapped": False})
    return analyses


def _checkpoint_path_analyses(
        queries: list[str], query_by_text: dict[str, dict[str, Any]],
        checkpoint_paths: dict[str, Path], *, missing_error: str,
        graph_created: bool) -> list[dict[str, Any]]:
    analyses: list[dict[str, Any]] = []
    for root_index, query in enumerate(queries):
        criterion = query_by_text[query]
        path = checkpoint_paths.get(query)
        if path is None:
            analyses.append({"observation_id": criterion["criterion_id"], "raw": None,
                             "error": missing_error, "graph_created": graph_created,
                             "root_mapped": False})
            continue
        root = _load_checkpoint_root(path)
        if root.get("error"):
            analyses.append({"observation_id": criterion["criterion_id"], "raw": None,
                             "error": root["error"], "graph_created": True,
                             "root_mapped": False,
                             "raw_checkpoint_sha256": _sha256_file(path),
                             "checkpoint_root_index": root_index,
                             "checkpoint_query": query})
        else:
            analyses.append({"observation_id": criterion["criterion_id"],
                             "raw_path": str(path), "graph_created": True,
                             "root_mapped": True, "checkpoint_recovered": True,
                             "raw_checkpoint_sha256": _sha256_file(path),
                             "checkpoint_root_index": root_index,
                             "checkpoint_query": query,
                             "traversal_truncated": root.get("traversal_truncated", False),
                             "traversal_frontier_count": root.get("traversal_frontier_count", 0)})
        del root
    return analyses


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


def _compile(source_root: Path, classes: Path, sources: list[Path], classpath: str,
             *, timeout: int) -> tuple[subprocess.CompletedProcess[str], float]:
    if classes.exists():
        shutil.rmtree(classes)
    classes.mkdir()
    return _run(["javac", "-proc:none", "-g", "-parameters", "-encoding", "UTF-8", "-cp", classpath,
                 "-d", str(classes), *map(str, sources)], cwd=source_root, timeout=timeout)


def _prepare_harness(test_text: str, method: str, write_test, compile_once,
                     *, test_path: str | None = None, start_line: int | None = None,
                     end_line: int | None = None, record_attempts=None,
                     constraint_resolver=None,
                     ) -> tuple[str, list[dict[str, Any]], list[dict[str, Any]]]:
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
    if constraint_resolver is None:
        raise ValueError("deleted Java declarations require source-attributed type constraints")
    resolution = constraint_resolver(missing)
    parameters = resolution.get("parameters")
    unresolved = resolution.get("unresolved")
    if not isinstance(parameters, list) or not isinstance(unresolved, list):
        raise ValueError("structural type constraint helper returned malformed output")
    resolved = {item.get("name"): item for item in parameters if isinstance(item, dict)}
    if set(resolved) != set(missing) or unresolved:
        reasons = {item.get("name"): item.get("reason") for item in unresolved
                   if isinstance(item, dict)}
        raise ValueError(f"deleted Java declaration types are not uniquely constrained: {reasons}")
    ordered = [resolved[name] for name in missing]
    if any(not isinstance(item.get("type"), str) or not item.get("evidence") for item in ordered):
        raise ValueError("structural type constraint lacks declared type or source evidence")
    harness = _replace_entry(test_text, method,
                             [(item["name"], item["type"]) for item in ordered])
    write_test(harness)
    result, seconds = compile_once()
    attempts.append({"kind": "source_attributed_structural_parameter_harness",
                     "parameters": ordered, "exit_code": result.returncode,
                     "seconds": seconds, "stderr": result.stderr[-6000:]})
    if record_attempts is not None:
        record_attempts(attempts)
    if result.returncode:
        raise ValueError("source-attributed structural Java harness does not compile; source closure or compiler issue remains")
    return harness, ordered, attempts


def _compile_type_constraint_helper(classes: Path, *, cwd: Path, timeout: int) -> float:
    result, seconds = _run(
        ["javac", "-proc:none", "-g", "-d", str(classes), str(TYPE_CONSTRAINT_SOURCE)],
        cwd=cwd, timeout=timeout)
    if result.returncode:
        raise RuntimeError("Java structural type constraint helper compilation failed: "
                           + result.stderr[-3000:])
    return seconds


def _resolve_structural_types(source_root: Path, helper_classes: Path, classpath: str,
                              test_path: str, method: str, start_line: int, end_line: int,
                              missing: list[str], *, timeout: int) -> tuple[dict[str, Any], float]:
    command = ["java", "-cp", str(helper_classes), TYPE_CONSTRAINT_CLASS,
               str(source_root), classpath or "-", test_path, method,
               str(start_line), str(end_line), *missing]
    result, seconds = _run(command, cwd=source_root, timeout=timeout)
    if result.returncode:
        raise RuntimeError("Java structural type constraint helper failed: " + result.stderr[-3000:])
    try:
        value = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise ValueError("Java structural type constraint helper returned invalid JSON") from exc
    if value.get("schema") != "mock-spec.java-type-constraints/v1":
        raise ValueError("Java structural type constraint helper schema mismatch")
    return value, seconds


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


_OBSERVATION_CALLS = {
    "value_assertion": (
        "assertEquals", "assertNotEquals", "assertThat", "assertTrue", "assertFalse",
        "assertNull", "assertNotNull", "assertSame", "assertNotSame", "assertArrayEquals"),
    "exception_observation": (
        "assertThrows", "assertDoesNotThrow", "assertThatThrownBy",
        "assertThatExceptionOfType", "assertThatCode", "expectThrows"),
    "interaction_observation": (
        "verify", "verifyZeroInteractions", "verifyNoInteractions",
        "verifyNoMoreInteractions", "inOrder"),
}


def _criterion_query(test_text: str, criterion: dict[str, Any]) -> str:
    lines = test_text.splitlines()
    line = criterion.get("line")
    if type(line) is not int or line < 1 or line > len(lines):
        raise ValueError("criterion has no valid source line")
    source_line = _java_code(lines[line - 1])
    kind = criterion.get("kind")
    if kind == "candidate_event":
        call = criterion.get("call")
        if not isinstance(call, str) or re.fullmatch(r"[A-Za-z_$][A-Za-z0-9_$]*", call) is None:
            raise ValueError("candidate event has no exact visible call name")
        occurrences = re.findall(rf"\b{re.escape(call)}\s*\(", source_line)
        constructor_owners = re.findall(
            r"\bnew\s+([A-Za-z_$][A-Za-z0-9_$]*(?:\.[A-Za-z_$][A-Za-z0-9_$]*)*)\s*\(",
            source_line)
        matching_constructors = [owner for owner in constructor_owners
                                 if owner.rsplit(".", 1)[-1] == call]
        if len(occurrences) != 1 or len(matching_constructors) > 1:
            raise ValueError("candidate event call is absent or repeated on its source line")
        if matching_constructors and matching_constructors[0] != call:
            raise ValueError("qualified or nested constructor owner is not an exact WALA seed")
        # WALA MethodReference.getSignature() is authenticated in existing probe output as
        # `package.SimpleType.<init>(...)`, so retain the visible constructor owner.
        fragment = f".{call}.<init>(" if matching_constructors else f".{call}("
    elif kind in _OBSERVATION_CALLS:
        allowed = _OBSERVATION_CALLS[kind]
        matches = re.findall(
            rf"\b({'|'.join(map(re.escape, allowed))})\s*\(", source_line)
        if len(matches) != 1:
            raise ValueError(f"{kind} line does not contain one supported observation invocation")
        fragment = "." + matches[0] + "("
    else:
        raise ValueError(f"unsupported Java observation kind: {kind}")
    return f"SOURCE_LINE:{line}:-1:{fragment}"


def _preflight_criterion_queries(
        test_text: str, criteria: list[dict[str, Any]]) -> tuple[
            list[str], dict[str, dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    candidates: list[tuple[dict[str, Any], str]] = []
    failures: list[dict[str, Any]] = []
    records: dict[str, dict[str, Any]] = {}
    for criterion in criteria:
        observation_id = criterion["criterion_id"]
        if not isinstance(observation_id, str) or observation_id in records:
            raise ValueError("criteria contain an invalid or duplicate observation identity")
        try:
            query = _criterion_query(test_text, criterion)
            candidates.append((criterion, query))
            records[observation_id] = {
                "observation_id": observation_id, "query": query, "status": "candidate"}
        except ValueError as exc:
            error = f"unsupported_seed_shape: {exc}"
            failures.append({"observation_id": observation_id, "raw": None,
                             "error": error, "graph_created": False, "root_mapped": False})
            records[observation_id] = {
                "observation_id": observation_id, "query": None,
                "status": "unsupported", "error": error}
    counts: dict[str, int] = {}
    for _, query in candidates:
        counts[query] = counts.get(query, 0) + 1
    queries: list[str] = []
    query_by_text: dict[str, dict[str, Any]] = {}
    for criterion, query in candidates:
        observation_id = criterion["criterion_id"]
        if counts[query] != 1:
            error = f"duplicate_seed_query: {query}"
            failures.append({"observation_id": observation_id, "raw": None,
                             "error": error, "graph_created": False, "root_mapped": False})
            records[observation_id].update(status="duplicate", error=error)
        else:
            queries.append(query)
            query_by_text[query] = criterion
            records[observation_id]["status"] = "ready"
    return (queries, query_by_text, failures,
            [records[criterion["criterion_id"]] for criterion in criteria])


def _merge_criterion_analyses(
        criteria: list[dict[str, Any]], analyses: list[dict[str, Any]],
        preflight_failures: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_id: dict[str, dict[str, Any]] = {}
    for item in [*analyses, *preflight_failures]:
        observation_id = item.get("observation_id")
        if not isinstance(observation_id, str) or observation_id in by_id:
            raise ValueError("criterion analyses contain an invalid or duplicate observation identity")
        by_id[observation_id] = item
    expected = [criterion["criterion_id"] for criterion in criteria]
    if set(by_id) != set(expected) or len(expected) != len(set(expected)):
        raise ValueError("criterion analyses do not cover the original observation identities")
    return [by_id[observation_id] for observation_id in expected]


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


def _reverse_path_index(raw: dict[str, Any], allowed_labels: set[str]
                        ) -> tuple[dict[int, dict[str, Any]], dict[int, int]]:
    seed = raw.get("seed_index")
    if type(seed) is not int:
        return {}, {}
    incoming: dict[int, list[dict[str, Any]]] = {}
    for edge in raw.get("slice_edges", []):
        labels = set(edge.get("labels", []))
        if labels and labels <= allowed_labels:
            incoming.setdefault(int(edge["to"]), []).append(edge)
    toward_seed: dict[int, dict[str, Any]] = {}
    distances = {seed: 0}
    queue = [seed]
    for target in queue:
        for edge in incoming.get(target, []):
            source = int(edge["from"])
            if source not in distances:
                distances[source] = distances[target] + 1
                toward_seed[source] = edge
                queue.append(source)
    return toward_seed, distances


def _indexed_dependency_path(toward_seed: dict[int, dict[str, Any]], distances: dict[int, int],
                             start: int, seed: int, max_edges: int) -> list[dict[str, Any]] | None:
    distance = distances.get(start)
    if distance is None or distance == 0 or distance > max_edges:
        return None
    path = []
    current = start
    while current != seed:
        edge = toward_seed.get(current)
        if edge is None:
            return None
        path.append(edge)
        current = int(edge["to"])
    return path


def _path_indexes(raw: dict[str, Any]) -> dict[str, tuple[dict[int, dict[str, Any]], dict[int, int]]]:
    return {
        "data": _reverse_path_index(raw, {"DATA_DEP", "HEAP_DATA_DEP"}),
        "control": _reverse_path_index(raw, {"CONTROL_DEP"}),
        "mixed": _reverse_path_index(raw, {"DATA_DEP", "CONTROL_DEP"}),
    }


def _consume_indexed_path(indexes: dict[str, tuple[dict[int, dict[str, Any]], dict[int, int]]],
                          family: str, start: int, seed: int,
                          budget: dict[str, int]) -> list[dict[str, Any]] | None:
    if budget["relations"] <= 0:
        budget["omitted_relations"] += 1
        return None
    toward_seed, distances = indexes[family]
    distance = distances.get(start)
    if distance is None or distance == 0:
        return None
    if distance > MAX_RELATION_PATH_EDGES or distance > budget["edges"]:
        budget["omitted_path_edges"] += distance
        budget["omitted_relations"] += 1
        return None
    path = _indexed_dependency_path(toward_seed, distances, start, seed, distance)
    if path:
        budget["relations"] -= 1
        budget["edges"] -= len(path)
    return path


def _outgoing_index(raw: dict[str, Any], allowed_labels: set[str]
                    ) -> dict[int, list[dict[str, Any]]]:
    outgoing: dict[int, list[dict[str, Any]]] = {}
    for edge in raw.get("slice_edges", []):
        labels = set(edge.get("labels", []))
        if labels and labels <= allowed_labels:
            outgoing.setdefault(int(edge["from"]), []).append(edge)
    return outgoing


def _indexed_path_between(outgoing: dict[int, list[dict[str, Any]]], start: int, target: int,
                          max_edges: int) -> list[dict[str, Any]] | None:
    queue: list[int] = [start]
    parent: dict[int, dict[str, Any]] = {}
    distances = {start: 0}
    for current in queue:
        if distances[current] >= max_edges:
            continue
        for edge in outgoing.get(current, []):
            reached = int(edge["to"])
            if reached in distances:
                continue
            parent[reached] = edge
            distances[reached] = distances[current] + 1
            if reached == target:
                path = []
                cursor = target
                while cursor != start:
                    step = parent[cursor]
                    path.append(step)
                    cursor = int(step["from"])
                return list(reversed(path))
            queue.append(reached)
    return None


def _typed_product_paths(raw: dict[str, Any], classes: dict[str, str],
                         opaque_by_path: dict[str, list[dict[str, Any]]],
                         indexes: dict[str, tuple[dict[int, dict[str, Any]], dict[int, int]]] | None = None,
                         budget: dict[str, int] | None = None,
                         ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    statements = raw.get("slice_statements", [])
    seed = raw.get("seed_index")
    if type(seed) is not int or not 0 <= seed < len(statements):
        return [], []
    results = []
    suppressed = []
    indexes = indexes or _path_indexes(raw)
    budget = budget or {"relations": MAX_RELATIONS, "edges": MAX_OBSERVATION_PROJECTED_EDGES,
                        "omitted_relations": 0, "omitted_path_edges": 0}
    families = (("value_flow", "data"), ("control_dependence", "control"))
    for index, statement in enumerate(statements):
        path = _source_for_signature(statement.get("method"), classes)
        line = statement.get("line")
        if path is None or type(line) is not int or line < 1:
            continue
        for relation_kind, family in families:
            dependency_path = _consume_indexed_path(indexes, family, index, seed, budget)
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


def _product_body_paths(raw: dict[str, Any], classes: dict[str, str],
                        indexes: dict[str, tuple[dict[int, dict[str, Any]], dict[int, int]]] | None = None,
                        budget: dict[str, int] | None = None,
                        ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    statements = raw.get("slice_statements", [])
    seed = raw.get("seed_index")
    if type(seed) is not int or not 0 <= seed < len(statements):
        return [], []
    indexed: dict[tuple[str, str, int], int] = {}
    for index, statement in enumerate(statements):
        instruction_index = statement.get("instruction_index")
        if type(instruction_index) is int:
            indexed[(statement.get("method"), statement.get("context"), instruction_index)] = index
    relations: list[dict[str, Any]] = []
    boundaries: list[dict[str, Any]] = []
    indexes = indexes or _path_indexes(raw)
    budget = budget or {"relations": MAX_RELATIONS, "edges": MAX_OBSERVATION_PROJECTED_EDGES,
                        "omitted_relations": 0, "omitted_path_edges": 0}
    control_outgoing = _outgoing_index(raw, {"CONTROL_DEP"})
    for evidence in raw.get("product_body_evidence", []):
        method = evidence.get("method")
        path = _source_for_signature(method, classes)
        line = evidence.get("line")
        statement_index = indexed.get((method, evidence.get("context"),
                                       evidence.get("instruction_index")))
        if path is None or type(line) is not int or line < 1 or statement_index is None:
            continue
        kind = evidence.get("kind")
        if kind == "retained_state_write_witness":
            boundaries.append({"source_path": path, "source_line": line,
                               "reason": "retained_state_write_without_heap_dependency_path",
                               "evidence": evidence})
            continue
        if kind == "value_return":
            dependency_path = _consume_indexed_path(indexes, "data", statement_index, seed, budget)
            if dependency_path:
                relations.append({"kind": "normal_return_dependence", "source_path": path,
                                  "source_line": line, "source_statement": statement_index,
                                  "seed_statement": seed, "dependency_path": dependency_path,
                                  "product_body_evidence": evidence})
            continue
        if kind == "exceptional_exit":
            dependency_path = _consume_indexed_path(
                indexes, "mixed", statement_index, seed, budget)
            if dependency_path and any("CONTROL_DEP" in edge["labels"]
                                       for edge in dependency_path):
                relations.append({"kind": "exception_dependence", "source_path": path,
                                  "source_line": line, "source_statement": statement_index,
                                  "seed_statement": seed, "dependency_path": dependency_path,
                                  "product_body_evidence": evidence})
            continue
        if kind == "control_guard":
            controlled_index = indexed.get((method, evidence.get("context"),
                                            evidence.get("retained_instruction_index")))
            if controlled_index is None or controlled_index == statement_index:
                continue
            downstream = _consume_indexed_path(indexes, "mixed", controlled_index, seed, budget)
            control_path = _indexed_path_between(
                control_outgoing, statement_index, controlled_index, MAX_RELATION_PATH_EDGES)
            if control_path and downstream is not None:
                relations.append({"kind": "control_dependence", "source_path": path,
                                  "source_line": line, "source_statement": statement_index,
                                  "seed_statement": seed,
                                  "dependency_path": [*control_path, *downstream],
                                  "product_body_evidence": evidence})
    return relations, boundaries


def _write_root_evidence_projection(
        path: Path, analysis: dict[str, Any], raw: dict[str, Any] | None,
        published_relations: list[dict[str, Any]], unknown_boundaries: list[dict[str, Any]],
        unknown_effects: list[dict[str, Any]] | None = None) -> None:
    if raw is None:
        _write(path, {
            "schema": "mockspec.wala-root-evidence/v1",
            "observation_id": analysis["observation_id"],
            "status": "failed",
            "error": analysis.get("error", "root analysis unavailable"),
            "graph_created": analysis.get("graph_created", False),
            "root_mapped": False,
            "raw_checkpoint_sha256": analysis.get("raw_checkpoint_sha256"),
            "checkpoint_identity": {"root_index": analysis.get("checkpoint_root_index"),
                                    "query": analysis.get("checkpoint_query")},
            "unknown_boundaries": unknown_boundaries,
            "unknown_effects": unknown_effects or [],
        })
        return
    statements = raw.get("slice_statements", [])
    used_indexes: set[int] = set()
    used_edges: dict[tuple[int, int, tuple[str, ...]], dict[str, Any]] = {}
    for relation in published_relations:
        provenance = relation.get("provenance", {})
        for name in ("source_statement", "seed_statement"):
            index = provenance.get(name)
            if type(index) is int:
                used_indexes.add(index)
        for edge in provenance.get("dependency_path", []):
            source, target = int(edge["from"]), int(edge["to"])
            labels = tuple(edge.get("labels", []))
            used_indexes.update((source, target))
            used_edges[(source, target, labels)] = edge
    for boundary in unknown_boundaries:
        evidence = boundary.get("evidence")
        if isinstance(evidence, dict):
            for index, statement in enumerate(statements):
                if (statement.get("method") == evidence.get("method")
                        and statement.get("context") == evidence.get("context")
                        and statement.get("instruction_index") == evidence.get("instruction_index")):
                    used_indexes.add(index)
        details = boundary.get("details")
        if isinstance(details, dict):
            for hit in details.get("opaque_hits", []):
                if type(hit.get("statement_index")) is int:
                    used_indexes.add(hit["statement_index"])
    projected_statements = [{"original_index": index, "statement": statements[index]}
                            for index in sorted(used_indexes) if 0 <= index < len(statements)]
    total_edges = int(raw.get("slice_edge_count", len(raw.get("slice_edges", []))))
    projection = {
        "schema": "mockspec.wala-root-evidence/v1",
        "observation_id": analysis["observation_id"],
        "status": "projected_not_evaluated",
        "criterion": raw.get("criterion"),
        "checkpoint_identity": {"root_index": analysis.get("checkpoint_root_index"),
                                "query": analysis.get("checkpoint_query")},
        "seed_original_index": raw.get("seed_index"),
        "raw_checkpoint_sha256": analysis.get("raw_checkpoint_sha256"),
        "full_counts": {"statements": len(statements), "edges": total_edges},
        "projected_counts": {"statements": len(projected_statements),
                             "edges": len(used_edges),
                             "relations": len(published_relations)},
        "omitted_counts": {"statements": max(0, len(statements) - len(projected_statements)),
                           "edges": max(0, total_edges - len(used_edges))},
        "statements": projected_statements,
        "edges": list(used_edges.values()),
        "relations": published_relations,
        "unknown_boundaries": unknown_boundaries,
        "unknown_effects": unknown_effects or [],
        "frontier": {"truncated": raw.get("traversal_truncated", False),
                     "frontier_count": raw.get("traversal_frontier_count", 0)},
        "interpretation": "Omitted statements and edges were outside published relation witnesses; omission is not evidence of no influence.",
    }
    _write(path, projection)


def _write_shared_evidence(path: Path, contract: dict[str, Any], costs: dict[str, Any],
                           shared_backend: dict[str, Any]) -> None:
    _write(path, {
        "schema": "mockspec.wala-shared-evidence/v1",
        "identity": contract["identity"],
        "source_scope": contract["source_scope"],
        "entry_point": contract["entry_point"],
        "analysis_assumptions": contract["analysis_boundaries"]["assumptions"],
        "shared_unknown_effects": contract["analysis_boundaries"]["unknown_effects"],
        "cost": costs,
        "backend": {key: shared_backend.get(key) for key in (
            "schema_version", "wala_version", "configuration", "entry",
            "class_hierarchy_classes", "unresolved_classes", "call_graph_nodes",
            "application_call_graph_nodes", "call_graph_contexts", "pointer_instance_keys",
            "timings_millis", "limitations", "generic_mode_boundary",
            "observation_effect_stage")},
        "semantic_validation": contract["semantic_validation"],
    })


def _observed_opaque_unknown_effects(boundaries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    effects: list[dict[str, Any]] = []
    by_identity: dict[tuple[Any, ...], dict[str, Any]] = {}
    for boundary in boundaries:
        if boundary.get("reason") != "typed_path_crosses_opaque_placeholder":
            continue
        observation_id = boundary.get("scope")
        details = boundary.get("details", {})
        for hit in details.get("opaque_hits", []):
            key = (observation_id, hit.get("source_id"), hit.get("start_line"),
                   hit.get("end_line"), hit.get("name"))
            path_candidate = {"kind": details.get("kind"),
                              "source_path": details.get("source_path"),
                              "source_line": details.get("source_line")}
            existing = by_identity.get(key)
            if existing is not None:
                statement_index = hit.get("statement_index")
                indexes = existing["scope"]["observation_statement_indexes"]
                if type(statement_index) is int and statement_index not in indexes:
                    indexes.append(statement_index)
                candidates = existing["scope"]["suppressed_path_candidates"]
                if path_candidate not in candidates:
                    candidates.append(path_candidate)
                continue
            placeholder = {name: value for name, value in hit.items()
                           if name != "statement_index"}
            statement_index = hit.get("statement_index")
            effect = {
                "boundary_id": (f"opaque:{observation_id}:{hit.get('source_id')}:"
                                f"{hit.get('start_line')}:{hit.get('name')}"),
                "effect": "placeholder_method_body_does_not_provide_product_semantics",
                "scope": {"observation_id": observation_id, "placeholder": placeholder,
                          "observation_statement_indexes": (
                              [statement_index] if type(statement_index) is int else []),
                          "suppressed_path_candidates": [path_candidate]},
                "provenance": "typed observation dependency path suppression",
                "precision": "unknown_effect",
                "assumptions": [],
            }
            by_identity[key] = effect
            effects.append(effect)
    return effects


def _project_observation_effects(
        raw: dict[str, Any], observation_id: str, observation_endpoint: dict[str, Any],
        classes: dict[str, str], *, relation_limit: int = MAX_RELATIONS,
        unknown_limit: int = MAX_UNKNOWN_EFFECTS,
        ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    relations: list[dict[str, Any]] = []
    unknowns: list[dict[str, Any]] = []
    boundaries: list[dict[str, Any]] = []
    excluded_effect_count = 0
    excluded_effect_samples: list[dict[str, Any]] = []
    unmapped_writer_count = 0
    unmapped_writer_samples: list[dict[str, Any]] = []
    omitted_relation_count = 0
    omitted_unknown_count = 0
    unknown_ids: set[str] = set()
    demand = raw.get("observation_effect_demand", {})
    if demand.get("status") == "not_demanded_direct_read_absent":
        boundaries.append({"scope": observation_id,
                           "reason": "heap_effect_not_demanded_without_direct_observation_field_read",
                           "details": demand})
    elif demand.get("status") == "not_demanded_static_field_unmodeled":
        boundaries.append({"scope": observation_id,
                           "reason": "static_or_global_observation_field_unmodeled",
                           "details": demand})
    effect_error = raw.get("observation_effect_error")
    if isinstance(effect_error, str):
        boundaries.append({"scope": observation_id,
                           "reason": "observation_effect_stage_frontier",
                           "details": {"error": effect_error,
                                       "stage": raw.get("observation_effect_stage"),
                                       "base_root_preserved": True},
                           "affected_observation_ids": [observation_id]})
    elif demand.get("status") == "demanded_instance_field" and not raw.get(
            "observation_effect_evidence"):
        boundaries.append({"scope": observation_id,
                           "reason": "observation_effect_stage_missing",
                           "details": {"demand": demand, "base_root_preserved": True},
                           "affected_observation_ids": [observation_id]})
    for evidence in raw.get("observation_effect_evidence", []):
        analysis = evidence.get("analysis", {})
        if analysis.get("status") == "partial":
            boundaries.append({"scope": observation_id,
                               "reason": "observation_effect_budget_frontier",
                               "details": {"read": evidence.get("read"),
                                           "full_slice_truncated": evidence.get("full_slice_truncated"),
                                           "full_slice_frontier_count": evidence.get("full_slice_frontier_count"),
                                           "cost": analysis.get("cost"),
                                           "analysis_reason": analysis.get("reason")}})
        for row in analysis.get("visible_state", []):
            write = row.get("write", {})
            source_path = _source_for_signature(write.get("method"), classes)
            line = write.get("line")
            if source_path is None or type(line) is not int or line < 1:
                unmapped_writer_count += 1
                if len(unmapped_writer_samples) < 10:
                    unmapped_writer_samples.append(row)
                continue
            if len(relations) >= relation_limit:
                omitted_relation_count += 1
                continue
            relations.append({
                "kind": "state_flow", "modality": "analysis_may",
                "from": _endpoint(source_path, line, symbol=write.get("method")),
                "to": observation_endpoint,
                "provenance": {"backend": "WALA 1.7.2 FULL heap SDG + ModRef/points-to",
                               "observation_effect": row, "observed_read": evidence.get("read")},
                "precision": "HEAP_DATA_DEP path plus equal typed Mod/Ref pointer key to the original observation",
                "observation_ids": [observation_id],
                "assumptions": ["static context-sensitive may-depend evidence is not runtime feasibility"],
            })
        for row in analysis.get("opaque_state", []):
            if row.get("certainty") == "excluded":
                excluded_effect_count += 1
                if len(excluded_effect_samples) < 10:
                    excluded_effect_samples.append(row)
                continue
            call = row.get("call", {})
            read = row.get("read", evidence.get("read", {}))
            boundary_id = (f"wala-opaque-state:{observation_id}:{call.get('method')}:"
                           f"{call.get('instruction_index')}:{read.get('method')}:"
                           f"{read.get('instruction_index')}:{read.get('declared_field')}:"
                           f"{row.get('relation')}:{row.get('route', 'normal')}")
            effect = {
                "boundary_id": boundary_id,
                "effect": "opaque_argument_rooted_state_effect",
                "scope": {"observation_id": observation_id, "backend_evidence": row,
                          "observed_read": evidence.get("read"),
                          "authenticated_calls": evidence.get("authenticated_opaque_calls", [])},
                "provenance": "WALA exact call-graph summary binding, CFG route and typed points-to regions",
                "precision": "unknown_effect", "assumptions": [],
                "observation_ids": [observation_id],
            }
            if boundary_id in unknown_ids:
                continue
            unknown_ids.add(boundary_id)
            if len(unknowns) >= unknown_limit:
                omitted_unknown_count += 1
            else:
                unknowns.append(effect)
        for row in analysis.get("exceptions", []):
            call = row.get("call", {})
            read = row.get("read", evidence.get("read", {}))
            boundary_id = (f"wala-opaque-exception:{observation_id}:{call.get('method')}:"
                           f"{call.get('instruction_index')}:{read.get('method')}:"
                           f"{read.get('instruction_index')}:{read.get('declared_field')}:"
                           f"{row.get('relation')}")
            effect = {
                "boundary_id": boundary_id,
                "effect": "opaque_exception_type_or_runtime_feasibility_unproved",
                "scope": {"observation_id": observation_id, "backend_evidence": row,
                          "authenticated_calls": evidence.get("authenticated_opaque_calls", [])},
                "provenance": "WALA exact exceptional CFG route to original observation",
                "precision": "unknown_effect",
                "assumptions": ["caught-type compatibility is not proved"],
                "observation_ids": [observation_id],
            }
            if boundary_id in unknown_ids:
                continue
            unknown_ids.add(boundary_id)
            if len(unknowns) >= unknown_limit:
                omitted_unknown_count += 1
            else:
                unknowns.append(effect)
    if excluded_effect_count:
        boundaries.append({"scope": observation_id,
                           "reason": "opaque_effect_candidates_excluded",
                           "excluded_count": excluded_effect_count,
                           "sample_limit": 10,
                           "samples": excluded_effect_samples,
                           "affected_observation_ids": [observation_id]})
    if unmapped_writer_count:
        boundaries.append({"scope": observation_id,
                           "reason": "visible_state_writers_source_unmapped",
                           "unmapped_count": unmapped_writer_count,
                           "sample_limit": 10,
                           "samples": unmapped_writer_samples,
                           "affected_observation_ids": [observation_id]})
    if omitted_relation_count:
        boundaries.append({"scope": observation_id,
                           "reason": "observation_effect_relation_projection_budget",
                           "omitted_relations": omitted_relation_count,
                           "per_observation_limit": relation_limit,
                           "affected_observation_ids": [observation_id]})
    if omitted_unknown_count:
        boundaries.append({"scope": observation_id,
                           "reason": "observation_effect_unknown_projection_budget",
                           "omitted_unknown_effects": omitted_unknown_count,
                           "per_observation_limit": unknown_limit,
                           "affected_observation_ids": [observation_id]})
    return relations, unknowns, boundaries


def _bounded_observation_unknowns(
        effects: list[dict[str, Any]], analysis_count: int) -> tuple[list[dict[str, Any]], int, int]:
    limit = max(1, MAX_UNKNOWN_EFFECTS // max(1, analysis_count))
    retained = effects[:limit]
    return retained, len(effects) - len(retained), limit


def _is_cross_source_relation(relation: dict[str, Any], test_path: str) -> bool:
    return relation.get("from", {}).get("logical_path") != test_path


def _finalize_mapping_costs(costs: dict[str, Any], contract: dict[str, Any], *,
                            started: float, mapping_started: float) -> None:
    finished = time.monotonic()
    costs["timings_seconds"]["relationship_mapping_and_projection"] = finished - mapping_started
    costs["total_seconds"] = finished - started
    costs["total_scope"] = (
        "adapter admission, bounded source compilation, bytecode packaging, WALA CG/SDG, "
        "relationship mapping and per-root evidence projection; excludes shared and final result JSON serialization")
    contract["cost"].update({
        "timings_seconds": dict(costs["timings_seconds"]),
        "total_seconds": costs["total_seconds"],
        "total_scope": costs["total_scope"],
    })


def _contract(entry: dict[str, Any], context: dict[str, Any], sources: list[dict[str, Any]],
              criteria: list[dict[str, Any]], analyses: list[dict[str, Any]], costs: dict[str, Any],
              harness_parameters: list[dict[str, str]], boundaries: list[dict[str, Any]],
              opaque_methods: list[dict[str, Any]], evidence_output: Path | None = None) -> tuple[dict, dict]:
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
    observation_unknown_effects: list[dict[str, Any]] = []
    mapped_ids = set()
    graph_nodes = graph_edges = raw_cross_source = typed_cross_source = 0
    for analysis_index, analysis in enumerate(analyses):
        remaining_observations = len(analyses) - analysis_index - 1
        relation_capacity = max(0, MAX_RELATIONS - remaining_observations)
        observation_id = analysis["observation_id"]
        raw = _analysis_raw(analysis)
        if raw is None:
            if evidence_output is not None:
                _write_root_evidence_projection(
                    evidence_output / f"{observation_id}.json", analysis, None, [],
                    [{"scope": observation_id, "reason": analysis.get("error", "root unavailable")}])
            continue
        relation_start = len(relations)
        boundary_start = len(boundaries)
        omitted_non_effect_relations = 0
        projected_path_edges = 0
        mapped_ids.add(observation_id)
        graph_nodes += int(raw.get("slice_statement_count", 0))
        graph_edges += int(raw.get("slice_edge_count", 0))
        observation = next(item for item in observations if item["observation_id"] == observation_id)
        observation_endpoint = _endpoint(context["test_path"], observation["location"]["start_line"])
        effect_unknown_limit = max(1, MAX_UNKNOWN_EFFECTS // max(1, len(analyses)))
        effect_relations, effect_unknowns, effect_boundaries = _project_observation_effects(
            raw, observation_id, observation_endpoint, classes,
            relation_limit=max(0, relation_capacity - len(relations)),
            unknown_limit=effect_unknown_limit)
        omitted_effect_relations = 0
        for relation in effect_relations:
            if len(relations) < relation_capacity:
                relations.append(relation)
                if _is_cross_source_relation(relation, context["test_path"]):
                    typed_cross_source += 1
            else:
                omitted_effect_relations += 1
        if omitted_effect_relations:
            boundaries.append({"scope": observation_id,
                               "reason": "observation_effect_relation_projection_budget",
                               "omitted_relations": omitted_effect_relations,
                               "global_limit": MAX_RELATIONS,
                               "reserved_for_later_observations": remaining_observations,
                               "affected_observation_ids": [observation_id]})
        retained_effect_unknowns, omitted_effect_unknowns, per_observation_unknown_limit = (
            _bounded_observation_unknowns(effect_unknowns, len(analyses)))
        observation_unknown_effects.extend(retained_effect_unknowns)
        if omitted_effect_unknowns:
            boundaries.append({
                "scope": observation_id,
                "reason": "observation_effect_unknown_projection_budget",
                "omitted_unknown_effects": omitted_effect_unknowns,
                "per_observation_limit": per_observation_unknown_limit,
                "global_limit": MAX_UNKNOWN_EFFECTS,
                "affected_observation_ids": [observation_id],
            })
        boundaries.extend(effect_boundaries)
        path_indexes = _path_indexes(raw)
        path_budget = {"relations": MAX_RELATIONS, "edges": MAX_OBSERVATION_PROJECTED_EDGES,
                       "omitted_relations": 0, "omitted_path_edges": 0}
        body_paths, body_boundaries = _product_body_paths(
            raw, classes, path_indexes, path_budget)
        typed_paths, suppressed_paths = _typed_product_paths(
            raw, classes, opaque_by_path, path_indexes, path_budget)
        typed_paths = [*body_paths, *typed_paths]
        boundaries.extend({"scope": observation_id,
                           "reason": "typed_path_crosses_opaque_placeholder",
                           "details": suppressed} for suppressed in suppressed_paths)
        boundaries.extend({"scope": observation_id, **boundary}
                          for boundary in body_boundaries)
        if path_budget["omitted_relations"]:
            boundaries.append({"scope": observation_id,
                               "reason": "dependency_path_discovery_budget",
                               "omitted_relation_candidates": path_budget["omitted_relations"],
                               "omitted_path_edges": path_budget["omitted_path_edges"],
                               "relation_limit": MAX_RELATIONS,
                               "edge_limit": MAX_OBSERVATION_PROJECTED_EDGES,
                               "per_relation_edge_limit": MAX_RELATION_PATH_EDGES})
        for path in typed_paths:
            rkey = (path["kind"], path["source_path"], path["source_line"], observation_id)
            if rkey in relation_keys:
                continue
            if len(relations) >= relation_capacity:
                omitted_non_effect_relations += 1
                continue
            path_edges = len(path["dependency_path"])
            if (path_edges > MAX_RELATION_PATH_EDGES
                    or projected_path_edges + path_edges > MAX_OBSERVATION_PROJECTED_EDGES):
                boundaries.append({"scope": observation_id,
                                   "reason": "dependency_path_projection_budget",
                                   "source_path": path["source_path"],
                                   "source_line": path["source_line"],
                                   "path_edges": path_edges,
                                   "per_relation_limit": MAX_RELATION_PATH_EDGES,
                                   "per_observation_limit": MAX_OBSERVATION_PROJECTED_EDGES})
                continue
            relation_keys.add(rkey)
            projected_path_edges += path_edges
            typed_cross_source += 1
            relations.append({"kind": path["kind"], "modality": "analysis_may",
                              "from": _endpoint(path["source_path"], path["source_line"]),
                              "to": observation_endpoint,
                              "provenance": {"backend": "WALA 1.7.2 0-1-CFA + backward SDG",
                                             "source_statement": path["source_statement"],
                                             "seed_statement": path["seed_statement"],
                                             "dependency_path": path["dependency_path"],
                                             "product_body_evidence": path.get("product_body_evidence")},
                              "precision": ("product-body endpoint plus typed NO_HEAP SDG path to exact observation seed"
                                            if path.get("product_body_evidence") else
                                            "homogeneous typed NO_HEAP SDG dependency path to exact observation seed"),
                              "observation_ids": [observation_id],
                              "assumptions": ["context-insensitive predecessor traversal does not enforce matched calls"]})
        for statement_index, statement in enumerate(raw.get("slice_statements", [])):
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
                    if rkey not in relation_keys and len(relations) < relation_capacity:
                        relation_keys.add(rkey)
                        relations.append({"kind": "call_binding", "modality": "analysis_may",
                                          "from": _endpoint(context["test_path"], line,
                                                            symbol=context["entry_signature"]),
                                          "to": _endpoint(target_path, None, symbol=target),
                                          "provenance": {"backend": "WALA 1.7.2 0-1-CFA + backward SDG",
                                                         "source_statement": statement_index,
                                                         "ssa_instruction": statement.get("instruction")},
                                          "precision": "entry SSA call retained in exact observation slice",
                                          "observation_ids": [observation_id], "assumptions": []})
                    elif rkey not in relation_keys:
                        omitted_non_effect_relations += 1
        if omitted_non_effect_relations:
            boundaries.append({"scope": observation_id,
                               "reason": "relation_fair_share_projection_budget",
                               "omitted_relations": omitted_non_effect_relations,
                               "global_limit": MAX_RELATIONS,
                               "reserved_for_later_observations": remaining_observations,
                               "affected_observation_ids": [observation_id]})
        if evidence_output is not None:
            _write_root_evidence_projection(
                evidence_output / f"{observation_id}.json", analysis, raw,
                relations[relation_start:], boundaries[boundary_start:], retained_effect_unknowns)
        del raw
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
    unknown_effects.extend(_observed_opaque_unknown_effects(boundaries))
    unknown_effects.extend(observation_unknown_effects)
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
                "reason": "GENERIC_NO_HEAP omits heap dependencies; FULL control/exception dependencies are retained, while hash-backed predecessor traversal does not enforce matched call/return contexts"}]},
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
        "analysis_entry_status": "not_started", "type_constraint_status": "not_needed_or_not_started",
        "current_stage": "admission"}
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
            "effect_analyzer_sha256": hashlib.sha256(EFFECT_ANALYZER_SOURCE.read_bytes()).hexdigest(),
            "type_constraint_helper_sha256": hashlib.sha256(TYPE_CONSTRAINT_SOURCE.read_bytes()).hexdigest(),
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
            timings["type_constraint_helper_compilation"] = 0.0
            constraint_seconds = 0.0
            def write_test(text): test_path.write_text(text)
            def compile_once():
                return _compile(source_root, classes, paths, classpath,
                                timeout=_remaining(deadline, compile_timeout))
            def resolve_constraints(missing):
                nonlocal constraint_seconds
                entry_evidence["type_constraint_status"] = "running"
                if not timings["type_constraint_helper_compilation"]:
                    timings["type_constraint_helper_compilation"] = _compile_type_constraint_helper(
                        backend_classes, cwd=work,
                        timeout=_remaining(deadline, compile_timeout))
                value, seconds = _resolve_structural_types(
                    source_root, backend_classes, classpath, context["test_path"], method,
                    context["target_scope"][0]["start_line"],
                    context["target_scope"][0]["end_line"], missing,
                    timeout=_remaining(deadline, compile_timeout))
                constraint_seconds += seconds
                entry_evidence["type_constraint_status"] = (
                    "resolved" if not value.get("unresolved") else "ambiguous_or_unsupported")
                entry_evidence["type_constraint_unresolved"] = value.get("unresolved", [])
                return value
            harness, harness_parameters, attempts = _prepare_harness(
                source_rows[0]["text"], method, write_test, compile_once,
                test_path=context["test_path"],
                start_line=context["target_scope"][0]["start_line"],
                end_line=context["target_scope"][0]["end_line"],
                record_attempts=lambda value: _write(output / "compile-attempts.json", value),
                constraint_resolver=resolve_constraints)
            _write(output / "compile-attempts.json", attempts)
            timings["source_compilation"] = sum(item["seconds"] for item in attempts)
            timings["type_constraint_analysis"] = constraint_seconds
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
                ["javac", "-proc:none", "-g", "-cp", wala_cp, "-d", str(backend_classes),
                 str(BACKEND_SOURCE), str(EFFECT_ANALYZER_SOURCE)],
                cwd=work, timeout=_remaining(deadline, compile_timeout))
            timings["backend_compilation"] = backend_seconds
            (output / "backend-compile.stderr").write_text(backend_result.stderr)
            if backend_result.returncode:
                raise RuntimeError("WALA probe compilation failed")
            entry_evidence["current_stage"] = "observation_analysis"
            analyses: list[dict[str, Any]] = []
            library_cp = os.pathsep.join(map(str, dependencies))
            analysis_seconds = 0.0
            queries, query_by_text, preflight_failures, query_records = (
                _preflight_criterion_queries(harness, criteria))
            _write(output / "criteria-request.json", {
                "schema": "mockspec.wala-multi-root-request/v1",
                "criteria": [{"observation_id": record["observation_id"],
                              "query": record["query"]}
                             for record in query_records if record["status"] == "ready"],
                "excluded_criteria": [record for record in query_records
                                      if record["status"] != "ready"],
            })
            for failure in preflight_failures:
                (output / f"{failure['observation_id']}.stderr").write_text(failure["error"])
            criteria_file = work / "criteria-request.json"
            criteria_file.write_text(json.dumps(queries))
            checkpoint_directory = work / "root-checkpoints"
            checkpoint_directory.mkdir()
            shared: dict[str, Any] = {}
            checkpoint_paths: dict[str, Path] = {}
            if queries:
                base_command = [
                    "java", "-Xmx2g", "-cp", str(backend_classes) + os.pathsep + wala_cp,
                    "org.mockspec.research.WalaSliceProbe", str(application), library_cp,
                    entry_class, entry_selector, "CRITERIA_FILE:" + str(criteria_file),
                    "OPAQUE_SUMMARIES", "GENERIC_NO_HEAP", "OBSERVATION_EFFECT_SCAN",
                    "ROOT_OUTPUT_DIR:" + str(checkpoint_directory)]
                analysis_started = time.monotonic()
                try:
                    completed, _ = _run(
                        base_command, cwd=work,
                        timeout=_remaining(deadline, analysis_timeout * len(queries)))
                    analysis_seconds = time.monotonic() - analysis_started
                    for criterion in query_by_text.values():
                        (output / f"{criterion['criterion_id']}.stderr").write_text(completed.stderr)
                    if not completed.returncode:
                        multi = json.loads(completed.stdout)
                        if multi.get("schema_version") != "mockspec.wala-multi-root/v2":
                            raise ValueError("WALA multi-root response schema differs")
                        shared = multi.get("shared")
                        manifest = multi.get("root_manifest")
                        if not isinstance(shared, dict) or not isinstance(manifest, list):
                            raise ValueError("WALA multi-root response is malformed")
                        checkpoint_paths = _validated_root_checkpoint_paths(
                            checkpoint_directory, queries, manifest)
                    else:
                        checkpoint_paths = _validated_root_checkpoint_paths(
                            checkpoint_directory, queries)
                    analyses = _checkpoint_path_analyses(
                        queries, query_by_text, checkpoint_paths,
                        missing_error="WALA base phase failed before root checkpoint\n"
                                      + completed.stderr[-3000:],
                        graph_created="WALA_STAGE call_graph_complete" in completed.stderr)
                except subprocess.TimeoutExpired as exc:
                    analysis_seconds = time.monotonic() - analysis_started
                    stderr = exc.stderr or ""
                    if isinstance(stderr, bytes):
                        stderr = stderr.decode("utf-8", "replace")
                    for criterion in query_by_text.values():
                        (output / f"{criterion['criterion_id']}.stderr").write_text(stderr)
                    checkpoint_paths = _validated_root_checkpoint_paths(
                        checkpoint_directory, queries)
                    analyses = _checkpoint_path_analyses(
                        queries, query_by_text, checkpoint_paths,
                        missing_error="WALA base phase timeout before root checkpoint\n"
                                      + stderr[-3000:],
                        graph_created="WALA_STAGE call_graph_complete" in stderr)
            timings["wala_base_analysis"] = analysis_seconds

            effect_receipts: list[dict[str, Any]] = []
            demanded = _demanded_effect_roots(
                queries, query_by_text, analyses, checkpoint_paths)
            effect_seconds_total = 0.0
            for effect_index, (query, criterion, analysis) in enumerate(demanded):
                remaining = deadline - time.monotonic() - 30.0
                if remaining < 1:
                    analysis.update({
                        "effect_status": "not_attempted_total_deadline_frontier",
                        "effect_error": "effect phase not attempted: total deadline reserved for projection",
                        "effect_seconds": 0.0,
                    })
                    effect_receipts.append({"observation_id": criterion["criterion_id"],
                                            "query": query,
                                            "status": analysis["effect_status"],
                                            "seconds": 0.0,
                                            "error": analysis["effect_error"],
                                            "stderr_artifact": None})
                    continue
                effect_request = work / f"effect-request-{effect_index:05d}.json"
                effect_request.write_text(json.dumps([query]))
                effect_directory = work / f"effect-root-{effect_index:05d}"
                effect_directory.mkdir()
                effect_command = [
                    "java", "-Xmx2g", "-cp", str(backend_classes) + os.pathsep + wala_cp,
                    "org.mockspec.research.WalaSliceProbe", str(application), library_cp,
                    entry_class, entry_selector, "CRITERIA_FILE:" + str(effect_request),
                    "OPAQUE_SUMMARIES", "GENERIC_NO_HEAP", "OBSERVATION_EFFECTS",
                    "ROOT_OUTPUT_DIR:" + str(effect_directory),
                ]
                effect_started = time.monotonic()
                effect_status = "failed"
                effect_error = "effect process failed before checkpoint"
                effect_stderr = ""
                try:
                    effect_completed, _ = _run(
                        effect_command, cwd=work,
                        timeout=max(1, int(min(analysis_timeout, remaining))))
                    effect_stderr = effect_completed.stderr
                    effect_manifest = None
                    if not effect_completed.returncode:
                        effect_multi = json.loads(effect_completed.stdout)
                        if effect_multi.get("schema_version") != "mockspec.wala-multi-root/v2":
                            raise ValueError("WALA effect response schema differs")
                        effect_manifest = effect_multi.get("root_manifest")
                        if not isinstance(effect_manifest, list):
                            raise ValueError("WALA effect manifest is malformed")
                    effect_paths = _validated_root_checkpoint_paths(
                        effect_directory, [query], effect_manifest)
                    effect_path = effect_paths.get(query)
                    if effect_path is None:
                        effect_error = ("effect process failed before checkpoint\n"
                                        + effect_stderr[-3000:])
                    else:
                        effect_status, checkpoint_error = _attach_effect_checkpoint(
                            analysis, effect_path, query)
                        if checkpoint_error is not None:
                            effect_error = checkpoint_error
                except subprocess.TimeoutExpired as exc:
                    effect_status = "timeout"
                    timeout_stderr = exc.stderr or ""
                    effect_stderr = (timeout_stderr.decode("utf-8", "replace")
                                     if isinstance(timeout_stderr, bytes) else timeout_stderr)
                    effect_error = "effect process timeout before checkpoint\n" + effect_stderr[-3000:]
                except Exception as effect_failure:
                    effect_error = (f"effect process {type(effect_failure).__name__}: {effect_failure}\n"
                                    + effect_stderr[-3000:])
                effect_seconds = time.monotonic() - effect_started
                effect_seconds_total += effect_seconds
                (output / f"{criterion['criterion_id']}.effects.stderr").write_text(effect_stderr)
                analysis.update({"effect_status": effect_status, "effect_seconds": effect_seconds})
                if effect_status != "complete":
                    analysis["effect_error"] = effect_error
                effect_receipts.append({
                    "observation_id": criterion["criterion_id"], "query": query,
                    "status": effect_status, "seconds": effect_seconds,
                    "checkpoint_sha256": analysis.get("effect_checkpoint_sha256"),
                    "failed_authenticated_checkpoint_sha256": analysis.get(
                        "effect_failed_checkpoint_sha256"),
                    "error": None if effect_status == "complete" else effect_error,
                    "stderr_artifact": f"{criterion['criterion_id']}.effects.stderr",
                })
            timings["wala_effect_analysis"] = effect_seconds_total
            timings["wala_analysis"] = analysis_seconds + effect_seconds_total
            shared["observation_effect_stage"] = {
                "schema": "mockspec.wala-effect-stage/v1",
                "isolation": "one fresh JVM per demanded instance-field root",
                "base_checkpoint_count": len(checkpoint_paths),
                "base_expected_count": len(queries),
                "demanded_count": len(demanded),
                "receipts": effect_receipts,
            }
            analyses = _merge_criterion_analyses(criteria, analyses, preflight_failures)
            entry_evidence["graph_created"] = any(item.get("graph_created") for item in analyses)
            entry_evidence["observation_roots_mapped"] = sum(
                item.get("root_mapped") is True for item in analyses)
            mapping_started = time.monotonic()
            boundaries = _analysis_boundaries(analyses, closure_evidence)
            costs = {"timings_seconds": timings, "total_seconds": time.monotonic() - started,
                     "total_scope": "provisional through WALA analysis; finalized after relationship mapping and evidence projection",
                     "peak_memory": {"status": "unavailable", "bytes": None,
                                     "reason": "runner enforces -Xmx2g but does not collect child peak RSS"},
                     "cache_state": {"status": "cold", "reason": "fresh source/classes/application; one shared NO_HEAP base process plus one failure-contained FULL process per demanded instance-field root"},
                     "input_size": {"files": len(source_rows), "bytes": total_bytes},
                     "unavailable_phases": [{"phase": "semantic_validation", "reason": "not performed"}]}
            contract, evidence = _contract(
                entry, context, source_records, criteria, analyses, costs,
                harness_parameters, boundaries, opaque_methods, evidence_output=output)
            _finalize_mapping_costs(
                costs, contract, started=started, mapping_started=mapping_started)
            _write_shared_evidence(output / "evidence-shared.json", contract, costs, shared)
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
                                 "effect_analyzer_sha256": hashlib.sha256(EFFECT_ANALYZER_SOURCE.read_bytes()).hexdigest(),
                                 "type_constraint_helper_sha256": hashlib.sha256(TYPE_CONSTRAINT_SOURCE.read_bytes()).hexdigest(),
                                 "dependency_count": len(dependencies), "dependency_rehash": False,
                                 "target_executed": False, "selection": "no case allow-list"}}
    except Exception as exc:
        result = {"schema": SCHEMA, "status": "failed", "backend": "generic_wala_java",
                  "identity": {key: entry.get(key) for key in ("dataset", "case_id", "language", "snapshot_id")},
                  "result_contract": None, "error": {"type": type(exc).__name__, "message": str(exc)},
                  "entry_evidence": entry_evidence,
                  "provenance": {"target_executed": False, "dependency_rehash": False,
                                 "driver_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                                 "probe_sha256": hashlib.sha256(BACKEND_SOURCE.read_bytes()).hexdigest(),
                                 "effect_analyzer_sha256": hashlib.sha256(EFFECT_ANALYZER_SOURCE.read_bytes()).hexdigest(),
                                 "type_constraint_helper_sha256": hashlib.sha256(TYPE_CONSTRAINT_SOURCE.read_bytes()).hexdigest()},
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
