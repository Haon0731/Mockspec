"""Join frozen receipts and blinded reviews for within-round factorial comparisons."""

import hashlib
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
CONTRASTS = {
    "direct_compact": ("OC", "DC"),
    "direct_restored": ("OR", "DR"),
    "details_direct": ("DC", "DR"),
    "layout_compact": ("OC", "PC"),
    "layout_restored": ("OR", "PR"),
    "details_original": ("OC", "OR"),
    "details_path": ("PC", "PR"),
}


def read(path):
    return json.loads(path.read_text())


def read_batch(execution_path, review_dir, run_dir, count):
    reviews = read(review_dir / "review.json")["reviews"]
    profiles = {
        (str(r["repeat"]), r["arm"]): r for r in read(execution_path)["subjects"]
    }
    review_index = {r["blind_id"]: r for r in reviews}
    mapping = read(review_dir / "private-map.json")
    if set(mapping) != set(review_index) or len(profiles) != count:
        raise ValueError("incomplete reviews or executions")
    freeze = read(run_dir / "freeze.json")
    if freeze["attempts"] != count or any(
        hashlib.sha256((run_dir / file).read_bytes()).hexdigest() != expected
        for file, expected in freeze["sha256"].items()
    ):
        raise ValueError("frozen receipts changed")
    anonymous = {r["blind_id"]: r["slots"] for r in read(review_dir / "anonymous.json")}
    if set(anonymous) != set(mapping) or set(mapping.values()) != {
        f"{r}/{a}" for r, a in profiles
    }:
        raise ValueError("anonymous identifiers or assignments differ")
    records = {}
    for blind_id, path in mapping.items():
        r, arm = path.split("/")
        trace = read(run_dir / path / "trace.json")
        slots = (
            read(run_dir / path / "candidate.json")["slots"]
            if trace["status"] == "generated"
            else None
        )
        if slots != anonymous[blind_id]:
            raise ValueError("reviewed slots differ from the frozen candidate")
        execution = profiles[r, arm]
        review = review_index[blind_id]
        usage = [turn["usage"] for turn in trace["turns"] if turn["usage"]]
        records[path] = {
            "blind_id": blind_id,
            "delivered": int(trace["status"] == "generated"),
            "original_pass": int(execution["original_passed"])
            if execution["candidate_available"]
            else None,
            "mutants_detected": execution["mutation_detected"],
            "mutation_score": execution["mutation_score"],
            "f_stub": review.get("f_stub_matched"),
            "extra_constraints": review.get("extra_constraints"),
            "source_calls": trace["source_calls"],
            "prompt_bytes": trace["input_bytes"],
            "first_input_tokens": usage[0]["input_tokens"] if usage else None,
            "input_tokens": sum(u["input_tokens"] for u in usage) if usage else None,
            "output_tokens": sum(u["output_tokens"] for u in usage) if usage else None,
            "seconds": trace["elapsed_seconds"],
        }
    return records


def summarize():
    records = read_batch(HERE / "evaluation/execution.json", HERE, HERE / "run", 12)
    records.update(
        read_batch(
            HERE / "direct-evaluation/execution.json",
            HERE / "supplementary",
            HERE / "direct-run",
            6,
        )
    )
    deltas = {}
    for name, (base, variant) in CONTRASTS.items():
        rows = []
        for i in range(1, 4):
            left, right = [records[f"{i}/{arm}"] for arm in (base, variant)]
            rows.append(
                {
                    k: round(right[k] - v, 3)
                    if v is not None and right[k] is not None
                    else None
                    for k, v in left.items()
                    if k != "blind_id"
                }
            )
        deltas[name] = {"base": base, "variant": variant, "variant_minus_base": rows}
    return {"attempts": records, "contrasts": deltas}


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    result = summarize()
    with args.output.open("x", encoding="utf-8") as handle:
        json.dump(result, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
