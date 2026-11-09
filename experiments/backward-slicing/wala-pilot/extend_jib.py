"""Compile a bounded Jib source closure for the WALA feasibility pilot.

The retained three-source projection is intentionally kept as the baseline.  This
helper starts from its analysis-only harness and admits additional product source
only through the production Adapter and SourceStore.  It never executes target
code or treats the expanded source set as equivalent to the original projection.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import time

from mock_spec.adapters.java import load_task_with_repository
from mock_spec.validation import SourceStore


HERE = Path(__file__).resolve().parent
MAX_ADDITIONAL_SOURCES = 20
INITIAL_REQUIRED_TYPES = (
    "com.google.cloud.tools.jib.cli.ArtifactLayers",
    "com.google.cloud.tools.jib.cli.ArtifactProcessor",
    "com.google.cloud.tools.jib.cli.CommonCliOptions",
    "com.google.cloud.tools.jib.cli.CommonContainerConfigCliOptions",
    "com.google.cloud.tools.jib.cli.ContainerBuilders",
    "com.google.cloud.tools.jib.cli.Jar",
)


def _load_prepare():
    specification = importlib.util.spec_from_file_location(
        "wala_pilot_prepare", HERE / "prepare.py"
    )
    if specification is None or specification.loader is None:
        raise RuntimeError("cannot load wala-pilot prepare.py")
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


def _write_json(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _safe_target(output: Path, logical_path: str) -> Path:
    relative = PurePosixPath(logical_path)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError(f"unsafe source path: {logical_path}")
    return output / "source" / relative


def _copy_baseline(baseline: Path, output: Path) -> list[Path]:
    source_root = baseline / "source"
    if not source_root.is_dir():
        raise FileNotFoundError(f"missing baseline source directory: {source_root}")
    destination = output / "source"
    shutil.copytree(source_root, destination)
    sources = sorted(destination.rglob("*.java"))
    if len(sources) != 3:
        raise ValueError(f"expected the retained three-source baseline, found {len(sources)}")
    return sources


def _compile(
    output: Path, sources: list[Path], classpath: str, attempt: int
) -> dict[str, object]:
    classes = output / "expanded-classes"
    classes.mkdir(exist_ok=True)
    command = [
        "javac",
        "-g",
        "-encoding",
        "UTF-8",
        "-cp",
        classpath,
        "-d",
        str(classes),
        *map(str, sources),
    ]
    started = time.monotonic()
    try:
        completed = subprocess.run(command, capture_output=True, timeout=90)
        exit_code = completed.returncode
        stdout = completed.stdout
        stderr = completed.stderr
        timed_out = False
    except subprocess.TimeoutExpired as error:
        exit_code = None
        stdout = error.stdout or b""
        stderr = error.stderr or b""
        timed_out = True
    prefix = output / f"compile-{attempt}"
    prefix.with_suffix(".stdout.txt").write_bytes(stdout)
    prefix.with_suffix(".stderr.txt").write_bytes(stderr)
    return {
        "attempt": attempt,
        "source_count": len(sources),
        "exit_code": exit_code,
        "timed_out": timed_out,
        "seconds": time.monotonic() - started,
        "stderr_path": prefix.with_suffix(".stderr.txt").name,
        "command": command,
    }


def _diagnostic_types(stderr: str) -> set[str]:
    """Return fully qualified missing class names exposed by javac diagnostics."""
    missing: set[str] = set()
    lines = stderr.splitlines()
    for index, line in enumerate(lines):
        import_match = re.search(r"import ([a-zA-Z_$][\w$]*(?:\.[\w$]+)+);", line)
        if import_match:
            missing.add(import_match.group(1))
        if line.strip().startswith("symbol:") and "class " in line:
            simple = line.rsplit("class ", 1)[1].strip()
            for later in lines[index + 1 : index + 4]:
                package = re.search(r"location:\s+package\s+([\w.]+)", later)
                if package:
                    missing.add(package.group(1) + "." + simple)
                    break
    return missing


def _discover_source(store: SourceStore, qualified_name: str):
    simple_name = qualified_name.rsplit(".", 1)[-1]
    expected_suffix = "/" + qualified_name.replace(".", "/") + ".java"
    result = store.search(
        path_pattern=f"src/main/**/{simple_name}.java", max_output_chars=12_000
    )
    matches = [
        item
        for item in result.get("matches", [])
        if isinstance(item.get("logical_path"), str)
        and ("/" + item["logical_path"]).endswith(expected_suffix)
    ]
    if len(matches) != 1:
        return None, {
            "qualified_name": qualified_name,
            "status": result.get("status"),
            "complete": result.get("complete"),
            "limit_reason": result.get("limit_reason"),
            "match_count": len(matches),
        }
    source = store.analysis_source(matches[0]["source_id"])
    observed = store.read(source.source_id, source.start_line, source.end_line)
    store.record_observation(observed, response_id="wala-jib-source-closure")
    return source, {
        "qualified_name": qualified_name,
        "status": result.get("status"),
        "complete": result.get("complete"),
        "limit_reason": result.get("limit_reason"),
        "match_count": 1,
    }


def extend(old_root: Path, baseline: Path, output: Path) -> dict[str, object]:
    if output.exists():
        raise FileExistsError(f"output must not exist: {output}")
    output.mkdir(parents=True)
    prepare = _load_prepare()
    _, registry, dataset, snapshot_id, case_id = prepare.CASES["jib"]
    snapshot = old_root / registry / "formal-snapshots" / dataset / snapshot_id
    baseline_receipt = json.loads(
        (baseline / "preparation.json").read_text(encoding="utf-8")
    )
    if baseline_receipt.get("case_id") != case_id:
        raise ValueError("baseline case identity differs from Jib case")
    sources = _copy_baseline(baseline, output)
    task, repository = load_task_with_repository(snapshot, case_id=case_id)
    compile_attempts: list[dict[str, object]] = []
    additions: list[dict[str, object]] = []
    discovery: list[dict[str, object]] = []
    unresolved: list[str] = []
    try:
        store = SourceStore(task, repository=repository)
        runtime = json.loads(
            (snapshot / "control/runtime-lock.json").read_text(encoding="utf-8")
        )
        classpath = ":".join(prepare.dependencies(snapshot, runtime))
        pending = list(INITIAL_REQUIRED_TYPES)
        attempted_types: set[str] = set()
        attempt = 0
        while True:
            while pending:
                qualified_name = pending.pop(0)
                if qualified_name in attempted_types:
                    continue
                attempted_types.add(qualified_name)
                if len(additions) >= MAX_ADDITIONAL_SOURCES:
                    unresolved.append(qualified_name)
                    continue
                source, receipt = _discover_source(store, qualified_name)
                discovery.append(receipt)
                if source is None:
                    unresolved.append(qualified_name)
                    continue
                target = _safe_target(output, source.logical_path)
                if target.exists():
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(source.text, encoding="utf-8")
                sources.append(target)
                additions.append(
                    {
                        "source_id": source.source_id,
                        "logical_path": source.logical_path,
                        "start_line": source.start_line,
                        "end_line": source.end_line,
                        "sha256": _sha256(source.text.encode("utf-8")),
                    }
                )

            attempt += 1
            compile_receipt = _compile(output, sorted(sources), classpath, attempt)
            compile_attempts.append(compile_receipt)
            if compile_receipt["exit_code"] == 0 or compile_receipt["timed_out"]:
                break
            stderr = (output / str(compile_receipt["stderr_path"])).read_text(
                encoding="utf-8", errors="strict"
            )
            new_types = sorted(_diagnostic_types(stderr) - attempted_types)
            if not new_types or len(additions) >= MAX_ADDITIONAL_SOURCES:
                unresolved.extend(new_types)
                break
            pending.extend(new_types)

        manifest = {
            "case_id": case_id,
            "snapshot_id": snapshot_id,
            "scope": (
                "expanded compile-only source closure; not equivalent to the retained "
                "three-source projection"
            ),
            "baseline": {
                "path": str(baseline),
                "preparation_sha256": _sha256(
                    (baseline / "preparation.json").read_bytes()
                ),
                "harness_sha256": baseline_receipt.get("harness_sha256"),
                "harness_compile_exit_code": baseline_receipt.get(
                    "harness_compile", {}
                ).get("exit_code"),
                "source_count": 3,
            },
            "admission": (
                "additional product sources discovered and read through the production "
                "Adapter/SourceStore for the same reviewed snapshot"
            ),
            "max_additional_sources": MAX_ADDITIONAL_SOURCES,
            "additional_sources": additions,
            "discovery": discovery,
            "observations": list(store.observations()),
            "compile_attempts": compile_attempts,
            "unresolved_types": sorted(set(unresolved)),
            "dependency_count": len(runtime["dependency_hashes"]),
            "dependency_rehash": False,
            "target_executed": False,
            "boundary_note": (
                "Boundary source may be present for compilation only; downstream WALA "
                "analysis must keep configured boundary behavior opaque."
            ),
            "status": "compiled"
            if compile_attempts[-1]["exit_code"] == 0
            else "closure_incomplete",
        }
        _write_json(output / "manifest.json", manifest)
        return manifest
    finally:
        repository.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--old-root", required=True, type=Path)
    parser.add_argument("--baseline", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    arguments = parser.parse_args()
    result = extend(
        arguments.old_root.resolve(),
        arguments.baseline.resolve(),
        arguments.output.resolve(),
    )
    print(
        json.dumps(
            {
                "status": result["status"],
                "additional_source_count": len(result["additional_sources"]),
                "compile_attempts": result["compile_attempts"],
                "unresolved_types": result["unresolved_types"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
