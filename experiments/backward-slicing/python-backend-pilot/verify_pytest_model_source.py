"""Verify the host pytest sources used to justify the bounded capture summary."""
from __future__ import annotations

import argparse
import hashlib
import importlib
import json
from pathlib import Path
from typing import Any


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify(assumptions_path: Path) -> dict[str, Any]:
    assumptions = json.loads(assumptions_path.read_text(encoding="utf-8"))
    if assumptions.get("schema") != "mock-spec.pytest-raises-model/v1":
        raise ValueError("unsupported pytest model assumptions schema")

    pytest = importlib.import_module("pytest")
    actual_sources: list[dict[str, str]] = []
    failures: list[str] = []
    for expected in assumptions.get("sources", []):
        module_name = expected.get("module")
        if not isinstance(module_name, str):
            failures.append("source module is missing")
            continue
        module = importlib.import_module(module_name)
        source_path = Path(module.__file__).resolve()
        actual_digest = sha256(source_path)
        actual_sources.append({
            "module": module_name,
            "path": str(source_path),
            "sha256": actual_digest,
        })
        if actual_digest != expected.get("sha256"):
            failures.append(f"source digest mismatch: {module_name}")

    actual_version = getattr(pytest, "__version__", None)
    if actual_version != assumptions.get("pytest_version"):
        failures.append("pytest version mismatch")
    return {
        "schema": "mock-spec.pytest-model-source-receipt/v1",
        "status": "passed" if not failures else "blocked",
        "source_role": "host_verified_reference",
        "frozen_case_dependency_version": "unknown",
        "assumptions_sha256": sha256(assumptions_path),
        "actual_pytest_version": actual_version,
        "actual_sources": actual_sources,
        "failures": failures,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--assumptions", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    receipt = verify(args.assumptions)
    args.output.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    return 0 if receipt["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
