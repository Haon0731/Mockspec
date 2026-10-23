import asyncio
from copy import deepcopy

import pytest

from mock_spec.source_analysis import SourceAnalysis
from mock_spec.schemas import UnifiedTask
from test_schemas import valid_task_data


def _task(language: str = "python") -> UnifiedTask:
    data = valid_task_data()
    data["language"] = language
    return UnifiedTask.from_dict(data)


def test_exact_slice_coordinates():
    tools = SourceAnalysis(UnifiedTask.from_dict(valid_task_data()))
    result = asyncio.run(tools.inspect_source("mut:0", 11, 11))
    assert result["text"] == "    return dependency(value)"
    assert result["start_line"] == result["end_line"] == 11


@pytest.mark.parametrize("source_id,start,end", [
    ("../private/gold.json", 1, 1), ("src/target.py", 10, 11),
    ("mut:0", 1, 11), ("mut:0", 10, 12), ("mut:0", 11, 10),
    ("mut:0", True, 11), ("mut:0", 10, 11.0),
])
def test_reject_invisible_input(source_id, start, end):
    tools = SourceAnalysis(UnifiedTask.from_dict(valid_task_data()))
    result = asyncio.run(tools.inspect_source(source_id, start, end))
    assert result["status"] == "error"
    assert "text" not in result


def test_output_limit_explicitly_omits_evidence():
    tools = SourceAnalysis(UnifiedTask.from_dict(valid_task_data()), max_output_chars=512)
    result = tools.bounded({"status": "ok", "text": "x" * 600})
    assert result["status"] == "incomplete"
    assert "text" not in result


def test_source_output_limit_preserves_requested_range():
    data = valid_task_data()
    data["mut_source"]["text"] = "def target(value):\n    return " + repr("x" * 600)
    tools = SourceAnalysis(UnifiedTask.from_dict(data), max_output_chars=512)
    result = asyncio.run(tools.inspect_source("mut:0", 10, 11))
    assert result["status"] == "incomplete"
    assert result["source_id"] == "mut:0"
    assert result["start_line"] == 10 and result["end_line"] == 11
    assert "text" not in result and "facts" not in result
    assert "smaller range" in result["diagnostics"][0]


def test_incomplete_backend_remains_incomplete_after_dependency_filter(monkeypatch):
    tools = SourceAnalysis(UnifiedTask.from_dict(valid_task_data()))

    async def incomplete(source_id):
        return {"status": "incomplete", "facts": [{"kind": "condition"}],
                "diagnostics": ["truncated"], "limitations": []}

    monkeypatch.setattr(tools, "_analyze", incomplete)
    result = asyncio.run(tools.inspect_dependency_usage("mut:0"))
    assert result["status"] == "incomplete"
    assert result["diagnostics"] == ["truncated"]


@pytest.mark.parametrize("language", ["python", "java"])
@pytest.mark.parametrize(
    "malformed",
    [
        None,
        {"status": []},
        {"status": "complete"},
        {"status": "ok", "facts": {}},
        {"status": "ok", "facts": [42]},
        {"status": "ok", "facts": [{}]},
        {"status": "ok", "facts": [{"kind": ""}]},
        {"status": "ok", "facts": [{"kind": []}]},
        {"status": "ok", "facts": [{"kind": "call", "detail": object()}]},
        {"status": "ok", "facts": [{"kind": "call", "confidence": float("nan")}]},
        {"status": "ok", "facts": [{"kind": "call"}, {"kind": []}]},
        {"status": "ok", "facts": [], "backend": []},
        {"status": "error", "facts": []},
        {"status": "error", "facts": [], "error_code": []},
        {"status": "ok", "facts": [], "diagnostics": "unsafe"},
        {"status": "ok", "facts": [], "diagnostics": [object()]},
        {"status": "ok", "facts": [], "limitations": {}},
        {"status": "ok", "facts": [], "limitations": [object()]},
    ],
    ids=[
        "non-mapping", "unhashable-status", "unknown-status", "non-list-facts",
        "non-mapping-fact", "missing-fact-kind", "empty-fact-kind",
        "unhashable-fact-kind", "nested-unsafe-fact", "non-finite-fact",
        "mixed-valid-invalid-facts", "invalid-backend", "missing-error-code",
        "invalid-error-code", "non-list-diagnostics", "unsafe-diagnostic",
        "non-list-limitations", "unsafe-limitation",
    ],
)
@pytest.mark.parametrize(
    "method_name", ["inspect_program_structure", "inspect_dependency_usage"]
)
def test_malformed_backend_result_is_one_host_error(
    monkeypatch, language, malformed, method_name
):
    tools = SourceAnalysis(_task(language))

    async def return_malformed(source, *, timeout_seconds):
        return deepcopy(malformed)

    if language == "python":
        import mock_spec.python_analysis as backend

        monkeypatch.setattr(backend, "analyze_source_isolated", return_malformed)
    else:
        import mock_spec.java_analysis as backend

        monkeypatch.setattr(backend, "analyze_source", return_malformed)

    result = asyncio.run(getattr(tools, method_name)("mut:0"))

    assert result == {
        "status": "error",
        "backend": None,
        "source_id": "mut:0",
        "error_code": "analysis_error",
        "facts": [],
        "diagnostics": ["Analysis backend returned malformed common fields."],
        "limitations": ["Analysis failure does not establish absence of behavior."],
    }


@pytest.mark.parametrize("language", ["python", "java"])
def test_valid_backend_common_fields_and_language_facts_are_preserved(
    monkeypatch, language
):
    tools = SourceAnalysis(_task(language))
    backend_result = {
        "status": "incomplete",
        "backend": "synthetic",
        "source_id": "backend-must-not-override-task",
        "facts": [{"kind": "condition", "language_detail": ["kept"]}],
        "diagnostics": [
            "plain diagnostic",
            {"category": "unsupported", "message": "structured diagnostic"},
        ],
        "limitations": ["synthetic limitation"],
        "language_specific": {"kept": True},
    }

    async def return_valid(source, *, timeout_seconds):
        return deepcopy(backend_result)

    if language == "python":
        import mock_spec.python_analysis as backend

        monkeypatch.setattr(backend, "analyze_source_isolated", return_valid)
    else:
        import mock_spec.java_analysis as backend

        monkeypatch.setattr(backend, "analyze_source", return_valid)

    result = asyncio.run(tools.inspect_program_structure("mut:0"))

    assert result == {**backend_result, "source_id": "mut:0"}


@pytest.mark.parametrize(
    "method_name", ["inspect_program_structure", "inspect_dependency_usage"]
)
def test_malformed_result_with_long_source_id_remains_a_bounded_error(
    monkeypatch, method_name
):
    data = valid_task_data()
    source_id = "m" + "x" * 2000
    data["mut_source"]["source_id"] = source_id
    next(item for item in data["sources"] if item["role"] == "mut")[
        "source_id"
    ] = source_id
    tools = SourceAnalysis(UnifiedTask.from_dict(data), max_output_chars=512)

    async def malformed(source, *, timeout_seconds):
        return {"status": "ok", "facts": [{"kind": []}]}

    import mock_spec.python_analysis as backend

    monkeypatch.setattr(backend, "analyze_source_isolated", malformed)

    result = asyncio.run(getattr(tools, method_name)(source_id))

    assert result["status"] == "error"
    assert result["error_code"] == "analysis_error"
    assert result["facts"] == []
    assert "source_id" not in result
    assert len(repr(result)) < 512


@pytest.mark.real_data
def test_both_reviewed_tasks_source_access():
    from test_real_data import REVIEWS, load_java, load_python, snapshot
    for review in REVIEWS:
        loader = load_java if review["language"] == "java" else load_python
        task = loader(snapshot(review), case_id=review["case_id"])
        tools = SourceAnalysis(task)
        for sid in tools.store.ids():
            source = tools.store.get(sid)
            result = asyncio.run(tools.inspect_source(sid, source.start_line, source.end_line))
            assert result["status"] == "ok"
            assert result["text"] == "\n".join(source.text.splitlines())
