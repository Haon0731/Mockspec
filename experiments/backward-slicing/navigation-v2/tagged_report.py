"""Build anonymous slot packets and join the seven-case execution and review receipts."""

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import secrets

HERE = Path(__file__).resolve().parent


def read(path):
    return json.loads(path.read_bytes())


def write_new(path, data):
    with path.open("x", encoding="utf-8") as output:
        json.dump(data, output, indent=2, ensure_ascii=False, allow_nan=False)
        output.write("\n")


def verified_subjects(directory):
    run = directory / "run"
    freeze = read(run / "freeze.json")
    for name, digest in freeze["sha256"].items():
        if hashlib.sha256((run / name).read_bytes()).hexdigest() != digest:
            raise ValueError(f"frozen artifact changed: {name}")
    subjects = {
        str(Path(name).parent): read(run / name)
        for name in freeze["sha256"]
        if name.endswith("/trace.json")
    }
    if (
        len(subjects) != 42
        or Counter(t["status"] for t in subjects.values()) != freeze["statuses"]
    ):
        raise ValueError("incomplete or inconsistent frozen generation")
    return subjects


def blind(directory):
    subjects = verified_subjects(directory)
    public_path = directory / "anonymous.json"
    private_path = directory / "private-map.json"
    if public_path.exists() or private_path.exists():
        raise FileExistsError("blinded packets already exist")
    mapping = {secrets.token_hex(12): key for key in subjects}
    rows = []
    for identity, key in sorted(mapping.items()):
        delivered = subjects[key]["status"] == "generated"
        slots = (
            read(directory / "run" / key / "candidate.json")["slots"]
            if delivered
            else None
        )
        rows.append(dict(blind_id=identity, case=key.split("/")[0], slots=slots))
    write_new(private_path, mapping)
    write_new(public_path, dict(candidates=rows))


def summarize(directory):
    subjects = verified_subjects(directory)
    mapping = read(directory / "private-map.json")
    review = read(directory / "review.json")["reviews"]
    evaluation = read(directory / "evaluation.json")
    if (
        evaluation["generation_freeze_sha256"]
        != hashlib.sha256((directory / "run/freeze.json").read_bytes()).hexdigest()
    ):
        raise ValueError("evaluation used another generation freeze")
    execution = {
        f"{r['case']}/{r['repeat']}/{r['arm']}": r for r in evaluation["subjects"]
    }
    semantic = {mapping[r["blind_id"]]: r for r in review}
    if not (set(subjects) == set(execution) == set(semantic)) or len(review) != len(
        subjects
    ):
        raise ValueError("unpaired results")
    rows = []
    for key, t in sorted(subjects.items()):
        e, r = execution[key], semantic[key]
        usage = [turn.get("usage") for turn in t["turns"]]
        rows.append(
            dict(
                subject=key,
                delivered=t["status"] == "generated",
                original_passed=e["original_passed"],
                passed_rows=e["original_passed_rows"],
                total_rows=e["original_total_rows"],
                mutation_detected=e["mutation_detected"],
                mutation_valid=e.get("mutation_valid"),
                blind_review=r,
                input_bytes=t["input_bytes"],
                source_calls=t["source_calls"],
                seconds=t["elapsed_seconds"],
                **{
                    metric: sum(item[metric] for item in usage)
                    if usage and all(item and metric in item for item in usage)
                    else None
                    for metric in ("input_tokens", "output_tokens")
                },
            )
        )
    return dict(
        generation_freeze_sha256=evaluation["generation_freeze_sha256"], subjects=rows
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["blind", "summarize"])
    parser.add_argument("--directory", type=Path, default=HERE / "tagged")
    args = parser.parse_args()
    if args.command == "blind":
        blind(args.directory)
    else:
        print(
            json.dumps(
                summarize(args.directory), indent=2, ensure_ascii=False, allow_nan=False
            )
        )
