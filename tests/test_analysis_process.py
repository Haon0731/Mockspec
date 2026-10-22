"""Regression for reaped children whose asyncio protocol wait stays pending."""
import asyncio

import pytest

from mock_spec.analysis_process import communicate


def test_timeout_reaps_child_and_cancels_stale_protocol_wait(monkeypatch, tmp_path):
    class Process:
        returncode = None
        wait_cancelled = False

        async def wait(self):
            try:
                await asyncio.Future()
            except asyncio.CancelledError:
                self.wait_cancelled = True
                raise

        def kill(self):
            # Simulate receipt of an OS exit status with lagging protocol completion.
            self.returncode = -9

    process = Process()

    async def spawn(*args, **kwargs):
        return process

    monkeypatch.setattr(asyncio, 'create_subprocess_exec', spawn)

    async def run():
        with pytest.raises(asyncio.TimeoutError):
            await communicate(['host-fixed-worker'], input_bytes=None,
                              timeout_seconds=.01, cwd=str(tmp_path))
        assert process.returncode == -9
        assert process.wait_cancelled
        assert not [t for t in asyncio.all_tasks()
                    if t is not asyncio.current_task() and not t.done()]

    asyncio.run(run())
