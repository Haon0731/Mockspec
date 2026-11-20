import importlib.util
from pathlib import Path
import sys


HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
PATH = HERE / "direct.py"
SPEC = importlib.util.spec_from_file_location("jib_direct_variants", PATH)
direct = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(direct)


def _inputs():
    full_prompt, compact_prompt = direct.load_frozen_prompts()
    _, full = direct.split_prompt(full_prompt)
    _, compact = direct.split_prompt(compact_prompt)
    restored, _ = direct.restore_details(compact, full)
    return compact, restored


def test_archives_are_lossless_and_prompts_are_smaller_than_old_c():
    prompts, proof = direct.build_direct_prompts()
    assert set(prompts) == {"DC", "DR"}
    assert proof["compact_archive"]["equivalent"] is True
    assert proof["restored_archive"]["equivalent"] is True
    assert all(size < proof["old_c_prompt_bytes"] for size in proof["prompt_bytes"].values())


def test_archive_round_trip_is_exact_for_both_detail_levels():
    compact, restored = _inputs()
    for package, has_restored in ((compact, False), (restored, True)):
        organized = direct.direct_package(package, restored=has_restored)
        assert direct.decompress_archive(organized["complete_evidence_archive"]) == package


def test_readable_cards_use_real_assertion_lines_and_actual_edges():
    _, restored = _inputs()
    organized = direct.direct_package(restored, restored=True)
    base_image = next(
        card for card in organized["observation_cards"]
        if "getBaseImage" in card["assertion_source"]
    )
    pairs = {(edge["from"], edge["to"]) for edge in base_image["independent_relations"]}
    assert ("CommonContainerConfigCliOptions.getFrom payload", "buildPlan.getBaseImage()") in pairs
    assert ("ArtifactProcessor.getJavaVersion branch", "buildPlan.getBaseImage()") in pairs
    assert all("no composed chain" in edge["interpretation"]
               for edge in base_image["independent_relations"])


def test_dr_exposes_only_registered_predicate_and_setter_fields():
    prompts, proof = direct.build_direct_prompts()
    _, compact = direct.split_prompt(prompts["DC"])
    _, restored = direct.split_prompt(prompts["DR"])
    compact_edges = [edge for card in compact["observation_cards"]
                     for edge in card["independent_relations"]]
    restored_edges = [edge for card in restored["observation_cards"]
                      for edge in card["independent_relations"]]
    assert all(not edge["restored_guard_or_setter_evidence"] for edge in compact_edges)
    assert all(set(edge["restored_guard_or_setter_evidence"]) <= {
        "predicate_producers", "setter_chain",
    } for edge in restored_edges)
    assert proof["restoration"]["restored_fields"] == ["predicate_producers", "setter_chain"]


def test_dc_and_dr_select_identical_direct_fact_ids():
    prompts, _ = direct.build_direct_prompts()
    packages = {arm: direct.split_prompt(prompt)[1] for arm, prompt in prompts.items()}
    for dc, dr in zip(packages["DC"]["observation_cards"], packages["DR"]["observation_cards"]):
        assert dc["observation_id"] == dr["observation_id"]
        assert [edge["fact_id"] for edge in dc["independent_relations"]] == [
            edge["fact_id"] for edge in dr["independent_relations"]
        ]
