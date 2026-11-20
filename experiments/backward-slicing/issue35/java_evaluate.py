"""Calibrate and evaluate the two Issue #35 Java cases in isolated containers."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import tempfile
import time
import uuid

from mock_spec.adapters.frozen_dataset import FrozenDataset


HERE = Path(__file__).resolve().parent
PREREGISTRATION = HERE / "java_pre_registration.json"
IMAGE = "sha256:7268546607aefcf0ab62e58f9b58d9c890d7f3b50bd0bec1231314cbb357bd36"
DATASET = "java-bechmark-strong"
INVENTORY_REVISION = "e8ed1a717304ae6437602a4da6389aae46d96f4a805bc140883d0a626742811f"
MARKERS = {
    "mock_stub": ("// === MOCK-STUB-START ===", "// === MOCK-STUB-END ==="),
    "verify": ("// === VERIFY-START ===", "// === VERIFY-END ==="),
}

REFERENCE = {
    "zipkin": {
        "mock_stub": """CqlSession session = mock(CqlSession.class);
KeyspaceMetadata keyspaceMetadata = mock(KeyspaceMetadata.class);
Node node = mock(Node.class);
Metadata metadata = mock(Metadata.class);
when(session.getMetadata()).thenReturn(metadata);
when(metadata.getNodes()).thenReturn(Collections.singletonMap(
    UUID.fromString(\"11111111-1111-1111-1111-111111111111\"), node));
when(node.getCassandraVersion()).thenReturn(Version.parse(\"3.11.3\"));
when(metadata.getKeyspace(\"zipkin2\")).thenReturn(Optional.of(keyspaceMetadata));""",
        "verify": """verify(session).getMetadata();
verify(metadata).getNodes();
verify(node).getCassandraVersion();
verify(metadata).getKeyspace(\"zipkin2\");""",
    },
    "jib": {
        "mock_stub": """StandardExplodedProcessor mockStandardExplodedProcessor = mock(StandardExplodedProcessor.class);
Jar mockJarCommand = mock(Jar.class);
CommonCliOptions mockCommonCliOptions = mock(CommonCliOptions.class);
CommonContainerConfigCliOptions mockCommonContainerConfigCliOptions = mock(CommonContainerConfigCliOptions.class);
ConsoleLogger mockLogger = mock(ConsoleLogger.class);
when(mockStandardExplodedProcessor.getJavaVersion()).thenReturn(8);
when(mockStandardExplodedProcessor.createLayers()).thenReturn(Arrays.asList(layer));
when(mockStandardExplodedProcessor.computeEntrypoint(anyList()))
    .thenReturn(
        ImmutableList.of(\"java\", \"-cp\", \"/app/explodedJar:/app/dependencies/*\", \"HelloWorld\"));
when(mockCommonContainerConfigCliOptions.getFrom()).thenReturn(Optional.empty());""",
        "verify": """org.mockito.Mockito.verify(mockStandardExplodedProcessor).createLayers();
org.mockito.Mockito.verify(mockStandardExplodedProcessor).computeEntrypoint(anyList());""",
    },
}

MUTATIONS = {
    "zipkin": {
        "omit_keyspace_lookup": (
            "return metadata.getKeyspace(keyspace).orElse(null);", "return null;"),
        "wrong_keyspace_argument": (
            "metadata.getKeyspace(keyspace).orElse(null)",
            "metadata.getKeyspace(keyspace + \"_mutant\").orElse(null)"),
        "duplicate_keyspace_lookup": (
            "return metadata.getKeyspace(keyspace).orElse(null);",
            "metadata.getKeyspace(keyspace);\n    return metadata.getKeyspace(keyspace).orElse(null);"),
    },
    "jib": {
        "omit_create_layers": (
            "List<FileEntriesLayer> layers = processor.createLayers();",
            "List<FileEntriesLayer> layers = Collections.emptyList();"),
        "omit_compute_entrypoint": (
            "? processor.computeEntrypoint(jarOptions.getJvmFlags())",
            "? Collections.emptyList()"),
        "duplicate_create_layers": (
            "List<FileEntriesLayer> layers = processor.createLayers();",
            "processor.createLayers();\n    List<FileEntriesLayer> layers = processor.createLayers();"),
    },
}

DOCKER_LOCK = Path("/tmp/mockspec-issue35-docker.lock")


def digest_text(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


@contextmanager
def docker_lock():
    with DOCKER_LOCK.open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def load_candidate(path: Path | None, case_name: str) -> tuple[dict[str, str], str]:
    if path is None:
        return REFERENCE[case_name], "developer_reference"
    value = json.loads(path.read_text())
    if not isinstance(value, dict) or set(value) != {"slots", "rationale"}:
        raise ValueError("candidate requires exactly slots and rationale")
    slots = value["slots"]
    if not isinstance(slots, dict) or set(slots) != {"MOCK-STUB", "VERIFY"}:
        raise ValueError("candidate slots require exactly MOCK-STUB and VERIFY")
    if any(not isinstance(slots[key], str) for key in slots):
        raise ValueError("candidate slots must be strings")
    if not isinstance(value["rationale"], list) or any(
        not isinstance(item, str) for item in value["rationale"]
    ):
        raise ValueError("candidate rationale must be a list of strings")
    return {"mock_stub": slots["MOCK-STUB"], "verify": slots["VERIFY"]}, digest_text(
        path.read_text()
    )


def ordinary_region_identity(
    raw_reference: str, masked: str, excluded_line_ranges: list[list[int]],
) -> tuple[str, str]:
    excluded = {
        line for start, end in excluded_line_ranges for line in range(start, end + 1)
    }
    raw_lines = [
        line for number, line in enumerate(raw_reference.splitlines(), 1)
        if number not in excluded
    ]
    masked_without_regions = masked
    for start, end in MARKERS.values():
        masked_without_regions = re.sub(
            rf"(?ms)^\s*{re.escape(start)}\n.*?^\s*{re.escape(end)}\n?",
            "", masked_without_regions,
        )

    def canonical(lines: list[str]) -> str:
        value = "\n".join(lines)
        value = re.sub(r"(?m)//.*$", "", value)
        value = re.sub(r"/\*.*?\*/", "", value, flags=re.S)
        return re.sub(r"\s+", "", value)

    return digest_text(canonical(raw_lines)), digest_text(
        canonical(masked_without_regions.splitlines())
    )


def inject(masked: str, candidate: dict[str, str]) -> str:
    result = masked
    for key, (start, end) in MARKERS.items():
        pattern = re.compile(
            rf"(?m)^(?P<indent>[ \t]*){re.escape(start)}\n"
            rf"(?P=indent){re.escape(end)}$"
        )
        match = pattern.search(result)
        if match is None or len(pattern.findall(result)) != 1:
            raise ValueError(f"expected one empty {key} region")
        indent = match.group("indent")
        body = "\n".join(indent + line for line in candidate[key].splitlines())
        replacement = f"{indent}{start}\n{body}\n{indent}{end}"
        result = result[:match.start()] + replacement + result[match.end():]
    return result


def mutate(source: str, case: str, mutant: str) -> str:
    if mutant == "original":
        return source
    old, new = MUTATIONS[case][mutant]
    if source.count(old) != 1:
        raise ValueError(f"mutation site is not unique: {case}/{mutant}")
    return source.replace(old, new)


def dependency_mounts(snapshot: Path, runtime: dict) -> tuple[list[str], str]:
    mounts: list[str] = []
    targets: list[str] = []
    for relative, _digest in runtime["dependency_hashes"]:
        path = PurePosixPath(relative)
        if len(path.parts) != 2 or path.parts[0] != "libs":
            raise ValueError("dependency path escapes libs")
        source = snapshot / "tree/repo/libs" / path.name
        if not source.is_file() or source.is_symlink():
            raise ValueError(f"dependency unavailable: {path.name}")
        target = f"/deps/{path.name}"
        mounts.extend(["-v", f"{source}:{target}:ro"])
        targets.append(target)
    return mounts, ":".join(targets)


def docker_base(work: Path, mounts: list[str]) -> list[str]:
    return [
        "docker", "run", "--rm", "--name", "mockspec-issue35-" + uuid.uuid4().hex[:12],
        "--network=none", "--read-only", "--cap-drop=ALL",
        "--security-opt=no-new-privileges", "--pids-limit=128", "--memory=768m",
        "--cpus=1", "--tmpfs", "/tmp:rw,nosuid,nodev,size=64m",
        "--user", f"{os.getuid()}:{os.getgid()}", "-v", f"{work}:/work:rw",
        *mounts, "-w", "/work", "-e", "HOME=/tmp", IMAGE,
    ]


def run(command: list[str], timeout: float) -> dict:
    started = time.perf_counter()
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
        return {"exit_code": result.returncode, "stdout": result.stdout,
                "stderr": result.stderr, "seconds": time.perf_counter() - started,
                "timed_out": False}
    except subprocess.TimeoutExpired as error:
        if command[:2] == ["docker", "run"] and "--name" in command:
            subprocess.run(["docker", "rm", "-f", command[command.index("--name") + 1]],
                           capture_output=True, timeout=15)
        return {"exit_code": None, "stdout": error.stdout or "", "stderr": error.stderr or "",
                "seconds": time.perf_counter() - started, "timed_out": True}


def result_status(compiled: dict, tested: dict | None) -> str:
    if compiled["timed_out"]:
        return "compile_timeout"
    if compiled["exit_code"] != 0:
        return "compile_failed"
    if tested is None:
        return "fixture_or_infrastructure_failed"
    if tested["timed_out"]:
        return "test_timeout"
    if tested["exit_code"] == 0:
        return "passed"
    test_output = f"{tested.get('stdout', '')}\n{tested.get('stderr', '')}"
    if any(marker in test_output for marker in (
        "java.lang.AssertionError",
        "org.mockito.exceptions.verification.",
        "Wanted but not invoked:",
        "TooManyActualInvocations",
        "ArgumentsAreDifferent",
    )):
        return "test_assertion_or_verification_failed"
    return "test_exception_or_fixture_failed"


def evaluate(
    case_name: str, mutant: str, output: Path, old_root: Path,
    candidate_path: Path | None = None,
) -> dict:
    prereg = json.loads(PREREGISTRATION.read_text())
    case_config = prereg["cases"][case_name]
    if mutant not in {"original", *case_config["mutants"]}:
        raise ValueError("mutant is not pre-registered")
    if output.exists():
        raise FileExistsError("output must not exist")
    output.mkdir(parents=True)
    registry = old_root / ".omx/developer-realistic-registry-max"
    snapshot = registry / "formal-snapshots" / DATASET / case_config["snapshot_id"]
    dataset = FrozenDataset(
        registry, dataset=DATASET, inventory_revision=INVENTORY_REVISION
    )
    with dataset.load_case(case_config["case_id"]) as frozen:
        runtime = json.loads((snapshot / "control/runtime-lock.json").read_text())
        if runtime["digest"] != case_config["runtime_lock_digest"]:
            raise ValueError("runtime identity changed")
        reference = REFERENCE[case_name]
        for key in ("mock_stub", "verify"):
            expected = case_config["reference"][f"gold_{key}_sha256"]
            if digest_text(reference[key] + "\n") != expected:
                raise ValueError(f"reference {key} differs from gold replay identity")
        raw_reference_path = old_root.parent.parent / "data" / case_config["reference"]["source"]
        raw_reference = raw_reference_path.read_text()
        if digest_text(raw_reference) != case_config["reference"]["source_sha256"]:
            raise ValueError("raw developer reference identity changed")
        raw_ordinary, masked_ordinary = ordinary_region_identity(
            raw_reference, frozen.masked_test.text,
            case_config["reference"]["excluded_record_line_ranges"],
        )
        if raw_ordinary != masked_ordinary:
            raise ValueError("raw reference ordinary region differs from masked test")
        candidate, candidate_identity = load_candidate(candidate_path, case_name)
        test_text = inject(frozen.masked_test.text, candidate)
        mounts, classpath = dependency_mounts(snapshot, runtime)
        with docker_lock(), tempfile.TemporaryDirectory(
            prefix="mockspec-issue35-java-"
        ) as temporary:
            work = Path(temporary)
            sources: list[str] = []
            for logical_path in frozen.source_paths:
                if logical_path == frozen.masked_test.logical_path:
                    text = test_text
                else:
                    text = frozen.read_source(logical_path).text
                    if logical_path == case_config["mut_path"]:
                        text = mutate(text, case_name, mutant)
                target = work / logical_path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(text)
                sources.append("/work/" + logical_path)
            (work / "classes").mkdir()
            compile_command = [*docker_base(work, mounts), "javac", "-encoding", "UTF-8",
                               "-cp", classpath, "-d", "/work/classes", *sources]
            compiled = run(compile_command, 120)
            tested = None
            if compiled["exit_code"] == 0:
                test_command = [*docker_base(work, mounts), "java", "-cp",
                                f"/work/classes:{classpath}", "org.junit.runner.JUnitCore",
                                case_config["test_class"]]
                tested = run(test_command, 60)
            result = {
                "schema_version": "mockspec.issue35-java-evaluation/v1",
                "case": case_name, "case_id": case_config["case_id"],
                "snapshot_id": case_config["snapshot_id"], "mutant": mutant,
                "subject": "developer_reference" if candidate_path is None else "candidate",
                "candidate_identity": candidate_identity,
                "runtime_lock_digest": runtime["digest"], "dependency_rehash": False,
                "reference_mock_stub_sha256": digest_text(reference["mock_stub"] + "\n"),
                "reference_verify_sha256": digest_text(reference["verify"] + "\n"),
                "ordinary_region_sha256": raw_ordinary,
                "source_count": len(sources), "compile": compiled, "test": tested,
                "status": result_status(compiled, tested),
            }
            (output / "result.json").write_text(json.dumps(result, indent=2) + "\n")
            return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--case", choices=sorted(REFERENCE), required=True)
    parser.add_argument("--mutant", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--candidate", type=Path)
    parser.add_argument("--old-root", type=Path, default=Path("/home/haon/projects/Stub-Tester"))
    args = parser.parse_args()
    print(json.dumps(evaluate(
        args.case, args.mutant, args.output, args.old_root, args.candidate
    ), indent=2))


if __name__ == "__main__":
    main()
