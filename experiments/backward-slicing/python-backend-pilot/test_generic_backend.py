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
    assert "external int selectedTestStartLineFact()" in query
    assert "external int selectedObservationLineFact()" in query
    assert "not function.isAsync()" in query
    assert "runner.getArg(0) = call" in query
    assert "product_return_to_observation" in query
    assert "product_guard_controls_return_to_observation" in query
    assert "product_exception_exit_unknown" in query
    assert "executedProductCall" in query
    assert 'API::moduleImport("asyncio").getMember("run")' in query
    assert "not selectedSupportPath" in query
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
    assert backend.loader_facts(sources, selection, scope) == [[3, "f", "src/x.py", 1, "f", 2, 0, ""]]
    source["text"] = "async def f():\n    return 1\n"
    assert backend.loader_facts(sources, selection, scope) == [[3, "f", "src/x.py", 1, "f", 2, -1, ""]]
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


def test_import_facts_require_unique_visible_definition_and_reject_shadowing():
    test = """from pkg.api import run as execute

def test_x():
    result = execute()
    assert result
"""
    product = "def run():\n    return 1\n"
    sources = [
        {"source_id": "test", "logical_path": "tests/test_x.py", "text": test},
        {"source_id": "source:1", "logical_path": "src/pkg/api.py", "text": product},
    ]
    selection = {"evidence": [{"stage": "masked_test", "hint_kind": "python_import",
                                "hint": "pkg.api", "candidates": ["src/pkg/api.py"]}]}
    scope = {"symbol": "test_x", "start_line": 3}
    assert backend.import_facts(sources, selection, scope) == [
        [4, "execute", "src/pkg/api.py", 1, "run", 2, 0, ""]
    ]
    sources[0]["text"] = test.replace("    result", "    execute = other\n    result")
    assert backend.import_facts(sources, selection, scope) == []
    sources[0]["text"] = test
    sources[1]["text"] += "\ndef run():\n    return 2\n"
    assert backend.import_facts(sources, selection, scope) == []
    sources[1]["text"] = product
    sources[1]["selection_role"] = "loader_support"
    sources[0]["text"] = test
    assert backend.import_facts(sources, selection, scope) == []
    sources[1].pop("selection_role")
    sources[0]["text"] = test.replace(
        "\ndef test_x():", "\nexecute = other\n\ndef test_x():")
    assert backend.import_facts(sources, selection, {"symbol": "test_x", "start_line": 5}) == []


def test_import_facts_mark_async_definition_for_explicit_await_query_path():
    sources = [
        {"source_id": "test", "logical_path": "tests/test_x.py", "text":
         "from pkg.api import run\nasync def test_x():\n    result = await run()\n    assert result\n"},
        {"source_id": "source:1", "logical_path": "src/pkg/api.py", "text":
         "async def run():\n    return 1\n"},
    ]
    selection = {"evidence": [{"stage": "masked_test", "hint_kind": "python_import",
                                "hint": "pkg.api", "candidates": ["src/pkg/api.py"]}]}
    assert backend.import_facts(sources, selection, {"symbol": "test_x", "start_line": 2}) == [
        [3, "run", "src/pkg/api.py", 1, "run", 2, 1, ""]
    ]


def test_asyncio_run_requires_unshadowed_module_and_raw_coroutine_stays_unwrapped():
    source = {"source_id": "source:1", "logical_path": "src/pkg/api.py", "text":
              "async def run():\n    return 1\n"}
    selection = {"evidence": [{"stage": "masked_test", "hint_kind": "python_import",
                                "hint": "pkg.api", "candidates": ["src/pkg/api.py"]}]}
    wrapped = "import asyncio\nfrom pkg.api import run\ndef test_x():\n    assert asyncio.run(run()) == 1\n"
    sources = [{"source_id": "test", "logical_path": "tests/test_x.py", "text": wrapped}, source]
    assert backend.import_facts(sources, selection, {"symbol": "test_x", "start_line": 3}) == [
        [4, "run", "src/pkg/api.py", 1, "run", 2, 2, "asyncio"]
    ]
    sources[0]["text"] = wrapped.replace("def test_x():", "def test_x(asyncio):")
    assert backend.import_facts(sources, selection, {"symbol": "test_x", "start_line": 3}) == [
        [4, "run", "src/pkg/api.py", 1, "run", 2, -1, ""]
    ]
    sources[0]["text"] = wrapped.replace(
        "from pkg.api import run", "from pkg.api import run\nasyncio = fake")
    assert backend.import_facts(sources, selection, {"symbol": "test_x", "start_line": 4}) == [
        [5, "run", "src/pkg/api.py", 1, "run", 2, -1, ""]
    ]
    sources[0]["text"] = "from pkg.api import run\ndef test_x():\n    assert run()\n"
    assert backend.import_facts(sources, selection, {"symbol": "test_x", "start_line": 2}) == [
        [3, "run", "src/pkg/api.py", 1, "run", 2, -1, ""]
    ]


def test_mixed_awaited_and_raw_async_diagnostic_marks_only_raw_call():
    sources = [
        {"source_id": "test", "logical_path": "tests/test_x.py", "text":
         "from pkg.api import run\nasync def test_x():\n    value = await run()\n    raw = run()\n    assert value\n"},
        {"source_id": "source:1", "logical_path": "src/pkg/api.py", "text":
         "async def run():\n    return 1\n"},
    ]
    selection = {"evidence": [{"stage": "masked_test", "hint_kind": "python_import",
                                "hint": "pkg.api", "candidates": ["src/pkg/api.py"]}]}
    diagnostics = []
    facts = backend.import_facts(sources, selection, {"symbol": "test_x", "start_line": 2},
                                 diagnostics)
    assert [row[6] for row in facts] == [1, -1]
    assert [(row["line"], row["stage"]) for row in diagnostics] == [
        (4, "coroutine_completion_unsupported")]


def test_product_body_rows_remain_typed_and_unknown_effects_stay_separate():
    entry = {"case_id": "case", "dataset": "dataset", "snapshot_id": "snapshot"}
    context = {"masking_contract": "python-slot-v1", "masked_test_sha256": "a" * 64,
               "target_selector": "tests/test_x.py::test_x", "selector_facts_sha256": "c" * 64,
               "target_scope": [{"symbol": "test_x", "start_line": 1, "end_line": 8}]}
    sources = [
        {"source_id": "test", "logical_path": "tests/test_x.py", "sha256": "a" * 64,
         "bytes": 5},
        {"source_id": "source:1", "logical_path": "src/x.py", "sha256": "b" * 64,
         "bytes": 5},
    ]
    criteria = [{"criterion_id": "criterion:0", "line": 7, "kind": "value_assertion",
                 "confidence": "explicit", "reason": "assert"}]
    base = {"targetPath": "tests/test_x.py", "targetLine": "7", "targetEndLine": "7",
            "observationLine": "7", "argumentIndex": "-1"}
    rows = [
        {"relation": "observation", "sourcePath": "tests/test_x.py", "sourceLine": "7",
         "sourceEndLine": "7", "detail": "selected", **base},
        {"relation": "product_return_to_observation", "sourcePath": "src/x.py",
         "sourceLine": "10", "sourceEndLine": "10", "detail": "import:return_value", **base},
        {"relation": "product_guard_controls_return_to_observation", "sourcePath": "src/x.py",
         "sourceLine": "8", "sourceEndLine": "10", "detail": "return:10:true", **base},
        {"relation": "product_exception_exit_unknown", "sourcePath": "src/x.py",
         "sourceLine": "9", "sourceEndLine": "9", "detail": "unknown may match", **base},
        {"relation": "product_call_unknown_effect", "sourcePath": "src/x.py",
         "sourceLine": "9", "sourceEndLine": "9", "detail": "opaque", **base},
        {"relation": "product_attribute_write_unknown_effect", "sourcePath": "src/x.py",
         "sourceLine": "11", "sourceEndLine": "11", "detail": "alias unknown", **base},
    ]

    contract = backend.build_contract(entry, context, sources, criteria, rows, costs(), [])

    assert [row["kind"] for row in contract["relations"]] == [
        "normal_return_dependence", "control_dependence"]
    assert all(row["modality"] == "analysis_may" for row in contract["relations"])
    assert {row["effect"] for row in contract["analysis_boundaries"]["unknown_effects"]} == {
        "unknown_product_call_return_exception_and_reachable_state_effects",
        "visible_attribute_write_alias_region_and_overwrite_not_proven",
        "product_exceptional_exit_expected_type_compatibility_unknown",
    }
    assert all(row["status"] == "not_evaluated" for row in contract["relation_coverage"])
    assert contract["semantic_validation"]["status"] == "not_evaluated"


def test_unknown_effects_retain_all_observation_ids_and_endpoints():
    entry = {"case_id": "case", "dataset": "dataset", "snapshot_id": "snapshot"}
    context = {"masking_contract": "python-slot-v1", "masked_test_sha256": "a" * 64,
               "target_selector": "tests/test_x.py::test_x", "target_scope": []}
    sources = [
        {"source_id": "test", "logical_path": "tests/test_x.py", "sha256": "a" * 64,
         "bytes": 5},
        {"source_id": "source:1", "logical_path": "src/x.py", "sha256": "b" * 64,
         "bytes": 5},
    ]
    criteria = [
        {"criterion_id": "criterion:0", "line": 7, "kind": "value_assertion",
         "confidence": "explicit", "reason": "first"},
        {"criterion_id": "criterion:1", "line": 8, "kind": "value_assertion",
         "confidence": "explicit", "reason": "second"},
    ]
    rows = []
    for line in (7, 8):
        target = {"targetPath": "tests/test_x.py", "targetLine": str(line),
                  "targetEndLine": str(line), "observationLine": str(line),
                  "argumentIndex": "-1"}
        rows.extend([
            {"relation": "observation", "sourcePath": "tests/test_x.py",
             "sourceLine": str(line), "sourceEndLine": str(line),
             "detail": "selected", **target},
            {"relation": "product_exception_exit_unknown", "sourcePath": "src/x.py",
             "sourceLine": "10", "sourceEndLine": "10", "detail": "exception", **target},
            {"relation": "product_attribute_write_unknown_effect", "sourcePath": "src/x.py",
             "sourceLine": "11", "sourceEndLine": "11", "detail": "write", **target},
        ])

    contract = backend.build_contract(entry, context, sources, criteria, rows, costs(), [])
    effects = {effect["effect"]: effect
               for effect in contract["analysis_boundaries"]["unknown_effects"]}
    for effect in effects.values():
        assert effect["observation_ids"] == ["criterion:0", "criterion:1"]
        assert effect["scope"]["observations"] == [
            {"logical_path": "tests/test_x.py", "start_line": 7, "end_line": 7},
            {"logical_path": "tests/test_x.py", "start_line": 8, "end_line": 8},
        ]


def test_loader_support_role_is_preserved_but_never_promoted_to_product_scope():
    entry = {"case_id": "case", "dataset": "dataset", "snapshot_id": "snapshot"}
    context = {"masking_contract": "python-slot-v1", "masked_test_sha256": "a" * 64,
               "target_selector": "tests/test_x.py::test_x", "target_scope": []}
    sources = [
        {"source_id": "test", "logical_path": "tests/test_x.py", "sha256": "a" * 64,
         "bytes": 5, "selection_role": "masked_test"},
        {"source_id": "source:1", "logical_path": "tests/test_utils.py", "sha256": "b" * 64,
         "bytes": 5, "selection_role": "loader_support"},
    ]
    criteria = [{"criterion_id": "criterion:0", "line": 7, "kind": "value_assertion",
                 "confidence": "explicit", "reason": "assert"}]
    observation = {"relation": "observation", "sourcePath": "tests/test_x.py",
                   "sourceLine": "7", "sourceEndLine": "7", "targetPath": "tests/test_x.py",
                   "targetLine": "7", "targetEndLine": "7", "observationLine": "7",
                   "argumentIndex": "-1", "detail": "selected"}
    contract = backend.build_contract(
        entry, context, sources, criteria, [observation], costs(), [])
    assert contract["source_scope"][1]["selection_role"] == "loader_support"
    assert contract["entry_point"]["mut_binding"]["status"] == "unresolved"


def test_support_loader_facts_require_visible_verified_pipeline_and_unique_target():
    test = """import asyncio
from .test_utils import load_function_from_source_ast
SRC = "src/pkg/api.py"
def test_x():
    run = load_function_from_source_ast(SRC, "run", start_line=1)
    assert asyncio.run(run())
"""
    helper = """import ast
import textwrap
def load_function_from_source_ast(module_path, function_name, namespace=None, start_line=None):
    source = open(module_path, encoding="utf-8").read()
    tree = ast.parse(source)
    functions = [node for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == function_name and (start_line is None or node.lineno == start_line)]
    if not functions:
        raise ValueError("missing")
    node = functions[-1]
    start = node.decorator_list[0].lineno if node.decorator_list else node.lineno
    lines = source.splitlines()
    body = textwrap.dedent("\\n".join(lines[start - 1:node.end_lineno]))
    prefix = "from __future__ import annotations\\n" + "\\n" * (start - 2)
    target_namespace = dict(namespace or {})
    target_namespace.setdefault("__name__", "visible_source")
    exec(compile(prefix + body, module_path, "exec"), target_namespace)
    return target_namespace[function_name]
"""
    sources = [
        {"source_id": "test", "logical_path": "tests/test_x.py", "text": test,
         "selection_role": "masked_test"},
        {"source_id": "source:1", "logical_path": "tests/test_utils.py", "text": helper,
         "selection_role": "loader_support"},
        {"source_id": "source:2", "logical_path": "src/pkg/api.py",
         "text": "async def run():\n    return 1\n", "selection_role": "candidate_product"},
    ]
    selection = {"evidence": [{"stage": "loader_support",
                                "candidates": ["tests/test_utils.py"]}]}
    scope = {"symbol": "test_x", "start_line": 4}
    assert backend.support_loader_facts(sources, selection, scope) == [
        [6, "run", "src/pkg/api.py", 1, "run", 2, 2, "asyncio"]]

    sources[1]["text"] = helper.replace('setdefault("__name__", "visible_source")',
                                         'setdefault("__builtins__", "visible_source")')
    assert backend.support_loader_facts(sources, selection, scope) == []

    sources[1]["text"] = helper.replace("ast.walk(tree)", "ast.iter_child_nodes(tree)")
    assert backend.support_loader_facts(sources, selection, scope) == []
    sources[1]["text"] = helper
    sources[2]["text"] += "\nasync def run():\n    return 2\n"
    sources[0]["text"] = test.replace(", start_line=1", "")
    assert backend.support_loader_facts(sources, selection, scope) == []


def test_support_loader_rejects_dynamic_arguments_decorators_and_callable_rebinding():
    helper = """import ast
def loader(module_path, function_name):
    source = open(module_path).read()
    tree = ast.parse(source)
    functions = [node for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == function_name]
    node = functions[-1]
    lines = source.splitlines()
    body = "\\n".join(lines[node.lineno - 1:node.end_lineno])
    namespace = {}
    exec(compile(body, module_path, "exec"), namespace)
    return namespace[function_name]
"""
    base_test = """from .test_utils import loader
PATH = "src/x.py"
def test_x():
    f = loader(PATH, "f")
    assert f()
"""
    sources = [
        {"source_id": "test", "logical_path": "tests/test_x.py", "text": base_test},
        {"source_id": "source:1", "logical_path": "tests/test_utils.py", "text": helper,
         "selection_role": "loader_support"},
        {"source_id": "source:2", "logical_path": "src/x.py", "text": "def f():\n    return 1\n"},
    ]
    selection = {"evidence": [{"stage": "loader_support",
                                "candidates": ["tests/test_utils.py"]}]}
    scope = {"symbol": "test_x", "start_line": 3}
    assert backend.support_loader_facts(sources, selection, scope)
    sources[0]["text"] = base_test.replace('loader(PATH, "f")', "loader(path_factory(), name)")
    assert backend.support_loader_facts(sources, selection, scope) == []
    for unsupported_call in (
        'loader(PATH, "f", 1)',
        'loader(PATH, "f", {}, 1)',
        'loader(PATH, "f", start_line=1, start_line=2)',
        'loader(*args)',
    ):
        sources[0]["text"] = base_test.replace('loader(PATH, "f")', unsupported_call)
        diagnostics = []
        assert backend.support_loader_facts(sources, selection, scope, diagnostics) == []
        assert diagnostics[0]["stage"] in {
            "loader_call_positional_shape_unsupported", "loader_call_arguments_not_static"}
    sources[0]["text"] = base_test.replace("    assert f()", "    f = other\n    assert f()")
    assert backend.support_loader_facts(sources, selection, scope) == []
    sources[0]["text"] = base_test
    sources[2]["text"] = "@decorator\ndef f():\n    return 1\n"
    assert backend.support_loader_facts(sources, selection, scope) == []


def test_support_loader_rejects_disconnected_out_of_order_and_duplicate_import_chains():
    helper = """import ast
def loader(module_path, function_name):
    source = open(module_path).read()
    tree = ast.parse(source)
    functions = [node for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == function_name]
    node = functions[-1]
    lines = source.splitlines()
    body = "\\n".join(lines[node.lineno - 1:node.end_lineno])
    namespace = {}
    exec(compile(body, module_path, "exec"), namespace)
    return namespace[function_name]
"""
    test = """from .test_utils import loader
PATH = "src/x.py"
def test_x():
    f = loader(PATH, "f")
    assert f()
"""
    sources = [
        {"source_id": "test", "logical_path": "tests/test_x.py", "text": test},
        {"source_id": "support", "logical_path": "tests/test_utils.py", "text": helper,
         "selection_role": "loader_support"},
        {"source_id": "product", "logical_path": "src/x.py", "text": "def f():\n    return 1\n"},
    ]
    selection = {"evidence": [{"stage": "loader_support",
                                "candidates": ["tests/test_utils.py"]}]}
    scope = {"symbol": "test_x", "start_line": 3}
    assert backend.support_loader_facts(sources, selection, scope)
    guarded = helper.replace(
        "    node = functions[-1]\n",
        "    if not functions:\n        raise ValueError('missing')\n    node = functions[-1]\n")
    sources[1]["text"] = guarded
    assert backend.support_loader_facts(sources, selection, scope)
    for invalid_guard in (
        guarded.replace("        raise ValueError('missing')",
                        "        namespace = {}"),
        guarded.replace("    node = functions[-1]",
                        "    else:\n        raise ValueError('other')\n    node = functions[-1]"),
        guarded.replace("if not functions", "if not tree"),
    ):
        sources[1]["text"] = invalid_guard
        assert backend.support_loader_facts(sources, selection, scope) == []
    sources[1]["text"] = helper.replace(
        "    source = open(module_path).read()",
        "    marker: object\n    source = open(module_path).read()")
    diagnostics = []
    assert backend.support_loader_facts(sources, selection, scope, diagnostics) == []
    assert diagnostics[0]["stage"] == "helper_annotation_without_value"
    sources[1]["text"] = helper
    prefixed = helper.replace(
        "    namespace = {}",
        '    prefix = "from __future__ import annotations\\n" + "\\n" * (node.lineno - 2)\n'
        "    namespace = {}").replace(
            'compile(body, module_path, "exec")',
            'compile(prefix + body, module_path, "exec")')
    sources[1]["text"] = prefixed
    assert backend.support_loader_facts(sources, selection, scope)
    for invalid_prefix in (
        prefixed.replace("from __future__ import annotations\\n", "async "),
        prefixed.replace("node.lineno - 2", "node.end_lineno - 2"),
        prefixed.replace("prefix + body", "body + prefix"),
    ):
        sources[1]["text"] = invalid_prefix
        assert backend.support_loader_facts(sources, selection, scope) == []
    sources[1]["text"] = helper
    diagnostics = []
    sources[1]["text"] = helper.replace(
        'exec(compile(body, module_path, "exec"), namespace)',
        'exec(compile("def f(): return 99", module_path, "exec"), namespace)')
    assert backend.support_loader_facts(sources, selection, scope, diagnostics) == []
    assert diagnostics[0]["stage"] == "helper_compile_chain"
    sources[1]["text"] = helper.replace(
        "compile(body, module_path, \"exec\")",
        "compile('fake' if body else 'fake', module_path, \"exec\")")
    assert backend.support_loader_facts(sources, selection, scope) == []
    sources[1]["text"] = helper.replace(
        'body = "\\n".join(lines[node.lineno - 1:node.end_lineno])',
        'body = "fake" if "\\n".join(lines[node.lineno - 1:node.end_lineno]) else "fake"')
    assert backend.support_loader_facts(sources, selection, scope) == []
    sources[1]["text"] = helper.replace(
        "compile(body, module_path, \"exec\")",
        "compile(body + '\\ndef f(): return 99', module_path, \"exec\")")
    assert backend.support_loader_facts(sources, selection, scope) == []
    sources[1]["text"] = helper.replace(
        "    tree = ast.parse(source)\n", "    tree = ast.parse(source)\n    source = source\n")
    assert backend.support_loader_facts(sources, selection, scope) == []
    sources[1]["text"] = helper
    sources[0]["text"] = test.replace(
        "from .test_utils import loader", "from .test_utils import loader\nfrom .fake import loader")
    assert backend.support_loader_facts(sources, selection, scope) == []
    sources[0]["text"] = test
    sources[1]["text"] = helper.replace("import ast", "import ast\nast = fake")
    assert backend.support_loader_facts(sources, selection, scope) == []
    sources[1]["text"] = helper.replace("import ast", "import ast\ncompile = fake")
    assert backend.support_loader_facts(sources, selection, scope) == []

    sources[1]["text"] = helper.replace(
        "def loader(module_path, function_name):",
        "def loader(module_path, function_name, start_line=None):")
    sources[2]["text"] += "\ndef f():\n    return 2\n"
    sources[0]["text"] = test.replace('loader(PATH, "f")',
                                      'loader(PATH, "f", start_line=1)')
    diagnostics = []
    assert backend.support_loader_facts(sources, selection, scope, diagnostics) == []
    assert diagnostics[-1]["stage"] == "helper_start_line_not_authenticated"


def test_module_scope_conditional_delete_loop_and_invalid_async_wrapper_are_rejected():
    product = {"source_id": "source:1", "logical_path": "src/pkg/api.py",
               "text": "async def run():\n    return 1\n"}
    selection = {"evidence": [{"stage": "masked_test", "hint_kind": "python_import",
                                "hint": "pkg.api", "candidates": ["src/pkg/api.py"]}]}
    base = "from pkg.api import run\ndef test_x():\n    assert run()\n"
    for mutation in ("if flag:\n    run = other\n", "del run\n",
                     "for run in values:\n    pass\n"):
        sources = [{"source_id": "test", "logical_path": "tests/test_x.py",
                    "text": base.replace("def test_x", mutation + "def test_x")}, product]
        assert backend.import_facts(sources, selection, {"symbol": "test_x",
                                                         "start_line": mutation.count(chr(10)) + 2}) == []
    invalid = "import asyncio\nfrom pkg.api import run\ndef test_x():\n    assert asyncio.run(run(), extra)\n"
    sources = [{"source_id": "test", "logical_path": "tests/test_x.py", "text": invalid}, product]
    diagnostics = []
    facts = backend.import_facts(sources, selection, {"symbol": "test_x", "start_line": 3},
                                 diagnostics)
    assert facts[0][6] == -1
    assert diagnostics == [{"scope": "visible_import_binding", "path": "tests/test_x.py",
                            "line": 4, "name": "run",
                            "stage": "coroutine_completion_unsupported"}]


def test_product_body_counts_exclude_unknown_effect_boundaries_from_typed_influence():
    criteria = [{"criterion_id": "criterion:0", "line": 7}]
    observation = {"relation": "observation", "sourcePath": "test.py",
                   "targetPath": "test.py", "observationLine": "7"}
    body = {"relation": "product_return_to_observation", "sourcePath": "src/x.py",
            "targetPath": "test.py", "observationLine": "7"}
    unknown = {"relation": "product_call_unknown_effect", "sourcePath": "src/x.py",
               "targetPath": "test.py", "observationLine": "7"}
    evidence = backend.summarize_entry_evidence(criteria, [observation, body, unknown])
    assert evidence["observation_linked_cross_source_relations"] == 1
    assert evidence["product_body_returns"] == 1
    assert evidence["product_unknown_effect_boundaries"] == 1
