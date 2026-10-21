"""Shared cancellable subprocess boundary for trusted analysis backends."""

from __future__ import annotations

import asyncio
import os
import tempfile


async def communicate(
    command: list[str],
    *,
    input_bytes: bytes | None,
    timeout_seconds: float,
    cwd: str,
    max_output_bytes: int = 1_000_000,
) -> tuple[int, bytes, bytes]:
    """Run a fixed host command with scrubbed environment and bounded file IO."""

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
                    if (os.fstat(stdout_file.fileno()).st_size > max_output_bytes
                            or os.fstat(stderr_file.fileno()).st_size > max_output_bytes):
                        raise OverflowError("analysis process output exceeded the host limit")
            if (os.fstat(stdout_file.fileno()).st_size > max_output_bytes
                    or os.fstat(stderr_file.fileno()).st_size > max_output_bytes):
                raise OverflowError("analysis process output exceeded the host limit")
        except BaseException:
            if process.returncode is None:
                process.kill()
            await process.wait()
            raise
        stdout_file.seek(0)
        stderr_file.seek(0)
        return process.returncode, stdout_file.read(), stderr_file.read()


__all__ = ["communicate"]
