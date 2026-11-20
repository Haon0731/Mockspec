import json
from pathlib import Path

import inputs
import python_eval


REGISTRATION = Path(__file__).resolve().parent / "python_eval"


def test_registered_references_reconstruct_from_frozen_slots():
    for case_key in python_eval.CASES:
        _, sources = inputs.load_case(case_key)
        envelope = json.loads((REGISTRATION / case_key / "reference.json").read_text())
        rebuilt = python_eval.inject(sources["test"]["text"], envelope["slots"])
        assert rebuilt == (REGISTRATION / case_key / "reference.py").read_text()


def test_slot_object_order_does_not_change_injection():
    _, sources = inputs.load_case("click_423")
    envelope = json.loads((REGISTRATION / "click_423/reference.json").read_text())
    reversed_slots = dict(reversed(list(envelope["slots"].items())))
    assert python_eval.inject(sources["test"]["text"], reversed_slots) == (
        REGISTRATION / "click_423/reference.py"
    ).read_text()


def test_mutations_stay_inside_registered_function_ranges_and_keep_line_count():
    for case_key, spec in python_eval.CASES.items():
        _, sources = inputs.load_case(case_key)
        source = next(item["text"] for item in sources.values() if item["logical_path"] == spec["mut"])
        start, end = spec["mut_range"]
        lines = source.splitlines()
        for mutant in spec["mutants"]:
            changed = python_eval._apply_mutation(source, spec, mutant)
            changed_lines = changed.splitlines()
            assert len(changed_lines) == len(lines)
            assert changed_lines[: start - 1] == lines[: start - 1]
            assert changed_lines[end:] == lines[end:]
            assert changed_lines[start - 1 : end] != lines[start - 1 : end]
