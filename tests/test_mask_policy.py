"""Upstream fixed context is preserved without weakening generated-slot checks."""
import pytest

from mock_spec.validation import InputValidationError, validate_java_mask, validate_python_mask


JAVA = """class Test {
  void test() {
    // === MOCK-STUB-START ===
    // === MOCK-STUB-END ===
    target(mock(Context.class));
    // === VERIFY-START ===
    // === VERIFY-END ===
    verifyZeroInteractions(fixedDependency);
  }
}
"""
PYTHON = """def test_target(mocker):
    context = mocker.Mock()
    # === PY-SLOT-START:mock_stub_001 ===
    pass
    # === PY-SLOT-END:mock_stub_001 ===
    assert target(context) == 1
"""


def test_legacy_mask_policy_remains_strict():
    with pytest.raises(InputValidationError, match="residual"):
        validate_java_mask(JAVA)
    with pytest.raises(InputValidationError, match="residual"):
        validate_python_mask(PYTHON, ("mock_stub_001",))


def test_frozen_agent_policy_accepts_fixed_context():
    validate_java_mask(JAVA, reject_residual_behavior=False)
    validate_python_mask(PYTHON, ("mock_stub_001",), reject_residual_behavior=False)


def test_frozen_agent_policy_still_rejects_filled_slots():
    with pytest.raises(InputValidationError, match="not empty"):
        validate_java_mask(JAVA.replace("// === MOCK-STUB-END", "when(x()).thenReturn(1);\n    // === MOCK-STUB-END"),
                           reject_residual_behavior=False)
    with pytest.raises(InputValidationError, match="non-placeholder"):
        validate_python_mask(PYTHON.replace("    pass", "    result = 1"), ("mock_stub_001",),
                             reject_residual_behavior=False)


def test_frozen_agent_policy_still_rejects_wrong_contract():
    with pytest.raises(InputValidationError, match="slots differ"):
        validate_python_mask(PYTHON, ("mock_stub_002",), reject_residual_behavior=False)
    with pytest.raises(InputValidationError, match="missing or duplicate"):
        validate_java_mask(JAVA.replace("// === VERIFY-END ===", ""), reject_residual_behavior=False)
