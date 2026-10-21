"""Evidence-preserving live-only CLI. No replay input or model tools."""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import hashlib
from importlib.metadata import version
import json
import math
import os
from pathlib import Path
import sys

from .adapters import java, python
from .inference import InferenceError, PROMPT_VERSION, _PROMPT_PATH, infer_scenario


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--language", choices=("java", "python"), required=True)
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--case-id", required=True)
    parser.add_argument("--model", default=os.environ.get("MOCKSPEC_MODEL"))
    parser.add_argument("--timeout", type=float, default=120)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    args.model = (args.model or "").strip()
    # Fail before creating artifacts or invoking a provider. Never read credential files.
    if not args.model or not os.environ.get("OPENAI_API_KEY", "").strip():
        print("configuration error: MOCKSPEC_MODEL/--model and OPENAI_API_KEY required",
              file=sys.stderr)
        return 2
    if not math.isfinite(args.timeout) or args.timeout <= 0:
        print("configuration error: timeout must be finite and positive", file=sys.stderr)
        return 2
    try:
        loader = java.load_task if args.language == "java" else python.load_task
        task = loader(args.snapshot, case_id=args.case_id)
    except (ValueError, OSError):
        print("input/output error: reviewed input and new output directory required",
              file=sys.stderr)
        return 2

    payload = json.dumps(task.to_dict(), ensure_ascii=False, separators=(",", ":"))
    artifact = {
        "mode": "live_inference",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "model": args.model,
        "resolved_model_version": None,
        "prompt_version": PROMPT_VERSION,
        "prompt_sha256": hashlib.sha256(_PROMPT_PATH.read_bytes()).hexdigest(),
        "task_schema_version": task.schema_version,
        "contract_schema_version": "mock-spec.scenario/v0.1",
        "case_id": task.case_id,
        "language": task.language,
        "snapshot_id": args.snapshot.name,
        "task_sha256": hashlib.sha256(payload.encode()).hexdigest(),
        "packages": {p: version(p) for p in ("openai-agents", "openai", "pydantic")},
        "parameters": {
            "timeout_seconds": args.timeout, "max_turns": 1,
            "store": False, "tracing_disabled": True,
            "sampling": "SDK/provider defaults; no override",
            "transport_retries": "SDK defaults; no application-level retry",
        },
        "raw_responses": [],
        "validation": "not_run",
        "semantic_review": "pending",
        "reference_answer_evaluation": "not_performed",
        "contract": None,
    }
    try:
        args.output_dir.mkdir(parents=True, exist_ok=False)
    except OSError:
        print("output error: new output directory required", file=sys.stderr)
        return 2
    try:
        contract = asyncio.run(infer_scenario(
            task, model=args.model, timeout_seconds=args.timeout,
            responses=artifact["raw_responses"],
        ))
        artifact["contract"] = contract.model_dump()
        artifact["validation"] = "schema_and_evidence_locations_passed"
        artifact["status"] = "completed"
        code = 0
    except InferenceError as exc:
        artifact["status"] = exc.category
        artifact["diagnostic_type"] = exc.diagnostic
        if exc.category in ("structure_error", "evidence_error"):
            artifact["validation"] = exc.category
        code = 2
    artifact["finished_at"] = datetime.now(timezone.utc).isoformat()
    try:
        text = json.dumps(artifact, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
        with (args.output_dir / "result.json").open("x", encoding="utf-8") as stream:
            stream.write(text)
    except (OSError, ValueError, TypeError):
        print("output error: could not persist run evidence", file=sys.stderr)
        return 2
    return code


if __name__ == "__main__":
    raise SystemExit(main())
