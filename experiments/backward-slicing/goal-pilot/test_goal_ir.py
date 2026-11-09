import json
from pathlib import Path
import subprocess


HERE = Path(__file__).resolve().parent
TOOLS = Path("/tmp/mockspec-step5b-tools")


def run_ir(tmp_path, *sources):
    subprocess.run(
        [str(TOOLS / "jdk/bin/javac"), "-Xlint", "-cp", str(TOOLS / "spoon.jar"),
         "-d", str(tmp_path), str(HERE / "GoalIR.java")],
        check=True, capture_output=True, text=True,
    )
    process = subprocess.run(
        [str(TOOLS / "jdk/bin/java"), "-cp", f"{tmp_path}:{TOOLS / 'spoon.jar'}", "GoalIR"],
        input=json.dumps({"sources": list(sources)}), check=True, capture_output=True, text=True,
    )
    return json.loads(process.stdout)


def method(result, type_suffix, name):
    owner = next(item for item in result["types"] if item["name"].endswith(type_suffix))
    return next(item for item in owner["methods"] if item["name"] == name)


def test_constructor_state_super_call_and_locations(tmp_path):
    result = run_ir(tmp_path,
        {"source_id": "base", "logical_path": "p/Base.java", "start_line": 40, "text": """
package p;
class Base { Base(String value) {} }
"""},
        {"source_id": "child", "logical_path": "p/Child.java", "start_line": 100, "text": """
package p;
class Child extends Base {
  private final String value;
  private String absent;
  Child(String input) {
    super(input.trim());
    this.value = input;
  }
}
"""},
    )
    child = next(item for item in result["types"] if item["name"] == "p.Child")
    assert child["superclass"] == "p.Base"
    assert next(field for field in child["fields"] if field["name"] == "absent")["initializer"] is None
    body = child["constructors"][0]["body"]["statements"]
    assert body[0]["kind"] == "expr"
    assert body[0]["value"]["kind"] == "call"
    assert body[0]["value"]["name"] == "<init>"
    assert body[0]["value"]["target"]["kind"] == "super"
    assert body[0]["value"]["target"]["type"] == "p.Base"
    assert body[0]["value"]["arguments"][0]["kind"] == "call"
    assignment = body[1]
    assert assignment["kind"] == "assign"
    assert assignment["target"]["kind"] == "field"
    assert assignment["target"]["target"]["kind"] == "this"
    assert assignment["location"]["source_id"] == "child"
    assert assignment["location"]["start_line"] == 107


def test_callsite_identity_arguments_and_assertion_literal(tmp_path):
    result = run_ir(tmp_path, {"source_id": "test", "logical_path": "T.java", "text": """
class T {
  String get() { return dependency.read("health"); }
  void test() { assertEquals("/actuator/health", app.getHealthUrl()); }
}
"""})
    get_call = method(result, "T", "get")["body"]["statements"][0]["value"]
    assertion = method(result, "T", "test")["body"]["statements"][0]["value"]
    assert get_call["kind"] == "call"
    assert get_call["arguments"][0]["value"] == "health"
    assert get_call["location"]["start_column"] > 1
    assert assertion["kind"] == "call"
    assert assertion["name"] == "assertEquals"
    assert assertion["arguments"][0] == {
        "kind": "literal", "text": '"/actuator/health"',
        "location": assertion["arguments"][0]["location"], "value": "/actuator/health",
    }
    actual = assertion["arguments"][1]
    assert actual["kind"] == "call"
    assert actual["name"] == "getHealthUrl"
    assert actual["location"] != get_call["location"]


def test_unsupported_statement_and_opaque_lambda_are_explicit(tmp_path):
    result = run_ir(tmp_path, {"source_id": "one", "logical_path": "One.java", "text": """
class One {
  void run(java.util.List<String> values) {
    values.forEach(value -> System.out.println(value));
    while (!values.isEmpty()) values.remove(0);
  }
}
"""})
    statements = method(result, "One", "run")["body"]["statements"]
    assert statements[0]["value"]["arguments"][0]["kind"] == "lambda"
    assert statements[0]["value"]["arguments"][0]["opaque"] is True
    assert statements[1]["kind"] == "unsupported_statement"
    assert {item["category"] if "category" in item else item["kind"] for item in result["unsupported"]}
    assert any(item["node_type"].startswith("CtWhile") for item in result["unsupported"])
