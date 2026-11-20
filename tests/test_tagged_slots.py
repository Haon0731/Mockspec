"""Production contract tests for tagged source-slot delivery."""

import pytest

from mock_spec.tagged_slots import (
    build_tagged_prompt,
    parse_tagged_candidate,
    parse_tagged_slots,
    tagged_instruction,
)


def test_python_relative_indentation_and_blank_line_are_preserved() -> None:
    raw = (
        "<mock_stub_001>\n"
        "first = 1\n"
        "  second = 2\n"
        "\n"
        "</mock_stub_001>\n"
        "<verify_001>\n"
        "\n"
        "</verify_001>\n"
    )

    assert parse_tagged_slots(raw, ["mock_stub_001", "verify_001"]) == {
        "mock_stub_001": "first = 1\n  second = 2\n",
        "verify_001": "",
    }


def test_empty_slot_and_crlf_framing_are_supported() -> None:
    raw = "<first>\r\n</first>\r\n<second>\r\n  pass\r\n</second>\r\n"

    assert parse_tagged_slots(raw, ["first", "second"]) == {
        "first": "",
        "second": "  pass",
    }


@pytest.mark.parametrize(
    "raw, message",
    [
        (" commentary\n<SLOT>\npass\n</SLOT>\n", "invalid tagged slot envelope"),
        (
            "<first>\n<first>\n</first>\n</first>\n<second>\n</second>\n",
            "duplicate, unexpected, or out-of-order",
        ),
        ("<first>\npass\n", "missing closing slot delimiter"),
    ],
)
def test_invalid_envelopes_are_rejected(raw: str, message: str) -> None:
    slots = ["SLOT"] if raw.startswith(" commentary") else ["first", "second"]
    with pytest.raises(ValueError, match=message):
        parse_tagged_slots(raw, slots)


def test_prompt_uses_dynamic_slots_and_canonical_payload_encoding() -> None:
    slots = ["MOCK-STUB", "VERIFY"]
    prompt = build_tagged_prompt({"case_id": "example"}, None, slots)

    assert tagged_instruction(slots) in prompt
    assert prompt.index("<MOCK-STUB>") < prompt.index("<VERIFY>")
    assert '"case_id":"example"' in prompt
    assert "first line relative to that marker" in prompt
    assert prompt.endswith("Navigation evidence:\nnull\n")


def test_host_candidate_envelope_has_no_model_authored_metadata() -> None:
    candidate = parse_tagged_candidate("<stub>\npass\n</stub>\n", ["stub"])

    assert candidate == {"slots": {"stub": "pass"}, "rationale": []}


@pytest.mark.parametrize("slots", [[], ["same", "same"], ["bad tag"]])
def test_slot_ids_are_safe_and_unique(slots: list[str]) -> None:
    with pytest.raises(ValueError):
        tagged_instruction(slots)


def test_single_slot_requires_a_sequence_of_ids() -> None:
    with pytest.raises(ValueError, match="sequence of slot ids"):
        tagged_instruction("stub")
