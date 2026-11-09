import json
from pathlib import Path
import subprocess
import sys


HERE = Path(__file__).resolve().parent
TOOLS = Path("/tmp/mockspec-step5b-tools")
sys.path.insert(0, str(HERE))
from compose import Composer  # noqa: E402


def ir(tmp_path, *sources):
    subprocess.run([str(TOOLS / "jdk/bin/javac"), "-cp", str(TOOLS / "spoon.jar"),
                    "-d", str(tmp_path), str(HERE / "GoalIR.java")], check=True)
    process = subprocess.run(
        [str(TOOLS / "jdk/bin/java"), "-cp", f"{tmp_path}:{TOOLS / 'spoon.jar'}", "GoalIR"],
        input=json.dumps({"sources": list(sources)}), text=True, capture_output=True, check=True)
    return json.loads(process.stdout)


def source(source_id, path, text):
    return {"source_id": source_id, "logical_path": path, "text": text}


def test_projects_lombok_builder_property_and_dynamic_override(tmp_path):
    result = Composer(ir(tmp_path,
        source("product", "p/Product.java", """
package p;
@lombok.Data class Product {
  private final String value;
  @lombok.Builder Product(String value) { this.value = value; }
  static Builder create() { return Product.builder(); }
}
"""),
        source("base", "p/Base.java", """
package p;
class Base {
  final external.Dependency dependency;
  Base(external.Dependency dependency) { this.dependency = dependency; }
  Product createProduct() { return Product.create().value(value()).build(); }
  String value() { return "base"; }
}
"""),
        source("child", "p/Child.java", """
package p;
class Child extends Base {
  Child(external.Dependency dependency) { super(dependency); }
  @Override String value() { return dependency.read("selected"); }
}
"""),
        source("test", "p/Test.java", """
package p;
class Test {
  @org.junit.Test void observes() {
    Child factory = new Child(dependency);
    Product product = factory.createProduct();
    org.junit.Assert.assertEquals("wanted", product.getValue());
  }
}
"""),
    )).compose("value")
    assert len(result["goals"]) == 1
    goal = result["goals"][0]
    assert goal["expected"] == "wanted"
    assert goal["truncated"] is False
    assert goal["projection"]["kind"] == "lombok_data_builder_property"
    normal = next(path for path in goal["paths"] if path["outcome"] == "return")
    assert normal["events"][0]["name"] == "read"
    assert normal["events"][0]["arguments"] == [{"kind": "literal", "value": "selected"}]
    assert normal["value"]["id"] == normal["events"][0]["result_symbol"]
    assert any(place["source_id"] == "child" for place in normal["evidence"])
    assert any(place["source_id"] == "base" for place in normal["evidence"])


def test_repeated_boundary_calls_get_fresh_results_and_exception_paths(tmp_path):
    result = Composer(ir(tmp_path,
        source("product", "q/Product.java", """
package q; @lombok.Data class Product { final String value; @lombok.Builder Product(String value){this.value=value;} }
"""),
        source("factory", "q/Factory.java", """
package q; class Factory { external.External dep; Factory(external.External dep){this.dep=dep;}
  Product make(){ String a=dep.read(); String b=dep.read(); return Product.builder().value(a+b).build(); }
}
"""),
        source("test", "q/Test.java", """
package q; class Test { @org.junit.Test void t(){ Factory f=new Factory(dep); Product p=f.make();
org.junit.Assert.assertEquals("xy",p.getValue()); } }
"""),
    )).compose("value")
    paths = result["goals"][0]["paths"]
    normal = next(path for path in paths if path["outcome"] == "return")
    ids = [event["call_id"] for event in normal["events"]]
    results = [event["result_symbol"] for event in normal["events"]]
    assert len(ids) == len(set(ids)) == 2
    assert len(results) == len(set(results)) == 2
    assert {event["receiver"] for event in normal["events"]} == {"dep"}
    assert any(path["outcome"] == "raise" for path in paths)


def test_java_boolean_short_circuit_does_not_evaluate_right_call(tmp_path):
    result = Composer(ir(tmp_path,
        source("product", "s/Product.java", """
package s; @lombok.Data class Product { boolean value; @lombok.Builder Product(boolean value){this.value=value;} }
"""),
        source("factory", "s/Factory.java", """
package s; class Factory { external.External dep; Factory(external.External dep){this.dep=dep;}
  Product make(){ return Product.builder().value(left() == null || left().equals(dep.read())).build(); }
  String left(){ return null; }
}
"""),
        source("test", "s/Test.java", """
package s; class Test { @org.junit.Test void t(){ Factory f=new Factory(dep); Product p=f.make();
org.junit.Assert.assertEquals(true,p.isValue()); } }
"""),
    )).compose("value")
    normal = next(path for path in result["goals"][0]["paths"] if path["outcome"] == "return")
    assert normal["value"] == {"kind": "literal", "value": True}
    assert normal["events"] == []


def test_boundary_raise_survives_when_call_is_another_calls_argument(tmp_path):
    result = Composer(ir(tmp_path,
        source("product", "v/Product.java", """
package v; @lombok.Data class Product { boolean value;
@lombok.Builder Product(boolean value){this.value=value;} }
"""),
        source("factory", "v/Factory.java", """
package v; class Factory { external.External dep; Factory(external.External dep){this.dep=dep;}
Product make(){return Product.builder().value(org.springframework.util.StringUtils.isEmpty(dep.read())).build();} }
"""),
        source("test", "v/Test.java", """
package v; class Test { @org.junit.Test void t(){Factory f=new Factory(dep); Product p=f.make();
org.junit.Assert.assertEquals(true,p.isValue());} }
"""),
    )).compose("value")
    paths = result["goals"][0]["paths"]
    raised = [path for path in paths if path["outcome"] == "raise"]
    assert len(raised) == 1
    assert raised[0]["events"][-1]["name"] == "read"
    assert raised[0]["events"][-1]["outcome"] == "raise"
    assert any(path["outcome"] == "return" for path in paths)


def test_ambiguous_or_unsupported_target_stays_unknown(tmp_path):
    result = Composer(ir(tmp_path,
        source("product", "r/Product.java", """
package r; @lombok.Data class Product { String value; }
"""),
        source("test", "r/Test.java", """
package r; class Test { @org.junit.Test void t(){ Product p=unknown.make();
org.junit.Assert.assertEquals("x",p.getValue()); } }
"""),
    )).compose("value")
    goal = result["goals"][0]
    assert goal["paths"][0]["outcome"] == "unknown"
    assert any(item["kind"] in {"producer_dispatch_ambiguous", "assertion_target_not_projectable"}
               for item in goal["paths"][0]["unknowns"])


def test_builder_projection_requires_identity_assignment_evidence(tmp_path):
    result = Composer(ir(tmp_path,
        source("product", "u/Product.java", """
package u; @lombok.Data class Product { final String value;
@lombok.Builder Product(String value){this.value=value + "!";} }
"""),
        source("factory", "u/Factory.java", """
package u; class Factory { Product make(){return Product.builder().value("x").build();} }
"""),
        source("test", "u/Test.java", """
package u; class Test { @org.junit.Test void t(){Factory f=new Factory(); Product p=f.make();
org.junit.Assert.assertEquals("x",p.getValue());} }
"""),
    )).compose("value")
    goal = result["goals"][0]
    assert goal["paths"][0]["outcome"] == "unknown"
    assert any(item["kind"] == "builder_projection_not_proven"
               for item in goal["paths"][0]["unknowns"])
