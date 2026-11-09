import json
from pathlib import Path
import subprocess


HERE = Path(__file__).resolve().parent
TOOLS = Path("/tmp/mockspec-step5b-tools")


def run_paths(tmp_path, sources, entry_methods=None):
    subprocess.run([str(TOOLS / "jdk/bin/javac"), "-cp", str(TOOLS / "spoon.jar"),
                    "-d", str(tmp_path), str(HERE / "JavaPaths.java")], check=True)
    proc = subprocess.run([str(TOOLS / "jdk/bin/java"), "-cp",
                           f"{tmp_path}:{TOOLS / 'spoon.jar'}", "JavaPaths"],
                          input=json.dumps({"sources": sources, "boundary_types": [],
                                            "entry_methods": entry_methods or []}),
                          text=True, capture_output=True, check=True)
    return json.loads(proc.stdout)


def test_cross_method_transform_and_mutually_exclusive_guards(tmp_path):
    sources = [
        {"source_id": "base", "logical_path": "p/Base.java", "text": """
package p;
class Base {
  String compose(String input) { return prefix() + normalize(input); }
  String prefix() { return "base:"; }
  String normalize(String input) {
    String clean = input.trim();
    if (clean.isEmpty()) return "empty";
    else return clean;
  }
}
"""},
        {"source_id": "child", "logical_path": "p/Child.java", "text": """
package p;
class Child extends Base {
  @Override String prefix() { return "child:"; }
}
"""},
    ]
    result = run_paths(tmp_path, sources, ["Base.compose(java.lang.String)"])
    normalize = next(m for m in result["methods"] if ".normalize(" in m["qualified_signature"])
    compose = next(m for m in result["methods"] if ".compose(" in m["qualified_signature"])
    polarities = {g["polarity"] for ret in normalize["returns"] for g in ret["guards"]}
    assert polarities == {"true", "false"}
    assert normalize["assignments"][0]["name"] == "clean"
    prefix = next(c for c in compose["calls"] if c["name"] == "prefix")
    assert prefix["resolution"] == "dynamic_dispatch_candidates"
    assert any("Child.prefix" in target for target in prefix["target_candidates"])
    assert result["bounds"]["max_expansion_depth"] == 5


def test_unknown_library_call_stays_opaque(tmp_path):
    result = run_paths(tmp_path, [{"source_id": "one", "logical_path": "One.java", "text": """
class One { String value(String x) { return External.convert(x); } }
"""}])
    method = result["methods"][0]
    call = method["calls"][0]
    assert call["resolution"] == "opaque_or_unresolved"
    assert call["normal_outcome"] == "opaque_value_if_returns_normally"
    assert call["exception_outcome"] == "possible_unknown_exception"


def test_following_statement_gets_only_proven_early_exit_guards(tmp_path):
    result = run_paths(tmp_path, [{"source_id": "one", "logical_path": "Early.java", "text": """
class Early {
  String value(String x) {
    if (x == null) return "missing";
    if (x.isEmpty()) throw new IllegalArgumentException();
    return External.convert(x);
  }
}
"""}])
    method = result["methods"][0]
    final_return = next(item for item in method["returns"] if "External.convert" in item["expression"])
    sequential = [g for g in final_return["guards"]
                  if g["kind"] == "sequential_early_exit_guard"]
    assert [(g["condition"], g["polarity"]) for g in sequential] == [
        ("x == null", "false"), ("x.isEmpty()", "false")]
