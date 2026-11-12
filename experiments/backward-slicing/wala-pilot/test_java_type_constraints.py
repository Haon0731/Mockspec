import json
from pathlib import Path
import subprocess


HERE = Path(__file__).resolve().parent
HELPER = HERE / "backend/src/main/java/org/mockspec/research/JavaTypeConstraints.java"


def run_constraints(tmp_path, sources, missing, *, method="selected"):
    root = tmp_path / "source"
    classes = tmp_path / "classes"
    root.mkdir(parents=True)
    classes.mkdir(parents=True)
    for logical_path, text in sources.items():
        path = root / logical_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    subprocess.run(
        ["javac", "-proc:none", "-d", str(classes), str(HELPER)], check=True,
        capture_output=True, text=True,
    )
    completed = subprocess.run(
        ["java", "-cp", str(classes), "org.mockspec.research.JavaTypeConstraints",
         str(root), "-", "p/Test.java", method, "1", "100", *missing],
        check=True, capture_output=True, text=True,
    )
    return json.loads(completed.stdout)


def test_static_call_recovers_fully_qualified_formals_with_source_evidence(tmp_path):
    result = run_constraints(tmp_path, {
        "p/Test.java": """package p;
final class Test {
  void selected() { Product.run(service, config); }
}
""",
        "p/Product.java": """package p;
final class Product {
  static void run(q.Service service, q.Config config) {}
}
""",
        "q/Service.java": "package q; public interface Service {}\n",
        "q/Config.java": "package q; public final class Config {}\n",
    }, ["service", "config"])
    assert result["unresolved"] == []
    assert [(row["name"], row["type"]) for row in result["parameters"]] == [
        ("service", "q.Service"), ("config", "q.Config")]
    service = result["parameters"][0]["evidence"][0]
    assert service["source_path"] == "p/Test.java"
    assert service["line"] == 3
    assert service["kind"] == "call_argument"
    assert service["argument_index"] == 0
    assert service["candidates"][0]["owner"] == "p.Product"
    assert "p.Product.run" in service["candidates"][0]["signature"]


def test_inherited_overload_uses_known_string_argument_to_recover_interface(tmp_path):
    result = run_constraints(tmp_path, {
        "p/Test.java": """package p;
final class Test {
  final Handler handler = new Handler();
  void selected() { handler.getResult(rs, "column"); }
}
""",
        "p/Base.java": """package p;
class Base {
  void getResult(q.ResultSet rs, String column) {}
  void getResult(q.ResultSet rs, int column) {}
  void getResult(q.CallableStatement cs, int column) {}
}
""",
        "p/Handler.java": "package p; final class Handler extends Base {}\n",
        "q/ResultSet.java": "package q; public interface ResultSet {}\n",
        "q/CallableStatement.java": "package q; public interface CallableStatement {}\n",
    }, ["rs"])
    assert result["unresolved"] == []
    parameter = result["parameters"][0]
    assert parameter["type"] == "q.ResultSet"
    candidates = parameter["evidence"][0]["candidates"]
    assert len(candidates) == 1
    assert candidates[0]["owner"] == "p.Handler"
    assert "java.lang.String" in candidates[0]["signature"]


def test_missing_receiver_uses_only_explicit_import_member_evidence(tmp_path):
    result = run_constraints(tmp_path, {
        "p/Test.java": """package p;
import q.ResultSet;
final class Test {
  void selected() { rs.getString("column"); }
}
""",
        "q/ResultSet.java": """package q;
public interface ResultSet { String getString(String column); }
""",
        "q/Unrelated.java": """package q;
public interface Unrelated { String getString(String column); }
""",
    }, ["rs"])
    assert result["unresolved"] == []
    assert result["parameters"][0]["type"] == "q.ResultSet"
    assert result["parameters"][0]["evidence"][0]["kind"] == "method_receiver"


def test_ambiguous_imported_receiver_and_ambiguous_overload_remain_unresolved(tmp_path):
    receiver = run_constraints(tmp_path / "receiver", {
        "p/Test.java": """package p;
import q.First;
import q.Second;
final class Test { void selected() { dependency.value(); } }
""",
        "q/First.java": "package q; public interface First { int value(); }\n",
        "q/Second.java": "package q; public interface Second { int value(); }\n",
    }, ["dependency"])
    assert receiver["parameters"] == []
    assert receiver["unresolved"][0]["reason"] == "ambiguous_or_conflicting_type_domain"
    assert set(receiver["unresolved"][0]["candidate_types"]) == {"q.First", "q.Second"}

    overload = run_constraints(tmp_path / "overload", {
        "p/Test.java": """package p;
final class Test { void selected() { Product.use(value); } }
""",
        "p/Product.java": """package p;
final class Product { static void use(q.First value) {} static void use(q.Second value) {} }
""",
        "q/First.java": "package q; public interface First {}\n",
        "q/Second.java": "package q; public interface Second {}\n",
    }, ["value"])
    assert overload["parameters"] == []
    assert overload["unresolved"][0]["reason"] == "ambiguous_or_conflicting_type_domain"


def test_explicit_object_formal_is_evidence_not_fallback(tmp_path):
    result = run_constraints(tmp_path, {
        "p/Test.java": "package p; final class Test { void selected() { Product.use(value); } }\n",
        "p/Product.java": "package p; final class Product { static void use(Object value) {} }\n",
    }, ["value"])
    assert result["unresolved"] == []
    assert result["parameters"][0]["type"] == "java.lang.Object"
    assert "java.lang.Object" in result["parameters"][0]["evidence"][0]["candidates"][0]["signature"]


def test_specific_formal_satisfies_object_supertype_use_without_fallback(tmp_path):
    result = run_constraints(tmp_path, {
        "p/Test.java": """package p;
final class Test {
  void selected() { Product.consume(request); Assert.same(request); }
}
""",
        "p/Product.java": "package p; final class Product { static void consume(q.Request request) {} }\n",
        "p/Assert.java": "package p; final class Assert { static void same(Object value) {} }\n",
        "q/Request.java": "package q; public interface Request {}\n",
    }, ["request"])
    assert result["unresolved"] == []
    parameter = result["parameters"][0]
    assert parameter["type"] == "q.Request"
    assert [use["line"] for use in parameter["evidence"]] == [3, 3]
    assert {candidate["type"] for use in parameter["evidence"] for candidate in use["candidates"]} == {
        "q.Request", "java.lang.Object"}


def test_conflicting_and_alternative_type_domains_remain_unresolved(tmp_path):
    conflicting = run_constraints(tmp_path / "conflicting", {
        "p/Test.java": "package p; final class Test { void selected() { A.use(value); B.use(value); } }\n",
        "p/A.java": "package p; final class A { static void use(String value) {} }\n",
        "p/B.java": "package p; final class B { static void use(Integer value) {} }\n",
    }, ["value"])
    assert conflicting["parameters"] == []
    assert conflicting["unresolved"][0]["candidate_types"] == []

    alternative = run_constraints(tmp_path / "alternative", {
        "p/Test.java": "package p; final class Test { void selected() { Product.use(value); } }\n",
        "p/Product.java": """package p;
final class Product { static void use(q.First value) {} static void use(q.Second value) {} }
""",
        "q/First.java": "package q; public interface First {}\n",
        "q/Second.java": "package q; public interface Second {}\n",
    }, ["value"])
    assert alternative["parameters"] == []
    assert set(alternative["unresolved"][0]["candidate_types"]) == {"q.First", "q.Second"}


def test_constructor_and_imported_inherited_field_keep_declaring_provenance(tmp_path):
    result = run_constraints(tmp_path, {
        'p/Test.java': '''package p;
import q.Fields;
final class Test { void selected() { new Product(value); int number = fields.count; } }
''',
        'p/Product.java': 'package p; final class Product { Product(q.Value value) {} }\n',
        'q/Value.java': 'package q; public interface Value {}\n',
        'q/Base.java': 'package q; public class Base { public int count; }\n',
        'q/Fields.java': 'package q; public class Fields extends Base {}\n',
    }, ['value', 'fields'])
    assert not result['unresolved']
    value, fields = result['parameters']
    assert value['type'] == 'q.Value'
    assert value['evidence'][0]['kind'] == 'constructor_argument'
    assert value['evidence'][0]['candidates'][0]['declaring_owners'] == ['p.Product']
    assert fields['type'] == 'q.Fields'
    evidence = fields['evidence'][0]
    assert evidence['kind'] == 'field_receiver'
    assert evidence['candidates'][0]['receiver_owner'] == 'q.Fields'
    assert evidence['candidates'][0]['declaring_owners'] == ['q.Base']
    assert evidence['candidates'][0]['signature'] == 'q.Base.count:int'


def test_inherited_generic_specialization_ignores_unrelated_same_name(tmp_path):
    result = run_constraints(tmp_path, {
        'p/Test.java': '''package p;
final class Test { final StringHandler handler = new StringHandler();
  void selected() { handler.accept(value); }
}
''',
        'p/Base.java': 'package p; class Base<T> { void accept(T value) {} }\n',
        'p/StringHandler.java': 'package p; final class StringHandler extends Base<String> {}\n',
        'p/Unrelated.java': 'package p; class Unrelated { void accept(Integer value) {} }\n',
    }, ['value'])
    assert not result['unresolved']
    parameter = result['parameters'][0]
    assert parameter['type'] == 'java.lang.String'
    candidate = parameter['evidence'][0]['candidates'][0]
    assert candidate['receiver_owner'] == 'p.StringHandler'
    assert candidate['declaring_owners'] == ['p.Base']
    assert 'p.Base.accept(java.lang.String)' in candidate['signature']
    assert 'Unrelated' not in json.dumps(parameter)
