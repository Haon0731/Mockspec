"""Run the bounded Jib original-observation probe without executing target tests."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import time

from build_jib_skeleton import build
from jib_observation_adapter import adapt_jib_observations, validate_adapter_observations


HERE = Path(__file__).resolve().parent
PROBE = HERE / "backend/src/main/java/org/mockspec/research/JibObservationProbe.java"
DEMAND_SLICER = PROBE.with_name("DemandCISlicer.java")
EXPECTATIONS = HERE / "jib_observation_expectations.json"
ENTRY_CLASS = "com.google.cloud.tools.jib.cli.jar.JibObservationEntry"
ENTRY_SELECTOR = (
    "observe(Lcom/google/cloud/tools/jib/cli/ArtifactProcessor;"
    "Lcom/google/cloud/tools/jib/cli/Jar;"
    "Lcom/google/cloud/tools/jib/cli/CommonCliOptions;"
    "Lcom/google/cloud/tools/jib/cli/CommonContainerConfigCliOptions;"
    "Lcom/google/cloud/tools/jib/plugins/common/logging/ConsoleLogger;)"
    "[Ljava/lang/Object;"
)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def run(args: argparse.Namespace) -> dict:
    args.output.mkdir(parents=True, exist_ok=False)
    expectations = json.loads(EXPECTATIONS.read_text(encoding="utf-8"))
    if sha256(args.test_source) != expectations["test_sha256"]:
        raise ValueError("test source hash differs from the pre-registered frozen test")
    if sha256(args.jar_files_source) != expectations["visible_mut_sha256"]:
        raise ValueError("JarFiles source hash differs from the pre-registered visible MUT")
    observation_adapter = adapt_jib_observations(
        args.test_source.read_text(encoding="utf-8"))
    validate_adapter_observations(observation_adapter, expectations["observations"])
    selected = set(args.criteria.split(",")) if args.criteria else None
    observations = [
        item for item in expectations["observations"]
        if selected is None or item["criterion"] in selected
    ]
    if selected is not None and {item["criterion"] for item in observations} != selected:
        raise ValueError("unknown criterion requested")
    libraries = [Path(item) for item in args.library_classpath.split(":" )]
    source_manifest = json.loads(args.source_manifest.read_text(encoding="utf-8"))
    if source_manifest.get("snapshot_id") != expectations["snapshot_id"]:
        raise ValueError("source manifest snapshot differs from the pre-registration")
    if source_manifest.get("case_id") != expectations["dataset_case_id"]:
        raise ValueError("source manifest case differs from the pre-registration")
    binding = json.loads(args.binding_report.read_text(encoding="utf-8"))
    if binding.get("case_identity", {}).get("case_id") != expectations["dataset_case_id"] \
            or binding.get("current_result", {}).get("binding_count") != 1:
        raise ValueError("issue21 automatic binding evidence is absent or for another case")
    binding_evidence = {
        "report_sha256": sha256(args.binding_report),
        "case_identity": binding["case_identity"],
        "target_call_site": binding["current_result"]["target_call_site"],
        "target_callee": binding["current_result"]["target_callee"],
        "binding_count": binding["current_result"]["binding_count"],
        "argument_parameter_indexes": binding["current_result"]["argument_parameter_indexes"],
        "return_binding_count": binding["current_result"]["return_binding_count"],
    }
    matches = [item for item in source_manifest.get("additional_sources", [])
               if item.get("logical_path", "").endswith("/ContainerBuilders.java")]
    if len(matches) != 1:
        raise ValueError("source manifest must admit exactly one ContainerBuilders.java")
    container_provenance = dict(matches[0])
    container_provenance.update({
        "snapshot_id": source_manifest["snapshot_id"],
        "case_id": source_manifest["case_id"],
        "manifest_sha256": sha256(args.source_manifest),
    })
    if container_provenance["sha256"] != sha256(args.container_builders_source):
        raise ValueError("ContainerBuilders does not match the audited source manifest")
    registration = {
        "schema": "mockspec.jib-observation-registration/v1",
        "target_executed": False,
        "entry_origin": "automatic_source_derived_observation_adapter",
        "timeout_seconds": 180,
        "java_heap": "2g",
        "control_dependence": args.control_dependence,
        "disabled_opaque_edge": args.disable_opaque_edge,
        "expectations_sha256": sha256(EXPECTATIONS),
        "probe_sha256": sha256(PROBE),
        "analysis_sources_sha256": {str(source): sha256(source) for source in (PROBE, DEMAND_SLICER)},
        "automatic_binding_report_sha256": sha256(args.binding_report),
        "test_sha256": sha256(args.test_source),
        "test_bytes": args.test_source.stat().st_size,
        "visible_mut_sha256": sha256(args.jar_files_source),
        "visible_mut_bytes": args.jar_files_source.stat().st_size,
        "container_builders": {
            "origin": "reviewed_frozen_source",
            "sha256": sha256(args.container_builders_source),
            "provenance": container_provenance,
        },
        "libraries": [{"path": str(path), "sha256": sha256(path)} for path in libraries],
        "observations": observations,
        "observation_adapter": {
            key: value for key, value in observation_adapter.items()
            if key not in {"entry_source", "sink_source"}
        },
        "negative_gates": [
            "same-receiver writes to a different builder field are not state support",
            "wrong getter field is not state support",
            "same method in a different context is not state support",
            "removing a selected field write or explicit unknown-return edge invalidates support",
        ],
        "unknown_boundaries": [
            "native structural collaborators may return null or throw",
            "native collection contents and exact scalar values are unknown",
            "Credentials.getFromCredentialRetrievers is a native structural boundary",
        ],
    }
    write_json(args.output / "pre-registration.json", registration)
    write_json(args.output / "automatic-binding-evidence.json", binding_evidence)
    source_snapshot = args.output / "analysis-source-snapshot"
    source_snapshot.mkdir()
    for source in (PROBE, DEMAND_SLICER, Path(__file__).resolve(), HERE / "build_jib_skeleton.py",
                   HERE / "jib_observation_adapter.py", EXPECTATIONS):
        shutil.copyfile(source, source_snapshot / source.name)
    started = time.monotonic()
    skeleton = build(
        args.jar_files_source, args.library_classpath, args.output / "skeleton",
        args.container_builders_source, container_provenance,
        test_source=args.test_source, observations=expectations["observations"],
    )
    if skeleton["status"] != "compiled":
        return {"status": "compile_failed", "target_executed": False}
    skeleton_adapter = skeleton.get("observation_adapter")
    if not isinstance(skeleton_adapter, dict):
        raise ValueError("compiled skeleton lacks source-derived observation adapter evidence")
    for key in ("schema", "status", "original_test_sha256", "generated_entry_sha256",
                "mut_call", "plan_binding", "criteria", "parse_provenance"):
        if skeleton_adapter.get(key) != observation_adapter.get(key):
            raise ValueError(f"compiled observation adapter differs at {key}")
    classes = args.output / "probe-classes"
    classes.mkdir()
    compile_command = [
        "javac", "-g", "-cp", args.wala_classpath, "-d", str(classes), str(PROBE), str(DEMAND_SLICER)
    ]
    compiled = subprocess.run(compile_command, capture_output=True, timeout=90)
    (args.output / "probe-compile.stdout").write_bytes(compiled.stdout)
    (args.output / "probe-compile.stderr").write_bytes(compiled.stderr)
    criteria = ",".join(
        "com.google.cloud.tools.jib.cli.jar.ObservationSink."
        + item["criterion"] + "(Ljava/lang/Object;)V"
        for item in observations
    )
    command: list[str] = []
    exit_code = compiled.returncode
    if exit_code == 0:
        command = [
            "/usr/bin/time", "-v", "-o", str(args.output / "analysis.time"),
            "timeout", "180", "java", "-Xmx2g", "-cp",
            str(classes) + ":" + args.wala_classpath,
            "org.mockspec.research.JibObservationProbe",
            str((args.output / "skeleton/application.jar").resolve()),
            args.library_classpath, ENTRY_CLASS, ENTRY_SELECTOR, criteria,
            args.control_dependence,
        ]
        if args.disable_opaque_edge is not None:
            command.append(str(args.disable_opaque_edge))
        with (args.output / "analysis.json").open("wb") as stdout, \
                (args.output / "analysis.stderr").open("wb") as stderr:
            exit_code = subprocess.run(command, stdout=stdout, stderr=stderr).returncode
    for class_name in (
        "com.google.cloud.tools.jib.api.JibContainerBuilder",
        "com.google.cloud.tools.jib.api.buildplan.ContainerBuildPlan",
        "com.google.cloud.tools.jib.api.buildplan.ContainerBuildPlan$Builder",
    ):
        completed = subprocess.run(
            ["javap", "-p", "-c", "-classpath", args.library_classpath, class_name],
            capture_output=True, timeout=30,
        )
        safe = class_name.rsplit(".", 1)[-1].replace("$", "-")
        (args.output / f"javap-{safe}.txt").write_bytes(completed.stdout)
    application_jar = args.output / "skeleton/application.jar"
    completed = subprocess.run(
        ["javap", "-p", "-c", "-v", "-classpath", str(application_jar),
         "com.google.cloud.tools.jib.cli.jar.JarFiles"],
        capture_output=True, timeout=30,
    )
    (args.output / "javap-JarFiles.txt").write_bytes(completed.stdout)
    receipt = {
        "status": "analysed" if exit_code == 0 else "failed",
        "exit_code": exit_code,
        "target_executed": False,
        "elapsed_seconds": time.monotonic() - started,
        "compile_command": compile_command,
        "analysis_command": command,
    }
    write_json(args.output / "receipt.json", receipt)
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--jar-files-source", required=True, type=Path)
    parser.add_argument("--container-builders-source", required=True, type=Path)
    parser.add_argument("--source-manifest", required=True, type=Path)
    parser.add_argument("--binding-report", required=True, type=Path)
    parser.add_argument("--test-source", required=True, type=Path)
    parser.add_argument("--library-classpath", required=True)
    parser.add_argument("--wala-classpath-file", required=True, type=Path)
    parser.add_argument("--criteria")
    parser.add_argument("--control-dependence", choices=("FULL", "NONE"), default="FULL")
    parser.add_argument("--disable-opaque-edge", type=int)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    args.wala_classpath = args.wala_classpath_file.read_text(encoding="utf-8").strip()
    receipt = run(args)
    print(json.dumps(receipt, indent=2))
    return 0 if receipt["status"] == "analysed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
