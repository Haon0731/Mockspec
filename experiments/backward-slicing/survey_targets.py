"""Read frozen masked tests to inventory possible slicing criteria, not slices.

Python uses AST observations; Java uses lexical hints with comments/literals
removed. Neither performs binding, reachability, or semantic goal extraction.
"""
from __future__ import annotations

import argparse
import ast
from collections import Counter
import hashlib
import json
from pathlib import Path
import re

from mock_spec.adapters.frozen_dataset import FrozenDataset
from mock_spec.validation import decode_json


def python_observations(text: str) -> dict[str, list[int]]:
    tree = ast.parse(text)
    found: dict[str, set[int]] = {
        key: set() for key in ("value_assertion", "exception_observation", "interaction_observation",
                              "parameterization", "dynamic_loading")
    }
    for node in ast.walk(tree):
        if isinstance(node, ast.Assert):
            found["value_assertion"].add(node.lineno)
        if not isinstance(node, ast.Call):
            continue
        name = node.func.attr if isinstance(node.func, ast.Attribute) else (
            node.func.id if isinstance(node.func, ast.Name) else ""
        )
        if name in {"raises", "assertRaises", "assertRaisesRegex"}:
            found["exception_observation"].add(node.lineno)
        elif name.startswith(("assert_called", "assert_not_called", "assert_any_call", "assert_has_calls",
                              "assert_awaited", "assert_not_awaited")):
            found["interaction_observation"].add(node.lineno)
        elif name in {"assertEqual", "assertNotEqual", "assertTrue", "assertFalse", "assertIs",
                      "assertIsNot", "assertIsNone", "assertIsNotNone", "assertIn", "assertNotIn",
                      "assertAlmostEqual", "assertCountEqual", "assertDictEqual", "assertListEqual"}:
            found["value_assertion"].add(node.lineno)
        if name == "parametrize":
            found["parameterization"].add(node.lineno)
        if name in {"load_function_from_source_ast", "exec", "eval", "compile", "import_module",
                    "spec_from_file_location", "module_from_spec"}:
            found["dynamic_loading"].add(node.lineno)
    return {key: sorted(value) for key, value in found.items()}


def java_observations(text: str) -> dict[str, list[int]]:
    # Preserve line numbers. This is intentionally lexical, not a Java frontend.
    code = re.sub(
        r'//[^\n]*|/\*.*?\*/|""".*?"""|"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'',
        lambda match: "".join("\n" if char == "\n" else " " for char in match.group()),
        text, flags=re.DOTALL,
    )
    patterns = {
        "value_assertion": r"\b(?:assertEquals|assertNotEquals|assertThat|assertTrue|assertFalse|assertNull|assertNotNull|assertSame|assertNotSame|assertArrayEquals)\s*\(|\bassert\s+",
        "exception_observation": r"\b(?:assertThrows|assertDoesNotThrow|assertThatThrownBy|assertThatExceptionOfType|assertThatCode|expectThrows)\s*\(|@Test\s*\(\s*expected\s*=",
        "interaction_observation": r"\b(?:verify|verifyZeroInteractions|verifyNoInteractions|verifyNoMoreInteractions|inOrder)\s*\(",
        "parameterization": r"@(?:ParameterizedTest|Parameters|MethodSource|ValueSource|CsvSource)\b",
        "dynamic_loading": r"\b(?:forName|getDeclaredMethod|invoke)\s*\(",
    }
    return {key: sorted({code.count("\n", 0, match.start()) + 1
                         for match in re.finditer(pattern, code)})
            for key, pattern in patterns.items()}


def survey(inventory: dict) -> dict:
    if inventory.get("input_policy") != "frozen-agent":
        raise ValueError("requires frozen-agent inventory")
    datasets = {}
    rows = []
    for entry in inventory["cases"]:
        row = {key: entry[key] for key in ("case_id", "language", "dataset", "snapshot_id")}
        try:
            key = (entry["dataset"], entry["inventory_revision"])
            if key not in datasets:
                datasets[key] = FrozenDataset(Path(inventory["registry_root"]),
                                              dataset=key[0], inventory_revision=key[1])
            with datasets[key].load_case(entry["case_id"]) as case:
                if case.snapshot_id != entry["snapshot_id"] or case.language != entry["language"]:
                    raise ValueError("inventory identity mismatch")
                source = case.masked_test
                analyze = python_observations if case.language == "python" else java_observations
                observations = analyze(source.text)
                row.update({"status": "observed", "scope": "whole_masked_test_file",
                            "precision": "python_ast_syntax" if case.language == "python" else "java_lexical_hint",
                            "logical_path": source.logical_path, "source_sha256": hashlib.sha256(source.text.encode()).hexdigest(),
                            "lines": source.end_line, "observations": observations,
                            "available_source_files": len(case.source_paths),
                            "prior_development_case": entry["case_id"] in {
                                "flask_308",
                                "com.google.cloud.tools.jib.cli.jar.JarFilesTest#testToJibContainerBuilder_explodedStandard_basicInfo@69ae66df",
                                "de.codecentric.boot.admin.client.registration.CloudFoundryApplicationFactoryTest#should_use_application_uri@d0085edf",
                            }})
        except Exception as exc:
            row.update(status="failed", error=f"{type(exc).__name__}: {exc}")
        rows.append(row)
    summary = {}
    for language in ("java", "python"):
        subset = [row for row in rows if row["language"] == language]
        counts: Counter = Counter()
        for row in subset:
            if row["status"] != "observed":
                continue
            for category, locations in row["observations"].items():
                if locations:
                    counts[category] += 1
            if not any(row["observations"][category] for category in (
                "value_assertion", "exception_observation", "interaction_observation"
            )):
                counts["no_recognized_observation"] += 1
        summary[language] = {"total": len(subset), "observed": sum(row["status"] == "observed" for row in subset),
                             "cases_with_signal": dict(counts)}
    return {"schema_version": "mockbench-study.target-survey/v1", "summary": summary, "cases": rows,
            "implementation_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "limitations": ["Whole masked-file syntax hints, not bound target-function criteria or complete semantic goal extraction.",
                            "Categories overlap; counts are cases, not parameterized scenarios.",
                            "Calls are name matches; custom assertions and aliases may be missed.",
                            "No recognized observation does not mean no test oracle.",
                            "No source analysis beyond masked tests; no slices, model calls, or target execution.",
                            "Prior development case flag is by case ID, not a claim of identical snapshot to historical runs."]}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inventory", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    report = survey(decode_json(args.inventory.read_text()))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    print(json.dumps(report["summary"], ensure_ascii=False))
