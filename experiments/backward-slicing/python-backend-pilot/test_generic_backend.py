from __future__ import annotations

import importlib.util
from pathlib import Path


_spec = importlib.util.spec_from_file_location(
    "generic_python_backend_subject", Path(__file__).with_name("generic_backend.py")
)
assert _spec is not None and _spec.loader is not None
backend = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(backend)


def costs() -> dict:
    return {
        "timings_seconds": {"database": 1.0, "query": 2.0, "decode": 0.1},
        "total_seconds": 3.1, "total_scope": "test",
        "peak_memory": {"status": "unavailable", "bytes": None, "reason": "test"},
        "cache_state": {"status": "cold", "reason": "test"},
        "input_size": {"files": 2, "bytes": 10},
        "graph_size": {"nodes": None, "edges": 2}, "unavailable_phases": [],
    }


def test_query_uses_external_selectors_and_has_no_case_allow_list():
    query = backend.QUERY.read_text()
    assert "external string selectedTestPathFact()" in query
    assert "external string selectedTestFunctionFact()" in query
    assert "external int selectedObservationLineFact()" in query
    assert "case_id" not in query
    assert "click_402" not in query


def test_same_line_observations_are_preserved_but_not_falsely_linked():
    entry = {"case_id": "renamed_case", "dataset": "dataset", "snapshot_id": "snapshot"}
    context = {"masking_contract": "python-slot-v1", "masked_test_sha256": "a" * 64,
               "target_selector": "tests/test_x.py::test_x",
               "target_scope": [{"symbol": "test_x", "start_line": 1, "end_line": 9}]}
    sources = [
        {"source_id": "test", "logical_path": "tests/test_x.py", "sha256": "a" * 64,
         "bytes": 5},
        {"source_id": "source:1", "logical_path": "src/x.py", "sha256": "b" * 64,
         "bytes": 5},
    ]
    criteria = [
        {"criterion_id": "criterion:0", "line": 7, "kind": "value_assertion",
         "confidence": "explicit", "reason": "first"},
        {"criterion_id": "criterion:1", "line": 7, "kind": "interaction_observation",
         "confidence": "explicit", "reason": "second"},
    ]
    rows = [{"relation": "resolved_product_result_to_observation",
             "sourcePath": "src/x.py", "sourceLine": "2", "sourceEndLine": "3",
             "targetPath": "tests/test_x.py", "targetLine": "7", "targetEndLine": "7",
             "observationLine": "7", "argumentIndex": "-1", "detail": "x"}]
    contract = backend.build_contract(entry, context, sources, criteria, rows, costs(), [])
    assert contract["relations"][0]["observation_ids"] == []
    assert len(contract["observations"]) == 2
    assert "observation_mapping_ambiguous" in contract["completion"]["failure_categories"]
    assert contract["semantic_validation"]["status"] == "not_evaluated"


def test_driver_environment_is_fixed_and_does_not_inherit_credentials_or_home():
    assert backend.DRIVER_ENV == {
        "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8", "PATH": "/bin:/usr/bin"
    }


def test_cross_source_relation_without_observation_is_not_claimed_as_observation_linked():
    criteria = [{"criterion_id": "criterion:0", "line": 7}]
    rows = [{"relation": "resolved_test_product_call", "sourcePath": "tests/test_x.py",
             "targetPath": "src/x.py", "observationLine": "0"}]
    evidence = backend.summarize_entry_evidence(criteria, rows)
    assert evidence["observation_linked_cross_source_relations"] == 0
    assert evidence["analysis_entry_status"] == "product_call_binding_only"


def test_observation_selector_targets_assert_expression_not_nested_calls():
    text = "def test_x():\n    assert f(x) == g(x)\n"
    assert backend.selector_locations(text, [{"line": 2, "kind": "value_assertion"}]) == [[2, 2, 12, "assert"]]
    assert backend.selector_locations(text, [{"line": 2}, {"line": 2}]) == []


def test_multiple_backend_roots_do_not_count_as_a_unique_observation():
    criteria = [{"criterion_id": "criterion:0", "line": 7}]
    row = {"relation": "observation", "sourcePath": "test.py", "targetPath": "test.py",
           "observationLine": "7"}
    evidence = backend.summarize_entry_evidence(criteria, [row, row])
    assert evidence["observations_mapped"] == 0
    assert evidence["missing_observations"] == ["criterion:0"]
    assert evidence["ambiguous_observation_lines"] == [7]


def test_loader_facts_reject_async_and_rebinding_and_other_test_scope():
    text = "def test_x():\n    f = loader()\n    result = f()\n    assert result\n\ndef loader():\n    pass\n"
    source = {"source_id": "source:1", "logical_path": "src/x.py", "text": "def f():\n    return 1\n"}
    sources = [{"source_id": "test", "logical_path": "tests/x.py", "text": text}, source]
    binding = {"status": "resolved", "loader": "loader", "function_definition": {
        "source_id": "source:1", "logical_path": "src/x.py", "name": "f", "start_line": 1, "end_line": 2},
        "test_bindings": [{"name": "f", "line": 2}], "call_sites": [{"name": "f", "line": 3}]}
    selection = {"dynamic_loader_bindings": [binding]}
    scope = {"symbol": "test_x", "start_line": 1, "end_line": 4}
    assert backend.loader_facts(sources, selection, scope) == [[3, "f", "src/x.py", 1, "f", 2]]
    source["text"] = "async def f():\n    return 1\n"
    assert backend.loader_facts(sources, selection, scope) == []
    source["text"] = "def f():\n    return 1\n"
    sources[0]["text"] = text.replace("    assert result", "    f = other; assert result")
    assert backend.loader_facts(sources, selection, scope) == []
    sources[0]["text"] = text
    binding["test_bindings"] = [{"name": "f", "line": 9}]
    assert backend.loader_facts(sources, selection, scope) == []


def test_exact_call_root_and_same_line_loader_rebinding_are_rejected():
    text = "def test_x():\n    with raises(ValueError):\n        f()\n"
    assert backend.selector_locations(text, [{"line": 2, "call": "raises"}]) == [[2, 2, 10, "call"]]
    sources = [{"source_id": "test", "logical_path": "test.py", "text":
        "def loader():\n    pass\ndef test_x():\n    f = loader(); f = other\n    f()\n"},
        {"source_id": "s", "logical_path": "x.py", "text": "def f():\n    return 1\n"}]
    selection = {"dynamic_loader_bindings": [{"status": "resolved", "loader": "loader",
        "function_definition": {"source_id": "s", "logical_path": "x.py", "name": "f", "start_line": 1, "end_line": 2},
        "test_bindings": [{"name": "f", "line": 4}], "call_sites": [{"name": "f", "line": 5}]}]}
    assert backend.loader_facts(sources, selection, {"symbol": "test_x", "start_line": 3}) == []
