"""Revalidate retained projections, then compile analysis-only parameter harnesses.

No target test, original build script, or original dependency body is executed.
"""
from __future__ import annotations

import argparse
import difflib
import hashlib
import json
from pathlib import Path, PurePosixPath
import subprocess
import sys
import time

from mock_spec.adapters.java import load_task_with_repository
from mock_spec.validation import SourceStore

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
# Avoid importing the identically named historical prepare module.
import importlib.util  # noqa: E402
_spec = importlib.util.spec_from_file_location("historical_prepare", HERE.parent / "prepare.py")
_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_module)
CASES = _module.CASES

CONFIG = {
    "cloudfoundry": {
        "class": "de.codecentric.boot.admin.client.registration.CloudFoundryApplicationFactoryTest",
        "method": "should_use_application_uri",
        "parameters": "PathMappedEndpoints pathMappedEndpoints",
        "boundaries": ["org.springframework.boot.actuate.endpoint.web.PathMappedEndpoints"],
    },
    "jib": {
        "class": "com.google.cloud.tools.jib.cli.jar.JarFilesTest",
        "method": "testToJibContainerBuilder_explodedStandard_basicInfo",
        "parameters": (
            "com.google.cloud.tools.jib.cli.ArtifactProcessor mockStandardExplodedProcessor, "
            "Jar mockJarCommand, CommonCliOptions mockCommonCliOptions, "
            "CommonContainerConfigCliOptions mockCommonContainerConfigCliOptions, ConsoleLogger mockLogger"
        ),
        "boundaries": [
            "com.google.cloud.tools.jib.cli.ArtifactProcessor",
            "com.google.cloud.tools.jib.cli.jar.StandardExplodedProcessor",
            "com.google.cloud.tools.jib.cli.Jar",
            "com.google.cloud.tools.jib.cli.CommonCliOptions",
            "com.google.cloud.tools.jib.cli.CommonContainerConfigCliOptions",
            "com.google.cloud.tools.jib.plugins.common.logging.ConsoleLogger",
        ],
    },
}


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def digest(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def harness(text, method, parameters):
    old = method + "()"
    if text.count(old) != 1:
        raise ValueError("test entry must have exactly one zero-argument declaration")
    return text.replace(old, method + "(" + parameters + ")")


def dependencies(snapshot, runtime):
    paths = []
    for relative, _ in runtime["dependency_hashes"]:
        path = PurePosixPath(relative)
        if len(path.parts) != 2 or path.parts[0] != "libs" or path.suffix != ".jar":
            raise ValueError("unexpected locked dependency path")
        actual = snapshot / "tree/repo" / path
        if actual.is_symlink() or not actual.is_file():
            raise ValueError("missing or symlink dependency")
        paths.append(str(actual))
    return paths


def compile_sources(output, source_files, classpath, label):
    classes = output / (label + "-classes")
    classes.mkdir()
    command = ["javac", "-g", "-encoding", "UTF-8", "-cp", classpath,
               "-d", str(classes), *map(str, source_files)]
    started = time.monotonic()
    try:
        result = subprocess.run(command, capture_output=True, timeout=90)
        code, stdout, stderr = result.returncode, result.stdout, result.stderr
    except subprocess.TimeoutExpired as error:
        code, stdout, stderr = None, error.stdout or b"", error.stderr or b""
    (output / (label + ".stdout.txt")).write_bytes(stdout)
    (output / (label + ".stderr.txt")).write_bytes(stderr)
    receipt = {"command": command, "exit_code": code, "seconds": time.monotonic() - started}
    write(output / (label + ".json"), receipt)
    return receipt


def prepare(old_root, name, output):
    output.mkdir(parents=True, exist_ok=False)
    _, registry, dataset, snapshot_id, case_id = CASES[name]
    snapshot = old_root / registry / "formal-snapshots" / dataset / snapshot_id
    prior = json.loads((HERE.parent / "results" / name / "sources.json").read_text(encoding="utf-8"))
    prior_task = json.loads((HERE.parent / "results" / name / "task.json").read_text(encoding="utf-8"))
    task, repository = load_task_with_repository(snapshot, case_id=case_id)
    try:
        if task.to_dict() != prior_task:
            raise ValueError("reviewed task differs from prior input")
        store = SourceStore(task, repository=repository)
        paths, mapping = [], []
        if name == "cloudfoundry":
            discovery = store.search(path_pattern="src/main/**/*.java", max_output_chars=30000)
            if not discovery.get("complete") and discovery.get("limit_reason") != "access_denied":
                raise ValueError("incomplete source discovery")
        for source in prior["sources"]:
            fresh = store.read(source["logical_path"], source["start_line"],
                               source["start_line"] + len(source["text"].splitlines()) - 1)
            if fresh.text.rstrip("\n") != source["text"].rstrip("\n"):
                raise ValueError("retained projection differs from approved input")
            logical = PurePosixPath(source["logical_path"])
            if logical.is_absolute() or ".." in logical.parts:
                raise ValueError("unsafe source path")
            target = output / "source" / logical
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(source["text"], encoding="utf-8")
            paths.append(target)
            mapping.append({k: source[k] for k in ("source_id", "logical_path", "start_line")}
                           | {"sha256": digest(source["text"]), "end_line": source["start_line"] + len(source["text"].splitlines()) - 1})
        runtime = json.loads((snapshot / "control/runtime-lock.json").read_text(encoding="utf-8"))
        libs = dependencies(snapshot, runtime)
        classpath = ":".join(libs)
        config = CONFIG[name]
        receipt = {"case_id": case_id, "snapshot_id": snapshot_id, "sources": mapping,
                   "admission": "fresh Adapter identity and retained SourceStore projections matched",
                   "dependency_count": len(libs), "dependency_rehash": False,
                   "runtime_lock_digest": runtime.get("digest"), "target_executed": False,
                   "config": config, "classpath": classpath}
        write(output / "preparation.json", receipt)
        receipt["raw_compile"] = compile_sources(output, paths, classpath, "raw")
        original = paths[0].read_text(encoding="utf-8")
        transformed = harness(original, config["method"], config["parameters"])
        paths[0].write_text(transformed, encoding="utf-8")
        (output / "harness.diff").write_text("".join(difflib.unified_diff(
            original.splitlines(True), transformed.splitlines(True),
            fromfile="reviewed-test", tofile="analysis-only-parameter-harness")), encoding="utf-8")
        receipt["harness_sha256"] = digest(transformed)
        receipt["harness_compile"] = compile_sources(output, paths, classpath, "harness")
        write(output / "preparation.json", receipt)
        return receipt
    finally:
        repository.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--old-root", required=True, type=Path)
    parser.add_argument("--case", choices=CONFIG, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    receipt = prepare(args.old_root.resolve(), args.case, args.output.resolve())
    print(json.dumps({k: receipt[k] for k in ("case_id", "raw_compile", "harness_compile")}, indent=2))
