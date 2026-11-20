"""Measure paired prompts with the pinned DeepSeek V4.1 chat encoding."""

from __future__ import annotations

import argparse
import csv
import hashlib
import importlib
import importlib.util
import json
import math
from pathlib import Path
import statistics
from typing import Any, Callable


MODEL_ID = "deepseek-flash"
MODEL_VERSION = "DeepSeek-V4.1-Flash-1017"
CONTEXT_TOKENS = 1_048_576
OUTPUT_RESERVE_TOKENS = 32_768
TOKENIZER_SHA256 = "c90dfa01249db1be4245780a052ede752e1361c612ac6d08e2bdada7d599476b"
ENCODING_SHA256 = "13231c592e958fb39e2af6001dae69d90e7a36b265702b9b390236fb387599e2"
THINKING_MODE = "thinking"
REASONING_EFFORT = "high"
MODEL_METADATA_URL = "https://api-docs.deepseek.com/api/list-models/"
TOKENIZER_REVISION = "1bae0e1088993b9b82de08f59de5be5a34e42170"
TOKENIZER_URL = (
    "https://huggingface.co/deepseek-ai/DeepSeek-V4.1-Flash-1017/resolve/"
    f"{TOKENIZER_REVISION}/tokenizer.json"
)
ENCODING_URL = (
    "https://huggingface.co/deepseek-ai/DeepSeek-V4.1-Flash-1017/resolve/"
    f"{TOKENIZER_REVISION}/encoding/encoding.py"
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_encoding(path: Path) -> Any:
    spec = importlib.util.spec_from_file_location("deepseek_v41_encoding", path)
    if spec is None or spec.loader is None:
        raise ValueError(f"cannot load encoding module: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _deepseek_counter(
    tokenizer_path: Path, encoding_path: Path
) -> Callable[[str], int]:
    try:
        tokenizer_module = importlib.import_module("tokenizers")
    except ImportError as error:
        raise RuntimeError("the tokenizers package is required") from error

    if _sha256(tokenizer_path) != TOKENIZER_SHA256 or _sha256(encoding_path) != ENCODING_SHA256:
        raise ValueError("tokenizer or chat encoder differs from the pinned release")
    tokenizer = tokenizer_module.Tokenizer.from_file(str(tokenizer_path))
    encoding = _load_encoding(encoding_path)

    def count(prompt: str) -> int:
        rendered = encoding.encode_messages(
            [{"role": "user", "content": prompt}],
            thinking_mode=THINKING_MODE,
            reasoning_effort=REASONING_EFFORT,
        )
        return len(tokenizer.encode(rendered, add_special_tokens=False).ids)

    return count


def measure_prompts(
    manifest: dict[str, Any],
    prompt_root: Path,
    count_prompt: Callable[[str], int],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    cases = manifest.get("cases")
    if not isinstance(cases, list) or len(cases) != 165:
        raise ValueError("prompt manifest must contain exactly 165 cases")
    for case in cases:
        for arm in ("base", "v2"):
            prompt = prompt_root / f"{case['token']}.{arm}.txt"
            data = prompt.read_bytes()
            receipt = case["arms"][arm]
            if hashlib.sha256(data).hexdigest() != receipt["prompt_sha256"]:
                raise ValueError(f"prompt hash mismatch: {case['case_id']}/{arm}")
            prompt_tokens = count_prompt(data.decode("utf-8"))
            total_tokens = prompt_tokens + OUTPUT_RESERVE_TOKENS
            rows.append(
                {
                    "case_id": case["case_id"],
                    "dataset": case["dataset"],
                    "language": case["language"],
                    "arm": arm,
                    "prompt_sha256": receipt["prompt_sha256"],
                    "prompt_bytes": len(data),
                    "chat_input_tokens": prompt_tokens,
                    "output_reserve_tokens": OUTPUT_RESERVE_TOKENS,
                    "combined_tokens": total_tokens,
                    "context_tokens": CONTEXT_TOKENS,
                    "headroom_tokens": CONTEXT_TOKENS - total_tokens,
                    "fits_context": total_tokens <= CONTEXT_TOKENS,
                }
            )
    return rows


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _write_summary(
    path: Path,
    rows: list[dict[str, Any]],
    tokenizer_path: Path,
    encoding_path: Path,
) -> None:
    def distribution(arm: str) -> dict[str, int | float]:
        values = sorted(
            row["chat_input_tokens"] for row in rows if row["arm"] == arm
        )
        return {
            "min": values[0],
            "median": statistics.median(values),
            "p95": values[math.ceil(0.95 * len(values)) - 1],
            "max": values[-1],
        }

    maxima = {
        arm: max((row for row in rows if row["arm"] == arm), key=lambda row: row["combined_tokens"])
        for arm in ("base", "v2")
    }
    summary = {
        "model": {
            "id": MODEL_ID,
            "version": MODEL_VERSION,
            "metadata_url": MODEL_METADATA_URL,
        },
        "context_tokens": CONTEXT_TOKENS,
        "output_reserve_tokens": OUTPUT_RESERVE_TOKENS,
        "chat_encoding": {
            "thinking_mode": THINKING_MODE,
            "reasoning_effort": REASONING_EFFORT,
            "revision": TOKENIZER_REVISION,
            "tokenizer_url": TOKENIZER_URL,
            "encoding_url": ENCODING_URL,
            "tokenizer_sha256": _sha256(tokenizer_path),
            "encoding_sha256": _sha256(encoding_path),
        },
        "rows": len(rows),
        "cases": len({row["case_id"] for row in rows}),
        "all_fit": all(row["fits_context"] for row in rows),
        "input_token_distribution": {
            arm: distribution(arm) for arm in ("base", "v2")
        },
        "maxima": maxima,
    }
    path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--prompt-root", required=True, type=Path)
    parser.add_argument("--tokenizer-json", required=True, type=Path)
    parser.add_argument("--encoding-py", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()

    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    counter = _deepseek_counter(args.tokenizer_json, args.encoding_py)
    rows = measure_prompts(manifest, args.prompt_root, counter)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    _write_csv(args.output_dir / "windows.csv", rows)
    _write_summary(
        args.output_dir / "window-summary.json",
        rows,
        args.tokenizer_json,
        args.encoding_py,
    )


if __name__ == "__main__":
    main()
