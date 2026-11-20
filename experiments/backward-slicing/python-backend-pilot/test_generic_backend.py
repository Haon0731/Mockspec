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


def test_import_facts_bind_unique_direct_import_and_module_alias():
    sources = [
        {"source_id": "test", "logical_path": "tests/test_termui.py", "text":
         "from click.termui import launch as open_url\n"
         "import click.helpers as helpers\n\n"
         "def test_x():\n"
         "    first = open_url('https://example.test')\n"
         "    second = helpers.abort(404)\n"
         "    assert first == second\n"},
        {"source_id": "source:1", "logical_path": "src/click/termui.py", "text":
         "def launch(url):\n    return url\n"},
        {"source_id": "source:2", "logical_path": "src/click/helpers.py", "text":
         "def abort(code):\n    raise RuntimeError(code)\n"},
    ]
    facts = backend.import_facts(sources, {"symbol": "test_x", "start_line": 4})
    assert facts == [
        [5, "launch", "src/click/termui.py", 1, "launch", 2],
        [6, "abort", "src/click/helpers.py", 1, "abort", 2],
    ]


def test_import_facts_bind_unaliased_module_and_dotted_module_calls():
    sources = [
        {"source_id": "test", "logical_path": "tests/test_x.py", "text":
         "import standalone\n"
         "import pkg.mod\n\n"
         "def test_x():\n"
         "    standalone.first()\n"
         "    pkg.mod.second()\n"},
        {"source_id": "source:1", "logical_path": "src/standalone.py", "text":
         "def first():\n    return 1\n"},
        {"source_id": "source:2", "logical_path": "src/pkg/mod.py", "text":
         "def second():\n    return 2\n"},
    ]
    assert backend.import_facts(sources, {"symbol": "test_x", "start_line": 4}) == [
        [5, "first", "src/standalone.py", 1, "first", 2],
        [6, "second", "src/pkg/mod.py", 1, "second", 2],
    ]


def test_import_facts_reject_shadowing_decorators_and_ambiguous_modules():
    test = {"source_id": "test", "logical_path": "tests/test_x.py", "text":
            "from pkg.mod import target\n\n"
            "def test_x():\n"
            "    target = replacement\n"
            "    target()\n"}
    product = {"source_id": "source:1", "logical_path": "src/pkg/mod.py", "text":
               "def target():\n    return 1\n"}
    scope = {"symbol": "test_x", "start_line": 3}
    assert backend.import_facts([test, product], scope) == []

    test["text"] = test["text"].replace("    target = replacement\n", "")
    product["text"] = "@wrapper\ndef target():\n    return 1\n"
    assert backend.import_facts([test, product], scope) == []

    product["text"] = "def target():\n    return 1\n"
    duplicate = {**product, "source_id": "source:2", "logical_path": "vendor/pkg/mod.py"}
    assert backend.import_facts([test, product, duplicate], scope) == []


def test_import_facts_reject_lexical_bindings_and_conditional_or_star_imports():
    product = {"source_id": "source:1", "logical_path": "src/pkg/mod.py", "text":
               "def target():\n    return 1\n"}
    tests = [
        "import pkg.mod as dependency\n\ndef test_x(dependency):\n    dependency.target()\n",
        "import pkg.mod as dependency\ndef dependency():\n    pass\n"
        "def test_x():\n    dependency.target()\n",
        "import pkg.mod as dependency\nclass dependency:\n    pass\n"
        "def test_x():\n    dependency.target()\n",
        "flag = True\nif flag:\n    import pkg.mod as dependency\n"
        "def test_x():\n    dependency.target()\n",
        "import pkg.mod as dependency\ndef test_x():\n    try:\n        pass\n"
        "    except Exception as dependency:\n        pass\n    dependency.target()\n",
        "from pkg.mod import *\ndef test_x():\n    target()\n",
        "from pkg.mod import target\nfrom extension import *\n"
        "def test_x():\n    target()\n",
    ]
    for text in tests:
        test = {"source_id": "test", "logical_path": "tests/test_x.py", "text": text}
        tree = backend.ast.parse(text)
        target = next(node for node in backend.ast.walk(tree)
                      if isinstance(node, backend.ast.FunctionDef) and node.name == "test_x")
        assert backend.import_facts([test, product], {
            "symbol": "test_x", "start_line": target.lineno,
        }) == []


def test_import_facts_ignore_calls_in_nested_functions_and_class_methods():
    test = {"source_id": "test", "logical_path": "tests/test_x.py", "text":
            "import pkg.mod\n\n"
            "def test_x():\n"
            "    def nested():\n        pkg.mod.target()\n"
            "    class Nested:\n        def method(self):\n            pkg.mod.target()\n"
            "    assert nested\n"}
    product = {"source_id": "source:1", "logical_path": "src/pkg/mod.py", "text":
               "def target():\n    return 1\n"}
    assert backend.import_facts([test, product], {"symbol": "test_x", "start_line": 3}) == []


def test_import_facts_reject_product_rebinding_module_rewrite_and_patch():
    base_product = {"source_id": "source:1", "logical_path": "src/pkg/mod.py", "text":
                    "def target():\n    return 1\n"}
    scope = {"symbol": "test_x", "start_line": 3}
    test_variants = [
        "import pkg.mod\n\ndef test_x():\n    pkg.mod.target = replacement\n"
        "    pkg.mod.target()\n",
        "import pkg.mod\n\ndef test_x():\n    mock.patch('pkg.mod.target')\n"
        "    pkg.mod.target()\n",
        "import pkg.mod\n\ndef test_x():\n    mock.patch.object(pkg.mod, 'target')\n"
        "    pkg.mod.target()\n",
        "import pkg.mod\n\n@mock.patch('pkg.mod.target')\n"
        "def test_x(mock_target):\n    pkg.mod.target()\n",
    ]
    for text in test_variants:
        test = {"source_id": "test", "logical_path": "tests/test_x.py", "text": text}
        tree = backend.ast.parse(text)
        target = next(node for node in backend.ast.walk(tree)
                      if isinstance(node, backend.ast.FunctionDef) and node.name == "test_x")
        assert backend.import_facts([test, base_product], {
            "symbol": "test_x", "start_line": target.lineno,
        }) == []

    product_variants = [
        "def target():\n    return 1\ntarget = replacement\n",
        "def target():\n    return 1\nclass target:\n    pass\n",
        "def target():\n    return 1\nfrom extension import target\n",
        "def target():\n    return 1\nfrom extension import *\n",
    ]
    test = {"source_id": "test", "logical_path": "tests/test_x.py", "text":
            "from pkg.mod import target\n\ndef test_x():\n    target()\n"}
    for text in product_variants:
        product = {**base_product, "text": text}
        assert backend.import_facts([test, product], scope) == []


def test_authenticated_loader_coordinates_become_entry_only_relations():
    rows = backend.authenticated_binding_rows(
        "tests/test_x.py", [[12, "target", "src/x.py", 4, "target", 8]],
        "authenticated_loader_product_call",
    )
    assert rows == [{
        "relation": "authenticated_loader_product_call",
        "sourcePath": "tests/test_x.py", "sourceLine": "12", "sourceEndLine": "12",
        "targetPath": "src/x.py", "targetLine": "4", "targetEndLine": "8",
        "observationLine": "0", "argumentIndex": "-1", "detail": "target",
    }]
    evidence = backend.summarize_entry_evidence([{"criterion_id": "c", "line": 14}], rows)
    assert evidence["product_call_bindings"] == 1
    assert evidence["observation_linked_cross_source_relations"] == 0
    assert evidence["analysis_entry_status"] == "product_call_binding_only"
    entry = {"case_id": "case", "dataset": "dataset", "snapshot_id": "snapshot"}
    context = {"masking_contract": "python-slot-v1", "masked_test_sha256": "a" * 64,
               "target_selector": "tests/test_x.py::test_x", "selector_facts_sha256": "b" * 64,
               "target_scope": [{"symbol": "test_x", "start_line": 1, "end_line": 14}]}
    sources = [
        {"source_id": "test", "logical_path": "tests/test_x.py", "sha256": "a" * 64,
         "bytes": 5},
        {"source_id": "source:1", "logical_path": "src/x.py", "sha256": "b" * 64,
         "bytes": 5},
    ]
    criteria = [{"criterion_id": "c", "line": 14, "kind": "value_assertion",
                 "confidence": "explicit", "reason": "assertion"}]
    contract = backend.build_contract(entry, context, sources, criteria, rows, costs(), [])
    assert contract["entry_point"]["mut_binding"]["status"] == "unique"
    assert [relation["kind"] for relation in contract["relations"]] == ["call_binding"]
    assert contract["relations"][0]["provenance"]["binding_origin"] \
        == "authenticated_visible_loader_frontend"
    assert contract["relations"][0]["provenance"]["selector_facts_sha256"] == "b" * 64
    assert "query_relation" not in contract["relations"][0]["provenance"]
    assert "query_sha256" not in contract["relations"][0]["provenance"]


def test_import_binding_requires_unique_scope_and_module():
    product = {"source_id": "p1", "logical_path": "src/pkg/m.py", "text": "def f(): pass"}
    for text in (
        "from pkg.m import f\ndef test_x():\n    if flag: from fallback import f\n    f()",
        "from pkg.m import f\ndef outer(f):\n    def test_x(): f()",
        "import pkg.m\ndef test_x():\n    pkg.m = other\n    pkg.m.f()",
        "from pkg.m import f\ndef test_x():\n    match obj:\n        case {'v': f}: f()",
    ):
        node = next(n for n in backend.ast.walk(backend.ast.parse(text))
                    if isinstance(n, backend.ast.FunctionDef) and n.name == "test_x")
        assert backend.import_facts([{"source_id": "test", "text": text}, product],
                                    {"symbol": "test_x", "start_line": node.lineno}) == []
    test = {"source_id": "test", "text": "from pkg.m import f\ndef test_x(): f()"}
    other = {"source_id": "p2", "logical_path": "other/pkg/m.py", "text": "pass"}
    assert backend.import_facts([test, product, other], {"symbol": "test_x", "start_line": 2}) == []
