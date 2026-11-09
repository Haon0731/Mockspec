"""Guard research counts against comments, literals, and mixed observations."""
from survey_targets import java_observations, python_observations


def test_java_ignores_comments_and_literals_preserving_locations():
    code = '''// assertEquals(1, 2);
String note = "assertThrows(() -> fail())";
/* verify(mock).call(); */
assertEquals(1, target());
assertThrows(Exception.class, () -> target());
'''
    result = java_observations(code)
    assert result["value_assertion"] == [4]
    assert result["exception_observation"] == [5]
    assert result["interaction_observation"] == []


def test_python_exception_and_value_observations_can_overlap_in_one_case():
    code = '''@pytest.mark.parametrize("value", [1, 2])
def test_target(value):
    """assert fake; pytest.raises(...)"""
    with pytest.raises(ValueError):
        target(value)
    assert state == 1
'''
    result = python_observations(code)
    assert result["value_assertion"] == [6]
    assert result["exception_observation"] == [4]
    assert result["parameterization"] == [1]


def test_no_known_assertion_is_not_fabricated_from_method_name():
    assert not any(java_observations("void testAssertEquals() { customOracle(result); }").values())
    assert not any(python_observations("def test_assert_target():\n    custom_oracle(result)").values())
