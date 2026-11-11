"""Fail-closed source adapter for the supported Jib observation test."""
from __future__ import annotations

import hashlib
import re
from typing import Any, Iterable, Sequence


SCHEMA = "mockspec.jib-observation-source-adapter/v1"
ASSERTIONS = {"assertEquals", "assertArrayEquals", "assertNull"}


class JibObservationAdapterError(ValueError):
    pass


def _mask_comments(text: str) -> str:
    out = list(text)
    index, state = 0, "code"
    while index < len(text):
        pair, char = text[index:index + 2], text[index]
        if state == "code" and pair in {"//", "/*"}:
            out[index:index + 2] = [" ", " "]
            state = "line" if pair == "//" else "block"
            index += 2
            continue
        if state == "line":
            if char == "\n":
                state = "code"
            else:
                out[index] = " "
        elif state == "block":
            if pair == "*/":
                out[index:index + 2] = [" ", " "]
                state, index = "code", index + 2
                continue
            if char not in "\r\n":
                out[index] = " "
        elif state == "code" and char in {'"', "'"}:
            state = char
        elif state in {'"', "'"}:
            if char == "\\":
                index += 1
            elif char == state:
                state = "code"
        index += 1
    if state == "block":
        raise JibObservationAdapterError("unterminated block comment")
    return "".join(out)


def _matching(text: str, opening: int, left: str, right: str) -> int:
    depth, index, quote = 0, opening, None
    while index < len(text):
        char = text[index]
        if quote:
            if char == "\\":
                index += 1
            elif char == quote:
                quote = None
        elif char in {'"', "'"}:
            quote = char
        elif char == left:
            depth += 1
        elif char == right:
            depth -= 1
            if depth == 0:
                return index
        index += 1
    raise JibObservationAdapterError(f"unbalanced {left}{right}")


def _split_args(text: str) -> list[str]:
    parts, start, quote = [], 0, None
    depth = {"(": 0, "[": 0, "{": 0}
    closing = {")": "(", "]": "[", "}": "{"}
    index = 0
    while index < len(text):
        char = text[index]
        if quote:
            if char == "\\":
                index += 1
            elif char == quote:
                quote = None
        elif char in {'"', "'"}:
            quote = char
        elif char in depth:
            depth[char] += 1
        elif char in closing:
            depth[closing[char]] -= 1
        elif char == "," and not any(depth.values()):
            parts.append(text[start:index].strip())
            start = index + 1
        index += 1
    parts.append(text[start:].strip())
    if quote or any(depth.values()) or any(not item for item in parts):
        raise JibObservationAdapterError("invalid invocation arguments")
    return parts


def _method_range(masked: str) -> tuple[int, int]:
    calls = list(re.finditer(r"\bJarFiles\s*\.\s*toJibContainerBuilder\s*\(", masked))
    if len(calls) != 1:
        raise JibObservationAdapterError(f"expected one exact JarFiles MUT call, found {len(calls)}")
    ranges = []
    for match in re.finditer(
        r"(?m)^[^\n;{}]*\b[A-Za-z_$][\w$]*\s*\([^;{}]*\)\s*"
        r"(?:throws\s+[^{}]+)?\{", masked
    ):
        opening = masked.find("{", match.start(), match.end())
        end = _matching(masked, opening, "{", "}")
        if opening < calls[0].start() < end:
            ranges.append((opening + 1, end))
    if len(ranges) != 1:
        raise JibObservationAdapterError("MUT call is not in one uniquely resolved method")
    return ranges[0]


def _statements(text: str, start: int, end: int) -> list[tuple[int, int, str]]:
    rows, item_start = [], start
    braces, parens, quote = 0, 0, None
    nested_assert = False
    index = start
    while index < end:
        char = text[index]
        if quote:
            if char == "\\":
                index += 1
            elif char == quote:
                quote = None
        elif char in {'"', "'"}:
            quote = char
        elif char == "{":
            braces += 1
        elif char == "}":
            braces -= 1
        elif char == "(":
            parens += 1
        elif char == ")":
            parens -= 1
        elif braces > 0 and re.match(r"assert[A-Za-z_$]*\s*\(", text[index:]):
            nested_assert = True
        elif char == ";" and braces == 0 and parens == 0:
            raw = text[item_start:index + 1]
            leading = len(raw) - len(raw.lstrip())
            rows.append((item_start + leading, index + 1, raw.strip()))
            item_start = index + 1
        index += 1
    if braces or parens or quote:
        raise JibObservationAdapterError("unbalanced selected method body")
    if nested_assert:
        raise JibObservationAdapterError("assertions under branches or nested blocks are unsupported")
    return rows


def _call(statement: str, names: Iterable[str]) -> tuple[str, list[str]] | None:
    pattern = "|".join(re.escape(name) for name in names)
    matches = list(re.finditer(rf"\b({pattern})\s*\(", statement))
    if not matches:
        return None
    if len(matches) != 1:
        raise JibObservationAdapterError("ambiguous assertion invocation")
    if statement[:matches[0].start()].strip():
        raise JibObservationAdapterError(
            "assertion must be the complete top-level statement"
        )
    opening = statement.find("(", matches[0].start(), matches[0].end())
    end = _matching(statement, opening, "(", ")")
    if statement[end + 1:].strip() != ";":
        raise JibObservationAdapterError("assertion must be a top-level expression statement")
    return matches[0].group(1), _split_args(statement[opening + 1:end])


def _expression(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _criterion(expression: str, plan: str) -> str:
    root = re.escape(plan)
    patterns = [
        (rf"\(\(FileEntriesLayer\) {root}\.getLayers\(\)\.get\(0\)\)\.getEntries\(\)"
         rf"\.get\(0\)\.getExtractionPath\(\)", "layer0Entry0ExtractionPath"),
        (rf"\(\(FileEntriesLayer\) {root}\.getLayers\(\)\.get\(0\)\)\.getEntries\(\)"
         rf"\.toArray\(new Object\[0\]\)", "layer0Entries"),
        (rf"{root}\.getLayers\(\)\.get\(0\)\.getName\(\)", "layer0Name"),
        (rf"{root}\.getEntrypoint\(\)\.get\(([0-9]+)\)", "entrypoint@"),
        (rf"{root}\.get([A-Z][A-Za-z0-9]*)\(\)\.size\(\)", "@Size"),
        (rf"{root}\.get([A-Z][A-Za-z0-9]*)\(\)", "@"),
    ]
    for pattern, template in patterns:
        match = re.fullmatch(pattern, expression)
        if match:
            if "@" not in template:
                return template
            value = match.group(1)
            if template != "entrypoint@":
                value = value[0].lower() + value[1:]
            return template.replace("@", value)
    raise JibObservationAdapterError(f"unsupported observed actual expression: {expression}")


def _entry_source(items: list[dict[str, Any]]) -> str:
    calls = []
    for item in items:
        actual = re.sub(rf"\b{re.escape(item['plan_local'])}\b", "plan", item["expression"])
        if item["criterion"] in {"layer0Entries", "layer0Entry0ExtractionPath"}:
            calls.append(f"    ObservationSink.{item['criterion']}(\n        {actual});")
        else:
            calls.append(f"    ObservationSink.{item['criterion']}({actual});")
    body = "\n".join(calls)
    return f"""package com.google.cloud.tools.jib.cli.jar;
import com.google.cloud.tools.jib.api.JibContainerBuilder;
import com.google.cloud.tools.jib.api.buildplan.ContainerBuildPlan;
import com.google.cloud.tools.jib.api.buildplan.FileEntriesLayer;
import com.google.cloud.tools.jib.cli.ArtifactProcessor;
import com.google.cloud.tools.jib.cli.CommonCliOptions;
import com.google.cloud.tools.jib.cli.CommonContainerConfigCliOptions;
import com.google.cloud.tools.jib.cli.Jar;
import com.google.cloud.tools.jib.plugins.common.logging.ConsoleLogger;
public final class JibObservationEntry {{
  public static Object[] observe(ArtifactProcessor processor, Jar jar,
      CommonCliOptions common, CommonContainerConfigCliOptions container, ConsoleLogger logger)
      throws Exception {{
    JibContainerBuilder builder =
        JarFiles.toJibContainerBuilder(processor, jar, common, container, logger);
    ContainerBuildPlan plan = builder.toContainerBuildPlan();
{body}
    return new Object[] {{plan}};
  }}
}}
"""


def _generated_statement(item: dict[str, Any]) -> str:
    actual = re.sub(rf"\b{re.escape(item['plan_local'])}\b", "plan", item["expression"])
    if item["criterion"] in {"layer0Entries", "layer0Entry0ExtractionPath"}:
        return f"ObservationSink.{item['criterion']}(\n        {actual});"
    return f"ObservationSink.{item['criterion']}({actual});"


def _sink_source(items: list[dict[str, Any]]) -> str:
    methods = "\n".join(
        f"  public static native void {item['criterion']}(Object value);" for item in items
    )
    return f"""package com.google.cloud.tools.jib.cli.jar;
public final class ObservationSink {{
{methods}
}}
"""


def adapt_jib_observations(source: str) -> dict[str, Any]:
    masked = _mask_comments(source)
    method_start, method_end = _method_range(masked)
    statements = _statements(masked, method_start, method_end)
    mut_re = re.compile(
        r"(?:[\w$.]+\.)?JibContainerBuilder\s+([A-Za-z_$][\w$]*)\s*=\s*"
        r"(?:[\w$.]+\.)?JarFiles\s*\.\s*toJibContainerBuilder\s*\((.*)\)\s*;",
        re.DOTALL,
    )
    mut = [(position, a, b, mut_re.fullmatch(text))
           for position, (a, b, text) in enumerate(statements)]
    mut = [(position, a, b, match) for position, a, b, match in mut if match]
    if len(mut) != 1:
        raise JibObservationAdapterError("exact MUT receiver declaration is not unique")
    mut_position, mut_start, mut_end, mut_match = mut[0]
    assert mut_match
    builder = mut_match.group(1)
    arguments = _split_args(mut_match.group(2))
    if len(arguments) != 5 or not all(re.fullmatch(r"[A-Za-z_$][\w$]*", item)
                                      for item in arguments):
        raise JibObservationAdapterError("MUT arguments must be five explicit local identifiers")
    plan_re = re.compile(
        rf"(?:[\w$.]+\.)?ContainerBuildPlan\s+([A-Za-z_$][\w$]*)\s*=\s*"
        rf"{re.escape(builder)}\s*\.\s*toContainerBuildPlan\s*\(\s*\)\s*;",
        re.DOTALL,
    )
    plans = [(position, a, b, plan_re.fullmatch(text))
             for position, (a, b, text) in enumerate(statements) if a >= mut_end]
    plans = [(position, a, b, match) for position, a, b, match in plans if match]
    if len(plans) != 1:
        raise JibObservationAdapterError("exact plan local declaration is not unique")
    plan_position, plan_start, plan_end, plan_match = plans[0]
    assert plan_match
    plan = plan_match.group(1)
    if plan_position != mut_position + 1:
        raise JibObservationAdapterError(
            "unsupported statement between MUT result and plan binding"
        )
    items: list[dict[str, Any]] = []
    for position, (start, end, statement) in enumerate(statements):
        if position <= plan_position:
            continue
        assertion = _call(statement, ASSERTIONS)
        if assertion is None:
            raise JibObservationAdapterError(
                "unsupported executable statement or rebinding after plan binding"
            )
        name, args = assertion
        if len(args) != (1 if name == "assertNull" else 2):
            raise JibObservationAdapterError(f"unsupported {name} argument count")
        actual = _expression(args[-1])
        items.append({
            "observation_id": f"criterion:{len(items)}", "criterion": _criterion(actual, plan),
            "assertion": name, "expression": actual, "plan_local": plan,
            "start_line": source.count("\n", 0, start) + 1,
            "end_line": source.count("\n", 0, end - 1) + 1,
            "observed_line": source.count("\n", 0, end - 1) + 1,
        })
    if not items or len({item["criterion"] for item in items}) != len(items):
        raise JibObservationAdapterError("derived observations are absent or not unique")
    for item in items:
        item.update({
            "id": item["observation_id"],
            "assert_range": {"start_line": item["start_line"], "end_line": item["end_line"]},
            "source_expression": item["expression"],
            "sink": "ObservationSink." + item["criterion"],
            "generated_statement": _generated_statement(item),
            "parse_provenance": "top-level supported JUnit assertion actual argument",
        })
    entry, sink = _entry_source(items), _sink_source(items)
    mut_call_offset = masked.find("JarFiles", mut_start, mut_end)
    return {
        "schema": SCHEMA, "status": "adapted",
        "original_test_sha256": hashlib.sha256(source.encode()).hexdigest(),
        "generated_entry_sha256": hashlib.sha256(entry.encode()).hexdigest(),
        "mut_call": {"line": source.count("\n", 0, mut_call_offset) + 1,
                     "callee": "JarFiles.toJibContainerBuilder", "receiver_local": builder,
                     "argument_locals": arguments},
        "plan_binding": {"line": source.count("\n", 0, plan_start) + 1,
                         "local": plan, "call": f"{builder}.toContainerBuildPlan()"},
        "criteria": items, "entry_source": entry, "sink_source": sink,
        "parse_provenance": {"backend": "strict_supported_java_syntax",
                             "expected_literals_used": False, "target_executed": False},
    }


def validate_adapter_observations(adapter: dict[str, Any], expected: Sequence[dict[str, Any]]) -> None:
    actual = [
        (item["observation_id"], item["criterion"], item["start_line"], item["end_line"],
         item["observed_line"], item["expression"])
        for item in adapter.get("criteria", [])
    ]
    wanted = [
        (item.get("id", item.get("observation_id")), item["criterion"],
         item.get("line", item.get("start_line")), item["end_line"], item["observed_line"],
         item.get("expression", item.get("source_expression")))
        for item in expected
    ]
    if actual != wanted:
        raise JibObservationAdapterError("source-derived observations differ from validation data")
