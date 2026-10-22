"""Historical command compatibility; implementation tests live in test_audited_run."""

import os
import subprocess
import sys
from pathlib import Path

from mock_spec import audited_run, live_v02


def test_legacy_command_uses_current_runner():
    assert live_v02.main is audited_run.main


def test_both_module_commands_accept_help():
    env = {**os.environ, "PYTHONPATH": str(Path(audited_run.__file__).parents[1])}
    for module in ("mock_spec.live_v02", "mock_spec.audited_run"):
        result = subprocess.run(
            [sys.executable, "-m", module, "--help"],
            env=env, capture_output=True, text=True, timeout=15,
        )
        assert result.returncode == 0
        assert "--agent-version {v0.1,v0.2}" in result.stdout
