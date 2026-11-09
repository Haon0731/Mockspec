"""Validate bounded Python path alternatives against executions of visible MUT source.

This is a mechanism check, not a soundness proof.  It compiles the admitted source
and supplies controlled dependency responses; it does not reimplement the MUT.
"""
from __future__ import annotations

import argparse
import ast
import __future__
from dataclasses import dataclass
import hashlib
import itertools
import json
from pathlib import Path
import re
import time
from types import SimpleNamespace
from typing import Any


RESULT_ATOM = re.compile(r"result\[(?P<event>[^]]+)]\s+is\s+not\s+None")


class EventRaised(RuntimeError):
    def __init__(self, event_id: str):
        super().__init__(f"controlled exception from {event_id}")
        self.event_id = event_id


@dataclass
class TraceRecorder:
    callsites: dict[str, str]
    lookup_mode: str
    aborter_mode: str
    fallback_mode: str

    def __post_init__(self) -> None:
        self.events: list[dict[str, Any]] = []
        self.ordinals: dict[str, int] = {}
        self.context = SimpleNamespace(app=SimpleNamespace(aborter=self.aborter))

    def invoke(
        self, target: str, mode: str, args: tuple[Any, ...], kwargs: dict[str, Any],
        *, normal_value: Any,
    ) -> Any:
        callsite = self.callsites[target]
        ordinal = self.ordinals.get(callsite, 0) + 1
        self.ordinals[callsite] = ordinal
        event_id = f"{callsite}#{ordinal}"
        outcome = "raise" if mode == "raise" else "normal"
        event = {
            "event_id": event_id,
            "callsite": callsite,
            "target": target,
            "arguments": json_safe(args),
            "keyword_arguments": json_safe(kwargs),
            "outcome": outcome,
        }
        if outcome == "normal":
            event["result_category"] = "none" if normal_value is None else "object"
        self.events.append(event)
        if outcome == "raise":
            raise EventRaised(event_id)
        return normal_value

    def lookup(self, *args: Any, **kwargs: Any) -> Any:
        normal = None if self.lookup_mode == "none" else self.context
        mode = "raise" if self.lookup_mode == "raise" else "normal"
        return self.invoke("_cv_app.get", mode, args, kwargs, normal_value=normal)

    def aborter(self, *args: Any, **kwargs: Any) -> Any:
        return self.invoke(
            "ctx.app.aborter", self.aborter_mode, args, kwargs,
            normal_value="aborter-return",
        )

    def fallback(self, *args: Any, **kwargs: Any) -> Any:
        return self.invoke(
            "_wz_abort", self.fallback_mode, args, kwargs,
            normal_value="fallback-return",
        )


def json_safe(value: Any) -> Any:
    if isinstance(value, tuple):
        return [json_safe(item) for item in value]
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return f"<{type(value).__name__}>"


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def callsites(source: dict[str, Any]) -> dict[str, str]:
    tree = ast.parse(source["text"], filename=source["logical_path"])
    found: dict[str, str] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        target = ast.unparse(node.func)
        if target in {"_cv_app.get", "ctx.app.aborter", "_wz_abort"}:
            line = int(source["start_line"]) + node.lineno - 1
            found[target] = f"{source['source_id']}:{line}:{node.col_offset}"
    if set(found) != {"_cv_app.get", "ctx.app.aborter", "_wz_abort"}:
        raise ValueError("visible MUT no longer contains the expected dependency callsites")
    return found


def compile_mut(source: dict[str, Any], recorder: TraceRecorder):
    text = "\n" * (int(source["start_line"]) - 1) + source["text"]
    namespace = {
        "_cv_app": SimpleNamespace(get=recorder.lookup),
        "_wz_abort": recorder.fallback,
        "__name__": "visible_mut_trace_matrix",
    }
    code = compile(
        text, source["logical_path"], "exec",
        flags=__future__.annotations.compiler_flag, dont_inherit=True,
    )
    exec(code, namespace)
    function = namespace.get("abort")
    if not callable(function):
        raise ValueError("visible source did not define abort")
    return function


def execute_case(source: dict[str, Any], modes: tuple[str, str, str]) -> dict[str, Any]:
    lookup_mode, aborter_mode, fallback_mode = modes
    recorder = TraceRecorder(callsites(source), lookup_mode, aborter_mode, fallback_mode)
    function = compile_mut(source, recorder)
    terminal: dict[str, Any]
    try:
        returned = function(403, "message", description="details")
        terminal = {"kind": "fallthrough", "return_value": json_safe(returned)}
    except EventRaised as exc:
        terminal = {"kind": "raise", "exception_event_id": exc.event_id}
    return {
        "configuration": {
            "lookup": lookup_mode,
            "aborter": aborter_mode,
            "fallback": fallback_mode,
        },
        "events": recorder.events,
        "terminal": terminal,
    }


def match_guard(guard: dict[str, Any], actual_by_id: dict[str, dict[str, Any]]) -> tuple[bool, str]:
    match = RESULT_ATOM.search(guard["expression"])
    if match is None:
        return False, "unsupported_guard_expression"
    event = actual_by_id.get(match.group("event"))
    if event is None or event.get("outcome") != "normal":
        return False, "guard_result_event_missing"
    is_non_null = event.get("result_category") != "none"
    expected = bool(guard["polarity"])
    return is_non_null == expected, "ok" if is_non_null == expected else "guard_polarity_mismatch"


def match_path(path: dict[str, Any], trace: dict[str, Any]) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    expected_events = [
        (item["event_id"], item["target"], item["outcome"])
        for item in path["events"]
    ]
    actual_events = [
        (item["event_id"], item["target"], item["outcome"])
        for item in trace["events"]
    ]
    if expected_events != actual_events:
        reasons.append("event_sequence_mismatch")
    actual_by_id = {item["event_id"]: item for item in trace["events"]}
    for guard in path["guards"]:
        matched, reason = match_guard(guard, actual_by_id)
        if not matched:
            reasons.append(reason)
    expected_outcome = path["outcome"]
    actual_outcome = trace["terminal"]
    if expected_outcome["kind"] != actual_outcome["kind"]:
        reasons.append("terminal_kind_mismatch")
    elif expected_outcome["kind"] == "raise" and (
        expected_outcome.get("event_id") != actual_outcome.get("exception_event_id")
    ):
        reasons.append("terminal_exception_event_mismatch")
    return not reasons, reasons


def run(common_path: Path, raw_path: Path, analyzer_path: Path) -> dict[str, Any]:
    started = time.perf_counter()
    common_bytes = common_path.read_bytes()
    raw_bytes = raw_path.read_bytes()
    common = json.loads(common_bytes)
    raw = json.loads(raw_bytes)
    source = common["tasks"]["flask"]["mut_source"]
    source_bytes = source["text"].encode("utf-8")

    traces: list[dict[str, Any]] = []
    for modes in itertools.product(
        ("none", "object", "raise"), ("normal", "raise"), ("normal", "raise")
    ):
        trace = execute_case(source, modes)
        matches = []
        diagnostics: dict[str, list[str]] = {}
        for path in raw["paths"]:
            matched, reasons = match_path(path, trace)
            if matched:
                matches.append(path["path_id"])
            else:
                diagnostics[path["path_id"]] = reasons
        trace["matched_path_ids"] = matches
        if not matches:
            trace["match_diagnostics"] = diagnostics
        traces.append(trace)

    witness_counts = {
        path["path_id"]: sum(path["path_id"] in trace["matched_path_ids"] for trace in traces)
        for path in raw["paths"]
    }
    return {
        "status": "ok",
        "method": "execute admitted visible MUT source with a finite controlled response matrix",
        "matrix": {
            "lookup": ["none", "object", "raise"],
            "aborter": ["normal", "raise"],
            "fallback": ["normal", "raise"],
            "configuration_count": len(traces),
        },
        "traces": traces,
        "unmatched_trace_count": sum(not trace["matched_path_ids"] for trace in traces),
        "analysis_path_witness_counts": witness_counts,
        "all_analysis_paths_witnessed": all(count > 0 for count in witness_counts.values()),
        "hashes": {
            "common_json": sha256_bytes(common_bytes),
            "visible_mut_source": sha256_bytes(source_bytes),
            "python_raw_json": sha256_bytes(raw_bytes),
            "python_paths_py": sha256_bytes(analyzer_path.read_bytes()),
            "trace_matrix_py": sha256_bytes(Path(__file__).read_bytes()),
        },
        "elapsed_seconds": time.perf_counter() - started,
        "limitations": [
            "The finite response matrix is a mechanism check, not a soundness or completeness proof.",
            "Only the visible admitted MUT source is executed; dependency behavior is controlled by the host.",
            "Matching checks event id/target/outcome order, supported guard polarity, and terminal outcome.",
            "Repeated configurations can produce the same trace when a configured dependency is not reached.",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    here = Path(__file__).resolve().parent
    parser.add_argument("--common", type=Path, default=here / "inputs/common.json")
    parser.add_argument("--raw", type=Path, default=here / "python-raw.json")
    parser.add_argument("--analyzer", type=Path, default=here / "python_paths.py")
    parser.add_argument("--output", type=Path, default=here / "trace-matrix.json")
    args = parser.parse_args()
    result = run(args.common, args.raw, args.analyzer)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "configurations": result["matrix"]["configuration_count"],
        "unmatched": result["unmatched_trace_count"],
        "all_paths_witnessed": result["all_analysis_paths_witnessed"],
        "elapsed_seconds": result["elapsed_seconds"],
    }))


if __name__ == "__main__":
    main()
