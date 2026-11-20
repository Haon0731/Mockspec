from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


_PATH = Path(__file__).with_name("preflight165.py")
_SPEC = importlib.util.spec_from_file_location("navigation_v2_preflight165", _PATH)
assert _SPEC is not None and _SPEC.loader is not None
preflight = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(preflight)


def test_both_arms_share_every_component_except_navigation() -> None:
    arguments = {
        "case_id": "case",
        "language": "python",
        "masked_test": {"logical_path": "test_x.py", "text": "def test_x():\n    pass\n"},
        "permitted_cut_sources": [{"logical_path": "x.py", "text": "def x(): pass\n"}],
        "slot_ids": ["mock_stub_001"],
    }
    base, base_parts = preflight.build_prompt(**arguments, navigation_view=None)
    v2, v2_parts = preflight.build_prompt(
        **arguments, navigation_view={"schema_version": "navigation/v2"}
    )

    base_prefix, base_navigation = base.split("\n\nNavigation evidence:\n", 1)
    v2_prefix, v2_navigation = v2.split("\n\nNavigation evidence:\n", 1)
    assert base_prefix == v2_prefix
    assert base_navigation == "null\n"
    assert v2_navigation == '{"schema_version":"navigation/v2"}\n'
    assert base_parts["instruction"] == v2_parts["instruction"]
    assert base_parts["payload"] == v2_parts["payload"]


@pytest.mark.parametrize("language", ["java", "python"])
def test_strict_fenced_parser_preserves_slot_source(language: str) -> None:
    source = "class Example {}" if language == "java" else "def test_example():\n    pass"
    raw = f"```text\n<slot>\n{source}\n</slot>\n```\n"

    assert preflight.parse_fenced_candidate(raw, language, ["slot"]) == {
        "slot": source
    }


@pytest.mark.parametrize(
    "raw",
    [
        "prose\n```python\npass\n```\n",
        "```java\n<slot>\nclass X {}\n</slot>\n```\n",
        "```python\npass\n```\ntrailing",
        "```python\npass\n```\n```python\npass\n```\n",
        "```python\n```\n",
    ],
)
def test_strict_fenced_parser_rejects_ambiguous_delivery(raw: str) -> None:
    with pytest.raises(ValueError, match="exactly one"):
        preflight.parse_fenced_candidate(raw, "python", ["slot"])


def test_response_contract_requires_complete_file_and_fence() -> None:
    contract = preflight.response_contract("java")
    assert "every requested slot" in contract
    assert "exactly one fenced code block" in contract
    assert "only verbatim java source" in contract


def test_java_slot_injection_changes_only_region_body() -> None:
    masked = (
        "class Test {\n"
        "  // === MOCK-STUB-START ===\n"
        "  old\n"
        "  // === MOCK-STUB-END ===\n"
        "  // === VERIFY-START ===\n"
        "  // === VERIFY-END ===\n"
        "}\n"
    )
    injected = preflight.inject_candidate(
        masked, {"MOCK-STUB": "  replacement", "VERIFY": ""}, "java"
    )

    assert "  replacement\n  // === MOCK-STUB-END ===" in injected
    assert injected.startswith("class Test {\n  // === MOCK-STUB-START ===\n")
    assert injected.endswith("  // === VERIFY-END ===\n}\n")


def test_python_slot_injection_applies_marker_indentation() -> None:
    masked = (
        "def test_x():\n"
        "    # === PY-SLOT-START:stub_1 ===\n"
        "    # === PY-SLOT-END:stub_1 ===\n"
    )
    injected = preflight.inject_candidate(masked, {"stub_1": "x = 1\n  y = 2"}, "python")

    assert "    x = 1\n      y = 2\n" in injected


def test_visible_python_paths_resolve_only_unique_admitted_product_sources() -> None:
    masked = (
        "import os\n"
        "SRC = 'src/pkg/api.py'\n"
        "path = os.path.join(os.path.dirname(__file__), '..', 'src', 'pkg', 'worker.py')\n"
    )
    resolved = preflight._visible_source_candidates(
        masked,
        language="python",
        masked_path="tests/test_case.py",
        source_paths=(
            "tests/test_case.py",
            "repo/src/pkg/api.py",
            "src/pkg/worker.py",
            "tests/src/pkg/worker.py",
        ),
    )

    assert resolved == [
        {"logical_path": "repo/src/pkg/api.py", "evidence": "python_visible_static_path"},
        {"logical_path": "src/pkg/worker.py", "evidence": "python_visible_static_path"},
    ]


def test_visible_import_and_loader_names_resolve_candidate_sources() -> None:
    masked = (
        "from modules import launch_utils\n"
        "from test_utils import load_core_algos_module\n"
        "core_algos = load_core_algos_module('case')\n"
    )
    resolved = preflight._visible_source_candidates(
        masked,
        language="python",
        masked_path="tests/test_case.py",
        source_paths=(
            "tests/test_case.py",
            "src/modules/launch_utils.py",
            "src/verl/core_algos.py",
            "tests/test_utils.py",
        ),
    )

    assert resolved == [
        {"logical_path": "src/modules/launch_utils.py", "evidence": "python_visible_import"},
        {"logical_path": "src/verl/core_algos.py", "evidence": "python_visible_loader_name"},
    ]


def test_visible_java_test_type_resolves_same_package_candidate() -> None:
    resolved = preflight._visible_source_candidates(
        "package io.example;\npublic class ServiceTests {}\n",
        language="java",
        masked_path="src/test/java/io/example/ServiceTests.java",
        source_paths=(
            "src/test/java/io/example/ServiceTests.java",
            "src/main/java/io/example/Service.java",
        ),
    )

    assert resolved == [
        {
            "logical_path": "src/main/java/io/example/Service.java",
            "evidence": "java_same_package_test_type",
        }
    ]


def test_unsupported_language_is_rejected() -> None:
    with pytest.raises(ValueError, match="unsupported"):
        preflight.response_contract("kotlin")
