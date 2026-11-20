import copy
import json
from pathlib import Path

import navigation
import inputs


ROOT = Path(__file__).resolve().parents[3]
ZIPKIN = (
    ROOT / "out/mockbench-study/generic-entry-20261111-r1/java-zipkin-direct-r6"
    / "93218e5fbc887922f642/batch-result.json"
)
LANGFLOW = ROOT / "out/mockbench-study/langflow-backend-bridge-20261111-r6-contract-revalidation/case-result.json"


def _load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def _visible_sources(contract):
    paths = {source["source_id"]: source["logical_path"] for source in contract["source_scope"]}
    lines = {}
    for observation in contract["observations"]:
        location = observation["location"]
        path = paths[location["source_id"]]
        lines.setdefault(path, {})[location["start_line"]] = f"observation {observation['observation_id']}"
    for relation in contract["relations"]:
        for endpoint in (relation["from"], relation["to"]):
            if isinstance(endpoint, dict) and "logical_path" in endpoint and "start_line" in endpoint:
                line_map = lines.setdefault(endpoint["logical_path"], {})
                for line in range(endpoint["start_line"], endpoint.get("end_line", endpoint["start_line"]) + 1):
                    line_map[line] = f"visible line {line}"
    sources = []
    for path, line_map in lines.items():
        start, end = min(line_map), max(line_map)
        text = "\n".join(line_map.get(line, "") for line in range(start, end + 1))
        sources.append({"logical_path": path, "start_line": start, "text": text})
    return sources


def test_zipkin_does_not_copy_single_observation_evidence_to_second_assertion():
    batch = _load(ZIPKIN)
    contract = batch["result_contract"]

    projected = navigation.format_navigation(batch, _visible_sources(contract))

    cards = {card["observation_id"]: card for card in projected["observations"]}
    fact_sets = {row["fact_set_id"]: row["fact_refs"] for row in projected["fact_sets"]}
    assert len(fact_sets[cards["criterion:0"]["fact_ref_set_id"]]) == 2
    assert fact_sets[cards["criterion:1"]["fact_ref_set_id"]] == []
    assert projected["projection"]["projected_fact_count"] == 2
    assert all(fact["modality"] == "analysis_may" for fact in projected["facts"])
    assert projected["boundaries"] == contract["analysis_boundaries"]


def test_relation_order_does_not_change_projection():
    contract = _load(LANGFLOW)
    sources = _visible_sources(contract)
    shuffled = copy.deepcopy(contract)
    shuffled["relations"].reverse()

    first = navigation.format_navigation(contract, sources)
    second = navigation.format_navigation(shuffled, list(reversed(sources)))

    assert first == second
    assert first["projection"]["input_relation_count"] == len(contract["relations"])
    assert any(fact["modality"] == "unknown_effect" for fact in first["facts"])
    fact_sets = {row["fact_set_id"]: row["fact_refs"] for row in first["fact_sets"]}
    assert all(fact_sets[card["fact_ref_set_id"]] for card in first["observations"])


def test_exact_directed_chain_can_attach_an_unlabelled_predecessor():
    contract = _load(LANGFLOW)
    direct = next(row for row in contract["relations"] if row.get("observation_ids"))
    predecessor = {
        "kind": "value_flow",
        "modality": "analysis_may",
        "from": {"logical_path": "product.py", "start_line": 7, "end_line": 7},
        "to": copy.deepcopy(direct["from"]),
        "provenance": "test fixture",
        "precision": "exact endpoint",
        "assumptions": ["path feasibility unknown"],
        "observation_ids": [],
    }
    contract["relations"].append(predecessor)

    projected = navigation.format_navigation(contract, _visible_sources(contract))
    predecessor_fact = next(
        item for item in projected["facts"] if item["from"] == predecessor["from"]
    )
    predecessor_id = predecessor_fact["fact_id"]
    card = next(
        item for item in projected["observations"]
        if item["observation_id"] in direct["observation_ids"]
    )

    fact_refs = next(
        row["fact_refs"] for row in projected["fact_sets"]
        if row["fact_set_id"] == card["fact_ref_set_id"]
    )
    assert predecessor_id in fact_refs
    assert predecessor_fact["modality"] == "analysis_may"
    assert predecessor_fact["assumptions"] == ["path feasibility unknown"]


def test_source_scope_metadata_is_accepted_without_inventing_source_text():
    batch = _load(ZIPKIN)
    contract = batch["result_contract"]

    projected = navigation.format_navigation(batch, contract["source_scope"])

    assert projected["projection"]["projected_fact_count"] == len(contract["relations"])
    assert projected["source_lines"] == []
    assert projected["observations"][0]["location"]["logical_path"].endswith("SchemaTest.java")


def _keys(value):
    if isinstance(value, dict):
        for key, child in value.items():
            yield key
            yield from _keys(child)
    elif isinstance(value, list):
        for child in value:
            yield from _keys(child)


def test_full_and_compact_packages_share_inputs_and_exclude_evaluator_fields():
    contract, sources = inputs.load_case("jib")

    full = navigation.format_full_evidence(contract, sources)
    compact = navigation.format_navigation(contract, sources)

    assert full["projection"]["input_contract_sha256"] == compact["projection"]["input_contract_sha256"]
    assert full["projection"]["visible_sources_sha256"] == compact["projection"]["visible_sources_sha256"]
    assert full["projection"]["output_payload_bytes"] > compact["projection"]["output_payload_bytes"]
    assert len(full["source_lines"]) == len({row["source_line_id"] for row in full["source_lines"]})
    forbidden = {"semantic_validation", "cost", "reviewer", "notes"}
    assert forbidden.isdisjoint(_keys(full))
    assert forbidden.isdisjoint(_keys(compact))
    assert all(not key.startswith("_") for key in _keys(full))
    assert all(not key.startswith("_") for key in _keys(compact))
    assert "semantic_validation_not_evaluated" not in json.dumps({"b": full, "c": compact})
    relation_fields = {
        "kind", "modality", "from", "to", "provenance", "precision", "assumptions",
        "observation_ids", *navigation.RELATION_DETAIL_FIELDS,
    }
    assert all(set(relation) <= relation_fields for relation in contract["relations"])
    assert all(
        set(relation).intersection(navigation.RELATION_DETAIL_FIELDS)
        <= set(fact.get("bindings_and_path_details", {}))
        for relation, fact in zip(
            sorted(contract["relations"], key=navigation._digest),
            sorted(full["relations"], key=lambda row: row["fact_id"]),
        )
    )


def test_real_dynamic_bridge_and_weak_evidence_contracts_project_without_upgrading():
    for case_key in ("langflow_189", "ansible_119", "click_423", "click_402"):
        contract, sources = inputs.load_case(case_key)
        compact = navigation.format_navigation(contract, sources)
        assert compact["projection"]["input_relation_count"] == len(contract["relations"])
        assert compact["projection"]["visible_sources_sha256"]
        assert [fact["modality"] for fact in compact["facts"]] == [
            fact["modality"] for fact in navigation.format_navigation(contract, sources)["facts"]
        ]
        assert {"semantic_validation", "cost", "reviewer", "notes"}.isdisjoint(_keys(compact))
    langflow, sources = inputs.load_case("langflow_189")
    projected = navigation.format_navigation(langflow, sources)
    assert any(fact["modality"] == "unknown_effect" for fact in projected["facts"])
    assert any("frontend_bridge_required" in fact["precision"] for fact in projected["facts"])


def test_issue34_exception_and_fallback_relations_are_kept_as_may_evidence():
    study = _load(ROOT / "out/mockbench-study/python-issue26-27-20261111-r11/observation-study.json")
    contract = study["case_result"]

    projected = navigation.format_navigation(contract, [])

    kinds = {fact["kind"] for fact in projected["facts"]}
    assert "exception_dependence" in kinds
    assert "normal_return_dependence" in kinds
    assert all(
        fact["modality"] == "analysis_may"
        for fact in projected["facts"] if fact["kind"] in {"exception_dependence", "normal_return_dependence"}
    )


def test_navigation_cards_make_locations_unknowns_guards_and_successors_explicit():
    contract, sources = inputs.load_case("jib")
    projected = navigation.format_navigation(contract, sources)
    fact_sets = {row["fact_set_id"]: row["fact_refs"] for row in projected["fact_sets"]}

    for card in projected["observations"]:
        navigation_card = card["navigation"]
        assert navigation_card["last_visible_locations"]
        assert navigation_card["unknown"]["case_unknown_ref"] == "case_navigation.unknown"
        assert navigation_card["fact_ref_set_id"] in fact_sets
        assert navigation_card["guards"]["test"]["select_role"] == "guard:test"
        assert navigation_card["guards"]["product"]["select_role"] == "guard:product"
        assert navigation_card["successors"]["normal"]["select_role"] == "successor:normal"
        assert navigation_card["successors"]["exceptional"]["select_role"] == "successor:exceptional"
        assert navigation_card["unknown"]["select_role"] == "unknown"
    assert projected["case_navigation"]["unknown"]["boundary_refs"]
    assert "semantic_validation_not_evaluated" not in json.dumps(projected)
    roles = {role for fact in projected["facts"] for role in fact["roles"]}
    assert {"successor:normal", "successor:exceptional"} <= roles


def test_coordinate_free_jib_endpoints_are_not_presented_as_source_locations():
    contract, sources = inputs.load_case("jib")
    projected = navigation.format_navigation(contract, sources)

    for card in projected["observations"]:
        for location in card["navigation"]["last_visible_locations"]:
            assert set(location) >= {"logical_path", "start_line", "end_line"}
            assert isinstance(location["start_line"], int)
    assert all(
        "logical_path" in location
        for location in projected["case_navigation"]["last_visible_locations"]
    )
