"""Single reviewed case to an explicitly labelled scenario artifact."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
import sys
from typing import Any

from .adapters import java, python
from .scenario import validate_contract


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--language", choices=("java", "python"), required=True)
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--case-id", required=True)
    parser.add_argument("--model", default=os.environ.get("MOCKSPEC_MODEL"))
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument("--agent-version", choices=("v0.1", "v0.2"), default="v0.1")
    parser.add_argument("--max-turns", type=int, default=6, help="V0.2 only.")
    parser.add_argument("--max-tool-calls", type=int, default=8, help="V0.2 only.")
    parser.add_argument(
        "--response-json", type=Path,
        help="Validate a synthetic response offline; never reported as live inference.",
    )
    parser.add_argument("--output", type=Path, help="Default: JSON on stdout.")
    parser.add_argument(
        "--repository-read", action="store_true",
        help="V0.2: read additional lines of initially reviewed logical paths.",
    )
    args = parser.parse_args(argv)
    if args.repository_read and args.agent_version != "v0.2":
        parser.error("--repository-read requires --agent-version v0.2")
    source_store = None

    if args.response_json is None and not args.model:
        print("configuration error: set MOCKSPEC_MODEL or --model", file=sys.stderr)
        return 2

    loader = java.load_task if args.language == "java" else python.load_task
    try:
        if args.repository_read:
            from .validation import SourceStore
            adapter = java if args.language == "java" else python
            task, repository = adapter.load_task_with_repository(args.snapshot, case_id=args.case_id)
            source_store = SourceStore(task, repository=repository)
        else:
            task = loader(args.snapshot, case_id=args.case_id)
    except (ValueError, OSError):
        print("input error: reviewed snapshot/case validation failed", file=sys.stderr)
        return 2

    from .inference import InferenceError, PROMPT_VERSION, infer_scenario

    inference_kwargs: dict[str, Any] = {}
    if args.agent_version == "v0.2":
        from .budget import InferenceBudget
        from .inference_v02 import PROMPT_VERSION, infer_scenario
        try:
            inference_kwargs["budget"] = InferenceBudget(
                max_turns=args.max_turns, max_tool_calls=args.max_tool_calls,
            )
        except InferenceError as exc:
            print(f"configuration error: {exc}", file=sys.stderr)
            return 2
        inference_kwargs["tool_trace"] = []
        if source_store is not None:
            inference_kwargs["source_store"] = source_store

    try:
        if args.response_json is not None:
            # This file is a host-side testing input, never sent to the model.
            contract = validate_contract(args.response_json.read_text(encoding="utf-8"), task)
        else:
            contract = asyncio.run(infer_scenario(
                task, model=args.model, timeout_seconds=args.timeout,
                **inference_kwargs,
            ))
    except InferenceError as exc:
        print(f"inference error: {exc}", file=sys.stderr)
        return 2
    except (ValueError, OSError):
        print("invalid contract: response structure or allowed evidence check failed",
              file=sys.stderr)
        return 2

    artifact = {
        "mode": "synthetic_replay" if args.response_json is not None else "live_inference",
        "model": None if args.response_json is not None else args.model,
        "prompt_version": PROMPT_VERSION,
        "validation": "schema_and_evidence_locations_passed",
        "semantic_correctness_verified": False,
        "contract": contract.model_dump(),
    }
    if args.agent_version == "v0.2":
        artifact["tool_trace"] = inference_kwargs["tool_trace"]
    text = json.dumps(artifact, ensure_ascii=False, indent=2) + "\n"
    try:
        if args.output is None:
            sys.stdout.write(text)
        else:
            # Refuse to overwrite previous evidence or a source file.
            with args.output.open("x", encoding="utf-8") as stream:
                stream.write(text)
    except OSError:
        print("output error: cannot create output file (existing files are preserved)",
              file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
