import hashlib
import json

from mock_spec.backward_result import build_result_contract
from mock_spec.navigation_facts import SCHEMA_VERSION, project_navigation_facts


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _contract(language: str, product_path: str, product_text: str) -> dict:
    test_text = "def test_target():\n    assert target()\n"
    raw = {
        "status": "partial",
        "stage": "complete",
        "failure_categories": ["semantic_analysis_gap"],
        "input_context": {
            "target_selector": "test_module.py::test_target",
            "masked_test_sha256": _sha(test_text),
            "masking_contract": f"{language}-slot-v1",
        },
        "target_extraction": {
            "target_scope": {
                "selector": "test_module.py::test_target",
                "status": "located",
                "locations": [{"start_line": 1, "end_line": 2}],
            },
            "criteria": [
                {
                    "criterion_id": "criterion:0",
                    "line": 2,
                    "kind": "value_assertion",
                    "confidence": "explicit",
                    "reason": "visible assertion",
                }
            ],
        },
        "sources": [
            {
                "source_id": "test",
                "logical_path": "test_module.py",
                "sha256": _sha(test_text),
                "bytes": len(test_text.encode()),
            },
            {
                "source_id": "product",
                "logical_path": product_path,
                "sha256": _sha(product_text),
                "bytes": len(product_text.encode()),
            },
        ],
        "source_selection": {"truncated": False},
        "evidence": {"nodes": 0, "edges": 0, "criteria": []},
        "timings_seconds": {},
        "elapsed_seconds": 0.0,
    }
    return build_result_contract(
        raw,
        {
            "case_id": "case",
            "dataset": "dataset",
            "language": language,
            "snapshot_id": "a" * 64,
        },
    )


def _sources(product_path: str, product_text: str) -> dict:
    return {
        "test": {
            "logical_path": "test_module.py",
            "start_line": 1,
            "text": "def test_target():\n    assert target()\n",
        },
        "product": {
            "logical_path": product_path,
            "start_line": 1,
            "text": product_text,
        },
    }


def _relation(kind, source, target, observations=()):
    return {
        "kind": kind,
        "modality": "analysis_may",
        "from": source,
        "to": target,
        "provenance": "test fixture",
        "precision": "bounded fixture evidence",
        "assumptions": [],
        "observation_ids": list(observations),
    }


def test_symbol_only_java_entry_is_preserved_without_inventing_a_line():
    path = "src/Schema.java"
    contract = _contract("java", path, "class Schema {}\n")
    contract["entry_point"]["mut_binding"].update(
        status="unique",
        candidates=[{"logical_path": path, "symbol": "Schema.getMetadata(Session)"}],
        evidence=[],
    )
    binding = _relation(
        "call_binding",
        {"logical_path": "test_module.py", "start_line": 2, "end_line": 2},
        {"logical_path": path, "symbol": "Schema.getMetadata(Session)"},
    )
    binding.pop("observation_ids")
    contract["relations"] = [binding]

    result = project_navigation_facts(contract, _sources(path, "class Schema {}\n"))

    assert result["schema_version"] == SCHEMA_VERSION
    entry = next(call for call in result["calls"] if call["structural_role"] == "entry_call")
    assert entry["display"] == "Schema.getMetadata(Session)"
    assert entry["location"]["start_line"] == 2


def test_unresolved_entry_remains_an_empty_fact_map():
    path = "module.py"
    contract = _contract("python", path, "def target():\n    return True\n")
    contract["entry_point"]["mut_binding"].update(status="unresolved")

    result = project_navigation_facts(
        contract, _sources(path, "def target():\n    return True\n")
    )

    assert result["entry_binding"]["status"] == "unresolved"
    assert result["calls"] == []
    assert result["relations"] == []


def test_function_span_flow_does_not_upgrade_an_internal_call_to_assertion_flow():
    path = "module.py"
    product = "def target():\n    dependency()\n    return True\n"
    contract = _contract("python", path, product)
    contract["entry_point"]["mut_binding"].update(
        status="unique",
        candidates=[{"logical_path": path, "start_line": 1, "symbol": "target"}],
        evidence=[],
    )
    contract["relations"] = [
        _relation(
            "call_binding",
            {"logical_path": "test_module.py", "start_line": 2, "end_line": 2},
            {"logical_path": path, "start_line": 1, "end_line": 3},
        ),
        _relation(
            "value_flow",
            {"logical_path": path, "start_line": 1, "end_line": 3},
            {"logical_path": "test_module.py", "start_line": 2, "end_line": 2},
            ["criterion:0"],
        ),
    ]

    result = project_navigation_facts(contract, _sources(path, product))

    dependency = next(call for call in result["calls"] if call["display"] == "dependency")
    return_effect = next(effect for effect in dependency["effects"] if effect["effect"] == "return")
    assert return_effect["reaches_assertion"] is False
    encoded = json.dumps(result)
    for forbidden in (
        "verification_support",
        "recommended_action",
        "mandatory_interactions",
        "call_binding_is_interaction_candidate",
        "times(1)",
    ):
        assert forbidden not in encoded


def test_jib_shaped_wala_call_keeps_return_and_state_evidence_separate():
    path = "src/JarFiles.java"
    product = (
        "class JarFiles {\n"
        "  Object toBuilder(ArtifactProcessor processor) {\n"
        "    return processor.createLayers();\n"
        "  }\n"
        "}\n"
    )
    contract = _contract("java", path, product)
    method = "example.JarFiles.toBuilder(example.ArtifactProcessor)"
    call = {
        "method": method,
        "context": "Everywhere",
        "instruction_index": 27,
        "call_site": "invokeinterface ArtifactProcessor.createLayers@64",
        "declared_target": "example.ArtifactProcessor.createLayers()java.util.List",
    }
    contract["entry_point"]["mut_binding"].update(
        status="unique", candidates=[method], evidence=[]
    )
    binding = _relation(
        "call_binding",
        {"logical_path": "test_module.py", "start_line": 2, "end_line": 2},
        {"logical_path": path, "start_line": 2, "end_line": 4},
    )
    return_flow = _relation("value_flow", call, "layers")
    return_flow.pop("observation_ids")
    state_flow = _relation("state_flow", call, "builder.layers", ["criterion:0"])
    contract["relations"] = [binding, return_flow, state_flow]

    result = project_navigation_facts(contract, _sources(path, product))

    dependency = next(
        item for item in result["calls"] if item["display"].endswith("createLayers")
    )
    assert dependency["structural_role"] == "outbound_call"
    assert dependency["boundary_resolution"] == "mut_parameter_declared_target"
    assert dependency["location"]["start_line"] == 3
    assert dependency["call_id"].startswith("c:")
    return_effect = next(
        effect for effect in dependency["effects"] if effect["effect"] == "return"
    )
    assert return_effect["reaches_assertion"] is None
    assert return_effect["observation_ids"] == []
    assert [flow["kind"] for flow in dependency["other_flows"]] == ["state_flow"]
    assert dependency["other_flows"][0]["observation_ids"] == ["criterion:0"]


def test_ambiguous_entry_call_reports_ambiguous_binding():
    path = "module.py"
    product = "def target():\n    return True\n"
    contract = _contract("python", path, product)
    contract["entry_point"]["mut_binding"].update(
        status="ambiguous",
        candidates=[
            {"logical_path": path, "start_line": 1, "symbol": "target"},
            {"logical_path": path, "start_line": 1, "symbol": "other_target"},
        ],
        evidence=[],
    )
    contract["relations"] = [
        _relation(
            "call_binding",
            {"logical_path": "test_module.py", "start_line": 2, "end_line": 2},
            {"logical_path": path, "start_line": 1, "end_line": 2},
        )
    ]

    result = project_navigation_facts(contract, _sources(path, product))

    entry = next(call for call in result["calls"] if call["structural_role"] == "entry_call")
    assert entry["boundary_resolution"] == "ambiguous_mut_binding"


def test_python_absolute_imports_distinguish_visible_project_and_external_modules():
    path = "src/src/backend/pkg/service.py"
    product = (
        "from pkg.helper import dependency\n"
        "from pkg.hidden import hidden_dependency\n"
        "from werkzeug.utils import secure_filename\n"
        "def target():\n"
        "    dependency()\n"
        "    hidden_dependency()\n"
        "    secure_filename('name')\n"
        "    return True\n"
    )
    helper_path = "src/src/backend/pkg/helper.py"
    helper = "def dependency():\n    return True\n"
    contract = _contract("python", path, product)
    contract["source_scope"].append(
        {
            "source_id": "helper",
            "logical_path": helper_path,
            "sha256": _sha(helper),
            "bytes": len(helper.encode()),
            "selection_role": "candidate_product",
        }
    )
    contract["entry_point"]["mut_binding"].update(
        status="unique",
        candidates=[{"logical_path": path, "start_line": 4, "symbol": "target"}],
        evidence=[],
    )
    contract["relations"] = [
        _relation(
            "call_binding",
            {"logical_path": "test_module.py", "start_line": 2, "end_line": 2},
            {"logical_path": path, "start_line": 4, "end_line": 8},
        )
    ]
    sources = _sources(path, product)
    sources["helper"] = {
        "logical_path": helper_path,
        "start_line": 1,
        "text": helper,
    }

    result = project_navigation_facts(contract, sources)

    calls = {call["display"]: call for call in result["calls"]}
    assert calls["dependency"]["structural_role"] == "internal_call"
    assert calls["dependency"]["boundary_resolution"].startswith(
        "visible_product_import:"
    )
    assert calls["hidden_dependency"]["structural_role"] == "unresolved_call"
    assert calls["secure_filename"]["structural_role"] == "outbound_call"
    assert calls["secure_filename"]["boundary_resolution"].startswith(
        "external_import:werkzeug."
    )


def test_static_boundaries_only_remove_the_exact_evaluator_compatibility_note():
    path = "module.py"
    contract = _contract("python", path, "def target():\n    return True\n")
    contract["analysis_boundaries"]["unanalysed_regions"] = [
        {
            "scope": "frozen-case pytest version compatibility with the host-verified reference",
            "reason": "outside bounded model",
        },
        {
            "scope": "method reference resolution",
            "reason": "reference target is unavailable",
        },
    ]

    result = project_navigation_facts(
        contract, _sources(path, "def target():\n    return True\n")
    )

    assert result["analysis_boundaries"]["unanalysed_regions"] == [
        {
            "scope": "method reference resolution",
            "reason": "reference target is unavailable",
        }
    ]


def test_python_name_from_an_unrelated_local_scope_is_not_an_internal_call():
    path = "module.py"
    product = (
        "def unrelated():\n"
        "    def dependency():\n"
        "        return True\n"
        "    return dependency()\n"
        "def target():\n"
        "    dependency()\n"
        "    return True\n"
    )
    contract = _contract("python", path, product)
    contract["entry_point"]["mut_binding"].update(
        status="unique",
        candidates=[{"logical_path": path, "start_line": 5, "symbol": "target"}],
        evidence=[],
    )
    contract["relations"] = [
        _relation(
            "call_binding",
            {"logical_path": "test_module.py", "start_line": 2, "end_line": 2},
            {"logical_path": path, "start_line": 5, "end_line": 7},
        )
    ]

    result = project_navigation_facts(contract, _sources(path, product))

    dependency = next(call for call in result["calls"] if call["display"] == "dependency")
    assert dependency["structural_role"] == "unresolved_call"
    assert dependency["boundary_resolution"] == "dynamic_receiver_or_unresolved_name"


def test_python_imports_do_not_cross_function_scopes_but_local_imports_remain_visible():
    path = "module.py"
    product = (
        "def unrelated():\n"
        "    from werkzeug.utils import foreign_dependency\n"
        "    return foreign_dependency('name')\n"
        "def target():\n"
        "    from werkzeug.utils import local_dependency\n"
        "    foreign_dependency()\n"
        "    local_dependency('name')\n"
        "    return True\n"
    )
    contract = _contract("python", path, product)
    contract["entry_point"]["mut_binding"].update(
        status="unique",
        candidates=[{"logical_path": path, "start_line": 4, "symbol": "target"}],
        evidence=[],
    )
    contract["relations"] = [
        _relation(
            "call_binding",
            {"logical_path": "test_module.py", "start_line": 2, "end_line": 2},
            {"logical_path": path, "start_line": 4, "end_line": 8},
        )
    ]

    result = project_navigation_facts(contract, _sources(path, product))

    calls = {call["display"]: call for call in result["calls"]}
    assert calls["foreign_dependency"]["structural_role"] == "unresolved_call"
    assert calls["local_dependency"]["structural_role"] == "outbound_call"


def test_python_local_bindings_shadow_module_imports():
    path = "module.py"
    imported = ["parameter", "assigned", "looped", "managed", "caught"]
    product = (
        "from werkzeug.utils import " + ", ".join(imported) + "\n"
        "def target(parameter):\n"
        "    assigned = parameter\n"
        "    for looped in []:\n"
        "        pass\n"
        "    with parameter as managed:\n"
        "        pass\n"
        "    try:\n"
        "        pass\n"
        "    except Exception as caught:\n"
        "        pass\n"
        "    parameter()\n"
        "    assigned()\n"
        "    looped()\n"
        "    managed()\n"
        "    caught()\n"
        "    return True\n"
    )
    contract = _contract("python", path, product)
    contract["entry_point"]["mut_binding"].update(
        status="unique",
        candidates=[{"logical_path": path, "start_line": 2, "symbol": "target"}],
        evidence=[],
    )
    contract["relations"] = [
        _relation(
            "call_binding",
            {"logical_path": "test_module.py", "start_line": 2, "end_line": 2},
            {"logical_path": path, "start_line": 2, "end_line": 20},
        )
    ]

    result = project_navigation_facts(contract, _sources(path, product))

    calls = {call["display"]: call for call in result["calls"]}
    for name in imported:
        assert calls[name]["structural_role"] == "unresolved_call"


def test_python_parameter_shadows_a_module_level_function_declaration():
    path = "module.py"
    product = (
        "def dependency():\n"
        "    return True\n"
        "def target(dependency):\n"
        "    dependency()\n"
        "    return True\n"
    )
    contract = _contract("python", path, product)
    contract["entry_point"]["mut_binding"].update(
        status="unique",
        candidates=[{"logical_path": path, "start_line": 3, "symbol": "target"}],
        evidence=[],
    )
    contract["relations"] = [
        _relation(
            "call_binding",
            {"logical_path": "test_module.py", "start_line": 2, "end_line": 2},
            {"logical_path": path, "start_line": 3, "end_line": 5},
        )
    ]

    result = project_navigation_facts(contract, _sources(path, product))

    dependency = next(call for call in result["calls"] if call["display"] == "dependency")
    assert dependency["structural_role"] == "unresolved_call"


def test_python_nested_function_body_calls_are_not_on_the_entry_path():
    path = "module.py"
    product = (
        "def target():\n"
        "    def helper():\n"
        "        dependency()\n"
        "    return True\n"
    )
    contract = _contract("python", path, product)
    contract["entry_point"]["mut_binding"].update(
        status="unique",
        candidates=[{"logical_path": path, "start_line": 1, "symbol": "target"}],
        evidence=[],
    )
    contract["relations"] = [
        _relation(
            "call_binding",
            {"logical_path": "test_module.py", "start_line": 2, "end_line": 2},
            {"logical_path": path, "start_line": 1, "end_line": 4},
        )
    ]

    result = project_navigation_facts(contract, _sources(path, product))

    assert all(call["display"] != "dependency" for call in result["calls"])
