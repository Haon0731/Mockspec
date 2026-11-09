"""Try the frozen goal frontend on the admitted Jib projection, without fixes."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import time

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "goal-pilot"))
from prepare import CASES  # noqa: E402
from candidate_bindings import _run_ir  # noqa: E402
from compose import Composer  # noqa: E402
from mock_spec.adapters.java import load_task_with_repository  # noqa: E402
from mock_spec.validation import SourceStore  # noqa: E402


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def run(old_root):
    output = HERE / "transfer"
    output.mkdir(exist_ok=False)
    _, registry, dataset, snapshot, case_id = CASES["jib"]
    task, repository = load_task_with_repository(
        old_root / registry / "formal-snapshots" / dataset / snapshot, case_id=case_id)
    previous_task = json.loads((ROOT / "results/jib/task.json").read_text())
    sources = json.loads((ROOT / "results/jib/sources.json").read_text())["sources"]
    try:
        if task.to_dict() != previous_task:
            raise ValueError("reviewed task changed")
        store = SourceStore(task, repository=repository)
        for source in sources:
            fresh = store.read(source["source_id"], source["start_line"],
                               source["start_line"] + len(source["text"].splitlines()) - 1)
            if fresh.text.rstrip("\n") != source["text"].rstrip("\n"):
                raise ValueError("reviewed source changed")
    finally:
        repository.close()
    start = time.perf_counter()
    ir = _run_ir(sources)
    frontend_seconds = time.perf_counter() - start
    write(output / "ir.json", ir)
    start = time.perf_counter()
    result = Composer(ir).compose()
    compose_seconds = time.perf_counter() - start
    write(output / "goals.json", result)
    metrics = {
        "case_id": case_id, "snapshot_id": snapshot,
        "admission": "fresh task and all retained source projections match",
        "source_count": len(sources), "types": len(ir["types"]),
        "unsupported_nodes": len(ir["unsupported"]), "goals": len(result["goals"]),
        "paths": sum(len(goal["paths"]) for goal in result["goals"]),
        "compile_and_frontend_seconds": frontend_seconds, "compose_seconds": compose_seconds,
        "source_identities": {s["source_id"]: hashlib.sha256(s["text"].encode()).hexdigest() for s in sources},
        "frozen_code": {name: hashlib.sha256((ROOT / "goal-pilot" / name).read_bytes()).hexdigest()
                        for name in ("GoalIR.java", "compose.py", "candidate_bindings.py")},
        "boundary_policy": "processor methods are opaque; placeholder throw bodies are not behavioral evidence",
        "generation_consumer": False,
        "scope": "previously inspected admitted task, not a held-out sample; no new target adapter",
    }
    write(output / "metrics.json", metrics)
    print(json.dumps(metrics, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--old-root", type=Path, required=True)
    run(parser.parse_args().old_root)
