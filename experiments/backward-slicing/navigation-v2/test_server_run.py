from __future__ import annotations

import asyncio
import csv
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import server_run


def _write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value), encoding="utf-8")


def _bundle(tmp_path: Path) -> Path:
    prompts = tmp_path / "prompts"
    prompts.mkdir()
    cases = []
    rows = []
    for index in range(server_run.EXPECTED_CASES):
        case_id = f"case-{index}"
        token = f"{index:020d}"
        arms = {}
        for arm in server_run.ARMS:
            data = f"prompt {case_id} {arm}\n".encode()
            path = prompts / f"{token}.{arm}.txt"
            path.write_bytes(data)
            digest = hashlib.sha256(data).hexdigest()
            arms[arm] = {
                "prompt_path": f"prompts/{token}.{arm}.txt",
                "prompt_sha256": digest,
                "prompt_bytes": len(data),
            }
            rows.append(
                {
                    "case_id": case_id,
                    "arm": arm,
                    "prompt_sha256": digest,
                    "prompt_bytes": len(data),
                    "chat_input_tokens": 10,
                    "combined_tokens": 32778,
                    "context_tokens": 1048576,
                    "fits_context": "True",
                }
            )
        cases.append(
            {
                "case_id": case_id,
                "token": token,
                "language": "python",
                "slot_ids": ["slot"],
                "source_preflight": {"status": "ready"},
                "arms": arms,
            }
        )
    _write_json(tmp_path / "prompt-manifest.json", {"cases": cases})
    with (tmp_path / "windows.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    _write_json(
        tmp_path / "generation-config.json",
        {
            "schema_version": server_run.EXPECTED_SCHEMA,
            "experiment": {
                "arms": list(server_run.ARMS),
                "cases_per_arm": 165,
                "generations_per_case_arm": 1,
                "total_calls": 330,
            },
            "model": {
                "api_id": "deepseek-flash",
                "service_name_required": "DeepSeek-V4.1-Flash",
                "context_window_required": 1048576,
                "max_output_tokens_required_at_least": 32768,
                "effort_levels_required": ["low", "high", "max"],
            },
            "request": {
                "reasoning": {"effort": "high"},
                "top_p": 1.0,
                "max_output_tokens": 32768,
                "truncation": "disabled",
                "store": False,
                "tools": "omitted; all approved CUT sources are preloaded",
            },
            "execution": {
                "concurrency": 6,
                "wall_seconds_per_call": 600,
                "sdk_retries": 0,
                "application_retries": 0,
            },
        },
    )
    return tmp_path


def test_verify_bundle_checks_all_prompts_and_counterbalances(tmp_path: Path) -> None:
    verified = server_run.verify_bundle(_bundle(tmp_path))

    assert verified["case_count"] == 165
    assert verified["call_count"] == 330
    assert verified["input_tokens"] == 3300
    assert [row["arm"] for row in verified["prompts"][:4]] == ["base", "v2", "v2", "base"]


def test_verify_bundle_rejects_prompt_drift(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path)
    (bundle / "prompts/00000000000000000000.base.txt").write_text("changed")

    with pytest.raises(ValueError, match="prompt/window receipt differs"):
        server_run.verify_bundle(bundle)


def test_existing_terminal_result_is_resumable_and_interrupted_cell_is_not(
    tmp_path: Path,
) -> None:
    terminal = tmp_path / "terminal"
    terminal.mkdir()
    _write_json(terminal / "trace.json", {"prompt_sha256": "abc", "status": "failed"})
    assert server_run._existing_status(terminal, "abc") == "failed"

    interrupted = tmp_path / "interrupted"
    interrupted.mkdir()
    _write_json(interrupted / "started.json", {"prompt_sha256": "abc"})
    with pytest.raises(RuntimeError, match="refusing a duplicate call"):
        server_run._existing_status(interrupted, "abc")


def test_model_metadata_gate() -> None:
    config = {
        "model": {
            "api_id": "deepseek-flash",
            "service_name_required": "DeepSeek-V4.1-Flash",
            "context_window_required": 1048576,
            "max_output_tokens_required_at_least": 32768,
            "effort_levels_required": ["low", "high", "max"],
        }
    }
    metadata = {
        "id": "deepseek-flash",
        "name": "DeepSeek-V4.1-Flash",
        "context_window": 1048576,
        "max_output_tokens": 393216,
        "effort": {"supported_levels": ["low", "high", "max"]},
    }
    server_run.validate_model(metadata, config)
    with pytest.raises(ValueError, match="metadata differs"):
        server_run.validate_model({**metadata, "name": "changed"}, config)


class _FakeUsage:
    def model_dump(self) -> dict[str, int]:
        return {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15}


def test_run_one_writes_candidate_and_trace(tmp_path: Path) -> None:
    prompt = tmp_path / "prompt.txt"
    prompt.write_text("prompt")
    response = SimpleNamespace(
        id="response-1",
        model="deepseek-flash",
        status="completed",
        usage=_FakeUsage(),
        output_text="```text\n<slot>\nx = 1\n</slot>\n```",
    )
    client = SimpleNamespace(
        responses=SimpleNamespace(create=lambda **kwargs: _async_value(response))
    )
    row = {
        "case_id": "case",
        "token": "token",
        "arm": "base",
        "language": "python",
        "slots": ["slot"],
        "prompt_path": prompt,
        "prompt_sha256": hashlib.sha256(prompt.read_bytes()).hexdigest(),
    }
    config = {
        "model": {"api_id": "deepseek-flash"},
        "request": {
            "reasoning": {"effort": "high"},
            "top_p": 1.0,
            "max_output_tokens": 32768,
            "truncation": "disabled",
            "store": False,
        },
        "execution": {"wall_seconds_per_call": 600},
    }

    status = asyncio.run(
        server_run._run_one(
            client, asyncio.Semaphore(1), tmp_path / "out", row, config
        )
    )

    assert status == "generated"
    assert json.loads((tmp_path / "out/token/base/candidate.json").read_text()) == {"slot": "x = 1"}
    assert json.loads((tmp_path / "out/token/base/trace.json").read_text())["status"] == "generated"


async def _async_value(value: object) -> object:
    return value
