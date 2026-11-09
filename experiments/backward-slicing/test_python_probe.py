import json
from pathlib import Path
import subprocess
import sys


PROBE = Path(__file__).with_name("python_probe.py")


def run_probe(request):
    completed = subprocess.run(
        [sys.executable, str(PROBE)], input=json.dumps(request), text=True,
        capture_output=True, check=True,
    )
    return json.loads(completed.stdout)


def test_named_expression_guard_and_normal_return_successor_are_distinct():
    source = """
def abort(code):
    if ctx := query.get(None):
        ctx.app.aborter(code)
    fallback(code)

def test_behavior():
    abort(403)
    assert True
"""
    result = run_probe({
        "sources": [{"source_id": "case", "logical_path": "case.py", "start_line": 1,
                     "text": source}],
        "seeds": [{"source_id": "case", "line": 5}],
    })
    kinds = {edge["kind"] for edge in result["edges"]}
    assert "data_candidate" in kinds
    assert "lexical_control" in kinds
    assert "normal_branch_exit" in kinds
    assert "normal_predecessor" in kinds
    assert any(node.get("exception_possible") for node in result["nodes"])
    assert any(node.get("contains_unresolved_call") for node in result["nodes"])
    assert any("does not prove branch feasibility" in item for item in result["limitations"])


def test_automatic_assert_and_raises_seeds():
    source = """
import pytest
def operation():
    dependency.event()
def test_one():
    with pytest.raises(RuntimeError):
        operation()
    assert check()
"""
    result = run_probe({
        "sources": [{"source_id": "test", "logical_path": "test_one.py", "text": source}],
        "seeds": [],
    })
    assert len(result["seeds"]) == 2
    assert any(edge["kind"] == "call_event_candidate" for edge in result["edges"])
    assert any("does not guarantee" in item for item in result["limitations"])
