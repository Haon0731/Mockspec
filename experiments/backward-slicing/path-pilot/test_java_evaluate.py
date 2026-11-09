from pathlib import Path
import sys

import pytest


HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from java_evaluate import _candidate, _outside_regions, _run, inject  # noqa: E402


MASKED = """class T {
  void t() {
    // === MOCK-STUB-START ===
    // === MOCK-STUB-END ===
    run();
    // === VERIFY-START ===
    // === VERIFY-END ===
  }
}
"""


def test_inject_preserves_every_byte_outside_regions():
    candidate = {"mock_stub": "Object x = new Object();", "verify": "check(x);"}
    result = inject(MASKED, candidate)
    assert _outside_regions(result) == _outside_regions(MASKED)
    assert "    Object x = new Object();" in result
    assert "    check(x);" in result


def test_candidate_accepts_wrapped_and_rejects_missing_region():
    assert _candidate({"cloudfoundry": {"mock_stub": "x", "verify": "y"}}) == {
        "mock_stub": "x", "verify": "y"
    }
    with pytest.raises(ValueError, match="requires"):
        _candidate({"mock_stub": "x"})


def test_run_reports_timeout_instead_of_raising():
    result = _run([sys.executable, "-c", "import time; time.sleep(1)"], timeout=0.01)
    assert result.timed_out is True
    assert result.exit_code is None
