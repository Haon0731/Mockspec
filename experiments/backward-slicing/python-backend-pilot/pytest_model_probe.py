"""Static-only probes for the bounded pytest.raises capture model."""
import pytest


class Replacement:
    value = RuntimeError("replacement")


def raises_value_error():
    raise ValueError("visible probe literal")


def raises_type_error():
    raise TypeError("visible probe literal")


def alias_and_branch(flag):
    with pytest.raises(ValueError) as captured:
        raises_value_error()
    alias = captured
    if flag:
        assert "visible" in str(alias.value)


def overwritten_capture():
    with pytest.raises(ValueError) as captured:
        raises_value_error()
    captured = Replacement()
    assert "replacement" in str(captured.value)


def two_independent_captures():
    with pytest.raises(ValueError) as first:
        raises_value_error()
    assert "first" in str(first.value)
    with pytest.raises(TypeError) as second:
        raises_type_error()
    assert "second" in str(second.value)


def mismatched_type_filter():
    with pytest.raises(TypeError) as captured:
        raises_value_error()
    assert "never captured" in str(captured.value)


class OtherLibrary:
    def raises(self, expected):
        return pytest.raises(expected)


other_library = OtherLibrary()


def unrelated_raises_attribute():
    with other_library.raises(ValueError) as captured:
        raises_value_error()
    assert "not pytest identity" in str(captured.value)


def locally_rebound_pytest():
    pytest = other_library
    with pytest.raises(ValueError) as captured:
        raises_value_error()
    assert "rebound" in str(captured.value)


def catches_its_own_exception():
    try:
        raises_value_error()
    except ValueError:
        return "caught"


def callee_internal_catch_is_not_capture():
    with pytest.raises(ValueError) as captured:
        catches_its_own_exception()
    assert "not captured" in str(captured.value)


def with_body_catches_exception():
    with pytest.raises(ValueError) as captured:
        try:
            raises_value_error()
        except ValueError:
            pass
    assert "not captured" in str(captured.value)


def returns_normally():
    return "normal"


def normal_only_call_is_not_capture():
    with pytest.raises(ValueError) as captured:
        returns_normally()
    assert "not captured" in str(captured.value)


def value_read_inside_with_is_not_post_capture():
    with pytest.raises(ValueError) as captured:
        raises_value_error()
        assert "not filled yet" in str(captured.value)


def unreachable_observation():
    with pytest.raises(ValueError) as captured:
        raises_value_error()
    return
    assert "unreachable" in str(captured.value)


def tuple_expected_type_is_unsupported():
    with pytest.raises((ValueError, TypeError)) as captured:
        raises_value_error()
    assert "tuple" in str(captured.value)


def dynamic_expected_type_is_unsupported(expected):
    with pytest.raises(expected) as captured:
        raises_value_error()
    assert "dynamic" in str(captured.value)


def match_keyword_is_unsupported():
    with pytest.raises(ValueError, match="visible") as captured:
        raises_value_error()
    assert "match" in str(captured.value)


def check_keyword_is_unsupported():
    with pytest.raises(ValueError, check=lambda exc: True) as captured:
        raises_value_error()
    assert "check" in str(captured.value)


def locally_shadowed_str_is_not_builtin():
    str = lambda value: "shadowed"
    with pytest.raises(ValueError) as captured:
        raises_value_error()
    assert "shadowed" in str(captured.value)
