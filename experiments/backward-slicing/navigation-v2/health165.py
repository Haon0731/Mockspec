"""Project a frozen 165-case backward-analysis run and report static health.

This driver reads only the reviewed frozen registry and an existing batch-slice
result.  It does not execute target tests, loaders, generators, or model calls.
"""

from __future__ import annotations

import argparse
from collections import Counter
from collections.abc import Callable
import hashlib
import importlib
import json
import math
from pathlib import Path
import statistics
from typing import Any

from mock_spec.adapters.frozen_dataset import FrozenDataset
from mock_spec.bounded_navigation_view import (
    build_bounded_navigation_view,
    measure_bounded_navigation_view,
    serialize_bounded_navigation_view,
)
from mock_spec.navigation_facts import project_navigation_facts
from mock_spec.validation import InputValidationError


SCHEMA_VERSION = "mock-spec.navigation-v2-health/v1"


def _read(path: Path) -> Any:
    return json.loads(path.read_bytes())


def _encoded(value: Any) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode()
        + b"\n"
    )


def _write_new(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream:
        stream.write(_encoded(value))


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _write_delivery(
    output: Path, key: str, facts: dict[str, Any],
    count_tokens: Callable[[bytes], int], counter_name: str,
) -> dict[str, Any]:
    case_dir = output / "cases"
    case_dir.mkdir(exist_ok=True)
    archive = case_dir / f"{key}.facts.json"
    payload = _encoded(facts)
    with archive.open("xb") as stream:
        stream.write(payload)
    digest = _sha256(payload)
    view = build_bounded_navigation_view(
        facts, archive_sha256=digest, archive_locator=archive.name,
        token_counter=count_tokens, token_counter_name=counter_name,
    )
    delivery = serialize_bounded_navigation_view(view)
    measurement = measure_bounded_navigation_view(
        view, token_counter=count_tokens, token_counter_name=counter_name,
    )
    tokens = measurement["tokens"]
    if tokens > 8192 or measurement["serialized_bytes"] != len(delivery):
        raise ValueError("serialized view failed token budget verification")
    target = case_dir / f"{key}.view.json"
    with target.open("xb") as stream:
        stream.write(delivery)
    return {
        "facts_path": str(archive.relative_to(output)), "facts_sha256": digest,
        "facts_bytes": len(payload), "facts_tokens": count_tokens(payload),
        "estimated_tokens": math.ceil(len(payload) / 4),
        "view_path": str(target.relative_to(output)), "view_sha256": _sha256(delivery),
        "view_bytes": len(delivery), "view_tokens": tokens,
    }


def _nearest_rank(values: list[int], percentile: float) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[math.ceil(percentile * len(ordered)) - 1]


def _distribution(values: list[int]) -> dict[str, float | int | None]:
    return {
        "median": statistics.median(values) if values else None,
        "p95_nearest_rank": _nearest_rank(values, 0.95),
        "max": max(values) if values else None,
    }


def _observation_timeouts(boundaries: dict[str, Any]) -> list[str]:
    timeout_reasons = (
        "WALA observation timeout", "generic Java backend total deadline exhausted before observation",
    )
    return [row["scope"] for row in boundaries["unanalysed_regions"]
            if row.get("reason", "").startswith(timeout_reasons)]


def _unknown_reasons(contract: dict[str, Any], facts: dict[str, Any]) -> list[str]:
    reasons = set()
    if facts["entry_binding"]["status"] != "unique":
        reasons.add("entry_not_unique")
    if any(call["structural_role"] == "unresolved_call" for call in facts["calls"]):
        reasons.add("unresolved_call")
    if any(
        effect["reaches_assertion"] is None
        for call in facts["calls"]
        for effect in call["effects"]
    ):
        reasons.add("effect_reachability_unknown")
    boundaries = facts["analysis_boundaries"]
    if boundaries["unknown_effects"]:
        reasons.add("unknown_effect_boundary")
    if boundaries["unanalysed_regions"]:
        reasons.add("unanalysed_region")
    if boundaries["assumptions"]:
        reasons.add("analysis_assumption")
    if contract["completion"]["status"] != "complete":
        reasons.add("analysis_incomplete")
    if any(
        coverage["status"] not in {"verified_correct"}
        for coverage in contract["relation_coverage"]
    ):
        reasons.add("relation_coverage_incomplete")
    return sorted(reasons)


def _guard_count(facts: dict[str, Any]) -> tuple[int, int, int]:
    relation_guards = len(facts["guards"])
    annotations = [guard for call in facts["calls"] for guard in call["guards"]]
    unique_branches = {
        (
            guard["location"]["logical_path"],
            guard["location"]["start_line"],
            guard["location"]["end_line"],
            guard["expression"],
        )
        for guard in annotations
    }
    return relation_guards, len(annotations), len(unique_branches)


def _backend_tools(cases: list[dict[str, Any]]) -> dict[str, list[str]]:
    collected: dict[str, set[str]] = {
        "backend": set(),
        "schema": set(),
        "wala": set(),
        "codeql_version": set(),
        "driver_sha256": set(),
        "probe_sha256": set(),
        "query_template_sha256": set(),
    }
    for row in cases:
        backend = row.get("semantic_backend") or {}
        provenance = backend.get("provenance") or {}
        for name, source in (
            ("backend", backend),
            ("schema", backend),
            ("wala", provenance),
            ("codeql_version", provenance),
            ("driver_sha256", provenance),
            ("probe_sha256", provenance),
            ("query_template_sha256", provenance),
        ):
            value = source.get(name)
            if isinstance(value, str) and value:
                collected[name].add(value)
    return {name: sorted(values) for name, values in collected.items() if values}


def _source_records(case: Any, contract: dict[str, Any]) -> list[dict[str, Any]]:
    records = []
    for scope in contract["source_scope"]:
        source = (
            case.masked_test
            if scope["selection_role"] == "masked_test"
            else case.read_source(scope["logical_path"])
        )
        digest = _sha256(source.text.encode())
        if digest != scope["sha256"]:
            raise ValueError(f"source digest differs: {scope['logical_path']}")
        records.append(
            {
                "source_id": scope["source_id"],
                "logical_path": scope["logical_path"],
                "text": source.text,
                "start_line": source.start_line,
                "end_line": source.end_line,
            }
        )
    return records


def _validate_identity(
    *,
    row_identity: dict[str, Any],
    contract: dict[str, Any],
    entry: dict[str, Any],
    case: Any,
) -> None:
    expected = {
        "case_id": entry["case_id"],
        "dataset": entry["dataset"],
        "language": entry["language"],
        "snapshot_id": entry["snapshot_id"],
    }
    contract_identity = contract["identity"]
    for name, value in expected.items():
        if row_identity.get(name) != value or contract_identity.get(name) != value:
            raise InputValidationError(f"{name} identity differs across receipt and inventory")
    adapter = {
        "case_id": case.case_id,
        "language": case.language,
        "snapshot_id": case.snapshot_id,
        "target_selector": case.target_selector,
        "masking_contract": case._masking_contract,
        "masked_test_sha256": _sha256(case.masked_test.text.encode()),
    }
    for name, value in adapter.items():
        if contract_identity.get(name) != value:
            raise InputValidationError(f"{name} identity differs from authenticated case")


def _backend_error(row: dict[str, Any]) -> str | None:
    backend = row.get("semantic_backend") or {}
    completion = (row.get("result_contract") or {}).get("completion") or {}
    candidates = (backend.get("error"), completion.get("error"), row.get("error"))
    for candidate in candidates:
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip().replace("\n", " ")[:500]
        if isinstance(candidate, dict):
            error_type = candidate.get("type")
            message = candidate.get("message")
            if isinstance(message, str) and message.strip():
                prefix = f"{error_type}: " if isinstance(error_type, str) and error_type else ""
                return (prefix + message.strip().replace("\n", " "))[:500]
    categories = completion.get("failure_categories") or row.get("failure_categories") or []
    return ", ".join(categories) if categories else None


def _candidate_error(candidate: dict[str, Any]) -> str | None:
    error = candidate.get("error")
    if isinstance(error, str):
        return error.strip().replace("\n", " ")[:500] or None
    if not isinstance(error, dict):
        return None
    error_type = error.get("type")
    message = error.get("message")
    if not isinstance(message, str) or not message.strip():
        return str(error_type) if isinstance(error_type, str) else None
    lines = [line.strip() for line in message.splitlines() if line.strip()]
    exception = next(
        (line.split('"main" ', 1)[1] for line in lines if 'Exception in thread "main" ' in line),
        lines[0],
    )
    prefix = f"{error_type}: " if isinstance(error_type, str) and error_type else ""
    return (prefix + exception)[:500]


def _summarize(records: list[dict[str, Any]]) -> dict[str, Any]:
    by_language = {}
    for language in ("java", "python"):
        rows = [record for record in records if record["language"] == language]
        successful = [record for record in rows if record["projection_status"] == "success"]
        sizes = [record["facts_bytes"] for record in successful]
        tokens = [record["estimated_tokens"] for record in successful]
        observations = [record["observation_count"] for record in successful]
        control_guards = [record["control_guard_relation_count"] for record in successful]
        guard_annotations = [record["lexical_guard_annotation_count"] for record in successful]
        unique_guard_branches = [record["unique_lexical_guard_branch_count"] for record in successful]
        entry_status = Counter(record["entry_binding_status"] for record in successful)
        by_language[language] = {
            "total": len(rows),
            "projection_success": len(successful),
            "input_rejected": sum(record["projection_status"] == "input_rejected" for record in rows),
            "data_error": sum(record["projection_status"] == "data_error" for record in rows),
            "projection_error": sum(record["projection_status"] == "projection_error" for record in rows),
            "structurally_clean_without_detected_unknown": sum(
                not record["unknown_reasons"] for record in successful
            ),
            "with_detected_unknown": sum(
                bool(record["unknown_reasons"]) for record in successful
            ),
            "entry_unresolved": sum(
                record.get("entry_binding_status") == "unresolved" for record in successful
            ),
            "entry_not_uniquely_resolved": sum(
                record.get("entry_binding_status") != "unique" for record in successful
            ),
            "entry_binding_status": dict(sorted(entry_status.items())),
            "backend_terminal_status": dict(
                sorted(Counter(record["backend_status"] for record in rows).items())
            ),
            "facts_bytes": _distribution(sizes),
            "view_bytes": _distribution([record["view_bytes"] for record in successful]),
            "view_tokens": _distribution([record["view_tokens"] for record in successful]),
            "estimated_tokens_ceil_bytes_div_4": _distribution(tokens),
            "observations": _distribution(observations),
            "control_guard_relations": _distribution(control_guards),
            "lexical_guard_annotations": _distribution(guard_annotations),
            "unique_lexical_guard_branches": _distribution(unique_guard_branches),
        }
    successful = [record for record in records if record["projection_status"] == "success"]
    backend_failures = [
        {
            key: record.get(key)
            for key in (
                "case_id", "dataset", "language", "analysis_status",
                "backend_status", "backend_error",
            )
        }
        for record in records
        if record["analysis_status"]
        in {"failed", "timeout", "budget_exceeded", "blocked", "unsupported"}
        or record["backend_status"]
        in {"failed", "timeout", "budget_exceeded", "blocked", "unsupported"}
    ]
    candidate_failures = [
        {
            key: record.get(key)
            for key in (
                "case_id", "dataset", "language", "candidate_status",
                "candidate_stage", "candidate_error", "analysis_status", "backend_status",
            )
        }
        for record in records
        if record.get("candidate_status") == "failed"
    ]
    return {
        "total": len(records),
        "projection_success": len(successful),
        "input_rejected": sum(record["projection_status"] == "input_rejected" for record in records),
        "data_error": sum(record["projection_status"] == "data_error" for record in records),
        "projection_error": sum(record["projection_status"] == "projection_error" for record in records),
        "structurally_clean_without_detected_unknown": sum(
            not record["unknown_reasons"] for record in successful
        ),
        "with_detected_unknown": sum(bool(record["unknown_reasons"]) for record in successful),
        "entry_unresolved": sum(
            record.get("entry_binding_status") == "unresolved" for record in successful
        ),
        "entry_not_uniquely_resolved": sum(
            record.get("entry_binding_status") != "unique" for record in successful
        ),
        "entry_binding_status": dict(
            sorted(Counter(record["entry_binding_status"] for record in successful).items())
        ),
        "facts_bytes": _distribution([record["facts_bytes"] for record in successful]),
        "estimated_tokens_ceil_bytes_div_4": _distribution(
            [record["estimated_tokens"] for record in successful]
        ),
        "view_bytes": _distribution([record["view_bytes"] for record in successful]),
        "view_tokens": _distribution([record["view_tokens"] for record in successful]),
        "observations": _distribution(
            [record["observation_count"] for record in successful]
        ),
        "control_guard_relations": _distribution(
            [record["control_guard_relation_count"] for record in successful]
        ),
        "lexical_guard_annotations": _distribution(
            [record["lexical_guard_annotation_count"] for record in successful]
        ),
        "unique_lexical_guard_branches": _distribution(
            [record["unique_lexical_guard_branch_count"] for record in successful]
        ),
        "unknown_reasons": dict(
            sorted(
                Counter(
                    reason for record in successful for reason in record["unknown_reasons"]
                ).items()
            )
        ),
        "by_language": by_language,
        "largest_five": [
            {
                key: record[key]
                for key in (
                    "case_id",
                    "dataset",
                    "language",
                    "facts_bytes",
                    "estimated_tokens",
                    "observation_count",
                    "unique_lexical_guard_branch_count",
                )
            }
            for record in sorted(
                successful, key=lambda record: record["facts_bytes"], reverse=True
            )[:5]
        ],
        "projection_errors": [
            {
                key: record[key]
                for key in ("case_id", "dataset", "language", "error_type", "error")
            }
            for record in records
            if record["projection_status"] == "projection_error"
        ],
        "input_errors": [
            {
                key: record[key]
                for key in ("case_id", "dataset", "language", "projection_status", "error_type", "error")
            }
            for record in records
            if record["projection_status"] in {"input_rejected", "data_error"}
        ],
        "observation_timeouts": {r["case_id"]: r["observation_timeouts"] for r in records
                                 if r.get("observation_timeouts")},
        "backend_failure_summaries": dict(
            sorted(Counter(row["backend_error"] or "未提供错误摘要" for row in backend_failures).items())
        ),
        "backend_failures": backend_failures,
        "candidate_failure_count": len(candidate_failures),
        "candidate_failure_backend_overlap": sum(
            row["backend_status"] in {"failed", "timeout", "budget_exceeded", "blocked"}
            for row in candidate_failures
        ),
        "candidate_failure_summaries": dict(
            sorted(Counter(row["candidate_error"] or "未提供错误摘要" for row in candidate_failures).items())
        ),
        "candidate_failures": candidate_failures,
    }


def _report(
    summary: dict[str, Any], *, results: Path, inventory: Path, provenance: str, counter_name: str
) -> str:
    lines = [
        "# MockBench 165 例 navigation facts v2 静态体检",
        "",
        "本次只读取审核后的冻结源码，使用批量后向切片结果和生产 `project_navigation_facts()` 投影；没有调用 LLM、执行目标测试、loader 或 Mock 生成。",
        "",
        f"- 后向切片结果：`{results}`",
        f"- 冻结清单：`{inventory}`",
        f"- 切片收据来源：`{provenance}`。`fresh_run` 表示本次新执行后端；`replayed_receipt` 表示只重放历史收据。",
        "- 165 例统一使用 generic Java/Python 全量后端口径。#35 七例中的 Jib、Flask、Langflow 等历史专用切片不在本报告中混入或补回，因而不能假定会生成相同事实卡片。",
        f"- 归档字节含末尾换行。视图按落盘字节使用 `{counter_name}` 实测 token，硬上限 8,192。归档估算列保留 r1 的 ceil(bytes / 4) 口径。P95 使用 nearest-rank。",
        "- 守卫分三项：控制依赖关系卡数、调用上的词法守卫注解数、以及按 `file/start/end/expression（含极性）` 去重的源码分支数。同一 `if` 对多个调用的相同注解只计一个去重分支，不把控制关系边与源码分支相加。",
        "- `with_detected_unknown` 与成功产出重叠。它包含入口不唯一、未解析调用、`reaches_assertion=null`、分析边界、未完成分析或关系覆盖不足。它不表示 JSON 结构失败。",
        "",
        "## 覆盖与降级",
        "",
        "| 语言 | 总数 | facts 成功 | 检出 Unknown | 入口未唯一解析 | 输入拒绝/数据错 | 投影错误 | 后端状态 |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    for language in ("java", "python"):
        row = summary["by_language"][language]
        status = ", ".join(f"{key}={value}" for key, value in row["backend_terminal_status"].items())
        lines.append(
            f"| {language} | {row['total']} | {row['projection_success']} | "
            f"{row['with_detected_unknown']} | {row['entry_not_uniquely_resolved']} | "
            f"{row['input_rejected'] + row['data_error']} | {row['projection_error']} | {status} |"
        )
    lines += [
        "",
        f"165 例中 {summary['projection_success']} 例产出结构合法的 v2 facts；"
        f"{summary['structurally_clean_without_detected_unknown']} 例没有检出上述 Unknown。结构合法不等于语义完整。",
        "入口绑定状态分布："
        + ", ".join(
            f"{status}={count}"
            for status, count in summary["entry_binding_status"].items()
        )
        + f"；未唯一解析合计={summary['entry_not_uniquely_resolved']}。",
        "",
        "| 语言 | unique | unresolved | ambiguous | not_evaluated | 未唯一合计 |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for language, row in (
        ("java", summary["by_language"]["java"]),
        ("python", summary["by_language"]["python"]),
        ("全部", summary),
    ):
        statuses = row["entry_binding_status"]
        lines.append(
            f"| {language} | {statuses.get('unique', 0)} | "
            f"{statuses.get('unresolved', 0)} | {statuses.get('ambiguous', 0)} | "
            f"{statuses.get('not_evaluated', 0)} | {row['entry_not_uniquely_resolved']} |"
        )
    lines += [
        "",
        "## 体积与复杂度",
        "",
        "| 语言 | 指标 | 中位数 | P95 | 最大值 |",
        "| --- | --- | ---: | ---: | ---: |",
    ]
    for language, row in (
        ("java", summary["by_language"]["java"]),
        ("python", summary["by_language"]["python"]),
        ("全部", summary),
    ):
        for label, key in (
            ("facts bytes", "facts_bytes"),
            ("归档估算 token", "estimated_tokens_ceil_bytes_div_4"),
            ("有界视图 bytes", "view_bytes"),
            ("有界视图实测 tokens", "view_tokens"),
            ("观察点", "observations"),
            ("控制守卫关系边", "control_guard_relations"),
            ("词法守卫注解", "lexical_guard_annotations"),
            ("去重源码守卫分支", "unique_lexical_guard_branches"),
        ):
            values = row[key]
            lines.append(
                f"| {language} | {label} | {values['median']} | "
                f"{values['p95_nearest_rank']} | {values['max']} |"
            )
    if summary["projection_success"] == summary["total"]:
        lines += [
            "",
            "从生产静态输入、协议与序列化层看，165 例均可分层交付；底层失败或 Unknown 的案例仍只交付已知事实，不能按语义完整样本解释。这不等于现有生成实验已经可开箱运行全量对照：当前 tagged-study 只覆盖 #35 的 7 例/42 项，另外 158 例的统一执行与评估清单未由本次静态体检验证。因此结论是静态层具备后续接入条件，但全量 Base vs Base+v2 尚需先补齐并验证实验清单。届时还须以目标模型的真实 tokenizer 校验 `指令 + 测试/CUT 源码 + facts + 输出预留` 的组合窗口；本表的 bytes/4 只衡量 facts 本身，不是 prompt 可行性结论。",
        ]
    lines += [
        "",
        "## 最大的五份 facts",
        "",
        "| 排名 | 语言 | bytes | 估算 token | 观察 | 去重源码守卫分支 | Case ID |",
        "| ---: | --- | ---: | ---: | ---: | ---: | --- |",
    ]
    for index, row in enumerate(summary["largest_five"], 1):
        lines.append(
            f"| {index} | {row['language']} | {row['facts_bytes']} | "
            f"{row['estimated_tokens']} | {row['observation_count']} | "
            f"{row['unique_lexical_guard_branch_count']} | `{row['case_id']}` |"
        )
    lines += ["", "## 异常", "", "### 输入审核与投影", ""]
    if summary["input_errors"]:
        for error in summary["input_errors"]:
            lines.append(
                f"- `{error['case_id']}`：{error['projection_status']} / "
                f"{error['error_type']}：{error['error']}"
            )
    else:
        lines.append("输入审核拒绝和数据错误均为 0。")
    if summary["projection_errors"]:
        for error in summary["projection_errors"]:
            lines.append(
                f"- `{error['case_id']}`：{error['error_type']}：{error['error']}"
            )
    else:
        lines.append("投影器错误为 0。")
    lines += ["", "### 候选图预处理失败", ""]
    lines.append(
        f"候选图阶段失败 {summary['candidate_failure_count']} 例；其中 "
        f"{summary['candidate_failure_backend_overlap']} 例也在后续 semantic backend 失败，"
        "其余案例的后端仍可完成。该阶段与后端状态不是互斥漏斗。"
    )
    lines.append(
        "摘要计数："
        + "；".join(
            f"{reason}={count}"
            for reason, count in summary["candidate_failure_summaries"].items()
        )
    )
    lines.append("")
    for error in summary["candidate_failures"]:
        lines.append(
            f"- `{error['case_id']}`：stage={error['candidate_stage']} / "
            f"candidate={error['candidate_status']} / backend={error['backend_status']}："
            f"{error['candidate_error'] or '未提供错误摘要'}"
        )
    timed_out = summary["observation_timeouts"]
    lines += ["", "### 观察点超时", "",
              f"逐观察点检查 WALA 超时与整例时限耗尽，共 {sum(map(len, timed_out.values()))} 个观察点。"]
    lines.extend(f"- `{case_id}`：{', '.join(criteria)}" for case_id, criteria in timed_out.items())
    lines += ["", "### 底层静态分析失败（可与 facts 成功重叠）", ""]
    if summary["backend_failures"]:
        lines.append(
            "摘要计数："
            + "；".join(
                f"{reason}={count}"
                for reason, count in summary["backend_failure_summaries"].items()
            )
        )
        lines.append("")
        for error in summary["backend_failures"]:
            lines.append(
                f"- `{error['case_id']}`：analysis={error['analysis_status']} / "
                f"backend={error['backend_status']}：{error['backend_error'] or '未提供错误摘要'}"
            )
    else:
        lines.append("底层后端没有 failed/timeout/budget_exceeded 状态。")
    return "\n".join(lines) + "\n"


def run(
    results_path: Path,
    inventory_path: Path,
    output: Path,
    *,
    slice_provenance: str,
    tokenizer: str = "utf8-bytes",
) -> dict[str, Any]:
    if output.exists():
        raise FileExistsError("health output directory already exists")
    count_tokens: Callable[[bytes], int]
    if tokenizer == "utf8-bytes":
        count_tokens = len
        counter_name = tokenizer
    elif tokenizer == "o200k_base":
        tiktoken = importlib.import_module("tiktoken")
        encoding = tiktoken.get_encoding(tokenizer)
        counter_name = f"tiktoken/{tiktoken.__version__}/{tokenizer}/encode_ordinary"

        def count_tokens(data: bytes) -> int:
            return len(encoding.encode_ordinary(data.decode("utf-8")))
    else:
        raise ValueError(f"unsupported token counter: {tokenizer}")
    results = _read(results_path)
    inventory = _read(inventory_path)
    cases = results.get("cases")
    entries = inventory.get("cases")
    if not isinstance(cases, list) or len(cases) != 165:
        raise ValueError("backward results must contain exactly 165 cases")
    if not isinstance(entries, list) or len(entries) != 165:
        raise ValueError("inventory must contain exactly 165 cases")
    index = {(entry["dataset"], entry["case_id"]): entry for entry in entries}
    if len(index) != 165:
        raise ValueError("inventory identities are not unique")
    if Counter(entry["language"] for entry in entries) != {"java": 59, "python": 106}:
        raise ValueError("inventory denominator must be Java59/Python106")
    result_keys = [
        (row["identity"]["dataset"], row["identity"]["case_id"]) for row in cases
    ]
    if len(set(result_keys)) != 165:
        raise ValueError("result identities are not unique")
    if Counter(row["identity"]["language"] for row in cases) != {
        "java": 59,
        "python": 106,
    }:
        raise ValueError("result denominator must be Java59/Python106")
    output.mkdir(parents=True)
    registry = Path(inventory["registry_root"])
    datasets: dict[tuple[str, str], FrozenDataset] = {}
    records = []
    manifest = {}
    for row in cases:
        identity = row["identity"]
        key = (identity["dataset"], identity["case_id"])
        entry = index.get(key)
        if entry is None or entry["snapshot_id"] != identity["snapshot_id"]:
            raise ValueError(f"result identity is absent from inventory: {key}")
        token = _sha256((key[0] + "\0" + key[1]).encode())[:20]
        record: dict[str, Any] = {
            **identity,
            "backend_status": row.get("semantic_backend", {}).get(
                "status", row.get("status", "unknown")
            ),
            "analysis_status": row.get("status", "unknown"),
            "backend_error": _backend_error(row),
            "candidate_status": (row.get("candidate_diagnostics") or {}).get(
                "status", "missing"
            ),
            "candidate_stage": (row.get("candidate_diagnostics") or {}).get(
                "stage"
            ),
            "candidate_error": _candidate_error(row.get("candidate_diagnostics") or {}),
        }
        try:
            dataset_key = (entry["dataset"], entry["inventory_revision"])
            dataset = datasets.get(dataset_key)
            if dataset is None:
                dataset = FrozenDataset(
                    registry,
                    dataset=entry["dataset"],
                    inventory_revision=entry["inventory_revision"],
                )
                datasets[dataset_key] = dataset
            contract = row["result_contract"]
            with dataset.load_case(entry["case_id"]) as case:
                _validate_identity(
                    row_identity=identity,
                    contract=contract,
                    entry=entry,
                    case=case,
                )
                sources = _source_records(case, contract)
        except InputValidationError as error:
            record.update(
                projection_status="input_rejected",
                error_type=type(error).__name__,
                error=str(error),
            )
            records.append(record)
            continue
        except Exception as error:
            record.update(
                projection_status="data_error",
                error_type=type(error).__name__,
                error=str(error),
            )
            records.append(record)
            continue
        try:
            facts = project_navigation_facts(contract, sources)
            delivery = _write_delivery(output, token, facts, count_tokens, counter_name)
            relation_guards, lexical_annotations, unique_lexical_branches = _guard_count(facts)
            record.update(
                projection_status="success",
                **delivery,
                observation_count=len(facts["observations"]),
                control_guard_relation_count=relation_guards,
                lexical_guard_annotation_count=lexical_annotations,
                unique_lexical_guard_branch_count=unique_lexical_branches,
                call_count=len(facts["calls"]),
                entry_binding_status=facts["entry_binding"]["status"],
                unknown_reasons=_unknown_reasons(contract, facts),
                observation_timeouts=_observation_timeouts(contract["analysis_boundaries"]),
            )
            manifest[token] = {
                "case_id": identity["case_id"],
                "dataset": identity["dataset"],
                "language": identity["language"],
                "snapshot_id": identity["snapshot_id"],
                **delivery,
            }
        except Exception as error:
            record.update(
                projection_status="projection_error",
                error_type=type(error).__name__,
                error=str(error),
            )
        records.append(record)
    if len(records) != 165 or len({(r["dataset"], r["case_id"]) for r in records}) != 165:
        raise ValueError("health records do not preserve the 165-case denominator")
    summary = _summarize(records)
    result = {
        "schema_version": SCHEMA_VERSION,
        "scope": "frozen reviewed sources; backward slice then navigation facts projection; no LLM",
        "slice_provenance": slice_provenance,
        "inputs": {
            "results_path": str(results_path),
            "results_sha256": _sha256(results_path.read_bytes()),
            "inventory_path": str(inventory_path),
            "inventory_sha256": _sha256(inventory_path.read_bytes()),
        },
        "metrics": {
            "exact_token_counter": counter_name,
            "default_view_max_tokens": 8192,
            "token_estimate": "ceil(canonical_facts_file_bytes / 4)",
            "percentile": "nearest-rank",
            "guard_count": {
                "control_guard_relations": "top-level control-dependence guard cards",
                "lexical_guard_annotations": "all call-local Python lexical guard annotations",
                "unique_lexical_guard_branches": "deduplicated by logical_path/start/end/expression including polarity",
            },
        },
        "backend_tools": _backend_tools(cases),
        "projector": {
            "path": str(Path(project_navigation_facts.__code__.co_filename).resolve()),
            "sha256": _sha256(
                Path(project_navigation_facts.__code__.co_filename).read_bytes()
            ),
        },
        "summary": summary,
        "cases": records,
    }
    _write_new(output / "summary.json", result)
    _write_new(output / "manifest.json", manifest)
    (output / "report.md").write_text(
        _report(
            summary,
            results=results_path,
            inventory=inventory_path,
            provenance=slice_provenance,
            counter_name=counter_name,
        ),
        encoding="utf-8",
    )
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--inventory", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--slice-provenance",
        required=True,
        choices=("fresh_run", "replayed_receipt"),
    )
    parser.add_argument("--tokenizer", choices=("utf8-bytes", "o200k_base"), default="utf8-bytes")
    args = parser.parse_args()
    result = run(
        args.results,
        args.inventory,
        args.output,
        slice_provenance=args.slice_provenance,
        tokenizer=args.tokenizer,
    )
    print(json.dumps(result["summary"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
