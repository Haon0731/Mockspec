"""Run only requested Jib observation slices and cache their relation evidence."""
from __future__ import annotations

import argparse
import gc
import gzip
import hashlib
import json
from pathlib import Path
import resource
import shutil
import subprocess
import time
from typing import Any

import java_demand_analysis as demand


HERE = Path(__file__).resolve().parent
PYTHON = HERE.parents[2] / ".venv/bin/python"
PROBE_SOURCES = (
    HERE / "backend/src/main/java/org/mockspec/research/JibObservationProbe.java",
    HERE / "backend/src/main/java/org/mockspec/research/DemandCISlicer.java",
)
SUPPORT_FILES = (
    "pre-registration.json", "automatic-binding-evidence.json",
    "javap-JarFiles.txt", "javap-JibContainerBuilder.txt",
    "javap-ContainerBuildPlan.txt", "javap-ContainerBuildPlan-Builder.txt",
)


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def file_identity(paths: list[Path]) -> str:
    return demand.digest_json([
        {"path": str(path), "sha256": demand.sha256(path)} for path in paths
    ])


def observation_units(registration: dict) -> dict[str, demand.Unit]:
    units = {
        item["id"]: demand.Unit(
            item["id"],
            "com.google.cloud.tools.jib.cli.jar.ObservationSink."
            + item["criterion"] + "(Ljava/lang/Object;)V",
        )
        for item in registration["observations"]
    }
    units["all-observations"] = demand.Unit(
        "all-observations", None, tuple(item["id"] for item in registration["observations"])
    )
    return units


def java_base_command(receipt: dict) -> list[str]:
    command = list(receipt["analysis_command"])
    if command[:3] != ["/usr/bin/time", "-v", "-o"]:
        raise ValueError("baseline command does not have the registered time wrapper")
    command = command[4:]
    if len(command) < 4 or command[0] != "timeout" or command[-1] != "NONE":
        raise ValueError("only the registered NONE-control Jib command is supported")
    return command


def command_classpath(command: list[str]) -> list[Path]:
    index = command.index("-cp")
    return [Path(item) for item in command[index + 1].split(":")]


def shared_semantics(raw: dict) -> dict:
    return {key: value for key, value in raw.items()
            if key not in {"observations", "timings_millis", "demand_pdg"}}


def compile_probe(classpath: list[Path], output: Path) -> tuple[Path, dict]:
    classes = output / "probe-classes"
    classes.mkdir()
    command = [
        "javac", "-g", "-cp", ":".join(map(str, classpath)),
        "-d", str(classes), *map(str, PROBE_SOURCES),
    ]
    started = time.monotonic()
    completed = subprocess.run(command, capture_output=True, check=False)
    (output / "probe-compile.stdout").write_bytes(completed.stdout)
    (output / "probe-compile.stderr").write_bytes(completed.stderr)
    result = {"exit_code": completed.returncode, "seconds": time.monotonic() - started,
              "command": command}
    if completed.returncode != 0:
        raise RuntimeError("demand probe compilation failed")
    result["compiled_tree_sha256"] = demand.tree_identity(classes)
    return classes, result


def parse_peak_rss(path: Path) -> int | None:
    if not path.is_file():
        return None
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if "Maximum resident set size (kbytes):" in line:
            return int(line.rsplit(":", 1)[1].strip()) * 1024
    return None


def run_java(command: list[str], criteria: list[str], output: Path) -> tuple[dict, dict]:
    if not criteria:
        raise ValueError("cannot execute a Java demand run without criteria")
    command = list(command)
    main_index = command.index("org.mockspec.research.JibObservationProbe")
    command[main_index + 5] = ",".join(criteria)
    time_path = output / "java-analysis.time"
    stdout_path = output / "java-analysis.json"
    stderr_path = output / "java-analysis.stderr"
    started = time.monotonic()
    with stdout_path.open("wb") as stdout, stderr_path.open("wb") as stderr:
        completed = subprocess.run(
            ["/usr/bin/time", "-v", "-o", str(time_path), *command],
            stdout=stdout, stderr=stderr, check=False,
        )
    cost = {
        "status": "complete" if completed.returncode == 0 else "failed",
        "exit_code": completed.returncode,
        "seconds": time.monotonic() - started,
        "peak_rss_bytes": parse_peak_rss(time_path),
        "command": command,
        "requested_criteria": criteria,
    }
    if completed.returncode != 0:
        return {}, cost
    return json.loads(stdout_path.read_text(encoding="utf-8")), cost


def copy_support(baseline: Path, output: Path) -> None:
    for name in SUPPORT_FILES:
        source = baseline / name
        if not source.is_file():
            raise FileNotFoundError(f"baseline support file absent: {source}")
        shutil.copy2(source, output / name)


def compress_with_receipt(path: Path) -> dict:
    target = path.with_suffix(path.suffix + ".gz")
    with path.open("rb") as source, gzip.open(target, "wb", compresslevel=9) as destination:
        shutil.copyfileobj(source, destination)
    with gzip.open(target, "rb") as restored:
        restored_sha = hashlib.sha256()
        for chunk in iter(lambda: restored.read(1024 * 1024), b""):
            restored_sha.update(chunk)
    receipt = {
        "schema": "mockspec.analysis-compression/v1",
        "source": path.name, "source_bytes": path.stat().st_size,
        "source_sha256": demand.sha256(path),
        "compressed": target.name, "compressed_bytes": target.stat().st_size,
        "compressed_sha256": demand.sha256(target),
        "roundtrip_sha256": restored_sha.hexdigest(),
    }
    receipt["roundtrip_verified"] = receipt["source_sha256"] == receipt["roundtrip_sha256"]
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline", required=True, type=Path)
    parser.add_argument("--cache", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--roots", default="all-observations")
    parser.add_argument("--max-units", type=int, default=19)
    parser.add_argument("--max-depth", type=int, default=2)
    parser.add_argument("--max-method-pdgs", type=int, default=20000)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(f"output must not exist: {args.output}")
    args.output.mkdir(parents=True)
    args.cache.mkdir(parents=True, exist_ok=True)
    total_started = time.monotonic()

    receipt = json.loads((args.baseline / "receipt.json").read_text(encoding="utf-8"))
    registration = json.loads(
        (args.baseline / "pre-registration.json").read_text(encoding="utf-8")
    )
    baseline_summary_path = args.baseline / "summary.json"
    baseline_summary = json.loads(baseline_summary_path.read_text(encoding="utf-8"))
    units = observation_units(registration)
    roots = tuple(filter(None, args.roots.split(",")))
    planned = demand.plan(
        roots, units=units, max_units=args.max_units, max_depth=args.max_depth
    )
    selected = [units[name] for name in planned["selected_units"]]

    base_command = java_base_command(receipt)
    if args.max_method_pdgs <= 0:
        raise ValueError("max-method-pdgs must be positive")
    base_command.extend(["-1", str(args.max_method_pdgs)])
    classpath = command_classpath(base_command)
    baseline_probe_classes = classpath[0]
    main_index = base_command.index("org.mockspec.research.JibObservationProbe")
    application_jar = Path(base_command[main_index + 1])
    libraries = [Path(item["path"]) for item in registration["libraries"]]
    for item, path in zip(registration["libraries"], libraries, strict=True):
        if not path.is_file() or demand.sha256(path) != item["sha256"]:
            raise ValueError(f"registered library identity changed: {path}")
    if not application_jar.is_file() or not baseline_probe_classes.is_dir():
        raise FileNotFoundError("registered application or probe classes are absent")
    source_record = {
        "application_jar": {"path": str(application_jar),
                            "sha256": demand.sha256(application_jar)},
        "baseline_probe_classes": {
            "path": str(baseline_probe_classes),
            "tree_sha256": demand.tree_identity(baseline_probe_classes),
            "role": "semantic comparison baseline only",
        },
        "demand_probe_sources": [
            {"path": str(path), "sha256": demand.sha256(path)} for path in PROBE_SOURCES
        ],
        "libraries": registration["libraries"],
        "test_sha256": registration["test_sha256"],
        "visible_mut_sha256": registration["visible_mut_sha256"],
        "container_builders_sha256": registration["container_builders"]["sha256"],
    }
    source_identity = demand.digest_json(source_record)
    dependency_classpath = [path for path in classpath[1:] if path.is_file()]
    java_version = subprocess.run(
        ["java", "-version"], capture_output=True, text=True, check=False
    ).stderr.strip()
    baseline_raw_header = {
        "configuration": {
            "call_graph": "0-1-CFA",
            "reflection": "NONE",
            "heap_locations": "original_wala",
            "slice": (
                "CISlicer_NO_HEAP_base_SDG_plus_statement_ModRef_may_dependencies_"
                "plus_entry_SSA_opaque_return_may_edges/v1"
            ),
            "control_dependence": "NONE",
        },
        "registered_probe_sha256": registration["probe_sha256"],
        "entry_origin": registration["entry_origin"],
    }
    config_record = {
        "analysis_configuration": baseline_raw_header,
        "wala_classpath_identity": file_identity(dependency_classpath),
        "java_version": java_version,
        "entry_class": base_command[main_index + 3],
        "entry_selector": base_command[main_index + 4],
        "control_dependence": "NONE",
        "max_method_pdgs": args.max_method_pdgs,
        "runner_sha256": demand.sha256(Path(__file__)),
        "planner_sha256": demand.sha256(HERE / "java_demand_analysis.py"),
    }
    configuration_identity = demand.digest_json(config_record)
    context = "Everywhere"
    shared_key = demand.digest_json({
        "source_identity": source_identity,
        "configuration_identity": configuration_identity,
        "context": context,
        "summary_version": demand.SUMMARY_VERSION,
    })
    shared_path = args.cache / f"shared-{shared_key}.json"
    shared_cache: dict | None = None
    if shared_path.is_file():
        candidate = json.loads(shared_path.read_text(encoding="utf-8"))
        shared = candidate.get("shared_analysis")
        if (candidate.get("schema") == demand.SCHEMA
                and candidate.get("shared_key") == shared_key
                and isinstance(shared, dict)
                and candidate.get("shared_sha256") == demand.digest_json(shared)):
            shared_cache = candidate

    records: dict[str, dict] = {}
    attempts: list[dict] = []
    missing: list[demand.Unit] = []
    for unit in selected:
        key = demand.cache_key(
            unit=unit, source_identity=source_identity,
            configuration_identity=configuration_identity, context=context,
        )
        record = demand.valid_cache_record(args.cache / f"unit-{key}.json", key=key)
        if record is None or shared_cache is None or record.get("shared_key") != shared_key:
            missing.append(unit)
        else:
            records[unit.name] = record
            attempts.append({"unit": unit.name, "criterion": unit.criterion,
                             "status": "cache_hit", "cache_key": key})

    java_cost: dict[str, Any] = {
        "status": "not_run", "seconds": 0.0, "peak_rss_bytes": None,
        "reason": "all requested units passed identity and content validation",
    }
    fresh_raw: dict | None = None
    compile_cost: dict[str, Any] = {"status": "not_run", "seconds": 0.0,
                                    "reason": "warm identity-bound cache"}
    if missing:
        demand_classes, compile_cost = compile_probe(dependency_classpath, args.output)
        compile_cost["status"] = "complete"
        command_classpath_index = base_command.index("-cp") + 1
        base_command[command_classpath_index] = (
            str(demand_classes) + ":" + ":".join(map(str, dependency_classpath))
        )
        fresh_raw, java_cost = run_java(
            base_command, [unit.criterion for unit in missing if unit.criterion], args.output
        )
        if not fresh_raw:
            result = {
                "schema": demand.SCHEMA, "status": "partial", "plan": planned,
                "attempts": attempts + [
                    {"unit": unit.name, "status": "execution_failed"} for unit in missing
                ],
                "unexpanded_boundaries": planned["unexpanded_boundaries"] + [
                    {"unit": unit.name, "reason": "analysis_execution_failed"} for unit in missing
                ],
                "cost": {"java": java_cost, "total_seconds": time.monotonic() - total_started},
            }
            write_json(args.output / "result.json", result)
            return 1
        fresh_shared = shared_semantics(fresh_raw)
        if shared_cache is not None and fresh_shared != shared_cache["shared_analysis"]:
            raise RuntimeError("fresh global analysis facts differ from the identity-matched cache")
        if shared_cache is None:
            shared_cache = {
                "schema": demand.SCHEMA, "shared_key": shared_key,
                "source_identity": source_identity,
                "configuration_identity": configuration_identity,
                "context": context, "summary_version": demand.SUMMARY_VERSION,
                "shared_analysis": fresh_shared,
                "shared_sha256": demand.digest_json(fresh_shared),
            }
            write_json(shared_path, shared_cache)
        by_criterion = {item["criterion"]: item for item in fresh_raw["observations"]}
        expected = {unit.criterion for unit in missing}
        if set(by_criterion) != expected:
            raise RuntimeError("Java probe returned a different criterion set than demanded")
        for unit in missing:
            assert unit.criterion is not None
            observation = by_criterion[unit.criterion]
            key = demand.cache_key(
                unit=unit, source_identity=source_identity,
                configuration_identity=configuration_identity, context=context,
            )
            record = {
                "schema": demand.SCHEMA, "cache_key": key, "shared_key": shared_key,
                "unit": unit.name, "criterion": unit.criterion,
                "source_identity": source_identity,
                "configuration_identity": configuration_identity,
                "context": context, "summary_version": demand.SUMMARY_VERSION,
                "observation": observation,
                "observation_sha256": demand.digest_json(observation),
            }
            records[unit.name] = record
            if observation.get("analysis_status") == "budget_exhausted":
                attempts.append({
                    "unit": unit.name, "criterion": unit.criterion,
                    "status": "method_budget_exhausted", "cache_key": key,
                    "frontier": observation["method_pdg_frontier"],
                })
            else:
                write_json(args.cache / f"unit-{key}.json", record)
                attempts.append({"unit": unit.name, "criterion": unit.criterion,
                                 "status": "executed", "cache_key": key})

    assert shared_cache is not None or not selected
    selected_observations = [records[unit.name]["observation"] for unit in selected]
    method_boundaries = [
        {"unit": item["unit"], "reason": "method_pdg_budget",
         "frontier": item["frontier"]}
        for item in attempts if item["status"] == "method_budget_exhausted"
    ]
    baseline_raw = json.loads((args.baseline / "analysis.json").read_text(encoding="utf-8"))
    baseline_by_criterion = {
        item["criterion"]: item for item in baseline_raw["observations"]
    }
    observation_checks: list[dict[str, Any]] = []
    for unit in selected:
        assert unit.criterion is not None
        if records[unit.name]["observation"].get("analysis_status") == "budget_exhausted":
            observation_checks.append({"unit": unit.name, "equivalent": None,
                                       "reason": "method_pdg_budget"})
            continue
        actual = demand.canonical_observation(records[unit.name]["observation"])
        expected_observation = demand.canonical_observation(baseline_by_criterion[unit.criterion])
        observation_checks.append({
            "unit": unit.name,
            "equivalent": actual == expected_observation,
            "actual_sha256": demand.digest_json(actual),
            "baseline_sha256": demand.digest_json(expected_observation),
        })
    shared_equivalent = (
        shared_cache is not None
        and shared_cache["shared_analysis"] == shared_semantics(baseline_raw)
    )
    del baseline_raw
    gc.collect()

    raw = dict(shared_cache["shared_analysis"]) if shared_cache else {}
    if fresh_raw is not None and "demand_pdg" in fresh_raw:
        raw["demand_pdg"] = fresh_raw["demand_pdg"]
    raw["observations"] = selected_observations
    raw["timings_millis"] = (
        fresh_raw["timings_millis"] if fresh_raw is not None else {
            "scope_hierarchy_entry": 0, "call_graph_pointer": 0, "ci_graph": 0,
            "method_ssa_facts": 0, "total": 0,
        }
    )
    write_json(args.output / "analysis.json", raw)
    copy_support(args.baseline, args.output)
    time_source = args.output / "java-analysis.time"
    analysis_time = args.output / "analysis.time"
    if time_source.is_file():
        shutil.copy2(time_source, analysis_time)
    else:
        analysis_time.write_text("Maximum resident set size (kbytes): 0\n", encoding="utf-8")

    all_units = set(units) - {"all-observations"}
    complete_observation_set = (
        set(planned["selected_units"]) == all_units and not method_boundaries
    )
    summary_equivalent: bool | None = None
    summary_representation_equal: bool | None = None
    summary_stdout = args.output / "summarize.stdout"
    summary_stderr = args.output / "summarize.stderr"
    if complete_observation_set and planned["complete"]:
        with summary_stdout.open("wb") as stdout, summary_stderr.open("wb") as stderr:
            completed = subprocess.run(
                [str(PYTHON), str(HERE / "summarize_jib_observations.py"), str(args.output)],
                stdout=stdout, stderr=stderr, check=False,
            )
        if completed.returncode != 0:
            raise RuntimeError("current semantic summarizer rejected the demand result")
        current_summary = json.loads((args.output / "summary.json").read_text(encoding="utf-8"))
        summary_representation_equal = (
            current_summary == baseline_summary
        )
        summary_equivalent = (
            demand.canonical_summary(current_summary) == demand.canonical_summary(baseline_summary)
        )

    equivalence = {
        "schema": demand.SCHEMA,
        "baseline": str(args.baseline),
        "baseline_summary_sha256": demand.sha256(baseline_summary_path),
        "shared_analysis_equivalent": shared_equivalent,
        "observation_checks": observation_checks,
        "all_completed_observations_equivalent": all(
            item["equivalent"] for item in observation_checks
            if item["equivalent"] is not None
        ),
        "full_semantic_summary_equivalent": summary_equivalent,
        "full_summary_representation_equal": summary_representation_equal,
        "scope": (
            "full value/control/state/unknown summary" if summary_equivalent is not None
            else "selected raw observation dependency witnesses plus shared method/control facts"
        ),
    }
    write_json(args.output / "equivalence.json", equivalence)
    compression = compress_with_receipt(args.output / "analysis.json")
    write_json(args.output / "analysis-compression.json", compression)

    total_seconds = time.monotonic() - total_started
    result = {
        "schema": demand.SCHEMA,
        "status": "complete" if planned["complete"] and not method_boundaries else "partial",
        "target_executed": False,
        "plan": planned,
        "attempts": attempts,
        "unexpanded_boundaries": [*planned["unexpanded_boundaries"], *method_boundaries],
        "cache": {
            "hits": sum(item["status"] == "cache_hit" for item in attempts),
            "executed": sum(item["status"] == "executed" for item in attempts),
            "shared_key": shared_key,
            "source_identity": source_identity,
            "configuration_identity": configuration_identity,
            "context": context,
            "summary_version": demand.SUMMARY_VERSION,
        },
        "identities": {"source": source_record, "configuration": config_record},
        "dependency_footprint": demand.method_footprint(selected_observations),
        "demand_pdg": {
            "current_java_run": fresh_raw.get("demand_pdg") if fresh_raw else None,
            "per_observation": [
                {"unit": unit.name, "telemetry": records[unit.name]["observation"].get("demand_pdg")}
                for unit in selected
            ],
            "warm_note": (
                None if fresh_raw else
                "PDG counts are the identity-bound cold evidence cached with each observation; no PDG was built in this warm run"
            ),
        },
        "equivalence": equivalence,
        "cost": {
            "baseline": baseline_summary["result_contract"]["cost"],
            "java": java_cost,
            "compile": compile_cost,
            "mapping_seconds": (
                total_seconds - java_cost.get("seconds", 0.0) - compile_cost.get("seconds", 0.0)
            ),
            "total_seconds": total_seconds,
            "host_peak_rss_bytes": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024,
            "cache_state": "cold_or_partial" if missing else "warm",
            "full_call_graph_and_pointer_analysis_rebuilt": bool(missing),
            "reused_input_preparation": {
                "application_jar": str(application_jar),
                "baseline_receipt": str(args.baseline / "receipt.json"),
                "excluded_from_current_total": True,
            },
        },
        "limitations": [
            "The 0-1-CFA call graph and pointer analysis are still built in full on every cold Java invocation.",
            "Dependency slices are materialized only for selected observations; method facts are a bounded shared summary.",
            "Warm-cache cost excludes the earlier cold Java analysis and validates every identity-bound record.",
            "Full semantic equivalence is reported only when all 19 registered observations are selected.",
        ],
    }
    write_json(args.output / "result.json", result)
    write_json(args.output / "pre-registration.json.demand", {
        "schema": demand.SCHEMA, "baseline": str(args.baseline),
        "baseline_summary_sha256": demand.sha256(baseline_summary_path),
        "plan": planned, "budget": {"max_units": args.max_units, "max_depth": args.max_depth,
                                      "max_method_pdgs": args.max_method_pdgs},
        "source_identity": source_identity,
        "configuration_identity": configuration_identity,
        "context": context, "summary_version": demand.SUMMARY_VERSION,
    })
    completed_checks = [
        item for item in observation_checks if item["equivalent"] is not None
    ]
    semantic_pass = (
        summary_equivalent is True
        if complete_observation_set and planned["complete"]
        else (
            bool(completed_checks)
            and all(item["equivalent"] for item in completed_checks)
            and bool(method_boundaries or planned["unexpanded_boundaries"])
        )
    )
    return 0 if semantic_pass else 2


if __name__ == "__main__":
    raise SystemExit(main())
