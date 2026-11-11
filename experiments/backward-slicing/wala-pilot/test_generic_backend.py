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
