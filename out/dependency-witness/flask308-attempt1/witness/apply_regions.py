from __future__ import annotations

import ast
import io
import json
import os
import textwrap
import tokenize
from pathlib import Path

TARGET = Path("repo") / 'tests/test_308abort.py'
MOCK_EXPECTED = ['mock_stub_001', 'mock_stub_002', 'mock_stub_003', 'mock_stub_004', 'mock_stub_005', 'mock_stub_006']
VERIFY_EXPECTED = ['verify_001', 'verify_002', 'verify_003', 'verify_004']
OUT_DIR = Path("control_outputs") / "candidate_preview"
CANDIDATE_RUNTIME_ENV_KEY = 'STUBTESTER_CANDIDATE_RUNTIME'
MASKED_TEMPLATE = OUT_DIR / "masked_template.py"


def marker(slot_id: str, kind: str) -> str:
    return f"# === PY-SLOT-{kind}:{slot_id} ==="


def parse_slots(text: str, expected: list[str]) -> dict[str, str]:
    fragments = {}
    for slot_id in expected:
        start = marker(slot_id, "START")
        end = marker(slot_id, "END")
        start_index = text.find(start)
        if start_index == -1:
            raise RuntimeError(f"missing slot start: {start}")
        end_index = text.find(end, start_index + len(start))
        if end_index == -1:
            raise RuntimeError(f"missing slot end: {end}")
        fragments[slot_id] = text[start_index + len(start):end_index].strip()
    return fragments


def clean_generated_fragment(fragment: str) -> str:
    cleaned = fragment.strip("\n")
    if not cleaned.strip():
        return ""
    lines = [line.rstrip() for line in cleaned.splitlines()]
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    lines = textwrap.dedent("\n".join(lines)).splitlines()
    incidental_indent = minimum_code_indent(lines)
    if incidental_indent:
        lines = [remove_incidental_indent(line, incidental_indent) for line in lines]
    normalized = "\n".join(lines).strip("\n")
    return repair_unexpected_fragment_indentation(normalized)


def minimum_code_indent(lines: list[str]) -> int:
    indents = [
        leading_whitespace_len(line)
        for line in lines
        if line.strip() and not line.lstrip().startswith("#")
    ]
    return min(indents) if indents else 0


def remove_incidental_indent(line: str, incidental_indent: int) -> str:
    if not line.strip():
        return ""
    actual_indent = leading_whitespace_len(line)
    if actual_indent < incidental_indent:
        return line
    return line[incidental_indent:]


def repair_unexpected_fragment_indentation(fragment: str) -> str:
    syntax_error = python_fragment_syntax_error(fragment)
    if syntax_error is None or not is_indentation_syntax_error(syntax_error):
        return fragment
    repaired = "\n".join(repair_unexpected_indent_lines(fragment.splitlines())).strip("\n")
    if repaired == fragment or python_fragment_syntax_error(repaired) is not None:
        return fragment
    return repaired


def python_fragment_syntax_error(fragment: str) -> SyntaxError | None:
    try:
        ast.parse(fragment)
    except SyntaxError as exc:
        return exc
    return None


def is_indentation_syntax_error(exc: SyntaxError) -> bool:
    message = str(exc.msg).lower()
    return isinstance(exc, IndentationError) or "unexpected indent" in message or "unindent does not match" in message


def repair_unexpected_indent_lines(lines: list[str]) -> list[str]:
    bracket_depth_before = bracket_depth_before_lines(lines)
    repaired = []
    indent_stack = [0]
    previous_allows_indent = False
    for line_no, line in enumerate(lines, start=1):
        if not line.strip():
            repaired.append("")
            continue
        if bracket_depth_before[line_no] > 0:
            repaired.append(line)
            previous_allows_indent = False
            continue
        indent = leading_whitespace_len(line)
        while len(indent_stack) > 1 and indent < indent_stack[-1]:
            indent_stack.pop()
        expected_indent = indent_stack[-1]
        if indent > expected_indent:
            if previous_allows_indent:
                indent_stack.append(indent)
            else:
                line = line[indent - expected_indent:]
                indent = expected_indent
        repaired.append(line)
        previous_allows_indent = line_allows_following_indent(line)
    return repaired


def bracket_depth_before_lines(lines: list[str]) -> list[int]:
    depth_before = [0] * (len(lines) + 1)
    current_line = 1
    depth = 0
    text = "\n".join(lines) + "\n"
    try:
        tokens = tokenize.generate_tokens(io.StringIO(text).readline)
        for token in tokens:
            start_line = token.start[0]
            while current_line <= len(lines) and current_line <= start_line:
                depth_before[current_line] = depth
                current_line += 1
            if token.type != tokenize.OP:
                continue
            if token.string in "([{":
                depth += 1
            elif token.string in ")]}" and depth > 0:
                depth -= 1
    except tokenize.TokenError:
        return rough_bracket_depth_before_lines(lines)
    while current_line <= len(lines):
        depth_before[current_line] = depth
        current_line += 1
    return depth_before


def rough_bracket_depth_before_lines(lines: list[str]) -> list[int]:
    depth_before = [0] * (len(lines) + 1)
    depth = 0
    for line_no, line in enumerate(lines, start=1):
        depth_before[line_no] = max(depth, 0)
        for char in line:
            if char in "([{":
                depth += 1
            elif char in ")]}" and depth > 0:
                depth -= 1
    return depth_before


def line_allows_following_indent(line: str) -> bool:
    code = line_without_comment(line).rstrip()
    return code.endswith(":") or code.endswith("\\")


def line_without_comment(line: str) -> str:
    try:
        tokens = tokenize.generate_tokens(io.StringIO(line).readline)
        for token in tokens:
            if token.type == tokenize.COMMENT:
                return line[:token.start[1]]
            if token.type in {tokenize.NEWLINE, tokenize.NL, tokenize.ENDMARKER}:
                break
    except tokenize.TokenError:
        pass
    return line


def leading_whitespace_len(line: str) -> int:
    return len(line) - len(line.lstrip(" \t"))


def replace_region(source: str, slot_id: str, body: str) -> str:
    start = marker(slot_id, "START")
    end = marker(slot_id, "END")
    start_index = source.find(start)
    end_index = source.find(end, start_index + len(start)) if start_index != -1 else -1
    if start_index == -1 or end_index == -1:
        raise RuntimeError(f"missing target slot: {slot_id}")
    line_start = source.rfind("\n", 0, start_index)
    indent = ""
    if line_start != -1:
        prefix = source[line_start + 1:start_index]
        indent = prefix[: len(prefix) - len(prefix.lstrip())]
    lines = clean_generated_fragment(body).splitlines()
    formatted = "\n".join((indent + line) if line.strip() else "" for line in lines)
    region_start = line_start + 1 if line_start != -1 else start_index
    end_line_end = source.find("\n", end_index + len(end))
    if end_line_end == -1:
        region_end = end_index + len(end)
        trailing = ""
    else:
        region_end = end_line_end + 1
        trailing = "\n"
    replacement = f"{formatted}{trailing}" if formatted else ""
    return source[:region_start] + replacement + source[region_end:]


def main() -> int:
    result = {"ok": False}
    try:
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        if MASKED_TEMPLATE.is_file():
            masked_source = MASKED_TEMPLATE.read_text(encoding="utf-8")
        else:
            masked_source = TARGET.read_text(encoding="utf-8")
            MASKED_TEMPLATE.write_text(masked_source, encoding="utf-8")
        fragments = {}
        fragments.update(parse_slots(Path("mock_stub.txt").read_text(encoding="utf-8"), MOCK_EXPECTED))
        fragments.update(parse_slots(Path("verify.txt").read_text(encoding="utf-8"), VERIFY_EXPECTED))
        candidate = masked_source
        for slot_id in [*MOCK_EXPECTED, *VERIFY_EXPECTED]:
            candidate = replace_region(candidate, slot_id, fragments[slot_id])
        ast.parse(candidate)
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        (OUT_DIR / "candidate_test.py").write_text(candidate, encoding="utf-8")
        if CANDIDATE_RUNTIME_ENV_KEY and os.environ.get(CANDIDATE_RUNTIME_ENV_KEY) == "1":
            TARGET.chmod(0o644)
            TARGET.write_text(candidate, encoding="utf-8")
        result.update({"ok": True, "candidate_test": str(OUT_DIR / "candidate_test.py")})
        return 0
    except Exception as exc:
        result.update({"error": str(exc)})
        return 1
    finally:
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        (OUT_DIR / "result.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
