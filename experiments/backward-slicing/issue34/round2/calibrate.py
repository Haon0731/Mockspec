"""Calibrate the round-2 response budget with a synthetic ten-slot task."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
import time

from openai import AsyncOpenAI


MODEL = "deepseek-flash"
MAX_OUTPUT_TOKENS = 32768
SLOTS = [f"slot_{number:03d}" for number in range(1, 11)]
PROMPT = """This is a synthetic response-budget calibration. It contains no repository data.

Return one JSON object with exactly two fields. The first field is \"slots\", whose value is an
object containing exactly slot_001 through slot_010. Set each slot value to the Python statement
\"value = N\", where N is the slot number from 1 through 10. The second field is \"rationale\",
whose value is the one-element array [\"synthetic calibration\"]. Return JSON only.
"""


def write_json(path: Path, value: object) -> None:
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write("\n")


def validate(payload: object) -> None:
    if not isinstance(payload, dict) or set(payload) != {"slots", "rationale"}:
        raise ValueError("unexpected calibration envelope")
    slots = payload["slots"]
    if not isinstance(slots, dict) or list(slots) != SLOTS:
        raise ValueError("unexpected calibration slots")
    expected = {name: f"value = {index}" for index, name in enumerate(SLOTS, start=1)}
    if slots != expected or payload["rationale"] != ["synthetic calibration"]:
        raise ValueError("calibration content differs from the requested deterministic payload")


async def calibrate(output: Path) -> None:
    if os.environ.get("OPENAI_BASE_URL", "").rstrip("/") != "https://api.deepseek.com":
        raise ValueError("expected the configured project inference endpoint")
    output.mkdir(parents=True, exist_ok=False)
    (output / "prompt.txt").write_text(PROMPT, encoding="utf-8")
    write_json(output / "protocol.json", {
        "kind": "synthetic-ten-slot-response-budget-calibration",
        "model": MODEL,
        "max_output_tokens": MAX_OUTPUT_TOKENS,
        "reasoning": "provider default",
        "tools": [],
        "store": False,
        "retries": 0,
        "timeout_seconds": 600,
        "prompt_sha256": hashlib.sha256(PROMPT.encode()).hexdigest(),
        "repository_or_flask_input": False,
    })
    started = time.monotonic()
    async with AsyncOpenAI(max_retries=0, timeout=600) as client:
        response = await client.responses.create(
            model=MODEL,
            input=PROMPT,
            store=False,
            tools=[],
            truncation="disabled",
            include=["reasoning.encrypted_content"],
            max_output_tokens=MAX_OUTPUT_TOKENS,
        )
    raw = response.output_text.strip()
    if raw.startswith("```json\n") and raw.endswith("\n```"):
        raw = raw[8:-4]
    payload = json.loads(raw)
    validate(payload)
    write_json(output / "result.json", payload)
    write_json(output / "trace.json", {
        "status": response.status,
        "response_id": response.id,
        "resolved_model": response.model,
        "elapsed_seconds": time.monotonic() - started,
        "usage": response.usage.model_dump() if response.usage else None,
        "output_text_bytes": len(response.output_text.encode()),
        "validation": "ten slots and rationale matched exactly",
    })


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    arguments = parser.parse_args()
    asyncio.run(calibrate(arguments.output_dir))
