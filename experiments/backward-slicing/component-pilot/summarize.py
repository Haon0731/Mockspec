"""Audit identities and derive the report counts from retained execution receipts."""
from collections import Counter
import hashlib
import json
from pathlib import Path
import statistics

HERE = Path(__file__).resolve().parent


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def main():
    for relative, identity in read(HERE / "inputs/manifest.json").items():
        assert hashlib.sha256((HERE.parent / relative).read_bytes()).hexdigest() == identity["sha256"], relative
    schedule = read(HERE / "inputs/schedule.json")["launch_order"]
    provenance = read(HERE / "generation-provenance.json")
    assert [row["name"] for row in provenance["attempts"]] == schedule
    original = {}
    statuses = {arm: Counter() for arm in ("C", "R", "D", "RD")}
    identities = {}
    outside = set()
    for row in provenance["attempts"]:
        name = row["name"]
        candidate = HERE / row["output"]
        digest = hashlib.sha256(candidate.read_bytes()).hexdigest()
        result = read(HERE / "junit-original" / name / "result.json")
        assert result["candidate_sha256"] == digest == row["sha256"]
        original[name] = {key: result[key] for key in ("status", "candidate_sha256", "candidate_source_sha256")}
        original[name]["runtime_seconds"] = result["compile"]["elapsed_seconds"] + result.get("test", {}).get("elapsed_seconds", 0)
        identities[name] = result["candidate_source_sha256"]
        outside.add(result["outside_regions_sha256"])
        statuses[name.rstrip("12")][result["status"]] += 1
    assert len(outside) == 1, "ordinary context changed across candidates"
    common = read(HERE.parent / "path-pilot/inputs/common.json")
    expected_source = next(s["text"] for s in common["visible_sources"]["cloudfoundry"]
                           if s["logical_path"].endswith("/DefaultApplicationFactory.java"))
    expected_source_hash = hashlib.sha256(expected_source.encode()).hexdigest()
    mapping = read(HERE / "interaction-map.json")
    assert set(mapping) == set(schedule)
    mutant_receipts = {}
    eligible = []
    for name, entry in mapping.items():
        assert entry["eligible"] == (original[name]["status"] == "passed")
        if not entry["eligible"]:
            continue
        eligible.append(name)
        assert entry["injected_source_sha256"] == identities[name]
        for mutant, relative in entry["mutants"].items():
            receipt = read(HERE / relative)
            mutation = receipt["interaction_mutation"]
            assert receipt["candidate_source_sha256"] == identities[name]
            assert mutation["original_source_sha256"] == expected_source_hash
            assert mutation["mutant"] == mutant and mutation["match_count"] == 1
            mutant_receipts[relative] = {
                "mutant": mutant, "status": receipt["status"],
                "classification": mutation["failure_classification"],
                "runtime_seconds": receipt["compile"]["elapsed_seconds"] + receipt.get("test", {}).get("elapsed_seconds", 0),
            }
    ledger = read(HERE / "execution-ledger.json")
    assert len(ledger["attempts"]) == len(original) + len(mutant_receipts) <= ledger["limit"]
    assert all(row["status"] != "started" for row in ledger["attempts"])
    transfer = read(HERE / "transfer/goals.json")
    paths = [path for goal in transfer["goals"] for path in goal["paths"]]
    times = [row["runtime_seconds"] for row in original.values()]
    result = {
        "frozen_inputs_unchanged": True,
        "original": original,
        "conditions": {arm: {"attempts": sum(counts.values()), "statuses": dict(counts),
                            "packet_bytes": (HERE / "inputs" / f"{arm}.json").stat().st_size}
                       for arm, counts in statuses.items()},
        "common_source_bytes": (HERE.parent / "path-pilot/inputs/common.json").stat().st_size,
        "interaction": {"eligible_generations": eligible,
                        "unique_passing_injected_programs": len({identities[name] for name in eligible}),
                        "actual_mutant_runs": len(mutant_receipts), "receipts": mutant_receipts},
        "transfer": {"goals": len(transfer["goals"]), "paths": len(paths),
                     "outcomes": dict(Counter(path["outcome"] for path in paths)),
                     "events": sum(len(path["events"]) for path in paths),
                     "evidence_source_ids": sorted({loc["source_id"] for path in paths for loc in path["evidence"]}),
                     "unknown_kinds": sorted({item["kind"] for path in paths for item in path["unknowns"]}),
                     "processor_in_goal_evidence": any(loc["source_id"] == "processor"
                                                       for path in paths for loc in path["evidence"])},
        "execution_attempts": len(ledger["attempts"]),
        "execution_limit": ledger["limit"],
        "original_runtime_seconds": {"total": sum(times), "median": statistics.median(times),
                                      "min": min(times), "max": max(times)},
        "all_runtime_seconds": sum(times) + sum(row["runtime_seconds"] for row in mutant_receipts.values()),
        "interpretation": "one development case, two samples per arm, unequal evidence size; no population or causal component effect estimate",
    }
    (HERE / "summary.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: result[key] for key in ("conditions", "interaction", "transfer", "execution_attempts", "original_runtime_seconds")}, indent=2))


if __name__ == "__main__":
    main()
