"""Reproduce the bounded CG/ModRef pressure diagnostic without executing target classes."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import time


SOURCE = Path(__file__).parent / "backend/src/main/java/org/mockspec/research/WalaScaleProbe.java"
REGION_SOURCE = SOURCE.with_name("RuntimeRegionModRef.java")


def run(application: Path, libraries: str, entry_class: str, selector: str,
        wala_classpath: str, output: Path, *, entry_scope: bool = False,
        runtime_region: bool = False, ci_heap: bool = False) -> dict:
    if ci_heap and not entry_scope:
        raise ValueError("CI heap mode requires entry scope")
    output.mkdir(parents=True, exist_ok=False)
    arguments = [str(application.resolve()), libraries, entry_class, selector]
    if ci_heap and runtime_region:
        arguments.append("ENTRY_SCOPE_RUNTIME_REGION_CI_HEAP")
    elif ci_heap:
        arguments.append("ENTRY_SCOPE_CI_HEAP")
    elif entry_scope and runtime_region:
        arguments.append("ENTRY_SCOPE_RUNTIME_REGION")
    elif entry_scope:
        arguments.append("ENTRY_SCOPE")
    elif runtime_region:
        arguments.append("RUNTIME_REGION")
    registration = {
        "schema": "mockspec.jib-pressure-registration/v1", "wala": "1.7.2",
        "source_sha256": hashlib.sha256(SOURCE.read_bytes()).hexdigest(),
        "region_model_sha256": hashlib.sha256(REGION_SOURCE.read_bytes()).hexdigest(),
        "application_sha256": hashlib.sha256(application.read_bytes()).hexdigest(),
        "entry_origin": "diagnostic_override", "call_graph": "0-1-CFA", "reflection": "NONE",
        "timeout_seconds": 180, "java_heap": "2g", "arguments": arguments,
        "purpose": ("entry-scoped WALA CISlicer with FULL control and ModRef may-dependencies"
                    + (" over weak runtime region" if runtime_region else " over original heap locations")
                    if ci_heap
                    else "entry-scoped FULL/FULL slice plus runtime heap region coarsening with weak updates; full pointer analysis"
                    if entry_scope and runtime_region
                    else "runtime heap region coarsening with weak updates; full CG/PA and FULL/FULL dependencies"
                    if runtime_region else "entry-scoped FULL/FULL slice with explicit unknown initialization boundary"
                    if entry_scope else "whole-call-graph transitive Mod/Ref pressure; no PDGs or semantic slice"),
        "target_executed": False,
    }
    (output / "pre-registration.json").write_text(json.dumps(registration, indent=2) + "\n")
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="mockspec-wala-pressure-") as work:
        compile_command = ["javac", "-g", "-cp", wala_classpath, "-d", work, str(SOURCE), str(REGION_SOURCE)]
        compiled = subprocess.run(compile_command, capture_output=True, timeout=90)
        (output / "compile.stderr").write_bytes(compiled.stderr)
        (output / "compile.stdout").write_bytes(compiled.stdout)
        compile_seconds = time.monotonic() - started
        code = compiled.returncode
        command = []
        if code == 0:
            command = ["/usr/bin/time", "-v", "-o", str(output / "analysis.time"),
                       "timeout", "180", "java", "-Xmx2g",
                       "-Dmockspec.profile.receipt=" + str((output / "pressure-before-slice.json").resolve()),
                       "-cp", work + ":" + wala_classpath,
                       "org.mockspec.research.WalaScaleProbe", *arguments]
            with (output / "pressure.json").open("wb") as stdout, \
                    (output / "analysis.stderr").open("wb") as stderr:
                code = subprocess.run(command, stdout=stdout, stderr=stderr).returncode
        receipt = {
            "exit_code": code, "compile_command": compile_command, "analysis_command": command,
            "compile_seconds": compile_seconds, "compile_and_analysis_seconds": time.monotonic() - started,
            "status": "pressure_measured" if code == 0 else "failed",
            "semantic_status": "not_evaluated",
        }
    (output / "receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--application", type=Path, required=True)
    parser.add_argument("--library-classpath", required=True)
    parser.add_argument("--entry-class", required=True)
    parser.add_argument("--entry-selector", required=True)
    parser.add_argument("--wala-classpath-file", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--entry-scope", action="store_true")
    parser.add_argument("--runtime-region", action="store_true")
    parser.add_argument("--ci-heap", action="store_true")
    args = parser.parse_args()
    receipt = run(args.application, args.library_classpath, args.entry_class, args.entry_selector,
                  args.wala_classpath_file.read_text().strip(), args.output,
                  entry_scope=args.entry_scope, runtime_region=args.runtime_region,
                  ci_heap=args.ci_heap)
    print(json.dumps(receipt, indent=2))
    return 0 if receipt["exit_code"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
