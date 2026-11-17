"""Project one authenticated local analysis artifact into slice clues."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT / "src"))

from mock_spec.adapters.frozen_dataset import FrozenDataset  # type: ignore[import-untyped]  # noqa: E402
from slice_projection import project_local_slice  # noqa: E402


def _authenticated_observation_mapping(
    receipt_path: Path, local: dict, case_id: str,
) -> dict[str, str]:
    receipt_bytes = receipt_path.read_bytes()
    if hashlib.sha256(receipt_bytes).hexdigest() != local["baseline"]["sha256"]:
        raise ValueError("observation receipt digest differs from authenticated baseline")
    receipt = json.loads(receipt_bytes)
    cases = [item for item in receipt["cases"] if item["identity"]["case_id"] == case_id]
    if len(cases) != 1:
        raise ValueError("observation receipt must select exactly one case")
    receipt_observations = cases[0]["result_contract"]["observations"]
    mapping: dict[str, str] = {}
    for observation in local["analysis"]["observations"]:
        local_id = observation.get("local_observation_id", observation["observation_id"])
        line = observation["location"]["start_line"]
        matches = [
            item for item in receipt_observations
            if item["kind"] == "value_assertion"
            and item["location"]["source_id"] == "test"
            and item["location"]["start_line"] == line
            and item["location"]["end_line"] == observation["location"]["end_line"]
        ]
        if len(matches) != 1:
            raise ValueError("observation receipt cannot authenticate source-local criterion")
        mapping[local_id] = matches[0]["observation_id"]
    if set(mapping.values()) != {item["observation_id"] for item in receipt_observations}:
        raise ValueError("observation receipt identity set differs from local observations")
    return mapping


def project(inventory_path: Path, local_path: Path, observation_receipt: Path) -> dict:
    inventory = json.loads(inventory_path.read_text())
    local = json.loads(local_path.read_text())
    identity = local["identity"]
    entries = [item for item in inventory["cases"] if item["case_id"] == identity["case_id"]]
    if len(entries) != 1:
        raise ValueError("inventory must select exactly one case")
    entry = entries[0]
    for field in ("case_id", "dataset", "language", "snapshot_id", "inventory_revision"):
        if entry[field] != identity[field]:
            raise ValueError("local result identity differs from inventory")
    product_sources = [item for item in local["sources"] if item["source_id"] != "test"]
    test_sources = [item for item in local["sources"] if item["source_id"] == "test"]
    if len(product_sources) != 1 or len(test_sources) != 1:
        raise ValueError("local result must select one product source")
    dataset = FrozenDataset(
        Path(inventory["registry_root"]), dataset=entry["dataset"],
        inventory_revision=entry["inventory_revision"],
    )
    with dataset.load_case(entry["case_id"]) as case:
        if case.snapshot_id != entry["snapshot_id"]:
            raise ValueError("loaded snapshot differs from inventory")
        test = case.masked_test
        product = case.read_source(product_sources[0]["logical_path"])
        sources = [
            {"role": "test", "source_id": test.source_id,
             "logical_path": test.logical_path, "sha256": test_sources[0]["sha256"],
             "text": test.text},
            {"role": "product", "source_id": product.source_id,
             "logical_path": product.logical_path, "sha256": product_sources[0]["sha256"],
             "text": product.text},
        ]
        contract = project_local_slice(
            identity={key: identity[key] for key in ("case_id", "dataset", "language", "snapshot_id")},
            sources=sources, local_analysis=local["analysis"],
            expected_observation_ids=_authenticated_observation_mapping(
                observation_receipt, local, identity["case_id"],
            ),
            wall_seconds=local["cost"]["wall_seconds"],
        )
    return contract.model_dump(mode="json")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inventory", type=Path, required=True)
    parser.add_argument("--local-result", type=Path, required=True)
    parser.add_argument("--observation-receipt", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError("refusing to overwrite slice artifact")
    result = project(args.inventory, args.local_result, args.observation_receipt)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")


if __name__ == "__main__":
    main()
