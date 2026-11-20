"""Freeze the paired 165-case generation prompts without calling a model.

The prompt package is a deterministic derivative of the reviewed frozen case,
the source scope selected by the r2 static analysis, and the r2 bounded view.
It deliberately contains no reference-test or evaluation data.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
from pathlib import Path
import posixpath
import re
from typing import Any

from mock_spec.adapters.frozen_dataset import FrozenDataset
from mock_spec.tagged_slots import parse_tagged_slots


SCHEMA_VERSION = "mock-spec.navigation-v2-prompt-preflight/v1"
ARMS = ("base", "v2")
_LANGUAGES = {"java", "python"}


def _read(path: Path) -> Any:
    return json.loads(path.read_bytes())


def _encode(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def response_contract(language: str) -> str:
    """Return the exact, single-code-block response contract for one language."""
    if language not in _LANGUAGES:
        raise ValueError(f"unsupported prompt language: {language}")
    return (
        "Return exactly one fenced code block tagged `text`, with no prose before or "
        "after it. Inside the block, return every requested slot exactly once and in "
        "the supplied order, using `<slot-id>` and `</slot-id>` delimiter lines. Put "
        f"only verbatim {language} source between delimiters. A slot may be empty. "
        "For Python, make the first line relative to the marker indentation and keep "
        "only additional indentation on later lines. Fill only mock declarations, "
        "stub behavior, and interaction verifications supported by visible evidence. "
        "Do not use reference tests or dependency implementations."
    )


def build_prompt(
    *,
    case_id: str,
    language: str,
    masked_test: dict[str, Any],
    permitted_cut_sources: list[dict[str, Any]],
    navigation_view: dict[str, Any] | None,
    slot_ids: list[str],
) -> tuple[str, dict[str, int]]:
    """Build one arm while exposing exact component byte counts."""
    instruction = response_contract(language)
    payload = {
        "case_id": case_id,
        "language": language,
        "masked_test": masked_test,
        "permitted_cut_sources": permitted_cut_sources,
        "slot_ids": slot_ids,
    }
    navigation = navigation_view if navigation_view is not None else None
    parts = {
        "instruction": instruction,
        "payload": _encode(payload),
        "navigation": _encode(navigation),
    }
    prompt = (
        parts["instruction"]
        + "\n\nInput:\n"
        + parts["payload"]
        + "\n\nNavigation evidence:\n"
        + parts["navigation"]
        + "\n"
    )
    component_bytes = {name: len(value.encode()) for name, value in parts.items()}
    component_bytes["framing"] = len(prompt.encode()) - sum(component_bytes.values())
    return prompt, component_bytes


def parse_fenced_candidate(
    raw: str, language: str, slot_ids: list[str]
) -> dict[str, str]:
    """Extract exact source slots from the single fenced response contract."""
    if language not in _LANGUAGES:
        raise ValueError(f"unsupported candidate language: {language}")
    pattern = re.compile(r"\A```text\r?\n(.+?)\r?\n```\r?\n?\Z", re.DOTALL)
    match = pattern.fullmatch(raw)
    if match is None or "```" in match.group(1):
        raise ValueError("response must contain exactly one text code block")
    return parse_tagged_slots(match.group(1) + "\n", slot_ids)


def inject_candidate(
    masked_test: str, slots: dict[str, str], language: str
) -> str:
    """Inject parsed slots while retaining every line outside marked regions."""
    if language == "java":
        start_pattern = re.compile(r"^(?P<indent>\s*)// === (?P<slot>MOCK-STUB|VERIFY)-START ===\s*$")
        end_template = "// === {slot}-END ==="
    elif language == "python":
        start_pattern = re.compile(
            r"^(?P<indent>\s*)# === PY-SLOT-START:(?P<slot>[a-z0-9_]+) ===\s*$"
        )
        end_template = "# === PY-SLOT-END:{slot} ==="
    else:
        raise ValueError(f"unsupported candidate language: {language}")
    lines = masked_test.splitlines(keepends=True)
    output: list[str] = []
    seen: list[str] = []
    position = 0
    while position < len(lines):
        line = lines[position]
        match = start_pattern.fullmatch(line.rstrip("\r\n"))
        output.append(line)
        position += 1
        if match is None:
            continue
        slot = match.group("slot")
        if slot not in slots or slot in seen:
            raise ValueError("masked template slots differ from candidate slots")
        seen.append(slot)
        closing = match.group("indent") + end_template.format(slot=slot)
        while position < len(lines) and lines[position].rstrip("\r\n") != closing:
            position += 1
        if position >= len(lines):
            raise ValueError("masked template has an unterminated slot")
        source = slots[slot]
        if source:
            if language == "python":
                source = "\n".join(
                    match.group("indent") + part if part else part
                    for part in source.split("\n")
                )
            output.append(source + "\n")
        output.append(lines[position])
        position += 1
    if seen != list(slots):
        raise ValueError("masked template slot order differs from candidate slots")
    return "".join(output)


def _slot_ids(masked_test: str, language: str) -> list[str]:
    pattern = (
        r"// === (MOCK-STUB|VERIFY)-START ==="
        if language == "java"
        else r"# === PY-SLOT-START:([a-z0-9_]+) ==="
    )
    slots = re.findall(pattern, masked_test)
    if not slots or len(slots) != len(set(slots)):
        raise ValueError("masked test has invalid or duplicate slots")
    return slots


def _is_product_source(path: str, masked_path: str) -> bool:
    if path == masked_path:
        return False
    lowered = path.lower()
    parts = lowered.split("/")
    name = parts[-1]
    if any(part in {"test", "tests", "testing", "fixtures", "examples"} for part in parts):
        return False
    return not (
        name.startswith(("test_", "conftest"))
        or name.endswith(("_test.py", "test.java", "tests.java"))
    )


def _static_string(
    node: ast.AST, names: dict[str, str], masked_parent: str
) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Name):
        return names.get(node.id)
    if isinstance(node, ast.JoinedStr):
        parts: list[str] = []
        for value in node.values:
            piece = (
                value.value
                if isinstance(value, ast.Constant) and isinstance(value.value, str)
                else _static_string(value.value, names, masked_parent)
                if isinstance(value, ast.FormattedValue)
                else None
            )
            if piece is None:
                return None
            parts.append(piece)
        return "".join(parts)
    if not isinstance(node, ast.Call):
        return None
    function = node.func
    attribute = function.attr if isinstance(function, ast.Attribute) else None
    if attribute == "join":
        values = [_static_string(argument, names, masked_parent) for argument in node.args]
        if any(value is None for value in values):
            return None
        return posixpath.normpath(posixpath.join(*(value for value in values if value is not None)))
    if attribute in {"abspath", "normpath"} and len(node.args) == 1:
        return _static_string(node.args[0], names, masked_parent)
    if attribute == "dirname" and len(node.args) == 1:
        argument = node.args[0]
        if isinstance(argument, ast.Name) and argument.id == "__file__":
            return masked_parent
        static_value = _static_string(argument, names, masked_parent)
        return posixpath.dirname(static_value) if static_value is not None else None
    return None


def _unique_suffix_match(signal: str, product_paths: list[str]) -> str | None:
    normalized = posixpath.normpath(signal).lstrip("./")
    parts = [part for part in normalized.split("/") if part not in {"", ".", ".."}]
    for start in range(len(parts)):
        suffix = "/".join(parts[start:])
        matches = [path for path in product_paths if path.endswith(suffix)]
        if len(matches) == 1:
            return matches[0]
    return None


def _visible_source_candidates(
    masked_test: str,
    *,
    language: str,
    masked_path: str,
    source_paths: tuple[str, ...],
) -> list[dict[str, str]]:
    """Resolve visible source-name evidence to admitted unique product paths."""
    product_paths = [
        path for path in source_paths if _is_product_source(path, masked_path)
    ]
    signals: list[tuple[str, str]] = []
    if language == "java":
        package = re.search(r"^\s*package\s+([A-Za-z0-9_.]+)\s*;", masked_test, re.MULTILINE)
        classes = re.findall(
            r"\b(?:public\s+)?class\s+([A-Za-z_$][A-Za-z0-9_$]*)", masked_test
        )
        for class_name in classes:
            for suffix in ("Tests", "Test"):
                if class_name.endswith(suffix) and len(class_name) > len(suffix):
                    focal = class_name[: -len(suffix)] + ".java"
                    prefix = package.group(1).replace(".", "/") if package else ""
                    signals.append((f"{prefix}/{focal}".lstrip("/"), "java_same_package_test_type"))
        tree = None
    else:
        try:
            tree = ast.parse(masked_test)
        except SyntaxError as error:
            raise ValueError("masked Python test cannot be parsed for visible sources") from error
        names: dict[str, str] = {}
        masked_parent = posixpath.dirname(masked_path)
        for node in ast.walk(tree):
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                value_node = node.value
                value = _static_string(value_node, names, masked_parent) if value_node else None
                if value is not None:
                    for target in targets:
                        if isinstance(target, ast.Name):
                            names[target.id] = value
        for node in ast.walk(tree):
            value = _static_string(node, names, masked_parent)
            if value and value.endswith(".py"):
                signals.append((value, "python_visible_static_path"))
            if isinstance(node, ast.ImportFrom) and node.module:
                for alias in node.names:
                    signals.append(
                        (
                            node.module.replace(".", "/") + "/" + alias.name + ".py",
                            "python_visible_import",
                        )
                    )
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                match = re.fullmatch(r"load_([a-z0-9_]+)_module", node.func.id)
                if match:
                    signals.append((match.group(1) + ".py", "python_visible_loader_name"))
    resolved: dict[str, str] = {}
    for signal, evidence in signals:
        resolved_path = _unique_suffix_match(signal, product_paths)
        if resolved_path is not None:
            resolved.setdefault(resolved_path, evidence)
    return [
        {"logical_path": path, "evidence": resolved[path]}
        for path in sorted(resolved)
    ]


def _source_record(source: Any) -> dict[str, Any]:
    return {
        "source_id": source.source_id,
        "logical_path": source.logical_path,
        "start_line": source.start_line,
        "end_line": source.end_line,
        "text": source.text,
    }


def prepare(
    *,
    inventory_path: Path,
    results_path: Path,
    r2_dir: Path,
    output: Path,
    results_overlay_path: Path | None = None,
    prompt_dir: Path | None = None,
) -> dict[str, Any]:
    """Write both prompt arms and their immutable 165-case manifest."""
    if output.exists():
        raise FileExistsError("preflight prompt output directory already exists")
    inventory = _read(inventory_path)
    results = _read(results_path)
    overlay_results = _read(results_overlay_path) if results_overlay_path else None
    r2_manifest = _read(r2_dir / "manifest.json")
    entries = inventory.get("cases")
    rows = results.get("cases")
    if not isinstance(entries, list) or len(entries) != 165:
        raise ValueError("inventory must contain exactly 165 cases")
    if not isinstance(rows, list) or len(rows) != 165 or len(r2_manifest) != 165:
        raise ValueError("r2 inputs must preserve the 165-case denominator")
    row_index = {
        (row["identity"]["dataset"], row["identity"]["case_id"]): row
        for row in rows
    }
    if overlay_results is not None:
        for row in overlay_results.get("cases", []):
            row_index[(row["identity"]["dataset"], row["identity"]["case_id"])] = row
    token_index = {
        (receipt["dataset"], receipt["case_id"]): token
        for token, receipt in r2_manifest.items()
    }
    if len(row_index) != 165 or len(token_index) != 165:
        raise ValueError("case identities must be unique")

    output.mkdir(parents=True)
    prompt_dir = prompt_dir or output / "prompts"
    prompt_dir.mkdir()
    registry = Path(inventory["registry_root"])
    datasets: dict[tuple[str, str, str], FrozenDataset] = {}
    cases: list[dict[str, Any]] = []
    for entry in entries:
        key = (entry["dataset"], entry["case_id"])
        row = row_index.get(key)
        token = token_index.get(key)
        if row is None or token is None:
            raise ValueError(f"case missing from r2 inputs: {key}")
        if row["identity"]["snapshot_id"] != entry["snapshot_id"]:
            raise ValueError(f"snapshot identity differs: {key}")
        entry_registry = Path(entry.get("registry_root", registry))
        dataset_key = (
            str(entry_registry),
            entry["dataset"],
            entry["inventory_revision"],
        )
        dataset = datasets.get(dataset_key)
        if dataset is None:
            dataset = FrozenDataset(
                entry_registry,
                dataset=entry["dataset"],
                inventory_revision=entry["inventory_revision"],
            )
            datasets[dataset_key] = dataset
        contract = row["result_contract"]
        with dataset.load_case(entry["case_id"]) as case:
            masked_test = _source_record(case.masked_test)
            admitted_repository_source_count = len(case.source_paths) - 1
            masked_digest = _sha256(case.masked_test.text.encode())
            if masked_digest != contract["identity"]["masked_test_sha256"]:
                raise ValueError(f"masked test identity differs from r2: {key}")
            slots = _slot_ids(case.masked_test.text, entry["language"])
            cut_sources_by_path: dict[str, dict[str, Any]] = {}
            source_scope = contract["source_scope"]
            if any(
                scope["selection_role"] not in {"masked_test", "candidate_product"}
                for scope in source_scope
            ):
                raise ValueError("source scope contains an unknown selection role")
            if (
                len({scope["logical_path"] for scope in source_scope}) != len(source_scope)
                or len({scope["source_id"] for scope in source_scope}) != len(source_scope)
            ):
                raise ValueError("source scope contains duplicate paths or source ids")
            masked_scopes = [
                scope for scope in source_scope if scope["selection_role"] == "masked_test"
            ]
            if (
                len(masked_scopes) != 1
                or masked_scopes[0]["logical_path"] != case.masked_test.logical_path
                or masked_scopes[0]["sha256"] != masked_digest
            ):
                raise ValueError(f"masked source scope differs from r2: {key}")
            for scope in source_scope:
                if scope["selection_role"] == "masked_test":
                    continue
                source = case.read_source(scope["logical_path"])
                record = _source_record(source)
                if _sha256(source.text.encode()) != scope["sha256"]:
                    raise ValueError(f"source digest differs: {scope['logical_path']}")
                cut_sources_by_path[source.logical_path] = record
            recovered_sources: list[dict[str, str]] = []
            if entry["language"] == "python":
                candidates = {
                    candidate
                    for evidence in row["source_selection"]["evidence"]
                    if evidence.get("precision") == "exact_visible_path"
                    for candidate in evidence.get("candidates", [])
                }
                if len(candidates) == 1:
                    configured_path = next(iter(candidates))
                    if configured_path not in cut_sources_by_path:
                        cut_sources_by_path[configured_path] = _source_record(
                            case.read_source(configured_path)
                        )
                        recovered_sources.append(
                            {"logical_path": configured_path, "evidence": "exact_visible_path"}
                        )
            if not cut_sources_by_path:
                visible_candidates = _visible_source_candidates(
                    case.masked_test.text,
                    language=entry["language"],
                    masked_path=case.masked_test.logical_path,
                    source_paths=case.source_paths,
                )
                for candidate in visible_candidates:
                    path = candidate["logical_path"]
                    cut_sources_by_path[path] = _source_record(case.read_source(path))
                    recovered_sources.append(candidate)
            cut_sources = list(cut_sources_by_path.values())
        cut_sources.sort(key=lambda source: (source["logical_path"], source["source_id"]))
        view_path = r2_dir / r2_manifest[token]["view_path"]
        view_bytes = view_path.read_bytes()
        if _sha256(view_bytes) != r2_manifest[token]["view_sha256"]:
            raise ValueError(f"bounded view digest differs: {key}")
        view = json.loads(view_bytes)
        expected_identity = {
            "case_id": entry["case_id"],
            "dataset": entry["dataset"],
            "language": entry["language"],
            "snapshot_id": entry["snapshot_id"],
            "masked_test_sha256": masked_digest,
        }
        if any(
            view["identity"].get(name) != value
            for name, value in expected_identity.items()
        ):
            raise ValueError(f"bounded view identity differs from r2: {key}")
        receipt = r2_manifest[token]
        if any(
            receipt.get(name) != value
            for name, value in expected_identity.items()
            if name != "masked_test_sha256"
        ):
            raise ValueError(f"r2 manifest identity differs: {key}")
        arm_receipts = {}
        for arm in ARMS:
            prompt, components = build_prompt(
                case_id=entry["case_id"],
                language=entry["language"],
                masked_test=masked_test,
                permitted_cut_sources=cut_sources,
                navigation_view=view if arm == "v2" else None,
                slot_ids=slots,
            )
            prompt_path = prompt_dir / f"{token}.{arm}.txt"
            prompt_path.write_text(prompt, encoding="utf-8", newline="")
            payload = prompt.encode()
            arm_receipts[arm] = {
                "prompt_path": f"prompts/{prompt_path.name}",
                "prompt_sha256": _sha256(payload),
                "prompt_bytes": len(payload),
                "component_bytes": components,
            }
        cases.append(
            {
                "token": token,
                "case_id": entry["case_id"],
                "dataset": entry["dataset"],
                "language": entry["language"],
                "snapshot_id": entry["snapshot_id"],
                "masked_test_sha256": _sha256(masked_test["text"].encode()),
                "slot_ids": slots,
                "permitted_cut_source_count": len(cut_sources),
                "initial_cut_delivery": (
                    "embedded" if cut_sources else "blocked_no_static_focal_cut"
                ),
                "source_preflight": {
                    "status": "ready" if cut_sources else "blocked",
                    "admitted_repository_source_count": admitted_repository_source_count,
                    "reason": (
                        None
                        if cut_sources
                        else "no evidence-bound focal CUT was selected; whole-repository preloading cannot preserve the exact single-shot window contract"
                    ),
                },
                "recovered_sources": recovered_sources,
                "permitted_cut_sources": [
                    {
                        "source_id": source["source_id"],
                        "logical_path": source["logical_path"],
                        "sha256": _sha256(source["text"].encode()),
                        "bytes": len(source["text"].encode()),
                    }
                    for source in cut_sources
                ],
                "bounded_view_sha256": _sha256(view_bytes),
                "arms": arm_receipts,
            }
        )
    if len(cases) != 165 or len({case["token"] for case in cases}) != 165:
        raise ValueError("prompt package does not preserve the 165-case denominator")
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "scope": "offline prompt assembly; no model calls or reference-test data",
        "response_contract": "one exact language-matched fenced code block containing the complete test file",
        "inputs": {
            "inventory_path": str(inventory_path),
            "inventory_sha256": _sha256(inventory_path.read_bytes()),
            "results_path": str(results_path),
            "results_sha256": _sha256(results_path.read_bytes()),
            "results_overlay_path": (
                str(results_overlay_path) if results_overlay_path else None
            ),
            "results_overlay_sha256": (
                _sha256(results_overlay_path.read_bytes()) if results_overlay_path else None
            ),
            "r2_manifest_path": str(r2_dir / "manifest.json"),
            "r2_manifest_sha256": _sha256((r2_dir / "manifest.json").read_bytes()),
        },
        "cases": cases,
    }
    manifest_path = output / "prompt-manifest.json"
    manifest_path.write_text(_encode(manifest) + "\n", encoding="utf-8", newline="")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inventory", required=True, type=Path)
    parser.add_argument("--results", required=True, type=Path)
    parser.add_argument("--results-overlay", type=Path)
    parser.add_argument("--r2-dir", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument(
        "--prompt-dir",
        type=Path,
        help="optional temporary prompt-text directory; manifest stays under --output",
    )
    args = parser.parse_args()
    prepare(
        inventory_path=args.inventory,
        results_path=args.results,
        results_overlay_path=args.results_overlay,
        r2_dir=args.r2_dir,
        output=args.output,
        prompt_dir=args.prompt_dir,
    )


if __name__ == "__main__":
    main()
