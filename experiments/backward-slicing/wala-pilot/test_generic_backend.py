import importlib.util
from pathlib import Path
import subprocess

import pytest


HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("generic_java_backend", HERE / "generic_backend.py")
subject = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(subject)


def completed(code, stderr=""):
    return subprocess.CompletedProcess(["javac"], code, "", stderr)


def test_structural_harness_recovers_only_caret_constrained_type():
    source = "void selected() { consume(dependency); check(expected); }"
    results = iter([
        (completed(1, "X.java:1: error: cannot find symbol\n  symbol: variable dependency\n"
                      "X.java:1: error: cannot find symbol\n  symbol: variable expected\n"), 0.1),
        (completed(1, "X.java:1: error: incompatible types: Object cannot be converted to Service\n"
                      "void selected(Object dependency, Object expected) { consume(dependency); check(expected); }\n"
                      "                                                               ^\n"), 0.1),
        (completed(0), 0.1),
    ])
    written = []
    harness, parameters, attempts = subject._prepare_harness(
        source, "selected", written.append, lambda: next(results))
    assert harness == "void selected(Service dependency, Object expected) { consume(dependency); check(expected); }"
    assert parameters == [{"name": "dependency", "type": "Service"},
                          {"name": "expected", "type": "Object"}]
    assert len(attempts) == 3


def test_structural_harness_rejects_unconstrained_compile_failure():
    source = "void selected() { consume(dependency); }"
    results = iter([
        (completed(1, "X.java:1: error: cannot find symbol\n  symbol: variable dependency\n"), 0.1),
        (completed(1, "X.java:1: error: method consume cannot be applied\n"), 0.1),
    ])
    recorded = []
    with pytest.raises(ValueError, match="not uniquely constrained"):
        subject._prepare_harness(source, "selected", lambda _: None, lambda: next(results),
                                 record_attempts=lambda attempts: recorded.append(list(attempts)))
    assert len(recorded[-1]) == 2


def test_missing_variable_diagnostics_do_not_cross_blocks():
    stderr = (
        "/work/Product.java:9: error: cannot find symbol\n"
        "  symbol: class MissingProductType\n"
        "/work/SelectedTest.java:41: error: cannot find symbol\n"
        "    consume(dependency);\n"
        "            ^\n"
        "  symbol:   variable dependency\n"
        "  location: class SelectedTest\n"
    )
    assert subject._missing_variables(
        stderr, test_path="SelectedTest.java", start_line=40, end_line=42
    ) == ["dependency"]
    assert subject._missing_variables(
        stderr, test_path="Product.java", start_line=1, end_line=20
    ) == []


def test_type_update_accepts_tab_indented_caret_and_wildcard():
    stderr = (
        "SelectedTest.java:20: error: incompatible types: Object cannot be converted to Invoker<?>\n"
        "\tfilter.invoke(invoker, invocation);\n"
        "\t              ^\n"
    )
    assert subject._type_updates(stderr, ["invoker"]) == {"invoker": "Invoker<?>"}


def test_type_update_filters_diagnostic_path_and_range():
    stderr = (
        "/work/Product.java:20: error: incompatible types: Object cannot be converted to Service\n"
        "    consume(dependency);\n"
        "            ^\n"
        "/work/SelectedTest.java:80: error: incompatible types: Object cannot be converted to Service\n"
        "    consume(dependency);\n"
        "            ^\n"
    )
    assert subject._type_updates(
        stderr, ["dependency"], test_path="SelectedTest.java", start_line=40, end_line=60
    ) == {}


def test_type_update_does_not_guess_when_caret_misses_the_only_name():
    stderr = (
        "SelectedTest.java:20: error: incompatible types: Object cannot be converted to Service\n"
        "    consume(dependency);\n"
        "    ^\n"
    )
    assert subject._type_updates(stderr, ["dependency"]) == {}


def test_type_update_uses_unique_literal_compatible_overload():
    stderr = (
        "SelectedTest.java:36: error: no suitable method found for getResult(Object,String)\n"
        "    assertEquals(expected, handler.getResult(rs, \"column\"));\n"
        "                                  ^\n"
        "    method TypeHandler.getResult(ResultSet,String) is not applicable\n"
        "      (argument mismatch; Object cannot be converted to ResultSet)\n"
        "    method TypeHandler.getResult(ResultSet,int) is not applicable\n"
        "      (argument mismatch; Object cannot be converted to ResultSet)\n"
        "    method TypeHandler.getResult(CallableStatement,int) is not applicable\n"
        "      (argument mismatch; Object cannot be converted to CallableStatement)\n"
    )
    assert subject._type_updates(stderr, ["rs"]) == {"rs": "ResultSet"}


def test_type_update_preserves_integer_overload_ambiguity():
    stderr = (
        "SelectedTest.java:36: error: no suitable method found for getResult(Object,int)\n"
        "    assertEquals(expected, handler.getResult(statement, 1));\n"
        "                                  ^\n"
        "    method TypeHandler.getResult(ResultSet,String) is not applicable\n"
        "      (argument mismatch; Object cannot be converted to ResultSet)\n"
        "    method TypeHandler.getResult(ResultSet,int) is not applicable\n"
        "      (argument mismatch; Object cannot be converted to ResultSet)\n"
        "    method TypeHandler.getResult(CallableStatement,int) is not applicable\n"
        "      (argument mismatch; Object cannot be converted to CallableStatement)\n"
    )
    assert subject._type_updates(stderr, ["statement"]) == {}


def test_overload_filter_retains_reference_supertypes_and_numeric_widening():
    assert subject._literal_compatible('"column"', "Serializable")
    assert subject._literal_compatible("1", "long")
    stderr = (
        "SelectedTest.java:36: error: no suitable method found for getResult(Object,String)\n"
        "    handler.getResult(rs, \"column\");\n"
        "            ^\n"
        "    method Handler.getResult(ResultSet,String) is not applicable\n"
        "      (argument mismatch; Object cannot be converted to ResultSet)\n"
        "    method Handler.getResult(CallableStatement,Serializable) is not applicable\n"
        "      (argument mismatch; Object cannot be converted to CallableStatement)\n"
    )
    assert subject._type_updates(stderr, ["rs"]) == {}


def test_overload_update_requires_one_signature_and_rejects_varargs():
    duplicate = (
        "SelectedTest.java:36: error: no suitable method found for consume(Object,String)\n"
        "    consume(dependency, \"value\");\n"
        "    ^\n"
        "    method First.consume(Service,String) is not applicable\n"
        "    method Second.consume(Service,String) is not applicable\n"
    )
    assert subject._type_updates(duplicate, ["dependency"]) == {}

    varargs = (
        "SelectedTest.java:36: error: no suitable method found for consume(Object,String)\n"
        "    consume(dependency, \"value\");\n"
        "    ^\n"
        "    method First.consume(Service,String...) is not applicable\n"
    )
    assert subject._type_updates(varargs, ["dependency"]) == {}


def test_type_update_recovers_single_required_signature_but_rejects_type_variable():
    wildcard = (
        "SelectedTest.java:54: error: method invoke cannot be applied to given types;\n"
        "    filter.invoke(invoker, invocation);\n"
        "          ^\n"
        "  required: Invoker<?>,Invocation\n"
        "  found: Object,Invocation\n"
    )
    assert subject._type_updates(wildcard, ["invoker"]) == {"invoker": "Invoker<?>"}

    free_type_variable = (
        "SelectedTest.java:43: error: method shouldMigrate in class Comparator cannot be applied to given types;\n"
        "    comparator.shouldMigrate(newInvoker, oldInvoker, rule);\n"
        "              ^\n"
        "  required: ClusterInvoker<T>,ClusterInvoker<T>,MigrationRule\n"
        "  found: Object,Object,Object\n"
    )
    assert subject._type_updates(
        free_type_variable, ["newInvoker", "oldInvoker", "rule"]
    ) == {}

    named_type_variable = (
        "SelectedTest.java:43: error: method convert cannot be applied to given types;\n"
        "    convert(value);\n"
        "    ^\n"
        "  required: VALUE\n"
        "  found: Object\n"
        "  where VALUE is a type-variable:\n"
        "    VALUE extends Object declared in method <VALUE>convert(VALUE)\n"
    )
    assert subject._type_updates(named_type_variable, ["value"]) == {}


def test_observation_requires_one_assertion_on_source_line():
    assert subject._assertion_fragment("assertEquals(1, actual);", 1) == ".assertEquals("
    with pytest.raises(ValueError, match="one assertion"):
        subject._assertion_fragment("assertTrue(a); assertTrue(b);", 1)


def test_java_compilation_disables_annotation_processors():
    source = Path(subject._compile.__code__.co_filename).read_text()
    assert '"-proc:none"' in source


def test_backend_source_keeps_legacy_and_unique_source_line_criteria():
    source = subject.BACKEND_SOURCE.read_text()
    assert 'criterion.startsWith("SOURCE_LINE:")' in source
    assert "requestedOrdinal == -1 && matches.size() != 1" in source
    assert 'if ("RETURN".equals(criterion)' in source
    assert "GENERIC_NO_HEAP" in source
    assert "genericNoHeap" in source
    assert "hashBackedBackwardSlice(sdg, seed, 50_000)" in source
    assert "Slicer.computeBackwardSlice(sdg, seed)" in source


def test_bounded_source_closure_resolves_static_import_owner():
    class Source:
        text = "package p.internal; final class Resources {}"
        start_line = 1

    class Case:
        source_paths = ("src/main/java/p/Schema.java", "src/main/java/p/internal/Resources.java")

        def read_source(self, path):
            assert path == "src/main/java/p/internal/Resources.java"
            return Source()

    selected = [
        {"source_id": "test", "logical_path": "src/test/java/p/SchemaTest.java", "text": "", "start_line": 1},
        {"source_id": "source:1", "logical_path": "src/main/java/p/Schema.java",
         "text": "package p;\nimport static p.internal.Resources.load;\nfinal class Schema {}", "start_line": 1},
    ]
    rows, evidence = subject._expand_java_source_closure(
        Case(), selected, max_files=3, max_bytes=10_000)
    assert [row["logical_path"] for row in rows][-1] == "src/main/java/p/internal/Resources.java"
    assert evidence[-1]["status"] == "selected_unique_visible_type"


def test_typed_product_path_requires_homogeneous_sdg_labels():
    raw = {
        "seed_index": 2,
        "slice_statements": [
            {"method": "p.Product.value()I", "line": 7},
            {"method": "p.Product.value()I", "line": 8},
            {"method": "p.Test.test()V", "line": 20},
        ],
        "slice_edges": [
            {"from": 0, "to": 1, "labels": ["DATA_DEP"]},
            {"from": 1, "to": 2, "labels": ["HEAP_DATA_DEP"]},
        ],
    }
    paths, suppressed = subject._typed_product_paths(
        raw, {"p.Product": "src/main/java/p/Product.java"}, {})
    assert suppressed == []
    assert {(row["kind"], row["source_line"]) for row in paths} == {
        ("value_flow", 7), ("value_flow", 8)}
    raw["slice_edges"][1]["labels"] = ["CONTROL_DEP"]
    mixed, suppressed = subject._typed_product_paths(
        raw, {"p.Product": "src/main/java/p/Product.java"}, {})
    assert suppressed == []
    assert [(row["kind"], row["source_line"]) for row in mixed] == [("control_dependence", 8)]


def test_typed_product_path_through_opaque_placeholder_is_suppressed():
    raw = {
        "seed_index": 2,
        "slice_statements": [
            {"method": "p.Product.value()I", "line": 7},
            {"method": "p.Product.placeholder()I", "line": 12},
            {"method": "p.Test.test()V", "line": 20},
        ],
        "slice_edges": [
            {"from": 0, "to": 1, "labels": ["DATA_DEP"]},
            {"from": 1, "to": 2, "labels": ["DATA_DEP"]},
        ],
    }
    opaque = {"src/main/java/p/Product.java": [
        {"source_id": "source:1", "name": "placeholder", "start_line": 10, "end_line": 13}
    ]}
    paths, suppressed = subject._typed_product_paths(
        raw, {"p.Product": "src/main/java/p/Product.java"}, opaque)
    assert paths == []
    assert suppressed[0]["opaque_hits"][0]["name"] == "placeholder"


def test_unparsed_or_generic_overload_keeps_competing_signature_unknown():
    text = (
        "SelectedTest.java:10: error: no suitable method found for consume(Object,String)\n"
        '    consume(dep, "value");\n    ^\n'
        "    method First.consume(Service,String) is not applicable\n"
    )
    for competing in (
        "    method Other.consume(Other,String...) is not applicable\n",
        "    method Other.<T>consume(T,String) is not applicable\n",
        "    method Other.consume(Other,List<?>) is not applicable\n",
    ):
        assert subject._type_updates(text + competing, ["dep"]) == {}
