"""Frozen Step 6 live host launcher; capture bodies only, never auth headers."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
OUTPUT = ROOT / "out/live_inference/step6-live"
CASES = {
    "python": (
        ".omx/developer-realistic-registry-max-fix23/formal-snapshots/python-benchmark-new/"
        "241ef457f8d071c23e6acd658b684b11b54f13fa955e097d6bebe35dc755f47d",
        "flask_308",
    ),
    "java": (
        ".omx/developer-realistic-registry-max/formal-snapshots/java-bechmark-strong/"
        "5b93995c794af0152854a01ddda2fe77948b5ab828b05282b00728f7729f64d4",
        "com.google.cloud.tools.jib.cli.jar.JarFilesTest#"
        "testToJibContainerBuilder_explodedStandard_basicInfo@69ae66df",
    ),
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("language", choices=CASES)
    parser.add_argument("version", choices=("v0.1", "v0.2"))
    args = parser.parse_args()
    run_key = f"{args.language}-{args.version}"
    output = OUTPUT / run_key / "attempt-1"
    capture = OUTPUT / run_key / "attempt-1-http"
    if output.exists() or capture.exists():
        print("Refusing to overwrite existing attempt.", file=sys.stderr)
        return 2

    # Parse only the previously authorized key. Never source/execute the dotenv.
    matches = []
    for line in Path("/home/haon/projects/Stub-Tester/.env").read_text().splitlines():
        match = re.fullmatch(r"\s*(?:export\s+)?DEEPSEEK_API_KEY\s*=\s*(.*?)\s*", line)
        if match:
            value = match.group(1)
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            matches.append(value)
    if len(matches) != 1 or not matches[0] or any(c.isspace() for c in matches[0]):
        print("Authorized credential unavailable or ambiguous.", file=sys.stderr)
        return 2
    os.environ["OPENAI_API_KEY"] = matches[0]
    os.environ["OPENAI_BASE_URL"] = "https://api.deepseek.com"
    os.environ["OPENAI_AGENTS_DISABLE_TRACING"] = "1"

    import httpx2 as httpx
    from openai import AsyncOpenAI, DefaultAsyncHttpx2Client
    from agents import set_default_openai_client
    from mock_spec.live_v02 import main as run

    capture.mkdir(parents=True, exist_ok=False)
    sequence = 0

    def write(name: str, data: dict) -> None:
        with (capture / name).open("x") as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2)
            stream.write("\n")

    async def on_request(request: httpx.Request) -> None:
        nonlocal sequence
        if str(request.url) != "https://api.deepseek.com/responses":
            raise RuntimeError("Unexpected inference endpoint")
        sequence += 1
        request.extensions["capture_sequence"] = sequence
        body = await request.aread()
        write(f"request-{sequence:02d}.json", {
            "at": datetime.now(timezone.utc).isoformat(),
            "method": request.method,
            "url": str(request.url),
            "body_sha256": hashlib.sha256(body).hexdigest(),
            "body": json.loads(body),
            "headers": "intentionally not captured",
        })

    async def on_response(response: httpx.Response) -> None:
        body = await response.aread()
        number = response.request.extensions["capture_sequence"]
        write(f"response-{number:02d}.json", {
            "at": datetime.now(timezone.utc).isoformat(),
            "http_status": response.status_code,
            "body_sha256": hashlib.sha256(body).hexdigest(),
            "body": json.loads(body),
            "headers": "intentionally not captured",
        })

    client = AsyncOpenAI(
        api_key=matches[0], base_url="https://api.deepseek.com",
        http_client=DefaultAsyncHttpx2Client(event_hooks={
            "request": [on_request], "response": [on_response],
        }),
    )
    set_default_openai_client(client, use_for_tracing=False)
    snapshot, case_id = CASES[args.language]
    write("launcher.json", {
        "launcher_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "protocol_sha256": hashlib.sha256((ROOT / "docs/step6-live/protocol.md").read_bytes()).hexdigest(),
        "credential_source": "Step 4 authorized dotenv; key injected in memory only",
        "capture": "HTTP JSON request/response bodies; excludes headers and credentials",
    })
    return run([
        "--language", args.language, "--agent-version", args.version,
        "--snapshot", str(Path("/home/haon/projects/Stub-Tester") / snapshot),
        "--case-id", case_id, "--model", "deepseek-flash", "--timeout", "600",
        "--max-turns", "6", "--max-tool-calls", "8",
        "--max-output-tokens", "16384", "--max-total-tokens", "160000",
        "--max-tool-output-chars", "16000", "--analysis-timeout", "15",
        "--max-cost-usd", "0.75", "--input-cost-per-million", "0.30",
        "--output-cost-per-million", "1.20",
        "--experiment-ledger", str(OUTPUT / "ledger.json"),
        "--attempt-id", f"{run_key}-attempt-1", "--run-key", run_key,
        "--output-dir", str(output),
    ])


if __name__ == "__main__":
    raise SystemExit(main())
