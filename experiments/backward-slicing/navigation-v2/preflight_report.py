"""Join evaluator and prompt receipts for the paired MockBench-165 preflight."""

from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from math import ceil
from pathlib import Path


def _csv_rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream))


def write_report(root: Path) -> None:
    evaluations = _csv_rows(root / "eval-manifest.csv")
    windows = _csv_rows(root / "windows.csv")
    package = json.loads((root / "prompt-package/prompt-manifest.json").read_bytes())
    environment = json.loads((root / "controller-check.json").read_bytes())
    model = json.loads((root / "window-summary.json").read_bytes())
    if environment["status"] != "PASS" or environment["network"] != "none":
        raise ValueError("container preflight failed")
    prompts = {case["case_id"]: case for case in package["cases"]}
    cells = {(w["case_id"], w["arm"]): w for w in windows}
    ids = {e["case_id"] for e in evaluations}
    if not (len(evaluations) == len(prompts) == len(ids) == len(package["cases"]) == 165
            and ids == prompts.keys()
            and len(windows) == len(cells) == 330
            and set(cells) == {(i, arm) for i in ids for arm in ("base", "v2")}):
        raise ValueError("paired case membership differs")
    rows = []
    for entry in evaluations:
        case = prompts[entry["case_id"]]
        if (entry["dataset"], entry["generation_snapshot_id"], entry["navigation_view_sha256"]) != (
            case["dataset"], case["snapshot_id"], case["bounded_view_sha256"]
        ):
            raise ValueError(f"case identity differs: {entry['case_id']}")
        row = {**entry, "cut_files": case["permitted_cut_source_count"]}
        reasons = []
        if entry["input_alignment_status"] != "matched":
            reasons.append("evaluation_input_mismatch")
        if row["cut_files"] == 0:
            reasons.append("no_preloaded_cut")
        for arm in ("base", "v2"):
            w = cells[entry["case_id"], arm]
            if (w["prompt_sha256"] != case["arms"][arm]["prompt_sha256"] or
                    int(w["prompt_bytes"]) != case["arms"][arm]["prompt_bytes"]):
                raise ValueError("window measured on a different prompt")
            total = int(w["chat_input_tokens"]) + int(w["output_reserve_tokens"])
            remaining = int(w["context_tokens"]) - total
            if (total != int(w["combined_tokens"]) or remaining != int(w["headroom_tokens"])
                    or (remaining >= 0) != (w["fits_context"] == "True")):
                raise ValueError("inconsistent token ledger")
            if remaining < 0:
                reasons.append(arm + "_window_overflow")
            for field in ("prompt_sha256", "chat_input_tokens", "combined_tokens", "headroom_tokens"):
                row[f"{arm}_{field}"] = w[field]
        row["phase_one_status"] = ";".join(reasons) or "pass"
        rows.append(row)
    with (root / "execution-manifest.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)

    blocked = [r for r in rows if r["phase_one_status"] != "pass"]
    counts = Counter(w["arm"] for w in windows if w["fits_context"] == "True")
    lines = [
        "# MockBench-165 配对实验 · 第一步体检",
        "",
        f"阶段一门禁通过 {165-len(blocked)}/165 例。每例均使用与冻结评估模板对齐的导航身份与坐标。"
        "批量生成尚未启动，模型调用为 0。",
        "",
        "## 两组组合窗口",
        "",
        f"模型 {model['model']['id']}（{model['model']['version']}），上下文 "
        f"{model['context_tokens']:,} tokens，每次输出预留 {model['output_reserve_tokens']:,} tokens。"
        "每行统计完整聊天输入加输出预留，两组采用同一代码块槽位协议。",
        "按官方消息模板编码一条 user 消息（thinking/high），包括助手生成头。源文件在首个请求中提供；"
        "本表覆盖固定单轮输入，不含额外聊天历史或后续工具消息。",
        "",
        "| 条件 | 符合预算 | 中位数 | P95 | 最大组合窗口 | 最少余量 |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for arm in ("base", "v2"):
        totals = sorted(int(w["combined_tokens"]) for w in windows if w["arm"] == arm)
        lines.append(f"| {arm} | {counts[arm]}/165 | {totals[82]:,} | "
                     f"{totals[ceil(0.95*len(totals))-1]:,} | {totals[-1]:,} | "
                     f"{model['context_tokens']-totals[-1]:,} |")
    lines += [
        "",
        "## 评估入口与隔离",
        "",
        "Java 59 例、Python 106 例。逐例清单绑定参考测试位置与摘要、固定容器、"
        "候选评估入口和预登记变异体 ID。两组共用这批 "
        f"{sum(int(r['preregistered_mutant_count']) for r in rows)} 个变异体。"
        "参考测试与变异体仅在候选冻结后供评估器读取。"
        "离线控制器 check 通过，两种语言的镜像分别启动 javac 11.0.31 和 pytest 9.1.1。"
        "候选登记、干净原程序测试及变异执行属于第二步。",
        "",
        "## 尚待解决的案例",
        "",
        "| 案例 | 原因 |",
        "|---|---|",
    ]
    lines += (
        [f"| {r['case_id']} | {r['phase_one_status']} |" for r in blocked]
        if blocked
        else ["| 无 | 165 例全部通过 |"]
    )
    lines += [
        "",
        "快照、masked test、导航 view 与评估输入的逐例证据见[评估报告](evaluation-report.md)。"
        "两组各自的聊天输入、预留与余量见[窗口明细](windows.csv)。"
        "完整联接见[执行清单](execution-manifest.csv)，环境回执见[控制器检查](controller-check.json)。",
    ]
    (root / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output_root", type=Path)
    write_report(parser.parse_args().output_root)
