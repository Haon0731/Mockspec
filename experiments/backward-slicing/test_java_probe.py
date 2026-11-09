import json
from pathlib import Path
import subprocess

import pytest


HERE = Path(__file__).resolve().parent
TOOLS = Path("/tmp/mockspec-step5b-tools")


def run_probe(tmp_path: Path, request: dict) -> dict:
    subprocess.run(
        [str(TOOLS / "jdk/bin/javac"), "-cp", str(TOOLS / "spoon.jar"),
         "-d", str(tmp_path), str(HERE / "SliceProbe.java")],
        check=True, capture_output=True, text=True,
    )
    completed = subprocess.run(
        [str(TOOLS / "jdk/bin/java"), "-cp", f"{tmp_path}:{TOOLS / 'spoon.jar'}", "SliceProbe"],
        input=json.dumps(request), check=True, capture_output=True, text=True,
    )
    return json.loads(completed.stdout)


def test_cross_file_slice_keeps_typed_evidence_and_boundary(tmp_path):
    request = {
        "sources": [
            {"source_id": "service", "logical_path": "demo/Service.java", "text": """
package demo;
class Service {
  String transform(String input) {
    return hook(input);
  }
  protected String hook(String input) {
    return input;
  }
}
"""},
            {"source_id": "specialized", "logical_path": "demo/Specialized.java", "text": """
package demo;
class Specialized extends Service {
  @Override protected String hook(String input) {
    String changed = input.trim();
    if (changed.isEmpty()) return "fallback";
    return changed;
  }
}
"""},
            {"source_id": "client", "logical_path": "demo/Client.java", "text": """
package demo;
class Client {
  External external;
  Service service;
  String execute(String raw) {
    String value = raw;
    value = external.load(value);
    String result = service.transform(value);
    if (result.length() > 2) return result;
    return "short";
  }
}
"""},
        ],
        "seeds": [{"source_id": "client", "line": 10}],
        "boundary_types": ["External"],
    }
    result = run_probe(tmp_path, request)
    kinds = {edge["kind"] for edge in result["edges"]}
    assert result["status"] == "ok"
    assert "call_return" in kinds
    assert "expression_value_candidate" in kinds
    assert "data_candidate" in kinds
    assert "lexical_control" in kinds
    assert any(node.get("contains_boundary_call") for node in result["nodes"])
    assert any(node["source_id"] == "service" for node in result["nodes"])
    assert any(node["source_id"] == "specialized" for node in result["nodes"])
    assert any(edge.get("precision") == "name_arity_candidates" for edge in result["edges"])
    assert any(node.get("contains_unknown_call") for node in result["nodes"])
    assert any("not a sound reaching-def" in item for item in result["limitations"])


def test_prior_reassignment_is_a_candidate_but_future_write_is_not(tmp_path):
    source = """
class Reassign {
  String go(String original) {
    String value = original;
    value = original.trim();
    String answer = value;
    value = "future";
    return answer;
  }
}
"""
    result = run_probe(tmp_path, {
        "sources": [{"source_id": "one", "logical_path": "Reassign.java", "text": source}],
        "seeds": [{"source_id": "one", "line": 8}],
        "boundary_types": [],
    })
    candidate_lines = {
        next(node["start_line"] for node in result["nodes"] if node["id"] == edge["from"])
        for edge in result["edges"] if edge["kind"] == "data_candidate"
    }
    assert 5 in candidate_lines
    assert 7 not in candidate_lines


def test_opaque_placeholder_stops_before_throw(tmp_path):
    source = """class Example {
  String placeholder() { throw new UnsupportedOperationException(); }
  String use() {
    String result = placeholder();
    return result;
  }
}
"""
    result = run_probe(tmp_path, {
        "sources": [{"source_id": "one", "logical_path": "Example.java", "text": source}],
        "seeds": [{"source_id": "one", "line": 5}],
        "opaque_methods": [{"source_id": "one", "name": "placeholder", "start_line": 2, "end_line": 2}],
    })
    assert any(node.get("contains_boundary_call") for node in result["nodes"])
    assert not any(node["start_line"] == 2 for node in result["nodes"])


def test_typed_seed_binds_assertion_inside_same_line_if(tmp_path):
    source = """class Example {
  int product() { return 1; }
  void test(boolean enabled) {
    int result = product();
    if (enabled) assertEquals(1, result);
  }
}
"""
    result = run_probe(tmp_path, {
        "sources": [{"source_id": "one", "logical_path": "Example.java", "text": source}],
        "seeds": [{"source_id": "one", "line": 5, "kind": "value_assertion",
                   "call": "assertEquals", "criterion_id": "criterion-1"}],
    })
    assert "assertEquals" in next(
        node["code"] for node in result["nodes"] if node["id"] == result["seeds"][0]
    )
    assert result["seed_bindings"] == [
        {"criterion_id": "criterion-1", "seed": result["seeds"][0]}
    ]


def test_multiple_opaque_calls_are_retained_on_one_caller(tmp_path):
    source = """class Example {
  String first() { throw new UnsupportedOperationException(); }
  String second() { throw new UnsupportedOperationException(); }
  String use() {
    return first() + second();
  }
}
"""
    result = run_probe(tmp_path, {
        "sources": [{"source_id": "one", "logical_path": "Example.java", "text": source}],
        "seeds": [{"source_id": "one", "line": 5}],
        "opaque_methods": [
            {"source_id": "one", "name": "first", "start_line": 2, "end_line": 2},
            {"source_id": "one", "name": "second", "start_line": 3, "end_line": 3},
        ],
    })
    boundary = next(node for node in result["nodes"] if node.get("contains_boundary_call"))
    assert boundary["boundary_calls"] == ["first()", "second()"]


def test_typed_seed_rejects_ambiguous_same_line_observations(tmp_path):
    source = """class Example {
  void test() {
    assertEquals(1, first()); assertEquals(2, second());
  }
}
"""
    with pytest.raises(subprocess.CalledProcessError) as raised:
        run_probe(tmp_path, {
            "sources": [{"source_id": "one", "logical_path": "Example.java", "text": source}],
            "seeds": [{"source_id": "one", "line": 3, "kind": "value_assertion",
                       "call": "assertEquals", "criterion_id": "criterion-1"}],
        })
    assert "criterion seed is ambiguous" in raised.value.stderr
