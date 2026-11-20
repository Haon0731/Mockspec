import pytest

from tagged_transport import build_tagged_prompt, parse_tagged_slots, tagged_instruction


def test_dynamic_tags_preserve_python_relative_indentation_exactly():
    slots = ["mock_stub_001", "verify_001"]
    raw = (
        "<mock_stub_001>\n"
        "    first = 1\n"
        "      second = 2\n"
        "\n"
        "</mock_stub_001>\n"
        "<verify_001>\n"
        "\n"
        "</verify_001>\n"
    )

    assert parse_tagged_slots(raw, slots) == {
        "mock_stub_001": "    first = 1\n      second = 2\n",
        "verify_001": "",
    }


def test_parser_rejects_text_outside_the_exact_envelope():
    raw = " commentary\n<SLOT>\npass\n</SLOT>\n"

    with pytest.raises(ValueError, match="invalid tagged slot envelope"):
        parse_tagged_slots(raw, ["SLOT"])


def test_empty_slot_and_crlf_framing_are_supported():
    raw = "<first>\r\n</first>\r\n<second>\r\n  pass\r\n</second>\r\n"

    assert parse_tagged_slots(raw, ["first", "second"]) == {
        "first": "",
        "second": "  pass",
    }


def test_duplicate_or_out_of_order_delimiter_is_rejected():
    raw = "<first>\n<first>\n</first>\n</first>\n<second>\n</second>\n"

    with pytest.raises(ValueError, match="duplicate, unexpected, or out-of-order"):
        parse_tagged_slots(raw, ["first", "second"])


def test_prompt_uses_ordered_dynamic_slots_and_common_payload():
    slots = ["MOCK-STUB", "VERIFY"]
    prompt = build_tagged_prompt({"case_id": "example"}, None, slots)

    assert tagged_instruction(slots) in prompt
    assert prompt.index("<MOCK-STUB>") < prompt.index("<VERIFY>")
    assert '"case_id":"example"' in prompt
    assert "first line relative to that marker" in prompt
    assert prompt.endswith("Navigation evidence:\nnull\n")


@pytest.mark.parametrize("slots", [[], ["same", "same"], ["bad tag"]])
def test_slot_ids_are_safe_and_unique(slots):
    with pytest.raises(ValueError):
        tagged_instruction(slots)
