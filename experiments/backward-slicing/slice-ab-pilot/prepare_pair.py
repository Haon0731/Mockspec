"""Prepare paired source-only / source-plus-slice inputs after local acceptance."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import random
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "experiments/backward-slicing"))
from mock_spec.adapters.frozen_dataset import FrozenDataset  # type: ignore[import-untyped]  # noqa: E402
from slice_contract import SliceClueContract, validate_source_quotes  # noqa: E402


def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def prepare(gate_path: Path, inventory_path: Path, case_id: str, output: Path) -> dict:
    gate = json.loads(gate_path.read_text())
    rows = [row for row in gate["cases"] if row["case_id"] == case_id]
    if gate.get("approved") is not True or len(rows) != 1 or rows[0].get("approved") is not True:
        raise ValueError("case has not passed independent slice acceptance")
    row = rows[0]
    slice_path = (ROOT / row["slice_path"]).resolve()
    if not slice_path.is_relative_to(ROOT):
        raise ValueError("accepted slice must be inside repository")
    payload = slice_path.read_bytes()
    if digest(payload) != row["slice_sha256"]:
        raise ValueError("accepted slice changed")
    contract = SliceClueContract.model_validate_json(payload)
    for field in ("case_id", "dataset", "language", "snapshot_id"):
        if getattr(contract.identity, field) != row[field]:
            raise ValueError("accepted slice identity differs from gate")
    if [source.model_dump(mode="json") for source in contract.sources] != row["sources"]:
        raise ValueError("accepted source scope differs from gate")
    inventory = json.loads(inventory_path.read_text())
    if digest(inventory_path.read_bytes()) != gate["inventory_sha256"]:
        raise ValueError("inventory changed")
    dataset = FrozenDataset(Path(inventory["registry_root"]), dataset=row["dataset"],
                            inventory_revision=row["inventory_revision"])
    with dataset.load_case(case_id) as case:
        if case.snapshot_id != row["snapshot_id"] or contract.identity.case_id != case_id:
            raise ValueError("frozen identity differs from accepted slice")
        sources = {path: case.read_source(path) for path in case.source_paths}
        by_id = {source.source_id: sources[source.logical_path].text
                 for source in contract.sources}
        validate_source_quotes(contract, by_id)
        for source in contract.sources:
            if digest(sources[source.logical_path].text.encode()) != source.sha256:
                raise ValueError("slice source differs from frozen input")
        output.mkdir(parents=True, exist_ok=False)
        source_hashes = {path: digest(source.text.encode()) for path, source in sources.items()}
        order = ["A", "B"]
        random.Random(20261117).shuffle(order)
        manifest = {
            "case_id": case_id, "snapshot_id": case.snapshot_id,
            "slice_sha256": digest(payload), "gate_sha256": digest(gate_path.read_bytes()),
            "source_hashes": source_hashes, "target_path": case.masked_test.logical_path,
            "order": order, "seed": 20261117, "attempts_per_arm": 1,
            "timeout_seconds": 600, "model": "deepseek-v4-pro",
            "monetary_cap_usd": None, "mutation_feedback": False,
            "candidate_path": "candidate." + ("py" if case.language == "python" else "java"),
        }
        for arm in order:
            arm_dir = output / arm
            workspace = arm_dir / "workspace"
            workspace.mkdir(parents=True)
            for path, source in sources.items():
                target = workspace / "repo" / path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(source.text)
            if arm == "B":
                (workspace / "slice.json").write_bytes(payload)
            task = (
                "Restore the missing Mock setup and verification code in the supplied masked test.\n"
                f"Case: {case_id}\nMasked test: repo/{case.masked_test.logical_path}\n"
                "You may inspect the supplied source repository using the broker tools.\n"
                "Write the COMPLETE restored test file to " + manifest["candidate_path"] + ".\n"
                "Keep every byte outside the marked replacement regions unchanged, including the markers.\n"
                "Only fill the original PY-SLOT or MOCK-STUB/MOCK-VERIFY regions.\n"
                "Do not modify the product code, skip tests, remove original assertions, replace the CUT, "
                "or use hidden answers. Preserve ordinary test context.\n"
                "Use source evidence to choose dependency replacements, responses and verifications.\n"
                "This is one attempt with a 600 second limit. There is no mutation feedback.\n"
            )
            if arm == "B":
                task += ("Additional analysis clues are in slice.json. Relations are conditional on their "
                         "listed premises; UNKNOWN means unresolved, not no effect. These clues are not "
                         "a complete execution specification and do not authorize extra test requirements.\n")
            (arm_dir / "task.txt").write_text(task)
        (output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
        return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gate", type=Path, required=True)
    parser.add_argument("--inventory", type=Path, required=True)
    parser.add_argument("--case-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    prepare(args.gate, args.inventory, args.case_id, args.output)
