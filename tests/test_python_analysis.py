import asyncio
from pathlib import Path

import pytest

import mock_spec.analysis_process as analysis_process
from mock_spec.source_analysis import SourceAnalysis
from mock_spec.python_analysis import analyze_source, analyze_source_isolated
from mock_spec.schemas import Source, UnifiedTask
from test_schemas import valid_task_data


def _source(text: str, *, start_line: int = 40) -> Source:
    return Source(
        source_id="mut:synthetic",
        logical_path="src/synthetic.py",
        text=text,
        symbol="synthetic.target",
        start_line=start_line,
        end_line=start_line + len(text.splitlines()) - 1,
        resolution_status="resolved",
        target_precision="function",
    )


def _task(text: str, *, start_line: int = 40) -> UnifiedTask:
    data = valid_task_data()
    data["case_id"] = "noncase_synthetic"
    data["mut_source"] = {
        "source_id": "mut:0",
        "logical_path": "src/example.py",
        "text": text,
        "symbol": "example.target",
        "start_line": start_line,
        "end_line": start_line + len(text.splitlines()) - 1,
        "resolution_status": "resolved",
        "target_precision": "function",
    }
    return UnifiedTask.from_dict(data)


def test_structure_preserves_slice_offsets_and_branch_polarity() -> None:
    source = _source(
        "def choose(flag):\n"
        "    if flag:\n"
        "        return primary()\n"
        "    else:\n"
        "        return fallback() if enabled() else final()"
    )

    result = analyze_source(source)

    assert result["status"] == "ok"
    calls = {fact["callee"]: fact for fact in result["facts"] if fact["kind"] == "call"}
    assert calls["primary"]["start_line"] == 42
    assert calls["primary"]["quote"] == "        return primary()"
    assert calls["primary"]["lexical_guards"][-1]["polarity"] is True
    assert calls["fallback"]["lexical_guards"][-1]["polarity"] is True
    assert calls["final"]["lexical_guards"][-1]["polarity"] is False
    assert calls["fallback"]["lexical_guards"][0]["polarity"] is False
    conditions = [fact for fact in result["facts"] if fact["kind"] == "condition"]
    assert {(fact["construct"], fact["expression"]) for fact in conditions} == {
        ("if", "flag"),
        ("if_expression", "enabled()"),
    }
    definition = next(
        fact for fact in result["facts"] if fact["kind"] == "function_definition"
    )
    assert definition["name"] == "choose"
    assert definition["qualified_name"] == "choose"
    assert definition["match_precision"] == "syntax_match"
    assert definition["resolution_status"] == "resolved_local"


def test_structure_reports_nested_definition_names_without_resolving_calls() -> None:
    result = analyze_source(_source(
        "class Service:\n"
        "    def fetch(self):\n"
        "        return helper()"
    ))

    definitions = {
        fact["qualified_name"]: fact
        for fact in result["facts"]
        if fact["kind"] in {"class_definition", "function_definition"}
    }
    assert definitions["Service"]["match_precision"] == "syntax_match"
    assert definitions["Service.fetch"]["start_line"] == 41
    call = next(fact for fact in result["facts"] if fact["kind"] == "call")
    assert call["match_precision"] == "unresolved"
    assert call["resolution_status"] == "unresolved"


def test_structure_reports_return_raise_try_except_and_with_syntax() -> None:
    source = _source(
        "def load(manager):\n"
        "    try:\n"
        "        with manager.open() as item:\n"
        "            return item.read()\n"
        "    except LookupError as exc:\n"
        "        raise RuntimeError() from exc"
    )

    result = analyze_source(source)
    kinds = {fact["kind"] for fact in result["facts"]}

    assert {"try", "with", "return", "except_handler", "raise", "call"} <= kinds
    handler = next(fact for fact in result["facts"] if fact["kind"] == "except_handler")
    assert handler["exception"] == "LookupError"
    assert handler["bound_name"] == "exc"
    assert "path-feasibility" in " ".join(result["limitations"]).lower()


def test_dependency_usage_reports_only_same_expression_assignment_context() -> None:
    text = (
        "def target(dep):\n"
        "    result = dep.fetch()\n"
        "    if result:\n"
        "        result = 'shadowed'\n"
        "    return result"
    )
    tools = SourceAnalysis(_task(text))

    result = asyncio.run(tools.inspect_dependency_usage("mut:0"))

    assert result["status"] == "ok"
    call = next(fact for fact in result["facts"] if fact["kind"] == "call")
    assert call["callee"] == "dep.fetch"
    assert call["resolution_status"] == "unresolved"
    assert call["syntax_context"] == {
        "kind": "assignment_rhs",
        "targets": ["result"],
        "relation": "syntactic_containment",
    }
    serialized = repr(result["facts"])
    assert "reaching" not in serialized.lower()
    assert "declaration_line" not in serialized
    assert "flows_from" not in serialized


def test_named_expression_is_a_same_expression_relation() -> None:
    tools = SourceAnalysis(_task(
        "def target(dep):\n"
        "    if (value := dep.read()) is not None:\n"
        "        return value"
    ))

    result = asyncio.run(tools.inspect_dependency_usage("mut:0"))

    named = next(fact for fact in result["facts"] if fact["kind"] == "named_expression")
    assert named["target"] == "value"
    assert named["relation"] == "same_expression_assignment"
    call = next(fact for fact in result["facts"] if fact["kind"] == "call")
    assert call["syntax_context"]["kind"] == "assignment_rhs"


def test_with_body_and_nested_callee_contexts_are_precise() -> None:
    tools = SourceAnalysis(_task(
        "def target(manager, factory):\n"
        "    with manager.open() as item:\n"
        "        return (factory())(item)"
    ))

    result = asyncio.run(tools.inspect_dependency_usage("mut:0"))
    calls = {fact["callee"]: fact for fact in result["facts"] if fact["kind"] == "call"}

    assert calls["manager.open"]["syntax_context"]["kind"] == "with_context"
    assert calls["factory"]["syntax_context"]["kind"] == "call_callee"
    assert calls["factory()"]["syntax_context"]["kind"] == "return_value"


def test_comprehension_is_explicitly_unsupported_as_branch_analysis() -> None:
    result = analyze_source(_source(
        "def target(values):\n"
        "    return [dep(value) for value in values if allowed(value)]"
    ))

    unsupported = next(
        fact for fact in result["facts"] if fact["kind"] == "unsupported_construct"
    )
    assert unsupported["construct"] == "list_comprehension"
    assert unsupported["resolution_status"] == "unsupported"
    assert {fact["callee"] for fact in result["facts"] if fact["kind"] == "call"} == {
        "dep", "allowed"
    }


def test_match_cases_and_guard_are_lexically_reported() -> None:
    result = analyze_source(_source(
        "def target(value):\n"
        "    match value:\n"
        "        case item if valid(item):\n"
        "            return use(item)\n"
        "        case _:\n"
        "            return fallback()"
    ))

    branches = [fact for fact in result["facts"] if fact["kind"] == "branch"]
    assert [fact["branch"] for fact in branches] == ["case_0", "case_1"]
    guard = next(
        fact for fact in result["facts"]
        if fact["kind"] == "condition" and fact["construct"] == "match_guard"
    )
    assert guard["expression"] == "valid(item)"
    use = next(
        fact for fact in result["facts"]
        if fact["kind"] == "call" and fact["callee"] == "use"
    )
    assert use["lexical_guards"][-1]["branch"] == "case_0"


def test_assert_is_reported_as_a_condition_without_feasibility_claim() -> None:
    result = analyze_source(_source(
        "def target(value):\n"
        "    assert valid(value), 'invalid'\n"
        "    return value"
    ))

    condition = next(
        fact for fact in result["facts"]
        if fact["kind"] == "condition" and fact["construct"] == "assert"
    )
    assert condition["expression"] == "valid(value)"
    assert "path-feasibility" in " ".join(result["limitations"])


def test_parse_failure_is_analysis_error_not_empty_success() -> None:
    result = analyze_source(_source("def broken(:\n    pass", start_line=90))

    assert result["status"] == "error"
    assert result["error_code"] == "analysis_error"
    assert result["failure_kind"] == "parse_error"
    assert result["facts"] == []
    assert "visible line 90" in result["diagnostics"][0]


def test_direct_size_limit_describes_direct_parser_deadline() -> None:
    import mock_spec.python_analysis as python_analysis

    result = analyze_source(_source("x" * (python_analysis._MAX_SOURCE_CHARS + 1)))

    limitations = " ".join(result["limitations"])
    assert "direct parser has no independent wall-clock deadline" in limitations
    assert "isolated" in limitations


def test_isolated_size_limit_reports_host_deadline() -> None:
    import mock_spec.python_analysis as python_analysis

    result = asyncio.run(analyze_source_isolated(
        _source("x" * (python_analysis._MAX_SOURCE_CHARS + 1)),
        timeout_seconds=5,
    ))

    limitations = " ".join(result["limitations"])
    assert "host enforces the configured wall-clock deadline" in limitations
    assert "direct parser has no independent wall-clock deadline" not in limitations


class _HangingProcess:
    def __init__(self) -> None:
        self.returncode = None
        self.killed = False
        self._done = asyncio.Event()

    async def wait(self) -> int:
        await self._done.wait()
        assert self.returncode is not None
        return self.returncode

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9
        self._done.set()


def test_isolated_analysis_timeout_kills_reaps_and_scrubs_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = _HangingProcess()
    captured = {}

    async def fake_exec(*args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return process

    monkeypatch.setattr(analysis_process.asyncio, "create_subprocess_exec", fake_exec)
    monkeypatch.setenv("OPENAI_API_KEY", "must-not-reach-python-analysis")

    result = asyncio.run(analyze_source_isolated(
        _source("def target():\n    return dependency()"),
        timeout_seconds=0.01,
    ))

    assert result["status"] == "error"
    assert result["error_code"] == "timeout"
    assert result["failure_kind"] == "timeout"
    assert process.killed and process.returncode == -9
    assert captured["kwargs"]["env"] == {"LANG": "C.UTF-8", "LC_ALL": "C.UTF-8"}
    command = captured["args"]
    assert command[0] != "dependency"
    assert "-I" in command
    assert all("must-not-reach-python-analysis" not in str(item) for item in command)


def test_isolated_malformed_backend_result_has_distinct_failure_kind(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import mock_spec.python_analysis as python_analysis

    async def malformed(*args, **kwargs):
        return 0, b"not-json", b""

    monkeypatch.setattr(python_analysis, "_communicate", malformed)
    result = asyncio.run(analyze_source_isolated(
        _source("def target():\n    pass"), timeout_seconds=5
    ))

    assert result["error_code"] == "analysis_error"
    assert result["failure_kind"] == "malformed_result"


def test_isolated_analysis_cancellation_kills_and_reaps(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = _HangingProcess()

    async def fake_exec(*args, **kwargs):
        return process

    monkeypatch.setattr(analysis_process.asyncio, "create_subprocess_exec", fake_exec)

    async def cancel() -> None:
        task = asyncio.create_task(analyze_source_isolated(
            _source("def target():\n    return dependency()"),
            timeout_seconds=5,
        ))
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(cancel())
    assert process.killed and process.returncode == -9


def test_isolated_analysis_never_executes_visible_source(tmp_path: Path) -> None:
    marker = tmp_path / "must-not-exist"
    source = _source(
        "def target():\n"
        f"    __import__('pathlib').Path({str(marker)!r}).write_text('executed')\n"
        "    return dependency()"
    )

    result = asyncio.run(analyze_source_isolated(source))

    assert result["status"] == "ok"
    assert not marker.exists()
    assert any(fact["kind"] == "call" for fact in result["facts"])


def test_source_analysis_passes_configured_python_analysis_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import mock_spec.python_analysis as python_analysis

    seen = {}

    async def timeout(source, *, timeout_seconds):
        seen["source_id"] = source.source_id
        seen["timeout_seconds"] = timeout_seconds
        return {
            "status": "error", "backend": "cpython_ast",
            "source_id": source.source_id, "error_code": "timeout", "facts": [],
            "diagnostics": ["timed out"], "limitations": ["absence unsafe"],
        }

    monkeypatch.setattr(python_analysis, "analyze_source_isolated", timeout)
    tools = SourceAnalysis(
        _task("def target():\n    return dependency()"),
        analysis_timeout_seconds=0.25,
    )

    result = asyncio.run(tools.inspect_program_structure("mut:0"))

    assert result["error_code"] == "timeout"
    assert seen == {"source_id": "mut:0", "timeout_seconds": 0.25}


def test_program_analysis_rejects_path_like_source_id() -> None:
    tools = SourceAnalysis(_task("def target():\n    return dep()"))

    for method in (tools.inspect_program_structure, tools.inspect_dependency_usage):
        result = asyncio.run(method("../private/ground_truth.py"))
        assert result["status"] == "error"
        assert result["error_code"] == "invalid_source"
        assert result["facts"] == []


def test_analysis_output_limit_returns_explicit_useful_prefix() -> None:
    calls = "\n".join(f"    dep_{index}()" for index in range(30))
    tools = SourceAnalysis(_task(f"def target():\n{calls}"), max_output_chars=1000)

    result = asyncio.run(tools.inspect_program_structure("mut:0"))

    assert result["status"] == "incomplete"
    assert result["error_code"] == "output_limit"
    assert result["returned_fact_count"] < result["total_fact_count"]
    assert result["facts"]
    assert "absence" in " ".join(result["limitations"]).lower()


@pytest.mark.real_data
def test_flask_308_ast_uses_reviewed_visible_text_and_original_lines() -> None:
    from test_real_data import REVIEWS, load_python, snapshot

    review = next(item for item in REVIEWS if item["case_id"] == "flask_308")
    task = load_python(snapshot(review), case_id=review["case_id"])
    tools = SourceAnalysis(task)

    result = asyncio.run(tools.inspect_program_structure("mut"))

    assert result["status"] == "ok"
    calls = {fact["callee"]: fact for fact in result["facts"] if fact["kind"] == "call"}
    assert calls["_cv_app.get"]["start_line"] == 282
    assert calls["ctx.app.aborter"]["start_line"] == 283
    assert calls["ctx.app.aborter"]["lexical_guards"][-1]["polarity"] is True
    assert calls["_wz_abort"]["start_line"] == 285

