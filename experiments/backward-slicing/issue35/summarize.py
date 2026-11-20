"""Join blinded static reviews to the frozen execution and cost receipts."""
import argparse
import json
from pathlib import Path

from report import build_summary

HERE = Path(__file__).resolve().parent


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def execution(all_passed, passed, total):
    return {"status": "all_passed" if all_passed else "partial" if passed else "failed",
            "passed_rows": passed, "total_rows": total}


def pair_metrics(study):
    counts = {case: len(rows) for case, rows in read(study / "design-freeze.json")["cases"].items()}
    metrics = {}
    for row in read(study / "java_results/paired.json")["subjects"]:
        case, subject = row["case"], row["subject_id"]
        if subject == "developer-reference":
            continue
        _, repeat, arm = subject.split("-")
        metrics[(case, repeat, arm)] = {
            "execution": execution(row["main_score_eligible"], row["original_rows_passed"], counts[case]),
            "mutations": {"detected": row["mutation_detected"] if row["main_score_eligible"] else None, "valid": row["valid_mutation_denominator"]},
            "qualified": row["mutation_score"] is not None,
        }
    for row in read(study / "python_results/paired.json")["results"]:
        if row["variant"] == "reference":
            continue
        case, repeat, arm = row["variant"].split("/")
        metrics[(case, repeat, arm.lower())] = {
            "execution": execution(row["original_full_pass"], (row.get("original") or {}).get("passed", 0),
                                   counts[case]),
            "mutations": {"detected": row["raw_detected"], "valid": row["legal_denominator"]},
            "qualified": row["mutation_score"] is not None,
        }
    return metrics


def summarize(study):
    public = read(study / "anonymous.json")
    mapping = read(study / "private-map.json")
    reviews = read(study / "blind-semantic-review.json")["reviews"]
    static = {r["blind_id"]: r for r in reviews}
    if len(static) != 54 or set(static) != {c["blind_id"] for c in public["candidates"]}:
        raise ValueError("blind semantic review does not match the frozen cohort")
    subjects = {r["blind_id"]: r for r in mapping["attempts"]}
    metrics = pair_metrics(study)
    for candidate in public["candidates"]:
        ident = candidate["blind_id"]
        subject = subjects[ident]
        rating = static[ident]
        if rating["case"] != subject["case"] or candidate["case"] != subject["case"]:
            raise ValueError("review and execution case differ")
        if candidate["slots"] is None:
            trace = read(study / "run" / subject["trace"])
            candidate["review"] = {"status": "missing", "missing_reason": trace["reason"] or trace["error_type"]}
            continue
        key = (subject["case"], subject["repeat"], subject["arm"])
        candidate["review"] = {"status": "reviewed", **metrics[key],
                               **{k: rating[k] for k in (
                                   "f_stub", "unsupported_assertions", "extra_constraints", "notes")}}
    return build_summary(study / "run", mapping, public)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--study", type=Path, default=HERE)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    summary = summarize(args.study)
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(summary, stream, indent=2, ensure_ascii=False)
        stream.write("\n")
