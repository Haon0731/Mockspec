from __future__ import annotations

import json
from pathlib import Path

from verify_pytest_model_source import verify


def test_checked_host_pytest_sources_match_registered_assumptions() -> None:
    receipt = verify(Path(__file__).with_name("pytest_model_assumptions.json"))
    assert receipt["status"] == "passed"
    assert receipt["source_role"] == "host_verified_reference"
    assert receipt["frozen_case_dependency_version"] == "unknown"
    assert len(receipt["actual_sources"]) == 2


def test_source_digest_mismatch_is_blocked(tmp_path: Path) -> None:
    source = Path(__file__).with_name("pytest_model_assumptions.json")
    assumptions = json.loads(source.read_text(encoding="utf-8"))
    assumptions["sources"][0]["sha256"] = "0" * 64
    changed = tmp_path / "assumptions.json"
    changed.write_text(json.dumps(assumptions), encoding="utf-8")
    receipt = verify(changed)
    assert receipt["status"] == "blocked"
    assert receipt["failures"] == ["source digest mismatch: _pytest.raises"]
