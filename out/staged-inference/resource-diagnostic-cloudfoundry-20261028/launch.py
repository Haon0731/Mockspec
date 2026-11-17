"""One recorded diagnostic run; credentials stay in the process environment."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shlex
import sys


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "experiments/staged-inference")]


def write_json(path, value):
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write("\n")


def timestamp():
    return datetime.now(timezone.utc).isoformat()


def progress(event):
    event = {"at": timestamp(), **event}
    with (HERE / "progress.jsonl").open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(event, ensure_ascii=False) + "\n")
    print(json.dumps(event, ensure_ascii=False), flush=True)


def main():
    os.chdir(ROOT)
    preflight = json.loads((HERE / "preflight.json").read_text())
    if (HERE / "run").exists() or (HERE / "started.json").exists():
        raise RuntimeError("This one-shot run already started; refusing to retry or overwrite")
    for raw in (ROOT / ".env.local").read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.removeprefix("export ").split("=", 1)
        name = name.strip()
        if name not in {"OPENAI_API_KEY", "OPENAI_BASE_URL", "MOCKSPEC_MODEL"}:
            continue
        words = shlex.split(value, comments=True)
        if len(words) != 1:
            raise RuntimeError("Unsupported credential-file assignment")
        os.environ.setdefault(name, words[0])
    if not os.environ.get("OPENAI_API_KEY"):
        raise RuntimeError("Credential unavailable")
    if os.environ.get("OPENAI_BASE_URL", "").rstrip("/") != preflight["base_url"]:
        raise RuntimeError("Configured provider endpoint differs from preflight")
    os.environ["OPENAI_AGENTS_DISABLE_TRACING"] = "1"

    from provider_capture import ResponseCapture
    import run as staged_run

    (HERE / "http").mkdir()
    (HERE / "calls").mkdir()

    class JournaledCapture(ResponseCapture):
        def begin_call(self, sequence):
            super().begin_call(sequence)
            progress({"event": "model_call_started", "sequence": sequence})

        async def on_response(self, response):
            await super().on_response(response)
            sequence = self._active_sequence
            captures = self._captures[sequence]
            write_json(HERE / "http" / f"call-{sequence}-{len(captures)}.json", captures[-1])
            progress({"event": "http_response_retained", "sequence": sequence,
                      "http_status": captures[-1]["http_status"]})

        def finish_call(self, event, ledger, responses):
            try:
                super().finish_call(event, ledger, responses)
            finally:
                write_json(HERE / "calls" / f"call-{event['sequence']}.json", event)
                progress({"event": "model_call_finished", "sequence": event["sequence"],
                          "stage": event["stage"], "status": event["status"],
                          "elapsed_seconds": event["elapsed_seconds"],
                          "provider_usage": event["provider_usage"]})

    staged_run.ResponseCapture = JournaledCapture
    write_json(HERE / "started.json", {
        "at": timestamp(), "preflight_sha256": hashlib.sha256((HERE / "preflight.json").read_bytes()).hexdigest(),
        "launcher_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "credentials_present": True, "automatic_retries": 0,
    })
    code = staged_run.main(preflight["arguments"])
    write_json(HERE / "finished.json", {"at": timestamp(), "exit_code": code})
    return code


if __name__ == "__main__":
    raise SystemExit(main())
