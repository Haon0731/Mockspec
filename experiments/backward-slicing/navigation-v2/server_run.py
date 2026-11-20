"""Verify or execute the portable Issue #37 paired generation bundle.

The bundle is deliberately generation-only.  It contains prompts and immutable
receipts, but no reference tests, mutant bank, or evaluator inputs.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import time
from typing import Any

from openai import AsyncOpenAI

from mock_spec.tagged_slots import parse_tagged_slots


ARMS = ("base", "v2")
EXPECTED_CASES = 165
EXPECTED_CALLS = EXPECTED_CASES * len(ARMS)
EXPECTED_SCHEMA = "mockspec-issue37-generation-config/v1"


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _read_json(path: Path) -> Any:
    return json.loads(path.read_bytes())


def _write_new(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        stream.write("\n")


def _atomic_write(path: Path, value: Any) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        stream.write("\n")
    temporary.replace(path)


def _safe_prompt_path(bundle: Path, relative: str) -> Path:
    pure = PurePosixPath(relative)
    if pure.is_absolute() or ".." in pure.parts or pure.parts[:1] != ("prompts",):
        raise ValueError(f"unsafe prompt path: {relative}")
    path = bundle.joinpath(*pure.parts)
    if not path.is_file() or path.is_symlink():
        raise ValueError(f"prompt is absent or linked: {relative}")
    return path


def _parse_candidate(raw: str, slot_ids: list[str]) -> dict[str, str]:
    pattern = re.compile(r"\A```text\r?\n(.+?)\r?\n```\r?\n?\Z", re.DOTALL)
    match = pattern.fullmatch(raw)
    if match is None or "```" in match.group(1):
        raise ValueError("response must contain exactly one text code block")
    return parse_tagged_slots(match.group(1) + "\n", slot_ids)


def _load_windows(path: Path) -> dict[tuple[str, str], dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as stream:
        rows = list(csv.DictReader(stream))
    indexed = {(row["case_id"], row["arm"]): row for row in rows}
    if len(rows) != EXPECTED_CALLS or len(indexed) != EXPECTED_CALLS:
        raise ValueError("window ledger must contain 330 unique case/arm rows")
    return indexed


def verify_bundle(bundle: Path) -> dict[str, Any]:
    """Validate every portable input before credentials or network are used."""
    bundle = bundle.resolve()
    manifest_path = bundle / "prompt-manifest.json"
    windows_path = bundle / "windows.csv"
    config_path = bundle / "generation-config.json"
    if any(not path.is_file() or path.is_symlink() for path in (manifest_path, windows_path, config_path)):
        raise ValueError("portable bundle is missing a required regular file")
    manifest = _read_json(manifest_path)
    config = _read_json(config_path)
    windows = _load_windows(windows_path)
    cases = manifest.get("cases")
    if not isinstance(cases, list) or len(cases) != EXPECTED_CASES:
        raise ValueError("prompt manifest must contain 165 cases")
    case_ids = [case.get("case_id") for case in cases]
    tokens = [case.get("token") for case in cases]
    if len(set(case_ids)) != EXPECTED_CASES or len(set(tokens)) != EXPECTED_CASES:
        raise ValueError("case IDs and artifact tokens must be unique")
    if config.get("schema_version") != EXPECTED_SCHEMA:
        raise ValueError("unexpected generation configuration schema")
    experiment = config.get("experiment", {})
    execution = config.get("execution", {})
    request = config.get("request", {})
    if (
        experiment.get("arms") != list(ARMS)
        or experiment.get("cases_per_arm") != EXPECTED_CASES
        or experiment.get("generations_per_case_arm") != 1
        or experiment.get("total_calls") != EXPECTED_CALLS
        or execution.get("sdk_retries") != 0
        or execution.get("application_retries") != 0
        or execution.get("concurrency") != 6
        or execution.get("wall_seconds_per_call") != 600
        or request.get("max_output_tokens") != 32768
        or request.get("truncation") != "disabled"
        or request.get("store") is not False
        or request.get("tools") != "omitted; all approved CUT sources are preloaded"
    ):
        raise ValueError("generation configuration differs from the approved protocol")

    prompts: list[dict[str, Any]] = []
    for case_index, case in enumerate(cases):
        if case.get("source_preflight", {}).get("status") != "ready":
            raise ValueError(f"source preflight is not ready: {case['case_id']}")
        slots = case.get("slot_ids")
        if (
            not isinstance(slots, list)
            or not slots
            or len(slots) != len(set(slots))
            or any(not isinstance(slot, str) or re.fullmatch(r"[A-Za-z0-9_-]+", slot) is None for slot in slots)
        ):
            raise ValueError(f"invalid slots: {case['case_id']}")
        for arm in ARMS:
            receipt = case.get("arms", {}).get(arm, {})
            expected_name = f"prompts/{case['token']}.{arm}.txt"
            if receipt.get("prompt_path") != expected_name:
                raise ValueError(f"noncanonical prompt path: {case['case_id']}/{arm}")
            path = _safe_prompt_path(bundle, expected_name)
            data = path.read_bytes()
            window = windows.get((case["case_id"], arm))
            if (
                _sha256(data) != receipt.get("prompt_sha256")
                or len(data) != receipt.get("prompt_bytes")
                or window is None
                or window.get("prompt_sha256") != receipt.get("prompt_sha256")
                or int(window["prompt_bytes"]) != len(data)
                or window.get("fits_context") != "True"
                or int(window["combined_tokens"]) > int(window["context_tokens"])
            ):
                raise ValueError(f"prompt/window receipt differs: {case['case_id']}/{arm}")
            prompts.append(
                {
                    "case_id": case["case_id"],
                    "case_index": case_index,
                    "token": case["token"],
                    "language": case["language"],
                    "arm": arm,
                    "slots": slots,
                    "prompt_path": path,
                    "prompt_sha256": receipt["prompt_sha256"],
                    "prompt_bytes": len(data),
                    "chat_input_tokens": int(window["chat_input_tokens"]),
                }
            )
    # Counterbalance arm order while retaining a stable deterministic schedule.
    prompts.sort(key=lambda row: (row["case_index"], (row["arm"] == "v2") ^ (row["case_index"] % 2 == 1)))
    return {
        "bundle": str(bundle),
        "prompt_manifest_sha256": _sha256(manifest_path.read_bytes()),
        "windows_sha256": _sha256(windows_path.read_bytes()),
        "generation_config_sha256": _sha256(config_path.read_bytes()),
        "case_count": EXPECTED_CASES,
        "call_count": EXPECTED_CALLS,
        "input_tokens": sum(row["chat_input_tokens"] for row in prompts),
        "config": config,
        "prompts": prompts,
    }


def _model_payload(model: Any) -> dict[str, Any]:
    return model.model_dump() if hasattr(model, "model_dump") else dict(model)


def validate_model(metadata: dict[str, Any], config: dict[str, Any]) -> None:
    expected = config["model"]
    effort = metadata.get("effort", {}).get("supported_levels", [])
    if (
        metadata.get("id") != expected["api_id"]
        or metadata.get("name") != expected["service_name_required"]
        or metadata.get("context_window") != expected["context_window_required"]
        or metadata.get("max_output_tokens", 0) < expected["max_output_tokens_required_at_least"]
        or effort != expected["effort_levels_required"]
    ):
        raise ValueError("service model metadata differs from the approved configuration")


def _existing_status(destination: Path, prompt_sha256: str) -> str | None:
    trace_path = destination / "trace.json"
    started_path = destination / "started.json"
    if trace_path.is_file():
        trace = _read_json(trace_path)
        if trace.get("prompt_sha256") != prompt_sha256:
            raise ValueError(f"existing result belongs to another prompt: {destination}")
        if trace.get("status") not in {"generated", "failed"}:
            raise ValueError(f"invalid terminal status: {destination}")
        return trace["status"]
    if destination.exists() or started_path.exists():
        raise RuntimeError(
            f"interrupted cell requires manual reconciliation; refusing a duplicate call: {destination}"
        )
    return None


async def _run_one(
    client: Any,
    semaphore: asyncio.Semaphore,
    output: Path,
    row: dict[str, Any],
    config: dict[str, Any],
) -> str:
    async with semaphore:
        return await _run_one_locked(client, output, row, config)


async def _run_one_locked(
    client: Any,
    output: Path,
    row: dict[str, Any],
    config: dict[str, Any],
) -> str:
    destination = output / row["token"] / row["arm"]
    prior = _existing_status(destination, row["prompt_sha256"])
    if prior is not None:
        return prior
    destination.mkdir(parents=True, exist_ok=False)
    _write_new(
        destination / "started.json",
        {
            "case_id": row["case_id"],
            "arm": row["arm"],
            "prompt_sha256": row["prompt_sha256"],
        },
    )
    started = time.monotonic()
    trace: dict[str, Any] = {
        "case_id": row["case_id"],
        "arm": row["arm"],
        "prompt_sha256": row["prompt_sha256"],
        "status": "failed",
    }
    try:
        response = await asyncio.wait_for(
            client.responses.create(
                model=config["model"]["api_id"],
                input=row["prompt_path"].read_text(encoding="utf-8"),
                reasoning=config["request"]["reasoning"],
                top_p=config["request"]["top_p"],
                max_output_tokens=config["request"]["max_output_tokens"],
                truncation=config["request"]["truncation"],
                store=config["request"]["store"],
            ),
            timeout=config["execution"]["wall_seconds_per_call"],
        )
        trace.update(
            response_id=response.id,
            resolved_model=response.model,
            response_status=response.status,
            usage=response.usage.model_dump() if response.usage else None,
            text=response.output_text,
        )
        if response.status != "completed":
            raise ValueError(f"response status {response.status}")
        slots = _parse_candidate(response.output_text, row["slots"])
        _write_new(destination / "candidate.json", slots)
        trace["status"] = "generated"
    except Exception as error:
        trace.update(
            error_type=type(error).__name__,
            status_code=getattr(error, "status_code", None),
            reason=str(error) if isinstance(error, (ValueError, RuntimeError)) else None,
        )
    finally:
        trace["elapsed_seconds"] = round(time.monotonic() - started, 3)
        _atomic_write(destination / "trace.json", trace)
    return trace["status"]


async def execute(verified: dict[str, Any], output: Path) -> dict[str, Any]:
    api_key = os.environ.get("DEEPSEEK_API_KEY") or os.environ.get("OPENAI_API_KEY")
    base_url = os.environ.get("OPENAI_BASE_URL")
    if not api_key or base_url != "https://api.deepseek.com":
        raise ValueError("set DEEPSEEK_API_KEY and OPENAI_BASE_URL=https://api.deepseek.com")
    output.mkdir(parents=True, exist_ok=True)
    config = verified["config"]
    async with AsyncOpenAI(api_key=api_key, base_url=base_url, max_retries=0, timeout=600) as client:
        metadata = _model_payload(await client.models.retrieve(config["model"]["api_id"]))
        validate_model(metadata, config)
        protocol = {
            key: verified[key]
            for key in (
                "prompt_manifest_sha256",
                "windows_sha256",
                "generation_config_sha256",
                "case_count",
                "call_count",
                "input_tokens",
            )
        }
        protocol["model_metadata"] = metadata
        protocol_path = output / "protocol.json"
        if protocol_path.exists():
            if _read_json(protocol_path) != protocol:
                raise ValueError("run protocol differs from the existing output")
        else:
            _write_new(protocol_path, protocol)
        semaphore = asyncio.Semaphore(config["execution"]["concurrency"])
        statuses = await asyncio.gather(
            *(
                _run_one(client, semaphore, output, row, config)
                for row in verified["prompts"]
            )
        )
    summary = {
        "calls": len(statuses),
        "generated": statuses.count("generated"),
        "failed": statuses.count("failed"),
    }
    _atomic_write(output / "summary.json", summary)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--execute",
        action="store_true",
        help="perform the 330 paid calls; without this flag only verify the bundle",
    )
    args = parser.parse_args()
    verified = verify_bundle(args.bundle)
    if not args.execute:
        print(json.dumps({key: value for key, value in verified.items() if key not in {"config", "prompts"}}, indent=2))
        return
    if args.output is None:
        parser.error("--output is required with --execute")
    print(json.dumps(asyncio.run(execute(verified, args.output)), indent=2))


if __name__ == "__main__":
    main()
