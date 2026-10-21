import asyncio
from pathlib import Path

import pytest

from mock_spec import java_analysis
from mock_spec.adapters.java import load_task as load_java
from mock_spec.java_analysis import analyze_source
from mock_spec.schemas import Source


JAVA_TEXT = """class Sample {
  int inspect(int value) {
    int chosen = value > 1 ? value : 1;
    try {
      if (chosen > 0) {
        return helper(chosen);
      } else {
        throw new IllegalArgumentException();
      }
    } catch (RuntimeException problem) {
      return problem.hashCode();
    }
  }
  int helper(int value) { return value; }
}"""


def source(text: str = JAVA_TEXT) -> Source:
    return Source(
        source_id="mut:sample",
        logical_path="src/Sample.java",
        text=text,
        symbol="Sample.inspect",
        start_line=40,
        end_line=40 + len(text.splitlines()) - 1,
        resolution_status="resolved",
        target_precision="class",
    )


def test_real_spoon_extracts_bounded_structural_and_local_reference_facts() -> None:
    if java_analysis._backend_paths() is None:
        pytest.skip("pinned Spoon backend is unavailable")
    result = asyncio.run(analyze_source(source()))

    assert result["status"] == "ok"
    assert result["backend"] == "spoon"
    assert result["source_id"] == "mut:sample"
    kinds = {fact["kind"] for fact in result["facts"]}
    assert {
        "method", "call", "variable_read", "if", "conditional",
        "return", "throw", "try", "catch",
    } <= kinds
    assert all(
        fact["source_id"] == "mut:sample"
        and 40 <= fact["start_line"] <= fact["end_line"] <= source().end_line
        for fact in result["facts"]
    )

    helper_call = next(
        fact for fact in result["facts"]
        if fact["kind"] == "call" and fact["name"] == "helper"
    )
    assert helper_call["declaration_status"] == "resolved_local"
    assert helper_call["declared_at"]["source_id"] == "mut:sample"
    assert helper_call["lexical_guards"][-1]["branch"] == "true"
    assert helper_call["quote"] == "        return helper(chosen);"

    external_call = next(
        fact for fact in result["facts"]
        if fact["kind"] == "call" and fact["name"] == "hashCode"
    )
    assert external_call["declaration_status"] == "unresolved"
    assert external_call["declared_at"] is None
    assert any("not proof of data flow" in item for item in result["limitations"])
    assert any("loop, switch, and lambda" in item for item in result["limitations"])


def test_missing_or_unpinned_backend_is_explicit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MOCKSPEC_SPOON_JAR", "/not/visible/spoon.jar")
    monkeypatch.setenv("MOCKSPEC_JAVA", "/not/visible/java")

    result = asyncio.run(analyze_source(source()))

    assert result["status"] == "error"
    assert result["error_code"] == "backend_unavailable"
    assert result["facts"] == []
    assert result["diagnostics"][0]["category"] == "unsupported"


@pytest.mark.parametrize("timeout", [0, -1, float("nan"), float("inf"), True])
def test_timeout_must_be_positive_finite_number(timeout: float) -> None:
    with pytest.raises(ValueError, match="positive and finite"):
        asyncio.run(analyze_source(source(), timeout_seconds=timeout))


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


class _CompletedProcess:
    returncode = 0

    async def wait(self) -> int:
        return 0

    def kill(self) -> None:
        raise AssertionError("completed process must not be killed")


def test_subprocess_timeout_kills_and_reaps_and_scrubs_environment(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    process = _HangingProcess()
    captured = {}

    async def fake_exec(*args, **kwargs):
        captured.update(kwargs)
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    monkeypatch.setenv("OPENAI_API_KEY", "must-not-reach-spoon")

    with pytest.raises(asyncio.TimeoutError):
        asyncio.run(java_analysis._communicate(
            ["java"], input_bytes=b"{}", timeout_seconds=0.01, cwd=str(tmp_path)
        ))

    assert process.killed
    assert process.returncode == -9
    assert captured["env"] == {"LANG": "C.UTF-8", "LC_ALL": "C.UTF-8"}


def test_subprocess_cancellation_kills_and_reaps(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    process = _HangingProcess()

    async def fake_exec(*args, **kwargs):
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)

    async def cancel() -> None:
        task = asyncio.create_task(java_analysis._communicate(
            ["java"], input_bytes=None, timeout_seconds=5, cwd=str(tmp_path)
        ))
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(cancel())
    assert process.killed
    assert process.returncode == -9


def test_subprocess_output_limit_is_enforced_while_using_files(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    async def fake_exec(*args, **kwargs):
        kwargs["stdout"].write(b"123456789")
        kwargs["stdout"].flush()
        return _CompletedProcess()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    monkeypatch.setattr(java_analysis, "_MAX_PROCESS_OUTPUT", 8)

    with pytest.raises(OverflowError, match="output exceeded"):
        asyncio.run(java_analysis._communicate(
            ["java"], input_bytes=None, timeout_seconds=1, cwd=str(tmp_path)
        ))


@pytest.mark.real_data
def test_jarfiles_reviewed_mut_runs_through_current_spoon() -> None:
    from test_real_data import REVIEWS, snapshot

    review = next(item for item in REVIEWS if item["language"] == "java")
    if java_analysis._backend_paths() is None:
        pytest.skip("pinned Spoon backend is unavailable")
    task = load_java(snapshot(review), case_id=review["case_id"])

    result = asyncio.run(analyze_source(task.mut_source))

    assert result["status"] in {"ok", "incomplete"}
    assert result["source_id"] == task.mut_source.source_id
    assert any(fact["kind"] == "method" for fact in result["facts"])
    assert any(fact["kind"] == "call" for fact in result["facts"])
    assert all(
        task.mut_source.start_line <= fact["start_line"]
        <= fact["end_line"] <= task.mut_source.end_line
        for fact in result["facts"]
    )
