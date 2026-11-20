"""Regenerate the seven Issue #37 Python facts on the fix23 frozen snapshots."""

from __future__ import annotations

import argparse
import hashlib
import importlib
import json
from pathlib import Path
import subprocess
import sys
from typing import Any

from mock_spec.adapters.frozen_dataset import FrozenDataset
from mock_spec.bounded_navigation_view import (
    build_bounded_navigation_view,
    measure_bounded_navigation_view,
    serialize_bounded_navigation_view,
)
from mock_spec.navigation_facts import project_navigation_facts


CASES = (
    "hummingbot_175",
    "hummingbot_176",
    "hummingbot_177",
    "hummingbot_453",
    "redash_254",
    "tqsdk-python_420",
    "tqsdk-python_421",
)
DATASET = "python-benchmark-new"
REVISION = "0759c27f3d4cc7257d78d9ac2ced70089aedc60fb8d0f562b63cc8f750d7e8dc"


def _encoded(value: Any) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode()
        + b"\n"
    )


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _write_new(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_encoded(value))


def _token(case_id: str) -> str:
    return _sha256((DATASET + "\0" + case_id).encode())[:20]


def _source_records(case: Any, contract: dict[str, Any]) -> list[dict[str, Any]]:
    records = []
    for scope in contract["source_scope"]:
        source = (
            case.masked_test
            if scope["selection_role"] == "masked_test"
            else case.read_source(scope["logical_path"])
        )
        if _sha256(source.text.encode()) != scope["sha256"]:
            raise ValueError(f"source changed after backend run: {scope['logical_path']}")
        records.append(
            {
                "source_id": scope["source_id"],
                "logical_path": scope["logical_path"],
                "text": source.text,
                "start_line": source.start_line,
                "end_line": source.end_line,
            }
        )
    return records


def _coordinate_alignment(
    old_facts: dict[str, Any], new_facts: dict[str, Any]
) -> list[dict[str, Any]]:
    old = {row["observation_id"]: row for row in old_facts["observations"]}
    new = {row["observation_id"]: row for row in new_facts["observations"]}
    if old.keys() != new.keys():
        raise ValueError("observation membership changed across snapshot alignment")
    alignment = []
    for observation_id in sorted(old):
        before = old[observation_id]
        after = new[observation_id]
        if (before["kind"], before["origin"]) != (after["kind"], after["origin"]):
            raise ValueError(f"observation semantics changed: {observation_id}")
        old_location = before["location"]
        new_location = after["location"]
        if old_location["logical_path"] != new_location["logical_path"]:
            raise ValueError(f"observation path changed: {observation_id}")
        if (
            new_location["start_line"] != old_location["start_line"] + 1
            or new_location["end_line"] != old_location["end_line"] + 1
        ):
            raise ValueError(f"observation did not shift exactly one line: {observation_id}")
        alignment.append(
            {
                "observation_id": observation_id,
                "old_location": old_location,
                "fix23_location": new_location,
                "line_delta": 1,
            }
        )
    return alignment


def prepare(
    *, registry_root: Path, codeql: Path, r2_root: Path, output: Path
) -> dict[str, Any]:
    if not codeql.is_file():
        raise FileNotFoundError(f"CodeQL executable is absent: {codeql}")
    output.mkdir(parents=True, exist_ok=True)
    (output / "backend").mkdir(exist_ok=True)
    (output / "cases").mkdir(exist_ok=True)
    dataset = FrozenDataset(registry_root, dataset=DATASET, inventory_revision=REVISION)
    r2_manifest = json.loads((r2_root / "manifest.json").read_bytes())
    old_by_case = {row["case_id"]: (key, row) for key, row in r2_manifest.items()}

    tiktoken = importlib.import_module("tiktoken")
    encoding = tiktoken.get_encoding("o200k_base")

    def count_tokens(payload: bytes) -> int:
        return len(encoding.encode_ordinary(payload.decode()))

    counter_name = f"tiktoken/{tiktoken.__version__}/o200k_base/encode_ordinary"
    manifest: dict[str, dict[str, Any]] = {}
    alignments: list[dict[str, Any]] = []
    backend_script = Path(__file__).parents[1] / "python-backend-pilot/generic_backend.py"
    for case_id in CASES:
        key = _token(case_id)
        old_key, old_receipt = old_by_case[case_id]
        if old_key != key:
            raise ValueError(f"artifact key changed: {case_id}")
        entry = {
            "language": "python",
            "dataset": DATASET,
            "case_id": case_id,
            "inventory_revision": REVISION,
            "snapshot_id": dataset.snapshot_ids[case_id],
        }
        entry_path = output / "backend" / f"{key}-entry.json"
        _write_new(entry_path, entry)
        backend_output = output / "backend" / key
        receipt_path = backend_output / "batch-result.json"
        if receipt_path.is_file():
            result_code = 0
        else:
            if backend_output.exists():
                raise RuntimeError(f"incomplete backend directory must be preserved and moved: {case_id}")
            result = subprocess.run(
                [
                    sys.executable,
                    str(backend_script),
                    "--entry",
                    str(entry_path),
                    "--registry-root",
                    str(registry_root),
                    "--output",
                    str(backend_output),
                    "--codeql",
                    str(codeql),
                ],
                check=False,
                capture_output=True,
                text=True,
            )
            result_code = result.returncode
            (output / "backend" / f"{key}.stdout").write_text(result.stdout)
            (output / "backend" / f"{key}.stderr").write_text(result.stderr)
        backend = json.loads((backend_output / "batch-result.json").read_bytes())
        if result_code != 0 or backend.get("status") != "complete":
            raise RuntimeError(f"fix23 backend failed for {case_id}: {backend.get('error')}")
        contract = backend["result_contract"]
        with dataset.load_case(case_id) as case:
            sources = _source_records(case, contract)
            if contract["identity"]["snapshot_id"] != case.snapshot_id:
                raise ValueError(f"backend snapshot differs: {case_id}")
            if contract["identity"]["masked_test_sha256"] != _sha256(
                case.masked_test.text.encode()
            ):
                raise ValueError(f"backend masked test differs: {case_id}")
        facts = project_navigation_facts(contract, sources)
        facts_payload = _encoded(facts)
        facts_path = output / "cases" / f"{key}.facts.json"
        facts_path.write_bytes(facts_payload)
        facts_sha256 = _sha256(facts_payload)
        view = build_bounded_navigation_view(
            facts,
            archive_sha256=facts_sha256,
            archive_locator=facts_path.name,
            token_counter=count_tokens,
            token_counter_name=counter_name,
        )
        view_payload = serialize_bounded_navigation_view(view)
        view_path = output / "cases" / f"{key}.view.json"
        view_path.write_bytes(view_payload)
        measurement = measure_bounded_navigation_view(
            view, token_counter=count_tokens, token_counter_name=counter_name
        )
        if measurement["tokens"] > 8192:
            raise ValueError(f"bounded view exceeds budget: {case_id}")
        old_facts = json.loads((r2_root / old_receipt["facts_path"]).read_bytes())
        alignment = _coordinate_alignment(old_facts, facts)
        alignments.append({"case_id": case_id, "observations": alignment})
        manifest[key] = {
            "case_id": case_id,
            "dataset": DATASET,
            "language": "python",
            "snapshot_id": entry["snapshot_id"],
            "masked_test_sha256": contract["identity"]["masked_test_sha256"],
            "facts_path": str(facts_path.relative_to(output)),
            "facts_sha256": facts_sha256,
            "facts_bytes": len(facts_payload),
            "view_path": str(view_path.relative_to(output)),
            "view_sha256": _sha256(view_payload),
            "view_bytes": len(view_payload),
            "view_tokens": measurement["tokens"],
            "backend_path": str(backend_output.relative_to(output)),
            "r2_snapshot_id": old_receipt["snapshot_id"],
            "coordinate_alignment": alignment,
        }
        print(f"{case_id}: complete", flush=True)
    _write_new(output / "manifest.json", manifest)
    summary = {
        "schema_version": "mock-spec.issue37-fix23-overlay/v1",
        "scope": "seven fresh fix23 static analyses; no model or target-test execution",
        "registry_root": str(registry_root),
        "inventory_revision": REVISION,
        "case_count": len(manifest),
        "all_projection_complete": len(manifest) == len(CASES),
        "all_observations_shifted_one_line": all(
            observation["line_delta"] == 1
            for row in alignments
            for observation in row["observations"]
        ),
        "max_view_tokens": max(row["view_tokens"] for row in manifest.values()),
        "exact_token_counter": counter_name,
        "alignments": alignments,
    }
    _write_new(output / "summary.json", summary)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registry-root", required=True, type=Path)
    parser.add_argument("--codeql", required=True, type=Path)
    parser.add_argument("--r2-root", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    print(
        json.dumps(
            prepare(
                registry_root=args.registry_root,
                codeql=args.codeql,
                r2_root=args.r2_root,
                output=args.output,
            ),
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
