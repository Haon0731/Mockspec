"""Freeze the Issue #37 generation configuration and estimate its run budget."""

from __future__ import annotations

import argparse
import csv
import glob
import hashlib
import json
import math
from pathlib import Path
from statistics import mean, median
from typing import Any, Sequence


CALLS_PER_ARM = 165
ARMS = ("base", "v2")
CONCURRENCY = 6
OUTPUT_RESERVE = 32_768
WALL_SECONDS = 600


def _quantile(values: Sequence[float], quantile: float) -> float:
    ordered = sorted(values)
    return ordered[math.ceil(len(ordered) * quantile) - 1]


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _historical_receipts(trace_glob: str) -> dict[str, Any]:
    traces = [Path(path) for path in glob.glob(trace_glob)]
    if len(traces) != 42:
        raise ValueError(f"expected 42 tagged-study traces, found {len(traces)}")
    elapsed: list[float] = []
    final_output: dict[str, list[int]] = {arm: [] for arm in ARMS}
    for path in traces:
        arm = path.parent.name
        if arm not in final_output:
            raise ValueError(f"unexpected historical arm: {path}")
        trace = json.loads(path.read_bytes())
        elapsed.append(float(trace["elapsed_seconds"]))
        turns = trace.get("turns")
        if not isinstance(turns, list) or not turns or not turns[-1].get("usage"):
            raise ValueError(f"historical trace lacks final usage: {path}")
        final_output[arm].append(int(turns[-1]["usage"]["output_tokens"]))
    return {
        "trace_count": len(traces),
        "elapsed_seconds": {
            "mean": mean(elapsed),
            "median": median(elapsed),
            "p95": _quantile(elapsed, 0.95),
            "max": max(elapsed),
        },
        "final_response_output_tokens": {
            arm: {
                "mean": mean(final_output[arm]),
                "median": median(final_output[arm]),
                "p95": _quantile(final_output[arm], 0.95),
                "max": max(final_output[arm]),
            }
            for arm in ARMS
        },
    }


def _window_totals(path: Path) -> dict[str, int]:
    with path.open(encoding="utf-8", newline="") as stream:
        rows = list(csv.DictReader(stream))
    if len(rows) != 330:
        raise ValueError(f"expected 330 window rows, found {len(rows)}")
    membership = {(row["case_id"], row["arm"]) for row in rows}
    if len(membership) != 330 or any(row["fits_context"] != "True" for row in rows):
        raise ValueError("window ledger is incomplete or contains an overflow")
    totals = {
        arm: sum(int(row["chat_input_tokens"]) for row in rows if row["arm"] == arm)
        for arm in ARMS
    }
    if any(sum(row["arm"] == arm for row in rows) != CALLS_PER_ARM for arm in ARMS):
        raise ValueError("window ledger does not contain 165 rows per arm")
    return totals


def prepare(windows: Path, output: Path, trace_glob: str) -> None:
    output.mkdir(parents=True, exist_ok=True)
    history = _historical_receipts(trace_glob)
    input_tokens = _window_totals(windows)
    output_mean = {
        arm: round(
            history["final_response_output_tokens"][arm]["mean"] * CALLS_PER_ARM
        )
        for arm in ARMS
    }
    output_p95 = {
        arm: round(
            history["final_response_output_tokens"][arm]["p95"] * CALLS_PER_ARM
        )
        for arm in ARMS
    }
    waves = math.ceil((CALLS_PER_ARM * len(ARMS)) / CONCURRENCY)
    duration = history["elapsed_seconds"]
    budget: dict[str, Any] = {
        "schema_version": "mockspec-issue37-run-budget/v1",
        "call_count": 330,
        "calls_per_arm": 165,
        "windows_sha256": _sha256(windows),
        "input_tokens": {**input_tokens, "total": sum(input_tokens.values())},
        "estimated_output_tokens": {
            "empirical_mean": {**output_mean, "total": sum(output_mean.values())},
            "empirical_p95_per_call": {
                **output_p95,
                "total": sum(output_p95.values()),
            },
            "reserved_upper_bound": OUTPUT_RESERVE * CALLS_PER_ARM * len(ARMS),
        },
        "estimated_total_tokens": {
            "empirical_mean": sum(input_tokens.values()) + sum(output_mean.values()),
            "empirical_p95_per_call": sum(input_tokens.values()) + sum(output_p95.values()),
            "reserved_upper_bound": (
                sum(input_tokens.values())
                + OUTPUT_RESERVE * CALLS_PER_ARM * len(ARMS)
            ),
        },
        "estimated_generation_time_seconds": {
            "mean_throughput": 330 * duration["mean"] / CONCURRENCY,
            "median_throughput": 330 * duration["median"] / CONCURRENCY,
            "p95_per_call_planning": 330 * duration["p95"] / CONCURRENCY,
            "timeout_upper_bound": waves * WALL_SECONDS,
        },
        "estimate_basis": {
            "historical_receipts": history,
            "notes": [
                "Output estimates use the final response of the 42-call tagged study; reasoning tokens are included.",
                "The new run is tool-free and emits tagged slots, so historical output is a conservative proxy rather than a guarantee.",
                "Elapsed estimates assume six continuously occupied request slots and exclude evaluation time.",
            ],
        },
    }
    config = {
        "schema_version": "mockspec-issue37-generation-config/v1",
        "experiment": {
            "arms": list(ARMS),
            "cases_per_arm": CALLS_PER_ARM,
            "generations_per_case_arm": 1,
            "total_calls": CALLS_PER_ARM * len(ARMS),
            "pairing": "same case input and output contract; v2 adds only the bounded navigation view",
        },
        "model": {
            "api_id": "deepseek-flash",
            "service_name_required": "DeepSeek-V4.1-Flash",
            "intended_release": "DeepSeek-V4.1-Flash-1017",
            "immutable_service_build_id_available": False,
            "context_window_required": 1_048_576,
            "max_output_tokens_required_at_least": OUTPUT_RESERVE,
            "effort_levels_required": ["low", "high", "max"],
            "drift_gate": (
                "fetch model metadata immediately before launch and stop unless id, name, "
                "context window, output limit, and effort levels match; record every response.model"
            ),
        },
        "request": {
            "api": "Responses API",
            "reasoning": {"effort": "high"},
            "temperature": "omitted; ineffective in thinking mode",
            "top_p": 1.0,
            "seed": "omitted; the Responses API does not expose a seed parameter",
            "presence_penalty": "omitted; ineffective in thinking mode",
            "frequency_penalty": "omitted; ineffective in thinking mode",
            "max_output_tokens": OUTPUT_RESERVE,
            "truncation": "disabled",
            "store": False,
            "tools": "omitted; all approved CUT sources are preloaded",
            "text_format": "plain text containing exactly one fenced text block",
        },
        "execution": {
            "concurrency": CONCURRENCY,
            "wall_seconds_per_call": WALL_SECONDS,
            "sdk_retries": 0,
            "application_retries": 0,
            "failure_policy": (
                "retain timeout, transport, format, and generation failures in the fixed "
                "denominator; do not replace or rerun a failed cell"
            ),
            "scheduling": (
                "counterbalance Base/v2 order by stable case index while keeping no more "
                "than six calls active"
            ),
        },
        "official_evidence": {
            "model_metadata": "https://api-docs.deepseek.com/api/list-models/",
            "thinking_parameters": "https://api-docs.deepseek.com/guides/thinking_mode/",
            "responses_parameters": "https://api-docs.deepseek.com/api/create-response/",
            "release": "https://api-docs.deepseek.com/updates/?release=releasing-the-full-suite-of-application-apis",
            "weights_and_sampling": "https://huggingface.co/deepseek-ai/DeepSeek-V4.1-Flash-1017",
        },
    }
    (output / "generation-config.json").write_text(
        json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (output / "run-budget.json").write_text(
        json.dumps(budget, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    mean_minutes = budget["estimated_generation_time_seconds"]["mean_throughput"] / 60
    p95_minutes = (
        budget["estimated_generation_time_seconds"]["p95_per_call_planning"] / 60
    )
    report = f"""# 330 次正式生成配置与预算

## 运行配置

| 项目 | 冻结值 |
|---|---|
| 模型请求 ID | `deepseek-flash` |
| 要求的服务模型名 | `DeepSeek-V4.1-Flash` |
| 目标发布版 | `DeepSeek-V4.1-Flash-1017` |
| 推理模式 | thinking，`reasoning_effort=high` |
| temperature | 省略；thinking 模式下无效 |
| top_p | `1.0` |
| seed | 省略；Responses API 未提供该字段 |
| 输出上限 | 32,768 tokens |
| 每例生成次数 | Base 1 次、v2 1 次，共 330 次 |
| 并发度 | 6 |
| 单次墙钟上限 | 600 秒 |
| 重试 | SDK 0 次，应用 0 次；失败原样进入固定分母 |
| 工具 | 不发送；获准 CUT 已放入首个请求 |
| 存储与截断 | `store=false`，`truncation=disabled` |

API 的 `deepseek-flash` 是服务别名。服务端没有公开不可变权重构建 ID，因而不能用请求参数对 1017 权重做密码学固定。启动前必须读取模型 metadata，核对 ID、服务模型名、上下文、输出上限和 effort 列表；任一漂移立即停止。每个响应保留 `response.model`、response ID 和 usage。

## Token 与耗时估算

| 口径 | 输入 | 输出 | 合计 |
|---|---:|---:|---:|
| 经验均值 | {sum(input_tokens.values()):,} | {sum(output_mean.values()):,} | {budget['estimated_total_tokens']['empirical_mean']:,} |
| 历史逐调用 P95 | {sum(input_tokens.values()):,} | {sum(output_p95.values()):,} | {budget['estimated_total_tokens']['empirical_p95_per_call']:,} |
| 32,768 全额预留上界 | {sum(input_tokens.values()):,} | {budget['estimated_output_tokens']['reserved_upper_bound']:,} | {budget['estimated_total_tokens']['reserved_upper_bound']:,} |

输入量来自 330 行真实 tokenizer 账本。输出代理来自既有 42 次同模型 Base/v2 标签实验最终响应，包含 reasoning tokens。按并发 6，经验均值预计 API 生成约 {mean_minutes:.1f} 分钟；用历史单次 P95 规划约 {p95_minutes:.1f} 分钟。建议排期按 45–90 分钟，并另留候选落盘与后续容器评估时间。全部 55 个并发波次均触发 600 秒上限时，生成阶段的理论上界为 550 分钟。

## 估算边界

- 新运行不使用工具、只返回有序代码槽位。历史实验包含工具轮次，因此输出和耗时只作为保守代理。
- 模型服务排队和瞬时吞吐可能改变墙钟时间。
- 此处不含候选编译、原程序测试和 382 个变异体的运行时间。

官方依据：[Models API](https://api-docs.deepseek.com/api/list-models/) · [Thinking mode](https://api-docs.deepseek.com/guides/thinking_mode/) · [Responses API](https://api-docs.deepseek.com/api/create-response/) · [1017 发布仓库](https://huggingface.co/deepseek-ai/DeepSeek-V4.1-Flash-1017)
"""
    (output / "generation-plan.md").write_text(report, encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--windows", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument(
        "--trace-glob",
        default="experiments/backward-slicing/navigation-v2/tagged/run/*/*/*/trace.json",
    )
    args = parser.parse_args()
    prepare(args.windows, args.output, args.trace_glob)


if __name__ == "__main__":
    main()
