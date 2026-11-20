"""Run one isolated Responses API conversation per frozen input, without retries."""
import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
import sys
import time

from openai import AsyncOpenAI

HERE = Path(__file__).resolve().parent
POLICY = {
    "model": "deepseek-flash", "reasoning": "provider default", "max_output_tokens": 8192,
    "initial_prompt_byte_cap": 65536, "context_window_tokens": "provider unreported",
    "truncation": "disabled", "max_source_calls": 10, "wall_seconds": 600,
    "retries": 0, "store": False,
}


def resolved_policy(max_output_tokens):
    if not isinstance(max_output_tokens, int) or isinstance(max_output_tokens, bool):
        raise ValueError("max output tokens must be an integer")
    if max_output_tokens <= 0:
        raise ValueError("max output tokens must be positive")
    return {**POLICY, "max_output_tokens": max_output_tokens}


def function(name, description, fields):
    return {"type": "function", "name": name, "description": description, "strict": True,
            "parameters": {"type": "object", "additionalProperties": False,
                           "properties": {k: {"type": v} for k, v in fields.items()},
                           "required": list(fields)}}


TOOLS = [
    function("read_source", "Read inclusive lines of an approved source by source ID or logical path.",
             {"locator": "string", "start_line": "integer", "end_line": "integer"}),
    function("search_source", "Find a literal string in approved sources.", {"text": "string"}),
]


def write(path, value):
    with path.open("x", encoding="utf8") as handle:
        json.dump(value, handle, indent=2, ensure_ascii=False)
        handle.write("\n")


async def source_call(call, root):
    try:
        args = json.loads(call.arguments)
        if call.name == "read_source":
            command = ["read", args["locator"], str(args["start_line"]), str(args["end_line"])]
        elif call.name == "search_source":
            command = ["search", "--text", args["text"]]
        else:
            return "Unknown source tool"
        process = await asyncio.create_subprocess_exec(
            sys.executable, str(HERE / "source_tool.py"), "--old-root", str(root), *command,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
        output, _ = await process.communicate()
        return output.decode() if process.returncode == 0 else "Source request rejected or unavailable"
    except (KeyError, TypeError, ValueError):
        return "Invalid source arguments"


async def generate(client, arm, prompt, root, dest, policy):
    start = time.monotonic()
    history = [{"role": "user", "content": prompt}]
    calls = 0
    trace = []
    record = {"prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(), "turns": trace}
    try:
        async with asyncio.timeout(policy["wall_seconds"]):
            while True:
                reply = await client.responses.create(
                    model=policy["model"], input=history, store=False, tools=TOOLS,
                    tool_choice="auto" if calls < policy["max_source_calls"] else "none",
                    include=["reasoning.encrypted_content"], truncation="disabled",
                    max_output_tokens=policy["max_output_tokens"])
                event = {"response_id": reply.id, "resolved_model": reply.model, "status": reply.status,
                         "usage": reply.usage.model_dump() if reply.usage else None, "tools": [],
                         "text": reply.output_text}
                trace.append(event)
                if reply.status != "completed":
                    raise ValueError("incomplete model response")
                requested = [o for o in reply.output if o.type == "function_call"]
                if not requested:
                    raw = reply.output_text.strip()
                    if raw.startswith("```json\n") and raw.endswith("\n```"):
                        raw = raw[8:-4]
                    write(dest / f"generation-{arm}.json", json.loads(raw))
                    record["status"] = "generated"
                    break
                history.extend(o.model_dump(exclude_none=True) for o in reply.output)
                for action in requested:
                    if calls < policy["max_source_calls"]:
                        calls += 1
                        result = await source_call(action, root)
                    else:
                        result = "Source budget exhausted"
                    event["tools"].append({"name": action.name, "arguments": action.arguments, "result": result})
                    history.append({"type": "function_call_output", "call_id": action.call_id, "output": result})
    except Exception as error:
        record.update(status="failed", error_type=type(error).__name__,
                      status_code=getattr(error, "status_code", None), code=getattr(error, "code", None))
    finally:
        record.update(source_calls=calls, elapsed_seconds=time.monotonic()-start)
        write(dest / f"trace-{arm}.json", record)
    return record["status"] == "generated"


async def main(root, dest, max_output_tokens=POLICY["max_output_tokens"]):
    policy = resolved_policy(max_output_tokens)
    prompts = {arm: (HERE / "inputs" / arm / "prompt.txt").read_text() for arm in "abc"}
    if any(len(p.encode()) > policy["initial_prompt_byte_cap"] for p in prompts.values()):
        raise ValueError("prompt over common input cap")
    manifest = json.loads((HERE / "inputs/manifest.json").read_text())
    for arm, prompt in prompts.items():
        artifact = next(r for r in manifest["files"] if r["path"] == f"inputs/{arm}/prompt.txt")
        if hashlib.sha256(prompt.encode()).hexdigest() != artifact["sha256"]:
            raise ValueError("prompt differs from input manifest")
    if os.environ["OPENAI_BASE_URL"].rstrip("/") != "https://api.deepseek.com":
        raise ValueError("expected the configured project inference endpoint")
    dest.mkdir(parents=True, exist_ok=False)
    write(dest / "protocol.json", {**policy, "tools": TOOLS, "prompt_sha256": {
        arm: hashlib.sha256(p.encode()).hexdigest() for arm, p in prompts.items()}})
    async with AsyncOpenAI(max_retries=0, timeout=600) as client:
        succeeded = await asyncio.gather(*(generate(client, arm, prompt, root, dest, policy)
                                           for arm, prompt in prompts.items()))
    if not all(succeeded):
        raise SystemExit("generation incomplete; see per-arm traces")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--old-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--max-output-tokens", type=int, default=POLICY["max_output_tokens"],
                        help="per-response reasoning plus final-output budget (default: 8192)")
    args = parser.parse_args()
    asyncio.run(main(args.old_root, args.output_dir, args.max_output_tokens))
