"""Run one generated CloudFoundry Mock candidate against the reviewed test.

The evaluator admits the task through the production Adapter, copies only source
files returned by the reviewed repository capability into a temporary workspace,
and mounts only dependency archives named by the frozen runtime lock.  It does
not run any snapshot helper or build inside the frozen repository.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import tempfile
import time
import uuid

from mock_spec.adapters.java import load_task_with_repository
from mock_spec.validation import SourceStore


CASE_ID = (
    "de.codecentric.boot.admin.client.registration.CloudFoundryApplicationFactoryTest#"
    "should_use_application_uri@d0085edf"
)
DATASET = "java-bechmark-strong"
SNAPSHOT_ID = "ff6812732f1e2779e09005d721f464cebed6b587925130e6a2555a154097214a"
IMAGE = "sha256:7268546607aefcf0ab62e58f9b58d9c890d7f3b50bd0bec1231314cbb357bd36"
TEST_CLASS = (
    "de.codecentric.boot.admin.client.registration.CloudFoundryApplicationFactoryTest"
)
EVALUATION_TIMEOUT_SECONDS = 60.0
MARKERS = {
    "mock_stub": ("// === MOCK-STUB-START ===", "// === MOCK-STUB-END ==="),
    "verify": ("// === VERIFY-START ===", "// === VERIFY-END ==="),
}


@dataclass(frozen=True)
class RunResult:
    exit_code: int | None
    stdout: str
    stderr: str
    elapsed_seconds: float
    timed_out: bool = False


def _captured_text(value: str | bytes | None) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value or ""


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _candidate(value: object) -> dict[str, str]:
    if not isinstance(value, dict):
        raise ValueError("candidate must be an object")
    if "cloudfoundry" in value:
        value = value["cloudfoundry"]
    if not isinstance(value, dict):
        raise ValueError("cloudfoundry candidate must be an object")
    if not {"mock_stub", "verify"} <= value.keys():
        raise ValueError("candidate requires mock_stub and verify")
    if any(not isinstance(value[key], str) for key in ("mock_stub", "verify")):
        raise ValueError("candidate regions must be strings")
    return {key: value[key] for key in ("mock_stub", "verify")}


def inject(masked: str, candidate: dict[str, str]) -> str:
    """Replace exactly the two empty Java regions, preserving all other bytes."""
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
        body = candidate[key]
        if body:
            rendered = "\n".join(indent + line for line in body.splitlines()) + "\n"
        else:
            rendered = ""
        replacement = f"{indent}{start}\n{rendered}{indent}{end}"
        result = result[: match.start()] + replacement + result[match.end() :]
    return result


def _outside_regions(text: str) -> bytes:
    normalized = text
    for key, (start, end) in MARKERS.items():
        pattern = re.compile(
            rf"(?ms)^(?P<indent>[ \t]*){re.escape(start)}\n.*?"
            rf"^(?P=indent){re.escape(end)}$"
        )
        normalized, count = pattern.subn(f"<{key}>", normalized)
        if count != 1:
            raise ValueError(f"invalid {key} region after injection")
    return normalized.encode("utf-8")


def _dependency_mounts(snapshot: Path, runtime: dict) -> tuple[list[str], str]:
    libs = snapshot / "tree/repo/libs"
    mounts: list[str] = []
    targets: list[str] = []
    dependencies = runtime.get("dependency_hashes")
    if not isinstance(dependencies, list) or not dependencies:
        raise ValueError("runtime lock has no dependency list")
    for item in dependencies:
        if not (isinstance(item, list) and len(item) == 2 and isinstance(item[0], str)):
            raise ValueError("invalid runtime dependency entry")
        relative = PurePosixPath(item[0])
        if len(relative.parts) != 2 or relative.parts[0] != "libs":
            raise ValueError("dependency path escapes the reviewed libs directory")
        source = libs / relative.name
        if not source.is_file() or source.is_symlink():
            raise ValueError(f"reviewed dependency is unavailable: {relative.name}")
        target = f"/deps/{relative.name}"
        mounts.extend(["-v", f"{source}:{target}:ro"])
        targets.append(target)
    return mounts, ":".join(targets)


def _docker_base(work: Path, mounts: list[str]) -> list[str]:
    return [
        "docker", "run", "--rm", "--name", "mockspec-java-" + uuid.uuid4().hex[:12],
        "--network=none", "--read-only",
        "--cap-drop=ALL", "--security-opt=no-new-privileges", "--pids-limit=128",
        "--memory=768m", "--cpus=1", "--tmpfs", "/tmp:rw,nosuid,nodev,size=64m",
        "--user", f"{os.getuid()}:{os.getgid()}", "-v", f"{work}:/work:rw",
        *mounts, "-w", "/work", "-e", "HOME=/tmp", IMAGE,
    ]


def _run(command: list[str], timeout: float = EVALUATION_TIMEOUT_SECONDS) -> RunResult:
    started = time.perf_counter()
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as error:
        if command[:2] == ["docker", "run"] and "--name" in command:
            subprocess.run(["docker", "rm", "-f", command[command.index("--name") + 1]],
                           capture_output=True, timeout=15)
        return RunResult(
            exit_code=None,
            stdout=_captured_text(error.stdout),
            stderr=_captured_text(error.stderr),
            elapsed_seconds=time.perf_counter() - started,
            timed_out=True,
        )
    return RunResult(
        exit_code=result.returncode,
        stdout=result.stdout,
        stderr=result.stderr,
        elapsed_seconds=time.perf_counter() - started,
    )


def evaluate(candidate_path: Path, output: Path, old_root: Path) -> dict:
    if output.exists():
        raise FileExistsError("output directory must not exist")
    output.mkdir(parents=True)
    snapshot = (
        old_root / ".omx/developer-realistic-registry-max/formal-snapshots"
        / DATASET / SNAPSHOT_ID
    )
    task, repository = load_task_with_repository(snapshot, case_id=CASE_ID)
    store = SourceStore(task, repository=repository)
    candidate = _candidate(json.loads(candidate_path.read_text(encoding="utf-8")))
    injected = inject(task.masked_test.text, candidate)
    outside_digest = _digest(_outside_regions(injected))
    expected_outside = _digest(_outside_regions(task.masked_test.text))
    if outside_digest != expected_outside:
        raise ValueError("candidate changed content outside the reviewed regions")

    runtime = json.loads((snapshot / "control/runtime-lock.json").read_text(encoding="utf-8"))
    mounts, classpath = _dependency_mounts(snapshot, runtime)
    receipt: dict[str, object] = {
        "case_id": CASE_ID,
        "snapshot_id": SNAPSHOT_ID,
        "runtime_lock_digest": runtime.get("digest"),
        "runtime_expected": f"{runtime.get('runtime_name')} {runtime.get('runtime_version')}",
        "image_id": IMAGE,
        "candidate_sha256": _digest(candidate_path.read_bytes()),
        "candidate_source_sha256": _digest(injected.encode("utf-8")),
        "outside_regions_sha256": outside_digest,
        "dependency_count": len(runtime["dependency_hashes"]),
        "dependency_rehash": False,
        "source_search": None,
        "source_count": 0,
        "status": "prepared",
    }
    _write_json(output / "result.json", receipt)

    try:
        search = store.search(path_pattern="src/main/**/*.java", max_output_chars=30000)
        receipt["source_search"] = {
            key: search.get(key) for key in ("status", "complete", "limit_reason", "usage")
        }
        paths = [match.get("logical_path") for match in search.get("matches", [])]
        if not paths or any(not isinstance(path, str) for path in paths):
            raise RuntimeError("reviewed source discovery returned no usable Java paths")
        with tempfile.TemporaryDirectory(prefix="mockspec-java-path-eval-") as temporary:
            work = Path(temporary)
            source_files: list[str] = []
            for logical_path in paths:
                source = store.analysis_source(logical_path)
                target = work / logical_path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(source.text, encoding="utf-8")
                source_files.append("/work/" + logical_path)
            test_path = work / task.masked_test.logical_path
            test_path.parent.mkdir(parents=True, exist_ok=True)
            test_path.write_text(injected, encoding="utf-8")
            source_files.append("/work/" + task.masked_test.logical_path)
            (work / "classes").mkdir()
            receipt["source_count"] = len(source_files)
            deadline = time.perf_counter() + EVALUATION_TIMEOUT_SECONDS

            compile_command = [
                *_docker_base(work, mounts), "javac", "-encoding", "UTF-8",
                "-cp", classpath, "-d", "/work/classes", *source_files,
            ]
            _write_json(output / "compile.command.json", compile_command)
            compiled = _run(compile_command, timeout=max(0.001, deadline - time.perf_counter()))
            (output / "compile.stdout.txt").write_text(compiled.stdout, encoding="utf-8")
            (output / "compile.stderr.txt").write_text(compiled.stderr, encoding="utf-8")
            receipt["compile"] = {
                "exit_code": compiled.exit_code,
                "elapsed_seconds": compiled.elapsed_seconds,
            }
            if compiled.timed_out:
                receipt["compile"]["timed_out"] = True
            _write_json(output / "result.json", receipt)
            if compiled.exit_code != 0:
                receipt["status"] = (
                    "compile_timeout" if compiled.timed_out else "compile_failed"
                )
                _write_json(output / "result.json", receipt)
                return receipt

            test_command = [
                *_docker_base(work, mounts), "java", "-cp", f"/work/classes:{classpath}",
                "org.junit.runner.JUnitCore", TEST_CLASS,
            ]
            _write_json(output / "test.command.json", test_command)
            tested = _run(test_command, timeout=max(0.001, deadline - time.perf_counter()))
            (output / "test.stdout.txt").write_text(tested.stdout, encoding="utf-8")
            (output / "test.stderr.txt").write_text(tested.stderr, encoding="utf-8")
            receipt["test"] = {
                "exit_code": tested.exit_code,
                "elapsed_seconds": tested.elapsed_seconds,
                "junit_ok": tested.exit_code == 0 and "OK (1 test)" in tested.stdout,
            }
            if tested.timed_out:
                receipt["test"]["timed_out"] = True
            if tested.timed_out:
                receipt["status"] = "test_timeout"
            else:
                receipt["status"] = (
                    "passed" if receipt["test"]["junit_ok"] else "test_failed"
                )
            _write_json(output / "result.json", receipt)
            return receipt
    finally:
        repository.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidate", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--old-root", required=True, type=Path)
    args = parser.parse_args()
    result = evaluate(args.candidate, args.output, args.old_root)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
