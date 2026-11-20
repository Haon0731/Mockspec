"""Run the seven-case Base versus v2 study with verbatim tagged slot delivery."""

from __future__ import annotations

import asyncio
from collections import Counter
import json
import os
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
BACKWARD_SLICING = HERE.parent
sys.path.insert(0, str(BACKWARD_SLICING))
sys.path.insert(0, str(BACKWARD_SLICING / "issue35"))

from issue35 import generate as gen
from mock_spec.tagged_slots import parse_tagged_candidate


ARMS = ("base", "v2")
REPEATS = (1, 2, 3)
MAX_CONCURRENCY = 6


def _read(path: Path):
    return json.loads(path.read_bytes())


def _case_data(case: str):
    # Keep the audited adapters and snapshot checks in the existing study loader.
    import study

    return study.case_data(case)


def _prompt_path(inputs: Path, case: str, arm: str) -> Path:
    return inputs / case / f"{arm}-tagged.txt"


def _validate_inputs(inputs: Path) -> tuple[dict, dict, dict]:
    import study

    manifest = _read(inputs / "manifest.json")
    if set(manifest) != set(study.ALL_CASES):
        raise ValueError("tagged manifest does not match the seven registered cases")
    prompts = {}
    sources = {}
    for case, receipt in manifest.items():
        _, sources[case], _, _ = _case_data(case)
        actual_sources = {
            source_id: gen.sha(source["text"].encode())
            for source_id, source in sources[case].items()
        }
        if actual_sources != receipt["sources"]:
            raise ValueError(f"source drift for {case}")
        for arm in ARMS:
            path = _prompt_path(inputs, case, arm)
            prompt = path.read_text()
            if gen.sha(prompt.encode()) != receipt["prompts"][f"{arm}-tagged"]:
                raise ValueError(f"prompt drift for {case}/{arm}")
            prompts[case, arm] = prompt
    return manifest, prompts, sources


def _parse_tagged_candidate(raw, slots):
    return parse_tagged_candidate(raw, slots)


def _existing_status(destination: Path, prompt: str, slots: list[str]) -> str | None:
    if not destination.exists():
        return None
    trace_path = destination / "trace.json"
    if not trace_path.is_file():
        raise ValueError(f"incomplete attempt directory: {destination}")
    trace = _read(trace_path)
    if trace["prompt_sha256"] != gen.sha(prompt.encode()):
        raise ValueError(f"resumed attempt has different prompt: {destination}")
    candidate_path = destination / "candidate.json"
    if trace["status"] == "generated":
        candidate = _read(candidate_path)
        turns = trace.get("turns", [])
        if not turns or candidate != _parse_tagged_candidate(turns[-1]["text"], slots):
            raise ValueError(f"invalid resumed candidate: {destination}")
    elif candidate_path.exists():
        raise ValueError(f"failed attempt has a candidate: {destination}")
    return trace["status"]


async def _run_one(semaphore, client, destination, prompt, slots, sources):
    status = _existing_status(destination, prompt, slots)
    if status is not None:
        return status
    async with semaphore:
        await gen.attempt(
            client,
            destination,
            prompt,
            slots,
            sources,
            candidate_parser=_parse_tagged_candidate,
        )
    return _read(destination / "trace.json")["status"]


def _write_or_validate_protocol(out: Path, inputs: Path, model: dict, prompts: dict):
    receipt = {
        "model": model,
        "max_output_tokens": gen.POLICY["max_output_tokens"],
        "max_source_calls": 10,
        "wall_seconds": gen.POLICY["wall_seconds"],
        "retries": 0,
        "truncation": "disabled",
        "delivery": "ordered tagged verbatim slots",
        "input_manifest_sha256": gen.sha((inputs / "manifest.json").read_bytes()),
        "longest_prompt_bytes": max(
            len(prompt.encode()) for prompt in prompts.values()
        ),
    }
    path = out / "protocol.json"
    if path.exists():
        if _read(path) != receipt:
            raise ValueError("tagged run protocol drift")
    else:
        out.mkdir(parents=True, exist_ok=True)
        gen.save(path, receipt)


def _freeze(out: Path, expected_attempts: int):
    traces = sorted(out.glob("*/*/*/trace.json"))
    if len(traces) != expected_attempts:
        raise ValueError(f"expected {expected_attempts} attempts, found {len(traces)}")
    candidates = sorted(out.glob("*/*/*/candidate.json"))
    files = traces + candidates + [out / "protocol.json", out / "gate.json"]
    receipt = {
        "attempts": expected_attempts,
        "statuses": dict(Counter(_read(path)["status"] for path in traces)),
        "sha256": {
            str(path.relative_to(out)): gen.sha(path.read_bytes()) for path in files
        },
    }
    path = out / "freeze.json"
    if path.exists():
        if _read(path) != receipt:
            raise ValueError("tagged run freeze drift")
    else:
        gen.save(path, receipt)


async def run_tagged(inputs: Path, out: Path):
    """Run the Jib 3/3 gate, then the complete seven-case paired matrix."""
    if os.environ.get("OPENAI_BASE_URL", "").rstrip("/") != "https://api.deepseek.com":
        raise ValueError("expected the configured study endpoint")
    manifest, prompts, sources = _validate_inputs(inputs)
    if len(manifest) != 7:
        raise ValueError("tagged study requires seven frozen cases")
    async with gen.AsyncOpenAI(max_retries=0, timeout=600) as client:
        model = (await client.models.retrieve(gen.POLICY["model"])).model_dump()
        initial_bound = (
            max(len(prompt.encode()) for prompt in prompts.values())
            + len(gen.encode(gen.TOOLS).encode())
            + 4096
        )
        if (
            model["context_window"] < initial_bound + gen.POLICY["max_output_tokens"]
            or model["max_output_tokens"] < gen.POLICY["max_output_tokens"]
        ):
            raise ValueError("longest tagged prompt exceeds model capacity")
        _write_or_validate_protocol(out, inputs, model, prompts)
        semaphore = asyncio.Semaphore(MAX_CONCURRENCY)
        gate_statuses = await asyncio.gather(
            *(
                _run_one(
                    semaphore,
                    client,
                    out / "jib" / str(repeat) / "v2",
                    prompts["jib", "v2"],
                    manifest["jib"]["slots"],
                    sources["jib"],
                )
                for repeat in REPEATS
            )
        )
        gate = {
            "case": "jib",
            "arm": "v2",
            "attempts": 3,
            "statuses": dict(Counter(gate_statuses)),
        }
        gate_path = out / "gate.json"
        if gate_path.exists():
            if _read(gate_path) != gate:
                raise ValueError("Jib gate drift")
        else:
            gen.save(gate_path, gate)
        if gate_statuses != ["generated"] * 3:
            raise ValueError(f"Jib tagged delivery gate failed: {gate['statuses']}")

        tasks = []
        for case in manifest:
            for repeat in REPEATS:
                for arm in ARMS:
                    if case == "jib" and arm == "v2":
                        continue
                    tasks.append(
                        _run_one(
                            semaphore,
                            client,
                            out / case / str(repeat) / arm,
                            prompts[case, arm],
                            manifest[case]["slots"],
                            sources[case],
                        )
                    )
        await asyncio.gather(*tasks)
    _freeze(out, expected_attempts=42)
