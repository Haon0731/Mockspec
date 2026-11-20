import importlib.util
import json
from pathlib import Path


PATH = Path(__file__).with_name("java_evaluate.py")
SPEC = importlib.util.spec_from_file_location("issue35_java_evaluate", PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


def test_reference_fragments_match_private_gold_replay_hashes():
    config = __import__("json").loads(MODULE.PREREGISTRATION.read_text())
    for case, fragments in MODULE.REFERENCE.items():
        reference = config["cases"][case]["reference"]
        assert MODULE.digest_text(fragments["mock_stub"] + "\n") == reference["gold_mock_stub_sha256"]
        assert MODULE.digest_text(fragments["verify"] + "\n") == reference["gold_verify_sha256"]


def test_mutations_are_unique_and_change_product_source():
    config = __import__("json").loads(MODULE.PREREGISTRATION.read_text())
    old_root = Path("/home/haon/projects/Stub-Tester")
    for case, values in config["cases"].items():
        source = (old_root / ".omx/developer-realistic-registry-max/formal-snapshots"
                  / MODULE.DATASET / values["snapshot_id"] / "tree/repo"
                  / values["mut_path"]).read_text()
        for mutant in values["mutants"]:
            assert MODULE.mutate(source, case, mutant) != source


def test_candidate_contract(tmp_path):
    candidate = tmp_path / "candidate.json"
    candidate.write_text(json.dumps({
        "slots": {"MOCK-STUB": "int value = 1;", "VERIFY": ""},
        "rationale": ["bounded reason"],
    }))
    slots, identity = MODULE.load_candidate(candidate, "zipkin")
    assert slots == {"mock_stub": "int value = 1;", "verify": ""}
    assert len(identity) == 64


def test_raw_reference_ordinary_regions_match_masks():
    config = json.loads(MODULE.PREREGISTRATION.read_text())
    old_root = Path("/home/haon/projects/Stub-Tester")
    registry = old_root / ".omx/developer-realistic-registry-max"
    dataset = MODULE.FrozenDataset(
        registry, dataset=MODULE.DATASET, inventory_revision=MODULE.INVENTORY_REVISION
    )
    for values in config["cases"].values():
        raw = (old_root.parent.parent / "data" / values["reference"]["source"]).read_text()
        with dataset.load_case(values["case_id"]) as frozen:
            raw_digest, masked_digest = MODULE.ordinary_region_identity(
                raw, frozen.masked_test.text,
                values["reference"]["excluded_record_line_ranges"],
            )
        assert raw_digest == masked_digest


def test_test_failure_classification_uses_test_output_only():
    compile_with_warning = {
        "exit_code": 0, "timed_out": False,
        "stderr": "warning: javax.annotation.processing.FilerException",
    }
    assertion = {
        "exit_code": 1, "timed_out": False,
        "stdout": "1) x\njava.lang.AssertionError: expected", "stderr": "",
    }
    runtime_error = {
        "exit_code": 1, "timed_out": False,
        "stdout": "1) x\njava.lang.NullPointerException", "stderr": "",
    }
    assert MODULE.result_status(compile_with_warning, assertion) == "test_assertion_or_verification_failed"
    assert MODULE.result_status(compile_with_warning, runtime_error) == "test_exception_or_fixture_failed"
