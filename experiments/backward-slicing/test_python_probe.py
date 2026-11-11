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


def test_dependency_profile_tracks_assignment_without_unrelated_predecessor():
    source = """def value():
    return dependency.read()
def test_value():
    unrelated()
    result = value()
    assert result
"""
    result = run_probe({"sources": [{"source_id": "test", "logical_path": "test.py", "text": source}],
                        "seeds": [{"source_id": "test", "line": 6}], "dependency_only": True})
    assert not any(edge["kind"] == "normal_predecessor" for edge in result["edges"])
    assert any(node["start_line"] == 2 for node in result["nodes"])
    assert not any(node["start_line"] == 4 for node in result["nodes"])


def test_opaque_body_is_not_used_as_exception_evidence():
    source = """def placeholder():
    raise NotImplementedError()
def test_value():
    result = placeholder()
    assert result
"""
    result = run_probe({"sources": [{"source_id": "test", "logical_path": "test.py", "text": source}],
                        "seeds": [{"source_id": "test", "line": 5}], "dependency_only": True,
                        "opaque_methods": [{"source_id": "test", "name": "placeholder", "start_line": 1, "end_line": 2}]})
    assert any(node.get("contains_boundary_call") for node in result["nodes"])
    assert not any(node["start_line"] in {1, 2} for node in result["nodes"])


def test_nested_function_write_is_not_an_outer_definition():
    source = """def outer():
    x = source()
    def nested():
        x = unrelated()
        return x
    return x
"""
    result = run_probe({"sources": [{"source_id": "one", "logical_path": "one.py", "text": source}],
                        "seeds": [{"source_id": "one", "line": 6}], "dependency_only": True})
    assert any(node["start_line"] == 2 for node in result["nodes"])
    assert not any(node["start_line"] in {3, 4, 5} for node in result["nodes"])


def test_same_line_guard_does_not_replace_assertion_seed():
    result = run_probe({"sources": [{"source_id": "test", "logical_path": "test.py",
                                     "text": "def test_value():\n    if flag: assert value\n"}],
                        "seeds": [{"source_id": "test", "line": 2, "kind": "value_assertion",
                                   "criterion_id": "criterion:0"}], "dependency_only": True})
    seed = result["seeds"][0]
    assert next(node for node in result["nodes"] if node["id"] == seed)["kind"] == "Assert"
    assert result["seed_bindings"] == [{"criterion_id": "criterion:0", "seed": seed}]


def test_loop_candidate_seed_is_not_confused_with_target_variable_nodes():
    result = run_probe({"sources": [{"source_id": "test", "logical_path": "test.py",
        "text": "def test_value():\n    for index, value in enumerate(expected):\n        pass\n"}],
        "seeds": [{"source_id": "test", "line": 2, "kind": "candidate_event",
                   "call": "enumerate", "criterion_id": "criterion:0"}], "dependency_only": True})
    seed = result["seeds"][0]
    assert next(node for node in result["nodes"] if node["id"] == seed)["kind"] == "For"


def test_static_exec_loader_connects_visible_function_to_bound_callable():
    test_source = """from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / 'src' / 'pkg' / 'encryption.py'
def load_target():
    lines = MODULE_PATH.read_text().splitlines()
    start_line = 1
    end_line = 2
    function_source = '\\n'.join(lines[start_line - 1:end_line])
    namespace = {}
    exec(compile(function_source, str(MODULE_PATH), 'exec'), namespace)
    return namespace['target']
def test_target():
    target_fn = load_target()
    result = target_fn()
    assert result
"""
    product_source = "def target():\n    return dependency.read()\n"
    result = run_probe({
        "sources": [
            {"source_id": "test", "logical_path": "tests/test_target.py", "text": test_source},
            {"source_id": "source:1", "logical_path": "src/pkg/encryption.py", "text": product_source},
        ],
        "seeds": [{"source_id": "test", "line": 15}],
        "dependency_only": True,
    })

    assert result["dynamic_loader_bindings"][0]["status"] == "resolved"
    edges = result["edges"]
    assert any(edge["kind"] == "dynamic_function_origin"
               and edge["precision"] == "visible_path_namespace_key_and_definition"
               for edge in edges)
    assert any(node["source_id"] == "source:1" and node["kind"] == "dynamic_function_definition"
               for node in result["nodes"])
    assert not any(node.get("unresolved_calls") == ["target_fn()"] for node in result["nodes"])


def test_dynamic_namespace_key_remains_unknown_and_does_not_bind_same_named_definition():
    test_source = """from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / 'src' / 'pkg' / 'encryption.py'
def load_target(key):
    namespace = {}
    exec(compile(MODULE_PATH.read_text(), str(MODULE_PATH), 'exec'), namespace)
    return namespace[key]
target_fn = load_target('target')
assert target_fn()
"""
    result = run_probe({
        "sources": [
            {"source_id": "test", "logical_path": "tests/test_target.py", "text": test_source},
            {"source_id": "source:1", "logical_path": "src/pkg/encryption.py",
             "text": "def target():\n    return True\n"},
        ],
        "seeds": [{"source_id": "test", "line": 9}],
        "dependency_only": True,
    })

    assert result["dynamic_loader_bindings"][0]["status"] == "unknown"
    assert result["dynamic_loader_bindings"][0]["reason"] == "dynamic_namespace_key"
    assert not any(edge["kind"] == "dynamic_function_origin" for edge in result["edges"])
