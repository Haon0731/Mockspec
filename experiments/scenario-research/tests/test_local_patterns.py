from __future__ import annotations

import asyncio
from dataclasses import dataclass
import hashlib
from pathlib import Path
from types import SimpleNamespace
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import local_patterns  # noqa: E402
from inputs import ReviewedInput, load_java, load_python  # noqa: E402
from mock_spec.schemas import Interface, Source  # noqa: E402


@dataclass
class _Store:
    values: dict[str, Source]

    def get(self, source_id: str) -> Source:
        return self.values[source_id]


def _source(source_id: str, text: str, *, language: str) -> Source:
    return Source(
        source_id=source_id,
        logical_path=f"src/{source_id}.{'java' if language == 'java' else 'py'}",
        text=text,
        symbol="Synthetic",
        start_line=1,
        end_line=len(text.splitlines()),
        resolution_status="resolved",
        target_precision="class" if language == "java" else "function",
    )


def _reviewed(
    language: str,
    mut: Source,
    *, interfaces: tuple[Interface, ...] = (),
    supporting: tuple[Source, ...] = (),
) -> ReviewedInput:
    task = SimpleNamespace(
        language=language,
        mut_source=mut,
        permitted_interfaces=interfaces,
        supporting_sources=supporting,
    )
    values = {mut.source_id: mut, **{item.source_id: item for item in supporting}}
    hashes = {
        source_id: hashlib.sha256(source.text.encode("utf-8")).hexdigest()
        for source_id, source in values.items()
    }
    return ReviewedInput(task=task, sources=_Store(values), source_sha256=hashes)


def _java_case(method: str, operator: str, literal: int) -> tuple[ReviewedInput, dict]:
    mut = _source(
        "mut:synthetic",
        "class Target {\n"
        "  Object choose(ChosenDependency service) {\n"
        f"    return (service.{method}() {operator} {literal}) ? yes() : no();\n"
        "  }\n"
        "}",
        language="java",
    )
    support = _source(
        "support:dependency",
        "class ChosenDependency implements Dependency {\n"
        f"  public Integer {method}() {{ throw new UnsupportedOperationException(); }}\n"
        "}",
        language="java",
    )
    interface = Interface(
        symbol=f"ChosenDependency.{method}",
        signature=f"public Integer {method}()",
        source_id=support.source_id,
        start_line=2,
        end_line=2,
        resolution_status="partial",
    )
    expression = f"service.{method}() {operator} {literal}"
    parser = {
        "status": "ok",
        "backend": "spoon",
        "source_id": mut.source_id,
        "facts": [
            {
                "kind": "call",
                "source_id": mut.source_id,
                "start_line": 3,
                "end_line": 3,
                "quote": mut.text.splitlines()[2],
                "name": method,
                "target": "service",
                "declaration_status": "unresolved",
                "lexical_guards": [{"branch": "condition", "condition": expression}],
            },
            {
                "kind": "variable_read",
                "source_id": mut.source_id,
                "start_line": 3,
                "end_line": 3,
                "quote": mut.text.splitlines()[2],
                "name": "service",
                "declaration_status": "resolved_local",
                "declared_at": {"source_id": mut.source_id, "start_line": 2, "end_line": 2},
            },
            {
                "kind": "conditional",
                "source_id": mut.source_id,
                "start_line": 3,
                "end_line": 3,
                "quote": mut.text.splitlines()[2],
                "condition": expression,
            },
        ],
        "diagnostics": [],
    }
    return _reviewed("java", mut, interfaces=(interface,), supporting=(support,)), parser


@pytest.mark.parametrize(
    ("method", "operator", "literal", "expected"),
    [
        ("renamedVersion", "<=", 12, "le"),
        ("otherVersion", ">", 21, "gt"),
    ],
)
def test_java_binding_uses_source_names_threshold_and_operator(
    monkeypatch: pytest.MonkeyPatch,
    method: str,
    operator: str,
    literal: int,
    expected: str,
) -> None:
    reviewed, parser = _java_case(method, operator, literal)

    async def fake_analyze(source: Source, *, timeout_seconds: float) -> dict:
        return parser

    monkeypatch.setattr(local_patterns, "analyze_java_source", fake_analyze)
    result = asyncio.run(local_patterns.extract_patterns(reviewed))

    assert result["status"] == "ok"
    pattern = result["patterns"][0]
    assert pattern["dependency_call"]["method"] == method
    assert pattern["term"] == {
        "sort": "Int",
        "operator": expected,
        "literal": literal,
        "bounds": [-2147483648, 2147483647],
    }
    assert pattern["dependency_call"]["type_evidence"]["receiver_static_type"] == "ChosenDependency"
    assert pattern["dependency_call"]["type_evidence"]["receiver_qualified_type"] == "ChosenDependency"
    assert "exceptions are outside" in pattern["normal_return_assumption"]


def test_java_unrelated_implementation_does_not_supply_receiver_type(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reviewed, parser = _java_case("version", "<=", 8)
    mut = _source(
        "mut:synthetic",
        "class Target {\n"
        "  Object choose(Dependency service) {\n"
        "    return (service.version() <= 8) ? yes() : no();\n"
        "  }\n"
        "}",
        language="java",
    )
    reviewed = _reviewed(
        "java",
        mut,
        interfaces=reviewed.task.permitted_interfaces,
        supporting=reviewed.task.supporting_sources,
    )

    async def fake_analyze(source: Source, *, timeout_seconds: float) -> dict:
        return parser

    monkeypatch.setattr(local_patterns, "analyze_java_source", fake_analyze)
    pattern = asyncio.run(local_patterns.extract_patterns(reviewed))["patterns"][0]

    assert pattern["support_status"] == "unresolved"
    assert pattern["term"] is None
    assert pattern["controllability"] == "unresolved"


def test_java_same_short_name_in_another_package_does_not_bind(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reviewed, parser = _java_case("version", "<=", 8)
    interface = Interface(
        symbol="example.ChosenDependency.version",
        signature="public Integer version()",
        source_id="support:dependency",
        start_line=2,
        end_line=2,
        resolution_status="partial",
    )
    mut = _source(
        "mut:synthetic",
        "package consumer;\n"
        "import other.ChosenDependency;\n"
        "class Target {\n"
        "  Object choose(ChosenDependency service) {\n"
        "    return (service.version() <= 8) ? yes() : no();\n"
        "  }\n"
        "}",
        language="java",
    )
    support = reviewed.task.supporting_sources[0]
    reviewed = _reviewed("java", mut, interfaces=(interface,), supporting=(support,))
    for fact in parser["facts"]:
        if fact["kind"] in {"call", "variable_read", "conditional"}:
            fact["start_line"] = fact["end_line"] = 5
        if fact["kind"] == "variable_read":
            fact["declared_at"] = {
                "source_id": mut.source_id,
                "start_line": 4,
                "end_line": 4,
            }

    async def fake_analyze(source: Source, *, timeout_seconds: float) -> dict:
        return parser

    monkeypatch.setattr(local_patterns, "analyze_java_source", fake_analyze)
    pattern = asyncio.run(local_patterns.extract_patterns(reviewed))["patterns"][0]

    assert pattern["support_status"] == "unresolved"
    assert pattern["term"] is None


def test_java_missing_spoon_call_binding_emits_unresolved_diagnostic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reviewed, parser = _java_case("version", "<=", 8)
    parser["facts"] = [fact for fact in parser["facts"] if fact["kind"] != "call"]

    async def fake_analyze(source: Source, *, timeout_seconds: float) -> dict:
        return parser

    monkeypatch.setattr(local_patterns, "analyze_java_source", fake_analyze)
    result = asyncio.run(local_patterns.extract_patterns(reviewed))

    assert result["status"] == "incomplete"
    assert result["patterns"] == []
    assert any(
        item["code"] == "unresolved_java_call_binding" for item in result["diagnostics"]
    )


def _python_parser(source: Source) -> dict:
    from mock_spec.python_analysis import analyze_source

    return analyze_source(source)


@pytest.mark.parametrize(
    ("test", "expected"),
    [("is None", "is_none"), ("is not None", "is_not_none")],
)
def test_python_none_identity_and_renamed_local_are_distinct(
    monkeypatch: pytest.MonkeyPatch, test: str, expected: str
) -> None:
    mut = _source(
        "mut:python",
        "def choose(provider):\n"
        "    renamed = provider.lookup()\n"
        f"    if renamed {test}:\n"
        "        return 1\n"
        "    return 0",
        language="python",
    )
    reviewed = _reviewed("python", mut)

    async def fake_analyze(source: Source, *, timeout_seconds: float) -> dict:
        return _python_parser(source)

    monkeypatch.setattr(local_patterns, "analyze_python_source", fake_analyze)
    result = asyncio.run(local_patterns.extract_patterns(reviewed))

    pattern = result["patterns"][0]
    assert pattern["support_status"] == "supported"
    assert pattern["binding"]["name"] == "renamed"
    assert pattern["binding"]["kind"] == "single_local_assignment"
    assert pattern["term"]["operator"] == expected
    assert pattern["controllability"] == "unresolved"
    assert "not approved for mock configuration" in pattern["control_caveats"][0]


def test_python_complex_call_argument_degrades_explicitly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mut = _source(
        "mut:python",
        "def choose(provider):\n"
        "    if normalize(provider.lookup()) is None:\n"
        "        return 1\n"
        "    return 0",
        language="python",
    )
    reviewed = _reviewed("python", mut)

    async def fake_analyze(source: Source, *, timeout_seconds: float) -> dict:
        return _python_parser(source)

    monkeypatch.setattr(local_patterns, "analyze_python_source", fake_analyze)
    result = asyncio.run(local_patterns.extract_patterns(reviewed))

    assert result["status"] == "incomplete"
    assert result["patterns"][0]["support_status"] == "unsupported"
    assert result["patterns"][0]["term"] is None


def test_python_nonadjacent_local_binding_and_loop_are_explicitly_unsupported(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mut = _source(
        "mut:python",
        "def choose(provider, items):\n"
        "    value = provider.lookup()\n"
        "    observe()\n"
        "    if value is None:\n"
        "        return 1\n"
        "    for item in items:\n"
        "        if provider.lookup() is not None:\n"
        "            return item\n"
        "    return 0",
        language="python",
    )
    reviewed = _reviewed("python", mut)

    async def fake_analyze(source: Source, *, timeout_seconds: float) -> dict:
        return _python_parser(source)

    monkeypatch.setattr(local_patterns, "analyze_python_source", fake_analyze)
    result = asyncio.run(local_patterns.extract_patterns(reviewed))

    assert result["status"] == "incomplete"
    assert result["patterns"][0]["support_status"] == "unsupported"
    assert any(item["code"] == "unsupported_python_control" for item in result["diagnostics"])


def test_parser_failure_is_preserved_without_host_crash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mut = _source("mut:python", "def choose():\n    return dependency()", language="python")
    reviewed = _reviewed("python", mut)

    async def fake_analyze(source: Source, *, timeout_seconds: float) -> dict:
        return {
            "status": "error",
            "backend": "cpython_ast",
            "source_id": source.source_id,
            "error_code": "timeout",
            "facts": [],
            "diagnostics": ["timed out"],
        }

    monkeypatch.setattr(local_patterns, "analyze_python_source", fake_analyze)
    result = asyncio.run(local_patterns.extract_patterns(reviewed))

    assert result["status"] == "error"
    assert result["error_code"] == "timeout"
    assert result["patterns"] == []


@pytest.mark.real_data
def test_reviewed_cases_bind_to_actual_source_when_available() -> None:
    try:
        reviewed_java = load_java()
        reviewed_python = load_python()
    except (FileNotFoundError, ValueError):
        pytest.skip("reviewed snapshots are unavailable")

    java = asyncio.run(local_patterns.extract_patterns(reviewed_java))
    python = asyncio.run(local_patterns.extract_patterns(reviewed_python))

    java_pattern = java["patterns"][0]
    python_pattern = python["patterns"][0]
    assert java_pattern["support_status"] == "unresolved"
    assert java_pattern["dependency_call"]["method"] == "getJavaVersion"
    assert java_pattern["condition"]["literal"] == 8
    assert java_pattern["term"] is None
    assert java_pattern["condition"]["location"]["start_line"] == 69
    assert python_pattern["support_status"] == "supported"
    assert python_pattern["dependency_call"]["callee"] == "_cv_app.get"
    assert python_pattern["term"]["operator"] == "is_not_none"
    assert python_pattern["condition"]["location"]["start_line"] == 282
