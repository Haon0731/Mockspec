"""Run the registered Python observations using only their required CodeQL queries."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
from typing import Any

import demand_analysis as demand
import demand_roots


HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
PYTHON = REPO / ".venv/bin/python"

RELATION_QUERY_TABLE = {
    "flask call/value/control observations": ["flask-observation-flow"],
    "flask normal/exception execution": ["flask-abort", "exceptions"],
    "pytest captured exception observation": ["pytest-exception-flow"],
    "visible state write": ["visible-state-flow"],
    "argument and return-alias unknown state": ["unknown-effect-flow"],
    "receiver/global/escaped state": ["state-boundary-flow"],
    "handler/finally state": ["unknown-exception-state"],
}

SUPPORT_FILES = ("pytest-model-source-receipt.json",)


def digest_json(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def run_timed(command: list[str], time_file: Path, *, stdout: Path, stderr: Path) -> dict:
    started = time.monotonic()
    with stdout.open("wb") as out, stderr.open("wb") as err:
        completed = subprocess.run(
            ["/usr/bin/time", "-v", "-o", str(time_file), *command],
            stdout=out, stderr=err, check=False,
        )
    text = time_file.read_text(encoding="utf-8", errors="replace") if time_file.is_file() else ""
    rss_line = next((line for line in text.splitlines()
                     if "Maximum resident set size (kbytes):" in line), None)
    return {"exit_code": completed.returncode, "seconds": time.monotonic() - started,
            "peak_rss_bytes": int(rss_line.rsplit(":", 1)[1].strip()) * 1024 if rss_line else None}


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def canonical_study(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    case = value["case_result"]
    normalized = {
        "identity": case["identity"], "observations": case["observations"],
        "entry_point": case["entry_point"], "relations": case["relations"],
        "relation_coverage": case["relation_coverage"],
        "analysis_boundaries": case["analysis_boundaries"],
        "completion": case["completion"], "semantic_validation": case["semantic_validation"],
    }
    normalized["relations"] = sorted(
        normalized["relations"], key=lambda row: json.dumps(row, sort_keys=True)
    )
    return normalized


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--codeql", type=Path, required=True)
    parser.add_argument("--registry-root", type=Path, required=True)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--max-units", type=int, default=9)
    parser.add_argument("--max-depth", type=int, default=3)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(f"output must not exist: {args.output}")
    args.output.mkdir(parents=True)
    started = time.monotonic()

    observations = {
        "flask": json.loads((HERE / "observation_expectations.json").read_text()),
        "state": json.loads((HERE / "state_observation_expectations.json").read_text()),
        "recursive": json.loads((HERE / "recursive_observation_expectations.json").read_text()),
    }
    plan_result = demand.plan(
        ["flask-observations", "state-observations", "recursive-observation"],
        max_units=args.max_units, max_depth=args.max_depth,
    )
    plan_record = {"observations": observations, "relations": RELATION_QUERY_TABLE,
                   "plan": plan_result}
    plan_sha = digest_json(plan_record)
    config = {"threads": 2, "ram_mb": 4096, "timeout_seconds": 180,
              "language": "python", "query_units": plan_result["selected_units"],
              "qlpack_sha256": demand.sha256(HERE / "qlpack.yml")}
    budget = {"max_units": args.max_units, "max_depth": args.max_depth}

    version = json.loads(subprocess.check_output(
        [str(args.codeql), "version", "--format=json"], text=True
    ))
    version_string = version["version"]
    (args.output / "codeql-version.json").write_text(
        json.dumps(version, indent=2) + "\n", encoding="utf-8"
    )
    (args.output / "pre-registration.json").write_text(json.dumps({
        "schema": demand.SCHEMA, "plan_sha256": plan_sha, "plan": plan_record,
        "config": config, "config_sha256": digest_json(config),
        "budget": budget, "budget_sha256": digest_json(budget),
        "baseline": str(args.baseline),
        "baseline_studies": {
            name: demand.sha256(args.baseline / name)
            for name in ("observation-study.json", "state-observation-study.json")
        },
        "baseline_role": (
            "semantic comparison only; the stable pytest host-reference receipt is imported "
            "and revalidated against the current assumptions hash, but no baseline relation "
            "or mechanism verdict is imported"
        ),
    }, indent=2) + "\n", encoding="utf-8")

    with tempfile.TemporaryDirectory(prefix="mockspec-issue29-") as temp:
        work = Path(temp)
        flask_input = work / "flask-input"
        prepare_started = time.monotonic()
        with (args.output / "prepare-flask.stdout").open("wb") as stdout:
            subprocess.run([
                str(PYTHON), str(HERE / "prepare_flask.py"),
                "--registry-root", str(args.registry_root), "--output", str(flask_input),
            ], stdout=stdout, check=True)
        probe_input = work / "probe-input"
        probe_input.mkdir()
        for source in ("mechanism_probe.py", "pytest_model_probe.py", "demand_recursive_probe.py"):
            shutil.copy2(HERE / source, probe_input / source)
        source_roots = {"flask": flask_input, "probe": probe_input}
        source_ids = {kind: demand.source_identity(root) for kind, root in source_roots.items()}
        query_pack = work / "query-pack"
        query_pack.mkdir()
        shutil.copy2(HERE / "qlpack.yml", query_pack / "qlpack.yml")
        for query_source in HERE.glob("*.ql"):
            shutil.copy2(query_source, query_pack / query_source.name)
        shutil.copy2(HERE / "DemandRoots.qll", query_pack / "DemandRoots.qll")
        root_history: list[dict[str, Any]] = []

        def write_roots(reached_product: tuple[str, int] | None) -> None:
            text, record = demand_roots.generate(
                flask_source_root=flask_input,
                state_source=probe_input / "mechanism_probe.py",
                recursive_source=probe_input / "demand_recursive_probe.py",
                observation_expectations=observations["flask"],
                state_expectations=observations["state"],
                recursive_expectations=observations["recursive"],
                reached_product=reached_product,
            )
            (query_pack / "DemandRoots.qll").write_text(text, encoding="utf-8")
            root_history.append(record)

        write_roots(None)
        selector_sha = root_history[-1]["module_sha256"]
        plan_record["selector"] = {
            "kind": "generated_authenticated_root_facts",
            "initial": root_history[-1],
            "expectations_sha256": {
                "flask": demand.sha256(HERE / "observation_expectations.json"),
                "state": demand.sha256(HERE / "state_observation_expectations.json"),
                "recursive": demand.sha256(HERE / "recursive_observation_expectations.json"),
            },
        }
        plan_sha = digest_json({"plan": plan_record, "source_identities": source_ids})
        prereg_path = args.output / "pre-registration.json"
        prereg = json.loads(prereg_path.read_text(encoding="utf-8"))
        prereg["plan"] = plan_record
        prereg["plan_sha256"] = plan_sha
        prereg["source_identities"] = source_ids
        prereg_path.write_text(json.dumps(prereg, indent=2) + "\n", encoding="utf-8")
        prep_seconds = time.monotonic() - prepare_started
        databases: dict[str, Path] = {}
        database_cost: dict[str, dict] = {}

        def ensure_database(kind: str) -> Path:
            if kind in databases:
                return databases[kind]
            database = work / f"{kind}-db"
            result = run_timed([
                "timeout", "180", str(args.codeql), "database", "create", str(database),
                "--language=python", f"--source-root={source_roots[kind]}",
                "--threads=2", "--ram=4096", "--overwrite",
            ], args.output / f"{kind}-db.time", stdout=args.output / f"{kind}-db.stdout",
               stderr=args.output / f"{kind}-db.stderr")
            database_cost[kind] = result
            if result["exit_code"] != 0:
                raise RuntimeError(f"database creation failed for {kind}")
            databases[kind] = database
            return database

        def run_query(unit: demand.Unit, target: Path) -> dict:
            assert unit.database and unit.query
            database = ensure_database(unit.database)
            prefix = f"{unit.database}-{unit.name}"
            bqrs = work / f"{prefix}.bqrs"
            result = run_timed([
                "timeout", "180", str(args.codeql), "query", "run",
                f"--database={database}", "--threads=2", "--ram=4096",
                f"--output={bqrs}", str(query_pack / unit.query),
            ], args.output / f"{prefix}.time", stdout=args.output / f"{prefix}.stdout",
               stderr=args.output / f"{prefix}.stderr")
            if result["exit_code"] != 0:
                return {"status": "failed", **result}
            decode_result = run_timed([
                str(args.codeql), "bqrs", "decode", "--format=csv",
                f"--output={target}", str(bqrs),
            ], args.output / f"{prefix}-decode.time",
                stdout=args.output / f"{prefix}-decode.stdout",
                stderr=args.output / f"{prefix}-decode.stderr")
            return {"status": "complete" if decode_result["exit_code"] == 0 else "failed",
                    "query": result, "decode": decode_result,
                    "seconds": result["seconds"] + decode_result["seconds"]}

        def expand_reached_product(unit: demand.Unit, result_path: Path) -> None:
            if unit.name != "flask-observation-flow":
                return
            rows = [row for row in read_csv(result_path)
                    if row.get("relation") == "resolved_test_product_call"]
            endpoints = {(row["targetPath"], int(row["targetLine"])) for row in rows}
            if len(endpoints) != 1:
                raise RuntimeError(f"observation-rooted call binding is not unique: {sorted(endpoints)}")
            write_roots(next(iter(endpoints)))

        identity = {
            "plan_sha256": plan_sha,
            "mapper_sha256": digest_json({
                "observation": demand.sha256(HERE / "observation_mapping.py"),
                "state": demand.sha256(HERE / "state_observation_mapping.py"),
            }),
            "config_sha256": digest_json(config), "budget_sha256": digest_json(budget),
        }
        manifest = demand.execute_plan(
            plan_result, source_ids=source_ids, codeql_version=version_string,
            context="registered-flask-and-state-observations", cache=args.cache,
            output=args.output / "relations", identity=identity,
            summary_version="current-run-selected-mechanisms-v1",
            on_result=expand_reached_product,
            identity_for_unit=lambda _: {
                "root_selector_sha256": demand.sha256(query_pack / "DemandRoots.qll")
            },
            runner=run_query,
        )
        if not manifest["complete"] or any(
            row["status"] not in {"executed", "cache_hit"} for row in manifest["attempts"]
        ):
            (args.output / "result.json").write_text(json.dumps({
                "schema": demand.SCHEMA, "status": "partial",
                "unexpanded_boundaries": manifest["unexpanded_boundaries"],
                "attempts": manifest["attempts"], "prepare_seconds": prep_seconds,
                "database_cost": database_cost,
            }, indent=2) + "\n", encoding="utf-8")
            return 1

        stage = work / "mapping-stage"
        stage.mkdir()
        for name in SUPPORT_FILES:
            shutil.copy2(args.baseline / name, stage / name)
        shutil.copy2(flask_input / "input-manifest.json", stage / "input-manifest.json")
        for path in (args.output / "relations").glob("*.csv"):
            shutil.copy2(path, stage / path.name)
        for path in (args.output / "relations").glob("*.receipt.json"):
            shutil.copy2(path, stage / path.name)
        for path in args.output.glob("*.time"):
            shutil.copy2(path, stage / path.name)
        input_manifest = stage / "input-manifest.json"
        pytest_model = HERE / "pytest_model_assumptions.json"
        (stage / "pre-registration.json").write_text(json.dumps({
            "schema": "mock-spec.python-observation-pre-registration/v1",
            "expectations_sha256": demand.sha256(HERE / "observation_expectations.json"),
            "pytest_model_sha256": demand.sha256(pytest_model),
            "input_manifest_sha256": demand.sha256(input_manifest),
        }, indent=2) + "\n", encoding="utf-8")
        (stage / "state-pre-registration.json").write_text(json.dumps({
            "schema": "mock-spec.python-state-observation-pre-registration/v1",
            "expectations_sha256": demand.sha256(HERE / "state_observation_expectations.json"),
            "source_sha256": demand.sha256(HERE / "mechanism_probe.py"),
            "query_sha256": demand.sha256(HERE / "state-boundary-flow.ql"),
            "visible_query_sha256": demand.sha256(HERE / "visible-state-flow.ql"),
            "mapper_sha256": demand.sha256(HERE / "state_observation_mapping.py"),
        }, indent=2) + "\n", encoding="utf-8")

        pytest_rows = read_csv(stage / "flask-pytest-exception-flow.csv")
        pytest_observations = {int(row["observationLine"]) for row in pytest_rows
                               if row.get("observationLine", "").isdigit()}
        state_files = [
            "probe-visible-state-flow.csv", "probe-unknown-effect-flow.csv",
            "probe-state-boundary-flow.csv", "probe-unknown-exception-state.csv",
        ]
        state_rows = [row for name in state_files for row in read_csv(stage / name)]
        state_checks = {}
        for item in observations["state"]["observations"]:
            state_checks[item["observation_id"]] = any(
                row.get("functionName") == item["function"]
                and row.get("observedLine") == str(item["line"])
                and row.get("relation") == item["relation"] for row in state_rows
            )
        for item in observations["state"].get("unknown_cases", []):
            state_checks[f"unknown:{item['function']}"] = any(
                row.get("functionName") == item["function"]
                and row.get("relation") == item["relation"] for row in state_rows
            )
        selected_receipts = {
            path.name.removesuffix(".receipt.json"): {
                "receipt": path.name, "receipt_sha256": demand.sha256(path),
                "result_sha256": json.loads(path.read_text())["result_sha256"],
            }
            for path in sorted((args.output / "relations").glob("*.receipt.json"))
        }
        mechanism_summary = {
            "schema": "mock-spec.python-demand-mechanism-summary/v1",
            "status": "authenticated" if {120, 126} <= pytest_observations
                      and all(state_checks.values()) else "blocked",
            "root_selector_sha256": root_history[-1]["module_sha256"],
            "query_receipts": selected_receipts,
            "pytest_capture_model": {
                "status": "passed" if {120, 126} <= pytest_observations else "blocked",
                "required_observation_lines": [120, 126],
                "observed_lines": sorted(pytest_observations),
            },
            "model_evidence": {
                "status": "passed" if all(state_checks.values()) else "blocked",
                "checks": state_checks,
            },
            "state_boundary_evidence": {
                "status": "passed" if all(state_checks.values()) else "blocked",
                "checks": state_checks,
                "semantic_scope": "registered state observation roots only",
            },
            "relation_families": {
                "positive": sorted({row.get("relation", "") for row in state_rows
                                    if row.get("caseKind") in {"positive", "kill", "normal", "exception"}}),
                "negative": sorted({row.get("relation", "") for row in state_rows
                                    if row.get("caseKind") == "negative"}),
                "exception": sorted({row.get("relation", "") for row in state_rows
                                     if row.get("exitKind") == "exception"}),
            },
        }
        if mechanism_summary["status"] != "authenticated":
            raise RuntimeError(f"current selected query mechanism gate failed: {state_checks}")
        summary_path = stage / "demand-mechanism-summary.json"
        summary_path.write_text(json.dumps(mechanism_summary, indent=2) + "\n", encoding="utf-8")
        shutil.copy2(summary_path, args.output / summary_path.name)

        mapping_cost: dict[str, dict] = {}
        for name, script, expectation, destination in (
            ("observation", "observation_mapping.py", "observation_expectations.json", "observation-study.json"),
            ("state", "state_observation_mapping.py", "state_observation_expectations.json", "state-observation-study.json"),
        ):
            mapping_cost[name] = run_timed([
                str(PYTHON), str(HERE / script), "--results", str(stage),
                "--expectations", str(HERE / expectation),
                "--output", str(args.output / destination),
            ], args.output / f"{name}-mapping.time",
                stdout=args.output / f"{name}-mapping.stdout",
                stderr=args.output / f"{name}-mapping.stderr")
            if mapping_cost[name]["exit_code"] != 0:
                raise RuntimeError(f"{name} mapping failed")
        equivalence = {
            "flask": canonical_study(args.output / "observation-study.json")
            == canonical_study(args.baseline / "observation-study.json"),
            "state": canonical_study(args.output / "state-observation-study.json")
            == canonical_study(args.baseline / "state-observation-study.json"),
        }
        recursion_rows = read_csv(args.output / "relations/probe-recursive-boundary.csv")
        registered_recursive = observations["recursive"]["observation"]
        recursive_members = {row.get("functionName") for row in recursion_rows
                             if row.get("relation") == "recursive_expansion_boundary"
                             and row.get("observationRoot") == registered_recursive["function"]
                             and row.get("observationLine") == str(registered_recursive["line"])}
        recursion_boundary = recursive_members == {"recursive_left", "recursive_right"}
        executed = [row for row in manifest["attempts"] if row["status"] == "executed"]
        query_seconds = sum(row["query"]["seconds"] for row in executed)
        decode_seconds = sum(row["decode"]["seconds"] for row in executed)
        peak_candidates = [
            ("database", item.get("peak_rss_bytes")) for item in database_cost.values()
        ] + [("query", row["query"].get("peak_rss_bytes")) for row in executed] \
          + [("decode", row["decode"].get("peak_rss_bytes")) for row in executed] \
          + [("mapping", item.get("peak_rss_bytes")) for item in mapping_cost.values()]
        measured_peaks = [(phase, value) for phase, value in peak_candidates if value is not None]
        peak_phase, peak_bytes = max(measured_peaks, key=lambda item: item[1]) \
            if measured_peaks else ("unavailable", None)
        result = {
            "schema": demand.SCHEMA,
            "status": "passed" if all(equivalence.values()) and recursion_boundary else "blocked",
            "semantic_equivalence": equivalence, "selected_query_count": len(manifest["attempts"]),
            "baseline_query_count": 18, "attempts": manifest["attempts"],
            "recursive_boundary": {
                "status": "preserved" if recursion_boundary else "missing",
                "semantics": "delegated to fixed CodeQL query; no custom method expansion",
            },
            "summary": {
                "role": "current-run selected-mechanism verification",
                "file": summary_path.name,
                "sha256": demand.sha256(args.output / summary_path.name),
                "build_cost_included": True,
                "reason": "derived only from current query/cache-authenticated relation outputs",
            },
            "cost": {"prepare_seconds": prep_seconds, "database": database_cost,
                     "query_seconds": query_seconds, "decode_seconds": decode_seconds,
                     "mapping": mapping_cost,
                     "mapping_seconds": sum(item["seconds"] for item in mapping_cost.values()),
                     "peak_rss": {"bytes": peak_bytes, "phase": peak_phase,
                                  "scope": "maximum per-process RSS; not simultaneous run RSS"},
                     "comparison_limitations": [
                         "shared host had parallel Java work and OS caches were not cleared",
                         "r12 ran at a different time, so absolute speed differences are not causal evidence",
                         "cold/warm cache status and same-run phase measurements remain directly observed",
                     ],
                     "total_seconds": time.monotonic() - started,
                     "warm_cache_excludes_prior_build": all(
                         row["status"] == "cache_hit" for row in manifest["attempts"]
                     )},
            "graph_scope": {
                "full_parse": True, "dependency_units": manifest["attempts"],
                "source_identities": source_ids,
                "root_expansion_history": root_history,
                "demand_roots": [root_history[-1].get("reached_product"),
                                 *root_history[-1]["state_observations"],
                                 *root_history[-1]["recursive_functions"]],
                "input_scale": {
                    kind: {
                        "files": len([path for path in root.rglob("*") if path.is_file()]),
                        "bytes": sum(path.stat().st_size for path in root.rglob("*") if path.is_file()),
                        "loc": sum(len(path.read_text(encoding="utf-8", errors="replace").splitlines())
                                   for path in root.rglob("*") if path.is_file()),
                    } for kind, root in source_roots.items()
                },
                "unexpanded": manifest["unexpanded_boundaries"],
                "relation_provenance": selected_receipts,
                "backend_graph_nodes": None,
                "reason": "CodeQL does not export graph size for these queries",
            },
        }
        (args.output / "result.json").write_text(
            json.dumps(result, indent=2) + "\n", encoding="utf-8"
        )
        return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
