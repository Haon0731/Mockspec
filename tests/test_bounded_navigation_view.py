import hashlib
from copy import deepcopy

import pytest

from mock_spec.bounded_navigation_view import (
    build_bounded_navigation_view,
    measure_bounded_navigation_view,
    serialize_bounded_navigation_view,
)


def _facts() -> dict:
    return {
        "schema_version": "mock-spec.navigation-facts/v2",
        "identity": {
            "case_id": "case",
            "dataset": "python-benchmark-new",
            "language": "python",
            "snapshot_id": "a" * 64,
        },
        "entry_binding": {
            "origin": "automatic",
            "status": "unique",
            "candidates": [],
        },
        "observations": [
            {
                "observation_id": "o:1",
                "kind": "value_assertion",
                "location": {"logical_path": "test.py", "start_line": 10},
            },
            {
                "observation_id": "o:2",
                "kind": "exception_assertion",
                "location": {"logical_path": "test.py", "start_line": 20},
            },
        ],
        "analysis_boundaries": {
            "assumptions": [],
            "unknown_effects": [
                {"reason": "dynamic dispatch", "scope": "dependency call"}
            ],
            "unanalysed_regions": [],
        },
        "calls": [
            {
                "call_id": "c:1",
                "structural_role": "internal_call",
                "location": {"logical_path": "src.py", "start_line": 1},
                "fact_refs": ["r:1"],
                "effects": [{"observation_ids": ["o:1"]}],
            },
            {
                "call_id": "c:2",
                "structural_role": "outbound_call",
                "location": {"logical_path": "src.py", "start_line": 2},
                "fact_refs": ["r:2"],
                "effects": [{"observation_ids": ["o:2"]}],
            },
        ],
        "guards": [
            {
                "guard_id": "g:1",
                "fact_ref": "r:1",
                "from": {"logical_path": "src.py", "start_line": 1},
                "to": {"logical_path": "test.py", "start_line": 10},
                "observation_ids": ["o:1"],
                "provenance": {"backend": "fixture"},
            }
        ],
        "relations": [
            {
                "fact_id": "r:1",
                "kind": "value_flow",
                "modality": "analysis_may",
                "from_ref": "e:1",
                "to_ref": "e:2",
                "source_ref": "s:1",
                "observation_ids": ["o:1"],
            },
            {
                "fact_id": "r:2",
                "kind": "value_flow",
                "modality": "analysis_may",
                "from_ref": "e:3",
                "to_ref": "e:4",
                "source_ref": "s:2",
                "observation_ids": ["o:2"],
            },
        ],
        "endpoints": [
            {"endpoint_id": f"e:{number}", "value": {"logical_path": "src.py", "start_line": number}}
            for number in range(1, 5)
        ],
        "evidence_sources": [
            {
                "source_id": f"s:{number}",
                "precision": "fixture",
                "assumptions": [],
                "provenance": {
                    "dependency_path": [
                        {"from": index, "to": index + 1, "payload": "x" * 40}
                        for index in range(number * 4)
                    ]
                },
            }
            for number in range(1, 3)
        ],
        "projection": {"input_relation_count": 2},
    }


def test_bounded_view_is_deterministic_and_keeps_mandatory_semantics() -> None:
    facts = _facts()
    archive = hashlib.sha256(b"original facts file").hexdigest()

    first = build_bounded_navigation_view(
        facts,
        max_tokens=4_096,
        archive_sha256=archive,
        archive_locator="cases/case.facts.json",
    )
    second = build_bounded_navigation_view(
        facts,
        max_tokens=4_096,
        archive_sha256=archive,
        archive_locator="cases/case.facts.json",
    )

    assert first == second
    assert first["identity"] == facts["identity"]
    assert first["entry_binding"] == facts["entry_binding"]
    columns = first["observations"]["columns"]
    rows = first["observations"]["rows"]
    decoded = [dict(zip(columns, row)) for row in rows]
    for row in decoded:
        row["logical_path"] = first["observations"]["paths"][row.pop("path_ref")]
    assert decoded == [
        {
            "observation_id": item["observation_id"],
            "kind": item["kind"],
            "modality": item.get("modality"),
            "origin": item.get("origin"),
            "logical_path": item["location"]["logical_path"],
            "start_line": item["location"]["start_line"],
            "end_line": item["location"].get("end_line"),
        }
        for item in facts["observations"]
    ]
    assert first["analysis_boundaries"]["unknown_effects"]["rows"] == [
        ["dynamic dispatch", ["dependency call"], [0]]
    ]
    assert first["omissions"]["semantic"] == "budget_omission_is_not_semantic_absence"
    assert first["archive"]["sha256"] == archive
    assert first["archive"]["locator"] == "cases/case.facts.json"
    encoded = serialize_bounded_navigation_view(first)
    measurement = measure_bounded_navigation_view(first)
    assert measurement["tokens"] == len(encoded)
    assert measurement["serialized_bytes"] == len(encoded)
    assert len(encoded) <= 4_096


def test_atomic_fact_closure_and_compact_guard_and_dependency_path() -> None:
    view = build_bounded_navigation_view(_facts(), max_tokens=4_096)

    relation_ids = {item["fact_id"] for item in view["relations"]}
    endpoint_ids = {item["endpoint_id"] for item in view["endpoints"]}
    source_ids = {item["source_id"] for item in view["evidence_sources"]}
    for call in view["calls"]:
        assert set(call["fact_refs"]) <= relation_ids
    for relation in view["relations"]:
        assert {relation["from_ref"], relation["to_ref"]} <= endpoint_ids
        assert relation["source_ref"] in source_ids
    assert view["guards"] == [
        {"guard_id": "g:1", "fact_ref": "r:1", "observation_ids": ["o:1"]}
    ]
    for source in view["evidence_sources"]:
        reference = source["provenance"]["dependency_path_ref"]
        assert reference["archive_pointer"].startswith("/evidence_sources/")
        assert reference["step_count"] > 0
        assert len(reference["sha256"]) == 64


def test_nested_effect_and_other_flow_references_join_the_atomic_closure() -> None:
    facts = _facts()
    call = facts["calls"][0]
    call["fact_refs"] = []
    call["effects"][0]["evidence"] = [{"fact_ref": "r:1"}]
    call["other_flows"] = [{"fact_ref": "r:2", "observation_ids": ["o:1"]}]

    view = build_bounded_navigation_view(facts, max_tokens=4_096)

    relation_ids = {item["fact_id"] for item in view["relations"]}
    assert {"r:1", "r:2"} <= relation_ids


def test_shortest_referenced_evidence_chain_wins_before_stable_id() -> None:
    facts = _facts()
    facts["calls"][0]["call_id"] = "c:z-short"
    facts["calls"][0]["padding"] = "x" * 250
    facts["calls"][1]["call_id"] = "c:a-long"
    facts["calls"][1]["padding"] = "x" * 250
    only_short = deepcopy(facts)
    only_short["calls"] = [only_short["calls"][0]]
    only_long = deepcopy(facts)
    only_long["calls"] = [only_long["calls"][1]]
    one_call_budget = max(
        len(serialize_bounded_navigation_view(build_bounded_navigation_view(only_short, max_tokens=8_192))),
        len(serialize_bounded_navigation_view(build_bounded_navigation_view(only_long, max_tokens=8_192))),
    )

    view = build_bounded_navigation_view(facts, max_tokens=one_call_budget)

    assert [call["call_id"] for call in view["calls"]] == ["c:z-short"]


def test_round_robin_includes_multiple_observations_before_tail_facts() -> None:
    facts = _facts()
    facts["calls"].insert(
        1,
        {
            "call_id": "c:1b",
            "structural_role": "internal_call",
            "location": {"logical_path": "src.py", "start_line": 3},
            "fact_refs": ["r:1"],
            "effects": [{"observation_ids": ["o:1"]}],
            "padding": "x" * 150,
        },
    )
    full = build_bounded_navigation_view(facts, max_tokens=8_192)
    one_each_size = len(serialize_bounded_navigation_view(full)) - 250

    view = build_bounded_navigation_view(facts, max_tokens=one_each_size)

    call_ids = {item["call_id"] for item in view["calls"]}
    assert {"c:1", "c:2"} <= call_ids


def test_injected_counter_enforces_its_own_hard_limit() -> None:
    def twenty_byte_tokens(payload: bytes) -> int:
        return (len(payload) + 19) // 20

    view = build_bounded_navigation_view(
        _facts(),
        max_tokens=200,
        token_counter=twenty_byte_tokens,
        token_counter_name="fixture_20_utf8_bytes_per_token",
    )

    encoded = serialize_bounded_navigation_view(view)
    assert view["budget"]["counter"] == "fixture_20_utf8_bytes_per_token"
    measurement = measure_bounded_navigation_view(
        view,
        token_counter=twenty_byte_tokens,
        token_counter_name="fixture_20_utf8_bytes_per_token",
    )
    assert measurement["tokens"] == twenty_byte_tokens(encoded)
    assert measurement["tokens"] <= 200


def test_counter_and_counter_name_must_be_injected_together() -> None:
    with pytest.raises(ValueError, match="token_counter is required"):
        build_bounded_navigation_view(_facts(), token_counter_name="mislabelled")

    with pytest.raises(ValueError, match="token_counter_name is required"):
        build_bounded_navigation_view(_facts(), token_counter=lambda payload: len(payload))


@pytest.mark.parametrize("invalid", [-1, 1.5, True, "1"])
def test_counter_rejects_negative_and_non_integer_results(invalid: object) -> None:
    with pytest.raises(ValueError, match="non-negative integer"):
        build_bounded_navigation_view(
            _facts(),
            token_counter=lambda payload: invalid,  # type: ignore[arg-type,return-value]
            token_counter_name="invalid_fixture_counter",
        )


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda facts: facts.pop("schema_version"), "schema_version"),
        (
            lambda facts: facts.update(schema_version="mock-spec.navigation-facts/v1"),
            "schema_version",
        ),
        (lambda facts: facts["identity"].pop("dataset"), "identity.dataset"),
        (lambda facts: facts["identity"].update(language="ruby"), "identity.language"),
        (
            lambda facts: facts["entry_binding"].pop("origin"),
            "entry_binding.origin",
        ),
        (
            lambda facts: facts["entry_binding"].update(status="Unknown"),
            "entry_binding.status",
        ),
        (
            lambda facts: facts["analysis_boundaries"].pop("unknown_effects"),
            "analysis_boundaries.unknown_effects",
        ),
        (lambda facts: facts.pop("calls"), "calls"),
        (lambda facts: facts.update(relations={}), "relations"),
        (lambda facts: facts.update(projection=[]), "projection"),
    ],
)
def test_invalid_required_navigation_fields_are_rejected_with_paths(
    mutation, message: str
) -> None:
    facts = _facts()
    mutation(facts)

    with pytest.raises(ValueError, match=message):
        build_bounded_navigation_view(facts)


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (
            lambda facts: facts["observations"].append(
                deepcopy(facts["observations"][0])
            ),
            r"observations\[2\].observation_id",
        ),
        (
            lambda facts: facts["calls"].append(deepcopy(facts["calls"][0])),
            r"calls\[2\].call_id",
        ),
        (
            lambda facts: facts["relations"].append(
                {**facts["relations"][0], "modality": "different"}
            ),
            r"relations\[2\].fact_id",
        ),
        (
            lambda facts: facts["calls"][0]["effects"][0].update(
                observation_ids=["o:missing"]
            ),
            r"calls\[0\].effects\[0\].observation_ids\[0\]",
        ),
        (
            lambda facts: facts["relations"][0].update(
                observation_ids=["o:missing"]
            ),
            r"relations\[0\].observation_ids\[0\]",
        ),
    ],
)
def test_duplicate_ids_and_dangling_observations_are_rejected_before_budgeting(
    mutation, message: str
) -> None:
    facts = _facts()
    mutation(facts)

    with pytest.raises(ValueError, match=message):
        build_bounded_navigation_view(facts, max_tokens=1)


def test_identical_relation_rows_share_one_fact_and_keep_original_archive_hash() -> None:
    facts = _facts()
    facts["relations"].append(dict(reversed(list(facts["relations"][0].items()))))
    archive_hash = hashlib.sha256(serialize_bounded_navigation_view(facts) + b"\n").hexdigest()

    view = build_bounded_navigation_view(facts)

    assert len(facts["relations"]) == 3
    assert len(view["relations"]) == 2
    counts = view["omissions"]["categories"]["relations"]
    assert (counts["total"], counts["included"], counts["omitted"]) == (2, 2, 0)
    assert view["archive"]["sha256"] == archive_hash


def test_duplicate_relation_requires_identical_json_types() -> None:
    facts = _facts()
    facts["relations"][0]["extension"] = True
    facts["relations"].append({**facts["relations"][0], "extension": 1})

    with pytest.raises(ValueError, match=r"relations\[2\].fact_id"):
        build_bounded_navigation_view(facts)


def test_unresolved_entry_with_empty_fact_tables_is_valid() -> None:
    facts = _facts()
    facts["entry_binding"].update(status="unresolved", candidates=[])
    facts["observations"] = []
    facts["calls"] = []
    facts["guards"] = []
    facts["relations"] = []
    facts["endpoints"] = []
    facts["evidence_sources"] = []
    facts["analysis_boundaries"]["unknown_effects"] = [
        {"reason": "receiver target is Unknown", "scope": "dependency call"}
    ]

    view = build_bounded_navigation_view(facts)

    assert view["entry_binding"]["status"] == "unresolved"
    assert view["calls"] == []
    assert view["relations"] == []
    assert view["analysis_boundaries"]["unknown_effects"]["rows"] == [
        ["receiver target is Unknown", ["dependency call"], [0]]
    ]


def test_null_reachability_and_budget_omission_remain_valid() -> None:
    facts = _facts()
    facts["calls"][0]["effects"][0]["reaches_assertion"] = None
    full = build_bounded_navigation_view(facts, max_tokens=8_192)
    constrained_budget = len(serialize_bounded_navigation_view(full)) - 1

    view = build_bounded_navigation_view(facts, max_tokens=constrained_budget)

    assert view["omissions"]["truncated"] is True
    assert view["omissions"]["semantic"] == "budget_omission_is_not_semantic_absence"


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda facts: facts["calls"][0].update(fact_refs=["r:missing"]), "fact_ref"),
        (lambda facts: facts["relations"][0].update(from_ref="e:missing"), "endpoint"),
        (lambda facts: facts["relations"][0].update(source_ref="s:missing"), "source_ref"),
    ],
)
def test_dangling_reference_closures_are_rejected(mutation, message: str) -> None:
    facts = _facts()
    mutation(facts)

    with pytest.raises(ValueError, match=message):
        build_bounded_navigation_view(facts)


def test_default_archive_hash_matches_canonical_file_with_trailing_newline() -> None:
    facts = _facts()
    canonical_file = serialize_bounded_navigation_view(facts) + b"\n"

    view = build_bounded_navigation_view(facts)

    assert view["archive"] == {
        "sha256": hashlib.sha256(canonical_file).hexdigest(),
        "sha256_basis": "canonical_json_file_with_trailing_newline",
        "locator": "facts.json",
    }


def test_hadoop_shaped_long_tail_facts_fit_default_budget() -> None:
    facts = _facts()
    facts["observations"] = [
        {
            "observation_id": (
                f"o:{number + 1}" if number < 2 else f"criterion:{number}"
            ),
            "kind": "candidate_event",
            "origin": "candidate",
            "location": {
                "logical_path": "src/test/java/example/TestOutputStream.java",
                "start_line": 55 + number,
                "end_line": 55 + number,
            },
        }
        for number in range(34)
    ]
    facts["analysis_boundaries"] = {
        "assumptions": ["context-insensitive may analysis"],
        "unanalysed_regions": [
            {
                "reason": (
                    f"ValueError: observation line {55 + number} "
                    "does not contain one assertion invocation"
                ),
                "scope": f"criterion:{number}",
            }
            for number in range(34)
        ],
        "unknown_effects": [
            {"reason": "typed path crosses opaque placeholder", "scope": f"criterion:{number}"}
            for number in range(34)
        ],
    }
    for call in facts["calls"]:
        call["padding"] = "x" * 10_000
    for source in facts["evidence_sources"]:
        source["provenance"]["dependency_path"] = ["edge" * 10] * 6_000

    view = build_bounded_navigation_view(facts)

    encoded = serialize_bounded_navigation_view(view)
    assert len(encoded) <= 8_192
    assert view["omissions"]["truncated"] is True
    assert view["omissions"]["categories"]["calls"]["omitted"] > 0
