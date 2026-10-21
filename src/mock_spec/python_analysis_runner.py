"""Fixed isolated-process entry point for :mod:`mock_spec.python_analysis`."""

from __future__ import annotations

from pathlib import Path
import sys


# Isolated mode intentionally omits the script/package directory.  Add only the
# package root derived from this trusted installed file; no caller path is used.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mock_spec.python_analysis import _subprocess_main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(_subprocess_main())
