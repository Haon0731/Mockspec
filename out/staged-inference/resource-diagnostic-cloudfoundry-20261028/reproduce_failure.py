"""Replay retained outputs locally; the optional edit is a diagnostic counterfactual."""
from __future__ import annotations

import asyncio
from copy import deepcopy
import json
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "experiments/staged-inference")]

from controller import StagedRunConfig, execute_staged_run  # noqa: E402
from mock_spec.adapters.java import load_task_with_repository  # noqa: E402
from scripted import FixedScriptModel  # noqa: E402


def main():
    preflight = json.loads((HERE / "preflight.json").read_text())
    outputs = []
    for path in sorted((HERE / "http").glob("*.json"), key=lambda p: int(p.name.split("-")[1])):
        response = json.loads(path.read_text())["body"]
        for item in response["output"]:
            if item.get("type") == "message":
                for part in item["content"]:
                    if part.get("type") == "output_text":
                        outputs.append(json.loads(part["text"]))
    assert len(outputs) == 8
    results = []
    for name, correction in (
        ("exact-response-replay", 0),
        ("receipt-link-counterfactual", 1),
        ("state-field-counterfactual", 2),
    ):
        script = deepcopy(outputs)
        changes = []
        if correction:
            updates = script[-1]["question_updates"]
            update = next(item for item in updates if item["question_id"] == "q1")
            assert update["attempted_sequences"] == [1]
            update["attempted_sequences"] = [1, 2, 6]
            changes.append({"field": "question_updates[q1].attempted_sequences",
                            "before": [1], "after": [1, 2, 6]})
        if correction == 2:
            for update in script[-1]["question_updates"]:
                if update["state"] == "nonblocking" and update["acquisition"] != "not_needed":
                    changes.append({"field": f"question_updates[{update['question_id']}].acquisition",
                                    "before": update["acquisition"], "after": "not_needed"})
                    update["acquisition"] = "not_needed"
        directory = HERE / name
        if not directory.exists():
            asyncio.run(execute_staged_run(
                output_dir=directory, model=FixedScriptModel(script),
                config=StagedRunConfig(
                    variant="C", no_run_resource_limits=True,
                    max_total_tokens=None, timeout_seconds=None, max_output_tokens=393216,
                ),
                task_loader=lambda: load_task_with_repository(
                    Path(preflight["snapshot"]), case_id=preflight["case_id"],
                ),
            ))
        record = json.loads((directory / "inference_record.json").read_text())
        manifest = json.loads((directory / "run_manifest.json").read_text())
        retained = []
        for response in record["model_responses"]:
            for item in response["output"]:
                if item.get("type") == "message":
                    for part in item["content"]:
                        if part.get("type") == "output_text":
                            retained.append(json.loads(part["text"]))
        assert retained == script, "Existing replay differs from the declared counterfactual"
        summary = {
            "name": name, "evidence_type": "fixed_response_replay_not_live_generation",
            "changes": changes,
            "error": record["error"],
            "error_stage": manifest["error_stage"],
            "model_calls": len(record["model_calls"]),
            "final_spec_exists": (directory / "dependency_spec.json").exists(),
        }
        if correction == 2:
            assert summary["error_stage"] == "CONSTRUCT", summary
        elif correction == 1:
            assert summary["error"] == {
                "type": "ValueError", "message": "nonblocking question must explain why acquisition is not needed",
            }, summary
        else:
            assert summary["error"] == {
                "type": "ValueError", "message": "question evidence must be associated with its observed query receipt",
            }, summary
            assert summary["error_stage"] == "QUERY"
        results.append(summary)
    with (HERE / "offline-diagnosis.json").open("x") as stream:
        json.dump(results, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
