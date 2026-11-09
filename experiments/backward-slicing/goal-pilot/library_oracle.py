"""Run a strict expression oracle against the admitted frozen Java runtime."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import uuid

from mock_spec.adapters.java import load_task_with_repository


HERE = Path(__file__).resolve().parent
PATH_PILOT = HERE.parent / "path-pilot"
sys.path.insert(0, str(PATH_PILOT))
from java_evaluate import (  # noqa: E402
    CASE_ID,
    DATASET,
    EVALUATION_TIMEOUT_SECONDS,
    IMAGE,
    SNAPSHOT_ID,
    _dependency_mounts,
)


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _docker(work: Path, mounts: list[str], name: str) -> list[str]:
    return [
        "docker", "run", "--rm", "-i", "--name", name, "--network=none", "--read-only",
        "--cap-drop=ALL", "--security-opt=no-new-privileges", "--pids-limit=128",
        "--memory=768m", "--cpus=1", "--tmpfs", "/tmp:rw,nosuid,nodev,size=64m",
        "--user", f"{os.getuid()}:{os.getgid()}", "-v", f"{work}:/work:rw",
        "-v", f"{HERE}:/oracle:ro", *mounts, "-w", "/work", "-e", "HOME=/tmp", IMAGE,
    ]


def _run(command: list[str], timeout: float, *, stdin: str | None = None) -> dict[str, object]:
    started = time.perf_counter()
    try:
        process = subprocess.run(
            command, input=stdin, capture_output=True, text=True, timeout=timeout
        )
        return {"exit_code": process.returncode, "stdout": process.stdout,
                "stderr": process.stderr, "elapsed_seconds": time.perf_counter() - started,
                "timed_out": False}
    except subprocess.TimeoutExpired as error:
        if command[:2] == ["docker", "run"] and "--name" in command:
            subprocess.run(
                ["docker", "rm", "-f", command[command.index("--name") + 1]],
                capture_output=True, timeout=15, check=False,
            )
        stdout = error.stdout.decode(errors="replace") if isinstance(error.stdout, bytes) else error.stdout or ""
        stderr = error.stderr.decode(errors="replace") if isinstance(error.stderr, bytes) else error.stderr or ""
        return {"exit_code": None, "stdout": stdout, "stderr": stderr,
                "elapsed_seconds": time.perf_counter() - started, "timed_out": True}


def _validate_request(value: object) -> dict[str, object]:
    if not isinstance(value, dict) or not isinstance(value.get("queries"), list):
        raise ValueError("request must be an object with a queries array")
    return value


def evaluate(request_path: Path, output: Path, old_root: Path) -> dict[str, object]:
    if output.exists():
        raise FileExistsError("output directory must not exist")
    output.mkdir(parents=True)
    request_bytes = request_path.read_bytes()
    request = _validate_request(json.loads(request_bytes))
    snapshot = (
        old_root / ".omx/developer-realistic-registry-max/formal-snapshots"
        / DATASET / SNAPSHOT_ID
    )
    task, repository = load_task_with_repository(snapshot, case_id=CASE_ID)
    try:
        runtime = json.loads((snapshot / "control/runtime-lock.json").read_text(encoding="utf-8"))
        mounts, classpath = _dependency_mounts(snapshot, runtime)
        receipt: dict[str, object] = {
            "status": "prepared", "case_id": task.case_id, "snapshot_id": SNAPSHOT_ID,
            "request_sha256": _digest(request_bytes), "oracle_sha256": _digest((HERE / "LibraryOracle.java").read_bytes()),
            "runtime_lock_digest": runtime.get("digest"),
            "runtime_expected": f"{runtime.get('runtime_name')} {runtime.get('runtime_version')}",
            "image_id": IMAGE, "dependency_count": len(runtime["dependency_hashes"]),
            "dependency_rehash": False,
        }
        _write_json(output / "result.json", receipt)
        deadline = time.perf_counter() + EVALUATION_TIMEOUT_SECONDS
        with tempfile.TemporaryDirectory(prefix="mockspec-library-oracle-") as temporary:
            work = Path(temporary)
            (work / "classes").mkdir()
            compile_name = "mockspec-oracle-" + uuid.uuid4().hex[:12]
            compile_command = [
                *_docker(work, mounts, compile_name), "javac", "-encoding", "UTF-8",
                "-cp", classpath, "-d", "/work/classes", "/oracle/LibraryOracle.java",
            ]
            _write_json(output / "compile.command.json", compile_command)
            compiled = _run(compile_command, max(0.001, deadline - time.perf_counter()))
            (output / "compile.stdout.txt").write_text(str(compiled["stdout"]), encoding="utf-8")
            (output / "compile.stderr.txt").write_text(str(compiled["stderr"]), encoding="utf-8")
            receipt["compile"] = {key: compiled[key] for key in ("exit_code", "elapsed_seconds", "timed_out")}
            if compiled["exit_code"] != 0:
                receipt["status"] = "compile_timeout" if compiled["timed_out"] else "compile_failed"
                _write_json(output / "result.json", receipt)
                return receipt
            run_name = "mockspec-oracle-" + uuid.uuid4().hex[:12]
            run_command = [
                *_docker(work, mounts, run_name), "java", "-cp", f"/work/classes:{classpath}",
                "LibraryOracle",
            ]
            _write_json(output / "run.command.json", run_command)
            ran = _run(
                run_command, max(0.001, deadline - time.perf_counter()),
                stdin=json.dumps(request, ensure_ascii=False),
            )
            (output / "run.stdout.txt").write_text(str(ran["stdout"]), encoding="utf-8")
            (output / "run.stderr.txt").write_text(str(ran["stderr"]), encoding="utf-8")
            receipt["run"] = {key: ran[key] for key in ("exit_code", "elapsed_seconds", "timed_out")}
            if ran["exit_code"] != 0:
                receipt["status"] = "run_timeout" if ran["timed_out"] else "run_failed"
            else:
                results = json.loads(str(ran["stdout"]))
                if not isinstance(results, list) or len(results) != len(request["queries"]):
                    raise ValueError("oracle returned an invalid result array")
                receipt["status"] = "ok"
                receipt["results"] = results
            _write_json(output / "result.json", receipt)
            return receipt
    finally:
        repository.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--request", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--old-root", required=True, type=Path)
    args = parser.parse_args()
    result = evaluate(args.request, args.output, args.old_root)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
