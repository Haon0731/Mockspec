"""Run the bounded langflow_189 CodeQL bridge without executing its loader or target."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
from typing import Any

from langflow_contract import build_case_result
from langflow_mapping import map_result, read_rows, sha256
from prepare_langflow import EXPECTATIONS, prepare


HERE = Path(__file__).resolve().parent
QUERY = HERE / "langflow-origin-flow.ql"


def run_timed(command: list[str], time_path: Path, stdout_path: Path, stderr_path: Path) -> dict[str, Any]:
    started = time.monotonic()
    with stdout_path.open("wb") as stdout, stderr_path.open("wb") as stderr:
        completed = subprocess.run(
            ["/usr/bin/time", "-v", "-o", str(time_path), *command],
            stdout=stdout, stderr=stderr, check=False,
        )
    time_text = time_path.read_text(errors="replace") if time_path.exists() else ""
    rss = next((line for line in time_text.splitlines()
                if "Maximum resident set size (kbytes):" in line), None)
    return {"exit_code": completed.returncode, "seconds": time.monotonic() - started,
            "peak_rss_bytes": int(rss.rsplit(":", 1)[1].strip()) * 1024 if rss else None}


def issue22_binding(path: Path) -> tuple[dict[str, Any], str]:
    payload = json.loads(path.read_text())
    bindings = payload.get("source_selection", {}).get("dynamic_loader_bindings", [])
    if len(bindings) != 1:
        raise ValueError("issue22 artifact lacks a unique dynamic loader binding")
    return bindings[0], sha256(path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--codeql", required=True, type=Path)
    parser.add_argument("--registry-root", required=True, type=Path)
    parser.add_argument("--issue22", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(f"output must not exist: {args.output}")
    args.output.mkdir(parents=True)
    started = time.monotonic()
    expectations = json.loads(EXPECTATIONS.read_text())
    try:
        with tempfile.TemporaryDirectory(prefix="mockspec-langflow-backend-") as temporary:
            work = Path(temporary)
            input_root = work / "input"
            manifest = prepare(args.registry_root, input_root)
            shutil.copy2(input_root / "input-manifest.json", args.output / "input-manifest.json")
            old_binding, old_sha = issue22_binding(args.issue22)
            if old_binding != manifest["dynamic_loader_binding"]:
                raise ValueError("current authenticated loader binding differs from issue22")
            version = json.loads(subprocess.check_output(
                [str(args.codeql), "version", "--format=json"], text=True
            ))
            (args.output / "codeql-version.json").write_text(json.dumps(version, indent=2) + "\n")
            implementation = [
                Path(__file__).resolve(), HERE / "prepare_langflow.py", HERE / "langflow_mapping.py",
                HERE / "langflow_contract.py", QUERY, EXPECTATIONS, HERE.parent / "source_selection.py",
            ]
            pre_registration = {
                "schema": "mock-spec.langflow-backend-pre-registration/v1",
                "identity": {key: manifest[key] for key in
                             ("case_id", "dataset", "snapshot_id", "target_selector", "masking_contract")},
                "input_manifest_sha256": sha256(args.output / "input-manifest.json"),
                "issue22_artifact": {"path": str(args.issue22), "sha256": old_sha},
                "dynamic_loader_binding": old_binding,
                "invalid_token_identity_binding": manifest["invalid_token_identity_binding"],
                "expectations_sha256": sha256(EXPECTATIONS),
                "implementation_sha256": {str(path): sha256(path) for path in implementation},
                "expected_relations": expectations["preserve"], "expected_unknowns": expectations["unknown"],
                "configuration": {"codeql_version": version["version"], "threads": 2,
                                  "ram_mb": 4096, "timeout_seconds": 180},
                "target_executed": False,
            }
            (args.output / "pre-registration.json").write_text(
                json.dumps(pre_registration, indent=2) + "\n")

            database = work / "database"
            db_cost = run_timed([
                "timeout", "180", str(args.codeql), "database", "create", str(database),
                "--language=python", f"--source-root={input_root}", "--threads=2", "--ram=4096",
                "--overwrite",
            ], args.output / "database.time", args.output / "database.stdout",
                args.output / "database.stderr")
            if db_cost["exit_code"]:
                raise RuntimeError("CodeQL database creation failed")
            bqrs = work / "langflow.bqrs"
            query_cost = run_timed([
                "timeout", "180", str(args.codeql), "query", "run", f"--database={database}",
                "--threads=2", "--ram=4096", f"--output={bqrs}", str(QUERY),
            ], args.output / "query.time", args.output / "query.stdout", args.output / "query.stderr")
            if query_cost["exit_code"]:
                raise RuntimeError("CodeQL langflow query failed")
            csv_path = args.output / "langflow-origin-flow.csv"
            decode_cost = run_timed([
                str(args.codeql), "bqrs", "decode", "--format=csv", f"--output={csv_path}", str(bqrs),
            ], args.output / "decode.time", args.output / "decode.stdout", args.output / "decode.stderr")
            if decode_cost["exit_code"]:
                raise RuntimeError("CodeQL result decode failed")
            study = map_result(manifest, expectations, read_rows(csv_path))
            study["provenance"] = {"pre_registration_sha256": sha256(args.output / "pre-registration.json"),
                                   "query_csv_sha256": sha256(csv_path)}
            cost = {"database": db_cost, "query": query_cost, "decode": decode_cost,
                    "total_seconds": time.monotonic() - started,
                    "cache_state": "cold; OS and CodeQL distribution caches not isolated"}
            study["cost"] = cost
            study["case_result"] = build_case_result(manifest, study, expectations, cost)
            (args.output / "study.json").write_text(json.dumps(study, indent=2) + "\n")
            (args.output / "receipt.json").write_text(json.dumps({
                "status": "verified_incomplete", "target_executed": False,
                "study_sha256": sha256(args.output / "study.json"),
                "elapsed_seconds": time.monotonic() - started,
            }, indent=2) + "\n")
            print(json.dumps({"status": study["status"], "checks": study["checks"]}))
            return 0
    except Exception as exc:
        (args.output / "failure.json").write_text(json.dumps({
            "status": "failed", "stage": "langflow_backend_bridge", "type": type(exc).__name__,
            "message": str(exc), "elapsed_seconds": time.monotonic() - started,
            "target_executed": False,
        }, indent=2) + "\n")
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
