"""Bounded original/mutant executions with preserved receipts and deduplication."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "path-pilot"))
import java_evaluate  # noqa: E402
import interaction_evaluate  # noqa: E402

LIMIT = 24


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def now():
    return datetime.now(timezone.utc).isoformat()


def verify_frozen():
    for relative, identity in read(HERE / "inputs/manifest.json").items():
        if hashlib.sha256((HERE.parent / relative).read_bytes()).hexdigest() != identity["sha256"]:
            raise ValueError(f"frozen input changed: {relative}")


def execute(ledger, name, output, function):
    if len(ledger["attempts"]) >= LIMIT:
        raise RuntimeError("round execution budget exhausted")
    if output.exists():
        raise FileExistsError(f"refusing to repeat existing evaluation: {output}")
    attempt = {"id": name, "started_utc": now(), "output": str(output.relative_to(HERE)),
               "status": "started"}
    ledger["attempts"].append(attempt)
    write(HERE / "execution-ledger.json", ledger)
    try:
        result = function()
        attempt["status"] = result["status"]
        attempt["candidate_sha256"] = result["candidate_sha256"]
        return result
    except Exception as error:
        attempt["status"] = "host_error"
        attempt["error"] = f"{type(error).__name__}: {error}"
        raise
    finally:
        attempt["finished_utc"] = now()
        write(HERE / "execution-ledger.json", ledger)
        print(json.dumps(attempt), flush=True)


def run(stage, names, old_root):
    verify_frozen()
    schedule = read(HERE / "inputs/schedule.json")["launch_order"]
    if len(names) != len(set(names)) or any(name not in schedule for name in names):
        raise ValueError("candidates must be unique scheduled names")
    ledger_path = HERE / "execution-ledger.json"
    ledger = read(ledger_path) if ledger_path.exists() else {"limit": LIMIT, "attempts": []}
    mapping_path = HERE / "interaction-map.json"
    mapping = read(mapping_path) if mapping_path.exists() else {}
    for name in names:
        candidate = HERE / "candidates" / f"{name}.json"
        if not candidate.is_file():
            raise FileNotFoundError(candidate)
        original = HERE / "junit-original" / name
        if stage == "original":
            execute(ledger, f"original/{name}", original,
                    lambda: java_evaluate.evaluate(candidate, original, old_root))
            continue
        baseline_path = original / "result.json"
        baseline = read(baseline_path)
        if baseline["status"] != "passed":
            mapping[name] = {"eligible": False, "reason": "original did not pass"}
            write(mapping_path, mapping)
            continue
        interaction_evaluate.validate_baseline(candidate, baseline_path)
        source_hash = baseline["candidate_source_sha256"]
        matching = next((entry for entry in mapping.values()
                         if entry.get("eligible") and entry["injected_source_sha256"] == source_hash
                         and len(entry["mutants"]) == len(interaction_evaluate.MUTATIONS)), None)
        if matching:
            mapping[name] = {"eligible": True, "injected_source_sha256": source_hash,
                             "deduplicated": True, "mutants": matching["mutants"],
                             "candidate_sha256": baseline["candidate_sha256"]}
            write(mapping_path, mapping)
            continue
        entry = {"eligible": True, "injected_source_sha256": source_hash, "deduplicated": False,
                 "candidate_sha256": baseline["candidate_sha256"], "mutants": {}}
        mapping[name] = entry
        for mutant in interaction_evaluate.MUTATIONS:
            output = HERE / "junit-mutants" / name / mutant
            execute(ledger, f"{mutant}/{name}", output,
                    lambda: interaction_evaluate.evaluate(candidate, output, old_root, mutant, baseline_path))
            entry["mutants"][mutant] = str((output / "result.json").relative_to(HERE))
            write(mapping_path, mapping)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--stage", choices=("original", "mutants"), required=True)
    parser.add_argument("--candidates", nargs="+", required=True)
    parser.add_argument("--old-root", type=Path, required=True)
    args = parser.parse_args()
    run(args.stage, args.candidates, args.old_root)
