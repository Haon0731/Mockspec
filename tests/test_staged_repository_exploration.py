"""Repository-backed staged inference closes read, observe, and cite."""

from __future__ import annotations

import asyncio
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import sys


STAGED_ROOT = Path(__file__).parents[1] / "experiments" / "staged-inference"
sys.path.insert(0, str(STAGED_ROOT))

from controller import StagedRunConfig, execute_staged_run  # noqa: E402
from scripted import FixedScriptModel, query_script, synthetic_task  # noqa: E402
from mock_spec.schemas import UnifiedTask  # noqa: E402
from mock_spec.source_repository import ReviewedRepository  # noqa: E402


def _repository_run(tmp_path: Path, *, bad_quote: bool = False) -> dict:
    task = UnifiedTask.from_dict(synthetic_task())
    tree = tmp_path / "snapshot" / "tree" / "src"
    tree.mkdir(parents=True)
    lines = [
        "DYNAMIC_CONTRACT = 'dependency result is 2'",
        *[f"# context {line}" for line in range(2, 10)],
        "def target(value):",
        "    return dependency(value)",
    ]
    payload = ("\n".join(lines) + "\n").encode()
    source_path = tree / "example.py"
    source_path.write_bytes(payload)
    root_fd = os.open(tmp_path / "snapshot", os.O_RDONLY | os.O_DIRECTORY)
    try:
        repository = ReviewedRepository(
            root_fd,
            task=task,
            snapshot_id="synthetic-reviewed-snapshot",
            files_by_logical_path={
                "src/example.py": (
                    "tree/src/example.py", hashlib.sha256(payload).hexdigest()
                )
            },
        )
    finally:
        os.close(root_fd)

    outputs = query_script(7)
    for output in outputs[1:8]:
        output["query_attempt"]["tool"] = "inspect_source"
        output["query_attempt"]["arguments_json"] = json.dumps({
            "source_id": "src/example.py", "start_line": 1, "end_line": 1,
        })
    source_id = repository.source_id_for("src/example.py")
    dynamic = {
        "id": "dynamic-contract",
        "source_id": source_id,
        "start_line": 1,
        "end_line": 1,
        "quote": "wrong" if bad_quote else lines[0],
        "rationale": "The repository observation states the dependency result contract.",
    }
    construct = outputs[9]
    deliver = outputs[10]
    construct["contract"] = deepcopy(construct["contract"])
    construct["contract"]["evidence"].append(dynamic)
    construct["contract"]["dependency_constraints"][0]["evidence_ids"].append(
        "dynamic-contract"
    )
    deliver["contract"] = deepcopy(construct["contract"])

    return asyncio.run(execute_staged_run(
        output_dir=tmp_path / "run",
        model=FixedScriptModel(outputs),
        config=StagedRunConfig(),
        task_loader=lambda: (task, repository),
        requested_case_id=task.case_id,
    ))


def test_repository_mode_reads_more_than_six_times_and_strictly_cites_observation(
    tmp_path: Path,
) -> None:
    result = _repository_run(tmp_path)

    assert result["manifest"]["result_status"] == "SELECTED", result["record"]["error"]
    assert result["manifest"]["controller_limits"] == {
        "model_calls": None,
        "query_attempts": None,
        "termination": "shared_deadline_token_and_cost_budgets",
    }
    assert result["record"]["query_accounting"] == {
        "observed_attempts": 7, "budgeted_attempts": 7, "limit": None,
    }
    assert len(result["record"]["model_calls"]) == 11
    observations = result["record"]["source_observations"]
    assert len(observations) == 7
    assert [item["response_id"] for item in observations] == [
        f"query:{index}" for index in range(1, 8)
    ]
    assert all(item["text"] == "DYNAMIC_CONTRACT = 'dependency result is 2'"
               for item in observations)


def test_repository_observation_quote_mismatch_is_rejected(tmp_path: Path) -> None:
    result = _repository_run(tmp_path, bad_quote=True)

    assert result["contract"] is None
    assert result["manifest"]["result_status"] == "ERROR"
    assert result["manifest"]["error_stage"] == "CONSTRUCT"
    assert result["manifest"]["stop_reason"] == "output_invalid"
    assert len(result["record"]["source_observations"]) == 7
