"""Freeze a four-arm packet ablation without editing prior evidence."""
import hashlib
import json
from pathlib import Path
import random

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main():
    output = HERE / "inputs"
    output.mkdir(exist_ok=False)
    frozen = json.loads((ROOT / "goal-pilot/inputs/manifest.json").read_text())
    for relative, identity in frozen.items():
        if hashlib.sha256((ROOT / relative).read_bytes()).hexdigest() != identity["sha256"]:
            raise ValueError(f"previous frozen input changed: {relative}")
    packet = json.loads((ROOT / "goal-pilot/inputs/E.json").read_text())
    extra = packet["extra_evidence"]
    arms = {
        "C": {"candidate": packet["candidate"]},
        "R": {"candidate": packet["candidate"], "extra_evidence": {
            key: value for key, value in extra.items() if key != "check_of_supplied_candidate"
        }},
        "D": {"candidate": packet["candidate"], "extra_evidence": {
            key: extra[key] for key in ("check_of_supplied_candidate", "library_summary_scope", "checker_scope")
        }},
        "RD": packet,
    }
    for name, value in arms.items():
        write(output / f"{name}.json", value)
    (HERE / "revision-prompt.txt").write_bytes((ROOT / "goal-pilot/revision-prompt.txt").read_bytes())
    order = [f"{arm}{rep}" for rep in (1, 2) for arm in arms]
    random.Random(20261104).shuffle(order)
    write(output / "schedule.json", {"host_shuffle_seed": 20261104, "launch_order": order,
                                      "model_sampling_seed": "unavailable"})
    paths = [ROOT / name for name in frozen]
    paths += [HERE / "protocol.md", HERE / "prepare_inputs.py", HERE / "revision-prompt.txt",
              HERE / "interaction_evaluate.py", ROOT / "path-pilot/java_evaluate.py"]
    paths += sorted(output.glob("*.json"))
    write(output / "manifest.json", {str(path.relative_to(ROOT)): {
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "bytes": path.stat().st_size
    } for path in paths})
    print(json.dumps({"order": order, "packet_bytes": {
        arm: (output / f"{arm}.json").stat().st_size for arm in arms
    }}))


if __name__ == "__main__":
    main()
