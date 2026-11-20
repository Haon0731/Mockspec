"""Build the 158 r2 + 7 fix23 offline preflight cohort."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


FIX23_CASES = {
    "hummingbot_175",
    "hummingbot_176",
    "hummingbot_177",
    "hummingbot_453",
    "redash_254",
    "tqsdk-python_420",
    "tqsdk-python_421",
}


def _read(path: Path) -> Any:
    return json.loads(path.read_bytes())


def _write(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _case_map(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {row["case_id"]: row for row in payload["cases"]}


def build(
    *,
    base_inventory: Path,
    base_results: Path,
    base_navigation: Path,
    overlay_navigation: Path,
    overlay_registry: Path,
    overlay_revision: str,
    output: Path,
) -> None:
    if output.exists():
        raise FileExistsError(f"mixed cohort already exists: {output}")
    base_inventory_payload = _read(base_inventory)
    base_results_payload = _read(base_results)
    base_manifest = _read(base_navigation / "manifest.json")
    overlay_manifest = _read(overlay_navigation / "manifest.json")
    overlay_index_path = (
        overlay_registry
        / "formal-snapshots/indexes/python-benchmark-new"
        / f"{overlay_revision}.json"
    )
    overlay_index = _read(overlay_index_path)

    base_inventory_cases = _case_map(base_inventory_payload)
    base_result_cases = {
        row["identity"]["case_id"]: row for row in base_results_payload["cases"]
    }
    base_receipts = {row["case_id"]: (token, row) for token, row in base_manifest.items()}
    overlay_receipts = {
        row["case_id"]: (token, row) for token, row in overlay_manifest.items()
    }
    if not (
        FIX23_CASES <= overlay_index["cases"].keys()
        and FIX23_CASES <= overlay_receipts.keys()
    ):
        raise ValueError("fix23 overlay does not contain all seven approved cases")

    inventory_rows: list[dict[str, Any]] = []
    overlay_result_rows: list[dict[str, Any]] = []
    receipts: dict[str, Any] = {}
    output.mkdir(parents=True)
    for case_id in sorted(base_inventory_cases):
        use_overlay = case_id in FIX23_CASES
        token, receipt = (overlay_receipts if use_overlay else base_receipts)[case_id]
        navigation_root = overlay_navigation if use_overlay else base_navigation
        inventory_row = dict(base_inventory_cases[case_id])
        if use_overlay:
            inventory_row.update(
                inventory_revision=overlay_revision,
                snapshot_id=overlay_index["cases"][case_id],
                snapshot_root=str(
                    overlay_registry
                    / "formal-snapshots/python-benchmark-new"
                    / overlay_index["cases"][case_id]
                ),
                registry_root=str(overlay_registry),
            )
            result_row = _read(overlay_navigation / receipt["backend_path"] / "batch-result.json")
        else:
            inventory_row["registry_root"] = base_inventory_payload["registry_root"]
            result_row = base_result_cases[case_id]
        if inventory_row["snapshot_id"] != result_row["identity"]["snapshot_id"]:
            raise ValueError(f"inventory/result snapshot mismatch: {case_id}")
        if inventory_row["snapshot_id"] != receipt["snapshot_id"]:
            raise ValueError(f"inventory/navigation snapshot mismatch: {case_id}")
        copied = dict(receipt)
        for kind in ("facts", "view"):
            source = navigation_root / receipt[f"{kind}_path"]
            if _sha256(source) != receipt[f"{kind}_sha256"]:
                raise ValueError(f"{kind} digest mismatch: {case_id}")
            copied[f"{kind}_path"] = str(source.resolve())
        inventory_rows.append(inventory_row)
        if use_overlay:
            overlay_result_rows.append(result_row)
        if token in receipts:
            raise ValueError(f"duplicate navigation token: {token}")
        receipts[token] = copied

    if len(inventory_rows) != 165 or len(overlay_result_rows) != 7 or len(receipts) != 165:
        raise ValueError("mixed cohort does not contain 165 unique cases")

    mixed_inventory = dict(base_inventory_payload)
    mixed_inventory["registry_root"] = base_inventory_payload["registry_root"]
    mixed_inventory["cases"] = inventory_rows
    mixed_inventory["cohort_basis"] = (
        "Issue #37 mixed generation cohort: 158 original r2 snapshots and seven "
        "evaluation-aligned fix23 snapshots"
    )
    _write(output / "inventory.json", mixed_inventory)
    _write(output / "results-overlay.json", {"cases": overlay_result_rows})
    _write(output / "manifest.json", receipts)
    _write(
        output / "overlay-receipt.json",
        {
            "schema_version": "mockspec-issue37-fix23-overlay/v1",
            "base_cases": 158,
            "fix23_cases": sorted(FIX23_CASES),
            "case_count": 165,
            "base_inventory_sha256": _sha256(base_inventory),
            "base_results_sha256": _sha256(base_results),
            "base_manifest_sha256": _sha256(base_navigation / "manifest.json"),
            "overlay_registry_index_sha256": _sha256(overlay_index_path),
            "overlay_inventory_revision": overlay_revision,
            "overlay_manifest_sha256": _sha256(overlay_navigation / "manifest.json"),
        },
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-inventory", required=True, type=Path)
    parser.add_argument("--base-results", required=True, type=Path)
    parser.add_argument("--base-navigation", required=True, type=Path)
    parser.add_argument("--overlay-navigation", required=True, type=Path)
    parser.add_argument("--overlay-registry", required=True, type=Path)
    parser.add_argument("--overlay-revision", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    build(
        base_inventory=args.base_inventory,
        base_results=args.base_results,
        base_navigation=args.base_navigation,
        overlay_navigation=args.overlay_navigation,
        overlay_registry=args.overlay_registry,
        overlay_revision=args.overlay_revision,
        output=args.output,
    )


if __name__ == "__main__":
    main()
