"""Bounded Spoon analysis of one already-reviewed Java source value."""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
from importlib.resources import files
from pathlib import Path
import tempfile
from typing import Any

from .schemas import Source


_PINNED_SPOON_SHA256 = "52ec4e8b874cb3f2b5a87a65ee7bf9da992a5175dcc66f539387dbb2c2053c7c"
_DEFAULT_SPOON_JAR = "/tmp/mockspec-step5b-tools/spoon.jar"
_DEFAULT_JAVA = "/tmp/mockspec-step5b-tools/jdk/bin/java"
_MAX_PROCESS_OUTPUT = 1_000_000


def _failure(source_id: str, code: str, message: str) -> dict[str, Any]:
    category = "unsupported" if code == "backend_unavailable" else "analysis_error"
    return {
        "status": "error",
        "backend": "spoon",
        "source_id": source_id,
        "error_code": code,
        "facts": [],
        "limitations": [
            "No result establishes that a structure or dependency is absent.",
            "Spoon facts are syntax relationships, not data-flow or feasible-path proofs.",
        ],
        "diagnostics": [{"category": category, "code": code, "message": message}],
    }


def _backend_paths() -> tuple[Path, Path, Path] | None:
    jar = Path(os.environ.get("MOCKSPEC_SPOON_JAR", _DEFAULT_SPOON_JAR))
    java = Path(os.environ.get("MOCKSPEC_JAVA", _DEFAULT_JAVA))
    javac = java.with_name("javac")
    if not (jar.is_file() and java.is_file() and javac.is_file()):
        return None
    try:
        digest = hashlib.sha256(jar.read_bytes()).hexdigest()
    except OSError:
        return None
    if digest != _PINNED_SPOON_SHA256:
        return None
    return jar, java, javac


async def _communicate(
    command: list[str], *, input_bytes: bytes | None, timeout_seconds: float, cwd: str
) -> tuple[int, bytes, bytes]:
    with (
        tempfile.TemporaryFile(dir=cwd) as stdin_file,
        tempfile.TemporaryFile(dir=cwd) as stdout_file,
        tempfile.TemporaryFile(dir=cwd) as stderr_file,
    ):
        if input_bytes is not None:
            stdin_file.write(input_bytes)
            stdin_file.seek(0)
        process = await asyncio.create_subprocess_exec(
            *command,
            stdin=stdin_file if input_bytes is not None else asyncio.subprocess.DEVNULL,
            stdout=stdout_file,
            stderr=stderr_file,
            cwd=cwd,
            env={"LANG": "C.UTF-8", "LC_ALL": "C.UTF-8"},
        )
        deadline = asyncio.get_running_loop().time() + timeout_seconds
        try:
            while process.returncode is None:
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    raise asyncio.TimeoutError
                try:
                    await asyncio.wait_for(process.wait(), timeout=min(0.05, remaining))
                except asyncio.TimeoutError:
                    if (os.fstat(stdout_file.fileno()).st_size > _MAX_PROCESS_OUTPUT
                            or os.fstat(stderr_file.fileno()).st_size > _MAX_PROCESS_OUTPUT):
                        raise OverflowError("Spoon process output exceeded the host limit")
            if (os.fstat(stdout_file.fileno()).st_size > _MAX_PROCESS_OUTPUT
                    or os.fstat(stderr_file.fileno()).st_size > _MAX_PROCESS_OUTPUT):
                raise OverflowError("Spoon process output exceeded the host limit")
        except BaseException:
            if process.returncode is None:
                process.kill()
            await process.wait()
            raise
        stdout_file.seek(0)
        stderr_file.seek(0)
        return process.returncode, stdout_file.read(), stderr_file.read()


def _stderr_is_benign(stderr: bytes) -> bool:
    if not stderr:
        return True
    return all(
        not line or line.startswith("SLF4J(W):")
        for line in stderr.decode("utf-8", "replace").splitlines()
    )


async def analyze_source(
    source: Source, *, timeout_seconds: float = 15
) -> dict[str, Any]:
    """Return Spoon AST facts for exactly ``source.text`` and no filesystem source."""
    if not isinstance(source, Source):
        raise TypeError("source must be a Source")
    if (type(timeout_seconds) not in {int, float}
            or not math.isfinite(timeout_seconds) or timeout_seconds <= 0):
        raise ValueError("timeout_seconds must be positive and finite")
    backend = _backend_paths()
    if backend is None:
        return _failure(
            source.source_id,
            "backend_unavailable",
            "Pinned Spoon 11.2.1 and its Java 17+ toolchain are unavailable.",
        )
    jar, java, javac = backend
    helper = files("mock_spec").joinpath("tools/ScenarioSpoon.java")
    started = asyncio.get_running_loop().time()

    try:
        with tempfile.TemporaryDirectory(prefix="mockspec-spoon-") as work:
            remaining = timeout_seconds - (asyncio.get_running_loop().time() - started)
            if remaining <= 0:
                raise asyncio.TimeoutError
            compile_code, _, _ = await _communicate(
                [str(javac), "-cp", str(jar), "-d", work, str(helper)],
                input_bytes=None,
                timeout_seconds=remaining,
                cwd=work,
            )
            if compile_code != 0:
                return _failure(
                    source.source_id,
                    "backend_unavailable",
                    "The fixed Spoon helper could not be compiled by the configured toolchain.",
                )

            remaining = timeout_seconds - (asyncio.get_running_loop().time() - started)
            if remaining <= 0:
                raise asyncio.TimeoutError
            request = json.dumps(
                {
                    "source_id": source.source_id,
                    "text": source.text,
                    "start_line": source.start_line,
                },
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode()
            code, stdout, stderr = await _communicate(
                [str(java), "-cp", f"{work}{os.pathsep}{jar}", "ScenarioSpoon"],
                input_bytes=request,
                timeout_seconds=remaining,
                cwd=work,
            )
    except asyncio.TimeoutError:
        return _failure(source.source_id, "timeout", "Spoon analysis timed out.")
    except asyncio.CancelledError:
        raise
    except OverflowError:
        return _failure(
            source.source_id, "analysis_error", "Spoon process output exceeded the host limit."
        )
    except OSError:
        return _failure(source.source_id, "analysis_error", "Spoon process launch failed.")

    if code != 0:
        return _failure(source.source_id, "analysis_error", "Spoon analysis failed.")
    try:
        result = json.loads(stdout)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return _failure(source.source_id, "analysis_error", "Spoon returned invalid JSON.")
    if not isinstance(result, dict) or result.get("source_id") != source.source_id:
        return _failure(source.source_id, "analysis_error", "Spoon returned invalid identity.")
    if not _stderr_is_benign(stderr):
        result.setdefault("diagnostics", []).append(
            {
                "category": "unsupported",
                "code": "backend_stderr",
                "message": "Spoon emitted backend diagnostics; absence claims are unsafe.",
            }
        )
        if result.get("status") == "ok":
            result["status"] = "incomplete"
    return result
