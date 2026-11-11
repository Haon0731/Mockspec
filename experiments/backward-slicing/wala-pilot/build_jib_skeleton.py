"""Build a structure-only bytecode input around the visible Jib method.

The generated declarations are native or interfaces. They preserve only visible
types and method signatures needed to compile JarFiles; they do not prescribe
Mock returns, exceptions, or heap effects.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import time

from jib_observation_adapter import adapt_jib_observations, validate_adapter_observations


STUBS = {
    "com/google/cloud/tools/jib/cli/Jar.java": """package com.google.cloud.tools.jib.cli;
import java.util.List;
public class Jar { public native List<String> getJvmFlags(); }
""",
    "com/google/cloud/tools/jib/cli/CommonCliOptions.java": """package com.google.cloud.tools.jib.cli;
public class CommonCliOptions {}
""",
    "com/google/cloud/tools/jib/cli/Credentials.java": """package com.google.cloud.tools.jib.cli;
import com.google.cloud.tools.jib.api.CredentialRetriever;
import com.google.cloud.tools.jib.plugins.common.DefaultCredentialRetrievers;
import java.util.List;
public final class Credentials {
  public static native List<CredentialRetriever> getFromCredentialRetrievers(
      CommonCliOptions options, DefaultCredentialRetrievers retrievers);
}
""",
    "com/google/cloud/tools/jib/cli/CommonContainerConfigCliOptions.java": """package com.google.cloud.tools.jib.cli;
import com.google.cloud.tools.jib.api.buildplan.AbsoluteUnixPath;
import com.google.cloud.tools.jib.api.buildplan.ImageFormat;
import com.google.cloud.tools.jib.api.buildplan.Port;
import java.time.Instant;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.Set;
public class CommonContainerConfigCliOptions {
  public native Optional<String> getFrom();
  public native Set<Port> getExposedPorts();
  public native Set<AbsoluteUnixPath> getVolumes();
  public native Map<String,String> getEnvironment();
  public native Map<String,String> getLabels();
  public native Optional<String> getUser();
  public native Optional<ImageFormat> getFormat();
  public native List<String> getProgramArguments();
  public native List<String> getEntrypoint();
  public native Optional<Instant> getCreationTime();
}
""",
    "com/google/cloud/tools/jib/cli/ContainerBuilders.java": """package com.google.cloud.tools.jib.cli;
import com.google.cloud.tools.jib.api.InvalidImageReferenceException;
import com.google.cloud.tools.jib.api.JibContainerBuilder;
import com.google.cloud.tools.jib.api.buildplan.Platform;
import com.google.cloud.tools.jib.plugins.common.logging.ConsoleLogger;
import java.io.FileNotFoundException;
import java.util.Set;
public class ContainerBuilders {
  public static native JibContainerBuilder create(String baseImage, Set<Platform> platforms,
      CommonCliOptions options, ConsoleLogger logger)
      throws InvalidImageReferenceException, FileNotFoundException;
}
""",
    "com/google/cloud/tools/jib/cli/ArtifactProcessor.java": """package com.google.cloud.tools.jib.cli;
import com.google.cloud.tools.jib.api.buildplan.FileEntriesLayer;
import com.google.common.collect.ImmutableList;
import java.io.IOException;
import java.util.List;
public interface ArtifactProcessor {
  List<FileEntriesLayer> createLayers() throws IOException;
  ImmutableList<String> computeEntrypoint(List<String> flags) throws IOException;
  Integer getJavaVersion();
}
""",
    "com/google/cloud/tools/jib/cli/jar/JibDiagnosticEntry.java": """package com.google.cloud.tools.jib.cli.jar;
import com.google.cloud.tools.jib.api.JibContainerBuilder;
import com.google.cloud.tools.jib.api.buildplan.ContainerBuildPlan;
import com.google.cloud.tools.jib.cli.ArtifactProcessor;
import com.google.cloud.tools.jib.cli.CommonCliOptions;
import com.google.cloud.tools.jib.cli.CommonContainerConfigCliOptions;
import com.google.cloud.tools.jib.cli.Jar;
import com.google.cloud.tools.jib.plugins.common.logging.ConsoleLogger;
public final class JibDiagnosticEntry {
  public static ContainerBuildPlan observe(ArtifactProcessor processor, Jar jar,
      CommonCliOptions common, CommonContainerConfigCliOptions container, ConsoleLogger logger)
      throws Exception {
    JibContainerBuilder builder =
        JarFiles.toJibContainerBuilder(processor, jar, common, container, logger);
    return builder.toContainerBuildPlan();
  }
}
""",
}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build(source: Path, classpath: str, output: Path,
          container_builders_source: Path | None = None,
          container_builders_provenance: dict | None = None, *,
          test_source: Path | None = None,
          observations: list[dict] | tuple[dict, ...] = ()) -> dict:
    if output.exists():
        raise FileExistsError(f"output must not exist: {output}")
    output.mkdir(parents=True)
    source_root = output / "source"
    classes = output / "classes"
    source_target = source_root / "com/google/cloud/tools/jib/cli/jar/JarFiles.java"
    source_target.parent.mkdir(parents=True)
    shutil.copyfile(source, source_target)
    mappings: list[dict[str, object]] = [{
        "generated_path": str(source_target.relative_to(output)),
        "origin": "reviewed_visible_source",
        "sha256": _sha256(source_target),
    }]
    for relative, text in STUBS.items():
        if container_builders_source is not None and relative.endswith("ContainerBuilders.java"):
            continue
        target = source_root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        mappings.append({
            "generated_path": str(target.relative_to(output)),
            "origin": "visible_signature_structure_only",
            "sha256": _sha256(target),
        })
    observation_adapter = None
    if test_source is not None:
        observation_adapter = adapt_jib_observations(test_source.read_text(encoding="utf-8"))
        if observations:
            validate_adapter_observations(observation_adapter, observations)
        generated = {
            "com/google/cloud/tools/jib/cli/jar/JibObservationEntry.java":
                observation_adapter.pop("entry_source"),
            "com/google/cloud/tools/jib/cli/jar/ObservationSink.java":
                observation_adapter.pop("sink_source"),
        }
        observation_adapter["generator_sha256"] = _sha256(
            Path(__file__).with_name("jib_observation_adapter.py")
        )
        for relative, text in generated.items():
            target = source_root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8")
            mappings.append({
                "generated_path": str(target.relative_to(output)),
                "origin": "automatic_source_derived_observation_adapter",
                "sha256": _sha256(target),
                "source_sha256": observation_adapter["original_test_sha256"],
            })
    if container_builders_source is not None:
        target = source_root / "com/google/cloud/tools/jib/cli/ContainerBuilders.java"
        shutil.copyfile(container_builders_source, target)
        origin = "provided_source"
        if container_builders_provenance is not None:
            required = {"source_id", "logical_path", "sha256", "snapshot_id", "case_id"}
            if not required.issubset(container_builders_provenance):
                raise ValueError("ContainerBuilders provenance is incomplete")
            if container_builders_provenance["sha256"] != _sha256(target):
                raise ValueError("ContainerBuilders provenance hash mismatch")
            origin = "reviewed_frozen_source"
        mappings.append({
            "generated_path": str(target.relative_to(output)),
            "origin": origin,
            "sha256": _sha256(target),
            "provenance": container_builders_provenance,
        })
    sources = sorted(source_root.rglob("*.java"))
    classes.mkdir()
    command = ["javac", "-g", "-encoding", "UTF-8", "-cp", classpath,
               "-d", str(classes), *map(str, sources)]
    started = time.monotonic()
    completed = subprocess.run(command, capture_output=True, timeout=90)
    (output / "compile.stdout.txt").write_bytes(completed.stdout)
    (output / "compile.stderr.txt").write_bytes(completed.stderr)
    jar_path = output / "application.jar"
    if completed.returncode == 0:
        subprocess.run(["jar", "--create", "--file", str(jar_path), "-C", str(classes), "."],
                       check=True, capture_output=True)
    receipt = {
        "schema_version": "mockspec.jib-analysis-skeleton/v1",
        "status": "compiled" if completed.returncode == 0 else "compile_failed",
        "compile_exit_code": completed.returncode,
        "compile_seconds": time.monotonic() - started,
        "command": command,
        "mappings": mappings,
        "observation_adapter": observation_adapter,
        "assumptions": [
            (
                "JibObservationEntry and ObservationSink are automatically derived from the "
                "visible test; supplied observations validate the derived order and identity only."
                if observation_adapter is not None
                else "Only the diagnostic_override JibDiagnosticEntry is present; no observation "
                     "entry was recovered."
            ),
            "Native declarations preserve visible signatures but intentionally leave return, exception, and heap effects unknown.",
            "No concrete Mock behavior or hidden answer is present.",
            ("ContainerBuilders is the reviewed frozen source; its Credentials collaborator remains a native unknown boundary."
             if container_builders_provenance is not None
             else "ContainerBuilders is provided without authenticated review provenance or remains a native unknown-return boundary."),
        ],
        "target_executed": False,
        "application_jar": str(jar_path) if jar_path.exists() else None,
    }
    (output / "manifest.json").write_text(
        json.dumps(receipt, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return receipt


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--jar-files-source", required=True, type=Path)
    parser.add_argument("--classpath", required=True)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--container-builders-source", type=Path)
    parser.add_argument("--test-source", type=Path)
    args = parser.parse_args()
    print(json.dumps(build(args.jar_files_source, args.classpath, args.output,
                           args.container_builders_source,
                           test_source=args.test_source), indent=2))


if __name__ == "__main__":
    main()
