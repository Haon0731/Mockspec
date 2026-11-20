import json

import pytest

import study


@pytest.mark.parametrize("case", study.CASES)
def test_prompt_conditions_share_sources_and_only_ablate_one_key(case, tmp_path):
    prepared = tmp_path / "inputs"
    study.prepare(prepared)
    arms = {}
    common = set()
    for arm in study.ARMS:
        prompt = (prepared / case / f"{arm}.txt").read_text()
        header, navigation = prompt.split("\nNavigation evidence:\n")
        common.add(header)
        arms[arm] = json.loads(navigation)
    assert len(common) == 1
    assert (
        arms["v1"]["interpretation"].pop("call_binding_is_interaction_candidate")
        is True
    )
    assert arms["v1"] == arms["ablation"]
    assert arms["v2"] == study.read(prepared / case / "facts.json")
    for field in (
        "verification_support",
        "call_binding_is_interaction_candidate",
        "mandatory_interactions_require_separate_visible_support",
    ):
        assert field not in json.dumps(arms["v2"])


def test_tagged_inputs_cover_all_seven_frozen_cases(tmp_path):
    prepared = tmp_path / "tagged-inputs"

    study.prepare_tagged(prepared)

    manifest = study.read(prepared / "manifest.json")
    assert set(manifest) == set(study.ALL_CASES)
    for case in study.ALL_CASES:
        facts = study.read(prepared / case / "facts.json")
        assert facts["schema_version"] == "mock-spec.navigation-facts/v2"
        for arm in ("base-tagged", "v2-tagged"):
            prompt = (prepared / case / f"{arm}.txt").read_text()
            assert study.gen.sha(prompt.encode()) == manifest[case]["prompts"][arm]
        assert "call_binding_is_interaction_candidate" not in json.dumps(facts)
