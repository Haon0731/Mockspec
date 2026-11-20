import copy
import importlib.util
from pathlib import Path


PATH = Path(__file__).with_name("variants.py")
SPEC = importlib.util.spec_from_file_location("jib_navigation_variants", PATH)
variants = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(variants)


def _packages():
    full_prompt, compact_prompt = variants.load_frozen_prompts()
    _, full = variants.split_prompt(full_prompt)
    _, compact = variants.split_prompt(compact_prompt)
    restored, proof = variants.restore_details(compact, full)
    return full, compact, restored, proof


def test_baseline_is_byte_identical_to_frozen_jib_c_prompt():
    prompts, proofs = variants.build_prompts()
    assert prompts["OC"].encode() == (variants.JIB_PROMPTS / "C.txt").read_bytes()
    assert proofs["original_compact_is_frozen_c"] is True


def test_path_layout_recovers_exact_compact_and_restored_packages():
    _, compact, restored, _ = _packages()
    for package in (compact, restored):
        organized = variants.path_layout(package)
        proof = variants.layout_proof(package, organized)
        assert proof["equivalent"] is True
        assert proof["original_sha256"] == proof["recovered_sha256"]
        assert variants.recover_original_layout(organized) == package


def test_path_steps_partition_existing_fact_references():
    _, compact, _, _ = _packages()
    organized = variants.path_layout(compact)
    for path in organized["observation_paths"]:
        references = path["fact_set"]["fact_refs"]
        grouped = [row["fact_id"] for values in path["steps"].values() for row in values]
        assert sorted(grouped) == sorted(references)
        assert len(grouped) == len(set(grouped))
        assert all(fact_id in organized["fact_lookup"] for fact_id in grouped)


def test_restoration_changes_only_two_registered_fields():
    full, compact, restored, proof = _packages()
    stripped = copy.deepcopy(restored)
    restored_values = {}
    for fact in stripped["facts"]:
        details = fact.get("bindings_and_path_details", {})
        additions = {
            field: details.pop(field) for field in variants.RESTORED_FIELDS if field in details
        }
        if additions:
            restored_values[fact["fact_id"]] = additions
        if not details:
            fact.pop("bindings_and_path_details", None)
    assert stripped == compact
    assert sorted(restored_values) == proof["changed_fact_ids"]
    assert proof["restored_values_sha256"] == variants.digest(restored_values)
    full_by_id = {relation["fact_id"]: relation for relation in full["relations"]}
    for fact_id, additions in restored_values.items():
        source = full_by_id[fact_id]["bindings_and_path_details"]
        assert additions == {field: source[field] for field in additions}


def test_build_prompts_returns_complete_factorial_and_rendered_example():
    prompts, proof = variants.build_prompts()
    assert set(prompts) == {"OC", "PC", "OR", "PR"}
    assert proof["compact_layout"]["equivalent"] is True
    assert proof["restored_layout"]["equivalent"] is True
    assert proof["rendered_example"]["observation_id"]
    assert set(proof["rendered_example"]["steps"]) == {
        "guards", "calls", "returns", "exceptions", "remaining",
    }
