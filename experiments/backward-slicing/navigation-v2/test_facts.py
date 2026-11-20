import copy
import json
from pathlib import Path

import facts
import inputs
import navigation
from study import case_data


ROOT = Path(__file__).resolve().parents[3]


def _all_keys(value):
    if isinstance(value, dict):
        for key, child in value.items():
            yield key
            yield from _all_keys(child)
    elif isinstance(value, list):
        for child in value:
            yield from _all_keys(child)


def _call(payload, name):
    return (
        next(item for item in payload["calls"] if item["display"] == name)
        if any(item["display"] == name for item in payload["calls"])
        else next(item for item in payload["calls"] if item["display"].endswith(name))
    )


def _effect(call, name):
    return next(item for item in call["effects"] if item["effect"] == name)


def test_real_jib_is_compact_and_contains_source_grounded_dependency_calls():
    contract, sources = inputs.load_case("jib")

    projected = facts.format_facts(contract, sources)
    v1 = navigation.format_navigation(contract, sources)

    assert projected["schema_version"] == facts.SCHEMA_VERSION
    assert len(facts._encoded(projected)) < len(facts._encoded(v1)) * 0.8
    assert any(call["structural_role"] == "entry_call" for call in projected["calls"])
    processor = _call(projected, "ArtifactProcessor.createLayers")
    assert processor["structural_role"] == "outbound_call"
    assert processor["boundary_resolution"] == "mut_parameter_declared_target"
    assert (
        _call(projected, "JibContainerBuilder.setEntrypoint")["structural_role"]
        == "unresolved_call"
    )
    getter = _call(projected, "CommonContainerConfigCliOptions.getEnvironment")
    assert getter["location"]["start_line"] == 86
    assert _effect(getter, "return")["reaches_assertion"] is None
    assert getter["other_flows"]


def test_call_binding_and_non_value_relations_do_not_upgrade_return_reachability():
    contract, sources = inputs.load_case("jib")

    projected = facts.format_facts(contract, sources)

    entry = next(
        call for call in projected["calls"] if call["structural_role"] == "entry_call"
    )
    assert _effect(entry, "return")["reaches_assertion"] is None
    assert _effect(entry, "exception")["reaches_assertion"] is None
    for call in projected["calls"]:
        return_effect = _effect(call, "return")
        if return_effect["reaches_assertion"] is True:
            assert return_effect["evidence"]
            assert {item["kind"] for item in return_effect["evidence"]} == {
                "value_flow"
            }
        exception_effect = _effect(call, "exception")
        if exception_effect["reaches_assertion"] is True:
            assert exception_effect["evidence"]
            assert {item["kind"] for item in exception_effect["evidence"]} == {
                "exception_dependence"
            }


def test_real_flask_keeps_dynamic_boundaries_guards_and_channel_precision():
    contract, sources, _, _ = case_data("flask_308")

    projected = facts.format_facts(contract, sources)

    assert _call(projected, "abort")["structural_role"] == "entry_call"
    context_get = _call(projected, "_cv_app.get")
    app_aborter = _call(projected, "ctx.app.aborter")
    fallback = _call(projected, "_wz_abort")
    assert context_get["structural_role"] == "unresolved_call"
    assert app_aborter["structural_role"] == "unresolved_call"
    assert fallback["structural_role"] == "outbound_call"
    assert fallback["boundary_resolution"].startswith("external_import:")
    assert (
        app_aborter["guards"][0]["expression"]
        == "(ctx := _cv_app.get(None)) is not None"
    )
    assert _effect(app_aborter, "return")["reaches_assertion"] is False
    exception = _effect(app_aborter, "exception")
    assert exception["reaches_assertion"] is True
    assert {item["path_quantifier"] for item in exception["evidence"]} == {"may"}
    assert "host-verified" not in json.dumps(projected)


def test_multiple_python_calls_on_one_line_do_not_share_an_endpoint_effect():
    contract, _sources, _, _ = case_data("flask_308")
    modified = copy.deepcopy(contract)
    product = next(
        item
        for item in modified["source_scope"]
        if item["logical_path"] == "src/src/flask/helpers.py"
    )
    text = "def abort(code):\n    left(left())\n"
    product["bytes"] = len(text.encode())
    modified["entry_point"]["mut_binding"]["evidence"] = [
        {
            "logical_path": product["logical_path"],
            "start_line": 1,
            "end_line": 1,
        }
    ]
    modified["relations"] = [
        {
            "kind": "call_binding",
            "modality": "certain",
            "from": {
                "logical_path": "tests/test_308abort.py",
                "start_line": 1,
                "end_line": 1,
            },
            "to": {
                "logical_path": product["logical_path"],
                "start_line": 1,
                "end_line": 2,
            },
            "provenance": "fixture binding",
            "precision": "fixture function span",
            "assumptions": [],
            "observation_ids": [],
        },
        {
            "kind": "exception_dependence",
            "modality": "analysis_may",
            "from": {
                "logical_path": product["logical_path"],
                "start_line": 2,
                "end_line": 2,
            },
            "to": {
                "logical_path": "tests/test_308abort.py",
                "start_line": 120,
                "end_line": 120,
            },
            "provenance": "fixture line endpoint",
            "precision": "line only",
            "assumptions": [],
            "observation_ids": ["criterion:1"],
        },
    ]
    sources = {
        product["source_id"]: {
            "source_id": product["source_id"],
            "logical_path": product["logical_path"],
            "start_line": 1,
            "end_line": 2,
            "text": text,
        }
    }

    projected = facts.format_facts(modified, sources)

    calls = [call for call in projected["calls"] if call["display"] == "left"]
    assert len(calls) == 2
    assert len({call["call_id"] for call in calls}) == 2
    assert all(call["fact_refs"] for call in calls)
    assert all(
        _effect(call, "exception")["reaches_assertion"] is None for call in calls
    )


def test_all_frozen_cases_project_and_preserve_thin_evidence_boundaries():
    projected = {
        case: facts.format_facts(*inputs.load_case(case)) for case in inputs.case_keys()
    }

    zipkin = projected["zipkin"]
    zipkin_entry = next(
        call for call in zipkin["calls"] if call["structural_role"] == "entry_call"
    )
    assert zipkin_entry["location"]["start_line"] == 38
    assert "Schema.getKeyspaceMetadata" in zipkin_entry["display"]

    ansible = projected["ansible_119"]
    assert ansible["entry_binding"] == {
        "origin": "automatic",
        "status": "unresolved",
        "candidates": [],
        "evidence": [],
        "assumptions": [],
    }
    assert ansible["calls"] == []
    assert ansible["relations"] == []
    assert ansible["analysis_boundaries"]["unanalysed_regions"]

    click_423 = projected["click_423"]
    product_calls = [
        call for call in click_423["calls"] if call["structural_role"] != "entry_call"
    ]
    assert product_calls
    assert all(
        call["location"]["logical_path"] == "src/click/termui.py"
        and 261 <= call["location"]["start_line"] <= 290
        for call in product_calls
    )
    assert all(
        _effect(call, "return")["reaches_assertion"] in {False, None}
        for call in product_calls
    )

    click_402 = projected["click_402"]
    format_calls = [
        call for call in click_402["calls"] if call["structural_role"] != "entry_call"
    ]
    assert format_calls
    assert all(407 <= call["location"]["start_line"] <= 446 for call in format_calls)
    assert all(
        _effect(call, "return")["reaches_assertion"] is not True
        for call in format_calls
    )
    for payload in projected.values():
        call_ids = [call["call_id"] for call in payload["calls"]]
        assert len(call_ids) == len(set(call_ids))


def test_projection_has_no_test_decision_labels_and_is_order_stable():
    contract, sources = inputs.load_case("jib")
    reversed_contract = copy.deepcopy(contract)
    reversed_contract["relations"].reverse()

    first = facts.format_facts(contract, sources)
    second = facts.format_facts(
        reversed_contract, dict(reversed(list(sources.items())))
    )

    # The digest identifies the original ordered input; the projected facts are stable.
    assert first["projection"].pop("input_contract_sha256") != second["projection"].pop(
        "input_contract_sha256"
    )
    assert first == second
    forbidden = {
        "interpretation",
        "call_binding_is_interaction_candidate",
        "verification_support",
        "recommended_action",
        "mandatory_interactions",
        "times",
    }
    assert forbidden.isdisjoint(_all_keys(first))
    assert all(
        effect["reaches_assertion"] in {True, False, None}
        for call in first["calls"]
        for effect in call["effects"]
    )
