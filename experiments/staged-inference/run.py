"""CLI for the M1 variant-B/C synthetic replay."""

from __future__ import annotations

import argparse
import asyncio
from decimal import Decimal, InvalidOperation
import os
from pathlib import Path
import sys

from controller import StagedRunConfig, execute_staged_run
from agents.models.interface import Model
from mock_spec.validation import decode_json
from scripted import FixedScriptModel, query_script, task_for_replay_case


def _positive_decimal(value: str) -> Decimal:
    try:
        parsed = Decimal(value)
    except InvalidOperation:
        raise argparse.ArgumentTypeError("must be a decimal") from None
    if not parsed.is_finite() or parsed <= 0:
        raise argparse.ArgumentTypeError("must be positive")
    return parsed


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=["synthetic_replay", "live_inference"], required=True)
    parser.add_argument("--variant", choices=["B", "C"], required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--task-json", type=Path)
    parser.add_argument("--snapshot", type=Path)
    parser.add_argument("--case-id")
    parser.add_argument("--model", default=os.environ.get("MOCKSPEC_MODEL"))
    parser.add_argument(
        "--repository-read", action="store_true",
        help="bind reviewed whole files for live read-observe-cite exploration",
    )
    parser.add_argument(
        "--replay-case",
        choices=[
            "selected", "ambiguous", "inconclusive", "error", "cache_miss",
            "cache_unspecified", "legal_variation", "second_revision",
        ],
        default="selected",
        help="fixed result path to replay",
    )
    parser.add_argument(
        "--requested-case-id",
        help="case identity requested before input review; retained when loading fails",
    )
    parser.add_argument("--query-count", type=int, default=0,
                        help="fixed replay query attempts (legacy replay still enforces its configured limit)")
    parser.add_argument(
        "--max-model-calls", type=int, default=10,
        help="legacy replay limit; repository exploration stops on shared resource budgets",
    )
    parser.add_argument("--max-output-tokens", type=int, default=4096)
    parser.add_argument("--max-total-tokens", type=int, default=60000)
    parser.add_argument("--max-tool-output-chars", type=int, default=16000)
    parser.add_argument("--timeout-seconds", type=float, default=120)
    parser.add_argument("--parsing-timeout-seconds", type=float, default=15)
    parser.add_argument("--max-cost-usd", type=_positive_decimal)
    parser.add_argument("--input-cost-per-million", type=_positive_decimal)
    parser.add_argument("--output-cost-per-million", type=_positive_decimal)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    model: Model
    try:
        if args.mode == "live_inference":
            if args.task_json is not None:
                raise ValueError("live inference does not accept --task-json")
            if args.snapshot is None or not args.case_id:
                raise ValueError("live inference requires --snapshot and --case-id")
            if args.case_id != "flask_308":
                raise ValueError("live inference is limited to the reviewed Python case flask_308")
            if not (args.model or "").strip() or not os.environ.get("OPENAI_API_KEY", "").strip():
                raise ValueError("live inference requires model and OPENAI_API_KEY")
            from agents.models.openai_responses import OpenAIResponsesModel
            from openai import AsyncOpenAI

            model = OpenAIResponsesModel(
                model=args.model.strip(), openai_client=AsyncOpenAI(max_retries=0)
            )
            requested_case_id = args.case_id
            model_name = args.model.strip()
            load_task = None
        else:
            if args.snapshot is not None or args.case_id is not None or args.repository_read:
                raise ValueError(
                    "synthetic replay does not accept --snapshot, --case-id, or --repository-read"
                )

            def load_task() -> object:
                if args.task_json is None:
                    return task_for_replay_case(args.replay_case)
                return decode_json(args.task_json.read_text(encoding="utf-8"))

            model = FixedScriptModel(query_script(
                args.query_count, args.replay_case, variant=args.variant
            ))
            requested_case_id = args.requested_case_id
            if requested_case_id is None and args.task_json is None:
                requested_case_id = task_for_replay_case(args.replay_case)["case_id"]
            model_name = "scripted-offline"

        result = asyncio.run(execute_staged_run(
            output_dir=args.output_dir,
            model=model,
            config=StagedRunConfig(
                variant=args.variant, run_mode=args.mode, model_name=model_name,
                repository_read=args.repository_read,
                max_model_calls=args.max_model_calls,
                max_output_tokens=args.max_output_tokens,
                max_total_tokens=args.max_total_tokens,
                max_tool_output_chars=args.max_tool_output_chars,
                timeout_seconds=args.timeout_seconds,
                parsing_timeout_seconds=args.parsing_timeout_seconds,
                max_cost_usd=args.max_cost_usd,
                input_cost_per_million=args.input_cost_per_million,
                output_cost_per_million=args.output_cost_per_million,
            ),
            requested_case_id=requested_case_id,
            task_loader=load_task,
            reviewed_snapshot=(args.snapshot if args.mode == "live_inference" else None),
        ))
    except (OSError, ValueError, FileExistsError) as exc:
        print(f"staged inference error: {exc}", file=sys.stderr)
        return 2
    status = result["manifest"]["result_status"]
    print(status)
    if status == "ERROR":
        print("staged inference failed; inspect the run manifest", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
