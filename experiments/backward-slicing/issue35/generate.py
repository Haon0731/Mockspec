"""Freeze #35 inputs and run the balanced, one-attempt generation schedule."""

import argparse
import asyncio
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import time

from openai import AsyncOpenAI

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from issue34.generate import TOOLS, resolved_policy
from inputs import case_keys, load_case, load_prompt_sources
from navigation import format_full_evidence, format_navigation

POLICY = resolved_policy(32768)
ARMS = "ABC"


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def save(path, value):
    with path.open("x", encoding="utf-8") as stream:
        stream.write(encode(value) + "\n")


def sha(data):
    return hashlib.sha256(data).hexdigest()


def slot_ids(contract, test):
    if contract["identity"]["language"] == "java":
        pattern = r"// === (MOCK-STUB|VERIFY)-START ==="
    else:
        pattern = r"# === PY-SLOT-START:(\w+) ==="
    ids = re.findall(pattern, test)
    if not ids or len(set(ids)) != len(ids):
        raise ValueError("invalid or empty slot manifest")
    return ids


def make_prompt(shared, evidence):
    return (
        "Complete the masked test in one response. Use only the provided input and audited "
        "read/search tools. Preserve every line outside the marked regions. Supply all mock "
        "declarations, stub behavior and interaction assertions together. Justify assertions "
        "from the visible test goal. Mark unresolved behavior Unknown. The existence of a "
        "call on a path only supports a candidate interaction. Return only JSON with fields "
        "slots and rationale. slots maps every required slot name to replacement source text "
        "(relative indentation for Python). rationale is an array of short claims with visible "
        "locations or Unknown. Up to ten read/search calls.\nInput:\n"
        + encode(shared)
        + "\nNavigation evidence:\n"
        + encode(evidence)
        + "\n"
    )


def prepare(dest):
    dest.mkdir(parents=True, exist_ok=False)
    cases = {}
    for key in case_keys():
        contract, sources = load_case(key)
        initial = load_prompt_sources(key)
        ids = slot_ids(contract, sources["test"]["text"])
        shared = {
            "case_id": contract["identity"]["case_id"],
            "slot_ids": ids,
            "initial_sources": initial,
            "available_sources": [
                {k: v for k, v in row.items() if k != "text"}
                for row in sources.values()
            ],
        }
        case_dir = dest / key
        case_dir.mkdir()
        full = format_full_evidence(contract, sources)
        compact = format_navigation(contract, sources)
        evidence_manifest = {}
        prompts = {}
        for arm, evidence in zip(ARMS, [None, full, compact], strict=True):
            if evidence is not None:
                evidence = evidence.copy()
                evidence_manifest[arm] = evidence.pop("projection")
            prompt = make_prompt(shared, evidence).encode()
            (case_dir / f"{arm}.txt").write_bytes(prompt)
            prompts[arm] = {"sha256": sha(prompt), "bytes": len(prompt)}
        for field in (
            "input_contract_sha256",
            "visible_sources_sha256",
            "covered_relation_fact_ids",
        ):
            if evidence_manifest["B"][field] != evidence_manifest["C"][field]:
                raise ValueError(f"mismatched B/C provenance for {key}: {field}")
        save(case_dir / "projection.json", evidence_manifest)
        cases[key] = {
            "snapshot": contract["identity"]["snapshot_id"],
            "mask": contract["identity"]["masked_test_sha256"],
            "slots": ids,
            "prompts": prompts,
            "projection_sha256": sha((case_dir / "projection.json").read_bytes()),
            "sources": {i: sha(s["text"].encode()) for i, s in sources.items()},
        }
    save(dest / "manifest.json", cases)


def source_call(action, sources):
    try:
        args = json.loads(action.arguments)
        if action.name == "read_source":
            locator = args["locator"]
            choices = [
                s for i, s in sources.items() if locator in (i, s["logical_path"])
            ]
            if len(choices) != 1:
                raise ValueError
            source = choices[0]
            start, end = args["start_line"], args["end_line"]
            if (
                type(start) is not int
                or type(end) is not int
                or not source["start_line"] <= start <= end <= source["end_line"]
            ):
                raise ValueError
            lines = source["text"].splitlines()
            excerpt = "\n".join(
                lines[start - source["start_line"] : end - source["start_line"] + 1]
            )
            if len(excerpt.encode()) > 16000:
                return "Range exceeds byte budget; request a narrower interval."
            return encode(
                {
                    "source_id": source["source_id"],
                    "start_line": start,
                    "end_line": end,
                    "text": excerpt,
                }
            )
        if (
            action.name == "search_source"
            and isinstance(args["text"], str)
            and args["text"]
        ):
            matches = []
            size = 0
            for i, src in sources.items():
                for number, line in enumerate(
                    src["text"].splitlines(), src["start_line"]
                ):
                    if args["text"] in line:
                        entry = [i, number, line]
                        size += len(encode(entry).encode()) + 1
                        if size > 16000:
                            return encode({"matches": matches, "complete": False})
                        matches.append(entry)
            return encode({"matches": matches, "complete": True})
    except (ValueError, TypeError, KeyError):
        pass
    return "Invalid request for the approved source projection."


def parse_json_candidate(raw, slots):
    raw = raw.strip()
    if raw.startswith("```json\n") and raw.endswith("\n```"):
        raw = raw[8:-4]
    payload = json.loads(raw)
    if (
        not isinstance(payload, dict)
        or set(payload) != {"slots", "rationale"}
        or not isinstance(payload["slots"], dict)
        or set(payload["slots"]) != set(slots)
        or not all(isinstance(v, str) for v in payload["slots"].values())
        or not isinstance(payload["rationale"], list)
        or not all(isinstance(v, str) for v in payload["rationale"])
    ):
        raise ValueError("invalid candidate envelope or slots")
    return payload


async def attempt(client, dest, prompt, slots, sources, candidate_parser=None):
    dest.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    history = [{"role": "user", "content": prompt}]
    trace = {
        "input_bytes": len(prompt.encode()),
        "prompt_sha256": sha(prompt.encode()),
        "source_calls": 0,
        "turns": [],
        "status": "failed",
    }
    try:
        async with asyncio.timeout(POLICY["wall_seconds"]):
            while True:
                reply = await client.responses.create(
                    model=POLICY["model"],
                    input=history,
                    tools=TOOLS,
                    store=False,
                    tool_choice="auto" if trace["source_calls"] < 10 else "none",
                    include=["reasoning.encrypted_content"],
                    truncation="disabled",
                    max_output_tokens=POLICY["max_output_tokens"],
                )
                event = {
                    "response_id": reply.id,
                    "resolved_model": reply.model,
                    "usage": reply.usage.model_dump() if reply.usage else None,
                    "status": reply.status,
                    "text": reply.output_text,
                    "tools": [],
                }
                trace["turns"].append(event)
                if reply.status != "completed":
                    raise ValueError(f"response status {reply.status}")
                requests = [o for o in reply.output if o.type == "function_call"]
                if not requests:
                    parser = candidate_parser or parse_json_candidate
                    payload = parser(reply.output_text, slots)
                    save(dest / "candidate.json", payload)
                    trace["status"] = "generated"
                    break
                history.extend(o.model_dump(exclude_none=True) for o in reply.output)
                for action in requests:
                    result = "Source budget exhausted"
                    if trace["source_calls"] < 10:
                        trace["source_calls"] += 1
                        result = source_call(action, sources)
                    event["tools"].append(
                        {
                            "name": action.name,
                            "args": action.arguments,
                            "result": result,
                        }
                    )
                    history.append(
                        {
                            "type": "function_call_output",
                            "call_id": action.call_id,
                            "output": result,
                        }
                    )
    except Exception as error:
        trace.update(
            error_type=type(error).__name__,
            status_code=getattr(error, "status_code", None),
            reason=str(error) if isinstance(error, ValueError) else None,
        )
    finally:
        trace["elapsed_seconds"] = round(time.monotonic() - started, 3)
        save(dest / "trace.json", trace)
    print(dest, trace["status"], flush=True)


async def run(inputs_dir, dest):
    if os.environ.get("OPENAI_BASE_URL", "").rstrip("/") != "https://api.deepseek.com":
        raise ValueError("expected the configured study endpoint")
    manifest = json.loads((inputs_dir / "manifest.json").read_text())
    if set(manifest) != set(case_keys()):
        raise ValueError("six-case manifest required")
    cases = {}
    for key in case_keys():
        case = manifest[key]
        _, sources = load_case(key)
        if {i: sha(s["text"].encode()) for i, s in sources.items()} != case["sources"]:
            raise ValueError("source digest mismatch")
        prompts = {}
        for arm in ARMS:
            data = (inputs_dir / key / f"{arm}.txt").read_bytes()
            if sha(data) != case["prompts"][arm]["sha256"]:
                raise ValueError("prompt digest mismatch")
            prompts[arm] = data.decode()
        cases[key] = (sources, prompts)
    async with AsyncOpenAI(max_retries=0, timeout=600) as client:
        model = (await client.models.retrieve(POLICY["model"])).model_dump()
        longest = max(
            len(p.encode()) for _, prompts in cases.values() for p in prompts.values()
        )
        # Byte-level tokenization cannot exceed one token per UTF-8 byte. Include
        # the tool definitions and generous space for the first message envelope.
        initial_bound = longest + len(encode(TOOLS).encode()) + 4096
        if (
            model["context_window"] < initial_bound + POLICY["max_output_tokens"]
            or model["max_output_tokens"] < POLICY["max_output_tokens"]
        ):
            raise ValueError("longest full prompt exceeds model capacity")
        dest.mkdir(parents=True, exist_ok=False)
        save(
            dest / "protocol.json",
            {
                "model": model,
                "initial_input_token_upper_bound": initial_bound,
                "longest_prompt_bytes": longest,
                "max_output_tokens": 32768,
                "wall_seconds": 600,
                "max_source_calls": 10,
                "truncation": "disabled",
                "retries": 0,
                "tools": TOOLS,
                "input_manifest_sha256": sha(
                    (inputs_dir / "manifest.json").read_bytes()
                ),
            },
        )

        async def one_case(index, key):
            sources, prompts = cases[key]
            for repeat in range(3):
                first = (index + repeat) % 3
                for arm in ARMS[first:] + ARMS[:first]:
                    await attempt(
                        client,
                        dest / key / str(repeat + 1) / arm,
                        prompts[arm],
                        manifest[key]["slots"],
                        sources,
                    )

        await asyncio.gather(*(one_case(i, k) for i, k in enumerate(case_keys())))
        receipts = sorted(dest.glob("*/*/*/trace.json"))
        if len(receipts) != 54:
            raise ValueError("incomplete schedule")
        files = sorted(
            [*receipts, *dest.glob("*/*/*/candidate.json"), dest / "protocol.json"]
        )
        save(
            dest / "freeze.json",
            {
                "attempts": len(receipts),
                "statuses": dict(
                    Counter(json.loads(p.read_text())["status"] for p in receipts)
                ),
                "sha256": {
                    str(p.relative_to(dest)): sha(p.read_bytes()) for p in files
                },
            },
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=["prepare", "run"])
    parser.add_argument("--inputs", required=True, type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.operation == "prepare":
        prepare(args.inputs)
    elif args.output:
        asyncio.run(run(args.inputs, args.output))
    else:
        parser.error("run requires --output")
