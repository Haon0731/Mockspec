"""Safety checks for the analysis-only harness, not research quality metrics."""
import importlib.util
import json
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("wala_prepare", HERE / "prepare.py")
prepare = importlib.util.module_from_spec(spec)
spec.loader.exec_module(prepare)


@pytest.mark.parametrize("case", ["cloudfoundry", "jib"])
def test_harness_preserves_all_non_declaration_lines_and_empty_slots(case):
    sources = json.loads((HERE.parent / "results" / case / "sources.json").read_text())
    text = sources["sources"][0]["text"]
    config = prepare.CONFIG[case]
    changed = prepare.harness(text, config["method"], config["parameters"])
    before, after = text.splitlines(), changed.splitlines()
    differences = [i for i, pair in enumerate(zip(before, after)) if pair[0] != pair[1]]
    assert len(before) == len(after)
    assert len(differences) == 1
    assert "public void " in before[differences[0]]
    assert changed.replace(config["method"] + "(" + config["parameters"] + ")",
                           config["method"] + "()") == text
    assert "// === MOCK-STUB-START ===\n" in changed
    assert "when(" not in changed


def test_ambiguous_entry_rejected():
    with pytest.raises(ValueError):
        prepare.harness("void test() {} void test() {}", "test", "Object mock")


def test_locked_dependency_cannot_escape_snapshot(tmp_path):
    with pytest.raises(ValueError):
        prepare.dependencies(tmp_path, {"dependency_hashes": [["libs/../../answer.jar", "unused"]]})
