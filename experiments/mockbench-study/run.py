"""Explicit inventory and offline syntax-baseline commands for MockBench research."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from inventory import build_inventory, write_new_json
from mock_spec.validation import decode_json


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    inventory = commands.add_parser("inventory", help="read metadata only; no source admission")
    inventory.add_argument("--registry-root", required=True, type=Path)
    inventory.add_argument("--output", required=True, type=Path)
    inventory.add_argument("--input-policy", choices=("frozen-agent", "reviewed"), default="frozen-agent")
    intake = commands.add_parser("check-inputs", help="load every frozen input; no model or analysis")
    intake.add_argument("--inventory", required=True, type=Path)
    intake.add_argument("--output-dir", required=True, type=Path)
    batch = commands.add_parser("batch", help="run existing syntax tools on reviewed candidates")
    batch.add_argument("--inventory", required=True, type=Path)
    batch.add_argument("--output-dir", required=True, type=Path)
    batch.add_argument("--timeout-seconds", type=float, default=60)
    batch.add_argument("--limit", type=int)
    args = parser.parse_args(argv)
    try:
        if args.command == "inventory":
            result = build_inventory(args.registry_root, input_policy=args.input_policy)
            write_new_json(args.output, result)
            print(json.dumps(result["summary"], ensure_ascii=False))
        elif args.command == "check-inputs":
            from batch import check_inputs
            result = check_inputs(decode_json(args.inventory.read_text()), args.output_dir)
            print(json.dumps(result, ensure_ascii=False))
            return 0 if result["counts"]["failed"] == 0 else 1
        else:
            from batch import run_batch
            result = run_batch(decode_json(args.inventory.read_text()), args.output_dir,
                               timeout_seconds=args.timeout_seconds, limit=args.limit)
            print(json.dumps(result, ensure_ascii=False))
    except (ValueError, OSError) as exc:
        parser.exit(2, f"{type(exc).__name__}: {exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
