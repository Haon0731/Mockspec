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
        wait_task = asyncio.create_task(process.wait())
        deadline = asyncio.get_running_loop().time() + timeout_seconds
        try:
            while process.returncode is None:
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    raise asyncio.TimeoutError
                try:
                    await asyncio.wait_for(
                        asyncio.shield(wait_task), timeout=min(0.05, remaining)
                    )
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
            # Some event-loop child watchers reap a killed process and update
            # ``returncode`` without resolving an already-created wait task.
            # Poll the reaped state briefly, then cancel only that stale task.
            for _ in range(100):
                if process.returncode is not None:
                    break
                await asyncio.sleep(0.005)
            if wait_task.done():
                await wait_task
            elif process.returncode is not None:
                # The transport has an OS exit status, so the child is reaped;
                # only the protocol's stale completion task remains.
                wait_task.cancel()
                try:
                    await wait_task
                except asyncio.CancelledError:
                    pass
            else:
                # Do not abandon a child if signal delivery/reaping is slow.
                await asyncio.shield(wait_task)
            raise
        stdout_file.seek(0)
        stderr_file.seek(0)
        return process.returncode, stdout_file.read(), stderr_file.read()


__all__ = ["communicate"]
