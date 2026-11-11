"""Materialize a minimal, authenticated Flask analysis workspace in a temporary directory.

This adapter does not execute the target or copy hidden/reference material. The output directory is
analysis input and must not be committed; the persistent evidence stores only identities and hashes.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

from mock_spec.adapters.frozen_dataset import FrozenDataset

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from criteria import extract_criteria


DATASET = "python-benchmark-new"
REVISION = "0759c27f3d4cc7257d78d9ac2ced70089aedc60fb8d0f562b63cc8f750d7e8dc"
CASE_ID = "flask_308"
SELECTED_PATHS = (
    "tests/test_308abort.py",
    "src/src/flask/__init__.py",
    "src/src/flask/globals.py",
    "src/src/flask/helpers.py",
)


def digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def prepare(registry_root: Path, output: Path) -> dict:
    output.mkdir(parents=True, exist_ok=False)
    dataset = FrozenDataset(registry_root, dataset=DATASET, inventory_revision=REVISION)
    with dataset.load_case(CASE_ID) as case:
        if case.snapshot_id != "723639bebf43aa4f8a5c007ee21c31b0647d61488945905921fb2351be69b78c":
            raise ValueError("unexpected frozen snapshot")
        rows = []
        for logical_path in SELECTED_PATHS:
            if logical_path == case.masked_test.logical_path:
                source = case.masked_test
                role = "masked_test"
            else:
                source = case.read_source(logical_path)
                role = "visible_product_or_support"
            destination = output / logical_path
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(source.text, encoding="utf-8")
            rows.append({
                "logical_path": logical_path,
                "source_id": source.source_id,
                "sha256": digest(source.text),
                "bytes": len(source.text.encode("utf-8")),
                "role": role,
            })
        manifest = {
            "schema": "mock-spec.issue24-codeql-input/v2",
            "case_id": CASE_ID,
            "dataset": DATASET,
            "inventory_revision": REVISION,
            "snapshot_id": case.snapshot_id,
            "target_selector": case.target_selector,
            "masking_contract": case._masking_contract,
            "adaptation": "selected visible files materialized without executing target code",
            "target_extraction": extract_criteria(
                case.masked_test.text, case.language, case.target_selector
            ),
            "files": rows,
        }
    (output / "input-manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--registry-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(prepare(args.registry_root, args.output), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
