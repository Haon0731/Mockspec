"""Join frozen execution and blind semantic measurements without imputing failures."""

import json
from pathlib import Path


def read(path):
    return json.loads(path.read_bytes())


def summarize(directory):
    mapping = read(directory / "private-map.json")
    reviews = read(directory / "review.json")["reviews"]
    evaluation = read(directory / "evaluation.json")["subjects"]
    semantic = {mapping[r["blind_id"]]: r for r in reviews}
    executed = {f"{r['case']}/{r['repeat']}/{r['arm']}": r for r in evaluation}
    if len(reviews) != 18 or len(evaluation) != 18 or set(semantic) != set(executed):
        raise ValueError("incomplete or unpaired sample")
    rows = []
    for key in sorted(executed):
        r, e = semantic[key], executed[key]
        if r["case"] != e["case"] or e["candidate_available"] != (
            r["status"] == "reviewed"
        ):
            raise ValueError(f"inconsistent subject {key}")
        rows.append(
            {
                "subject": key,
                "extra_getter_verifies": r["extra_getter_verifies"],
                "extra_constraints": r["extra_constraints"],
                "reference_matched": r["reference_interactions_matched"],
                "supported_matched": r["supported_interactions_matched"],
                "original_passed_rows": e["original_passed_rows"],
                "original_total_rows": e["original_total_rows"],
                "mutation_detected": e["mutation_detected"],
                "mutation_valid": e["mutation_valid"],
            }
        )
    return rows


if __name__ == "__main__":
    print(json.dumps(summarize(Path(__file__).resolve().parent), indent=2))
