"""Criterion extraction must stay inside the selected test scope."""
from criteria import extract_criteria


def test_python_excludes_assertions_from_other_and_nested_functions():
    text = '''def test_other():
    assert wrong()

def test_selected():
    def later_callback():
        assert also_wrong()
    with pytest.raises(ValueError):
        target()
'''
    result = extract_criteria(text, "python", "tests/test_sample.py::test_selected")
    assert result["target_scope"]["locations"] == [
        {"symbol": "test_selected", "start_line": 4, "end_line": 8}
    ]
    assert [(item["line"], item["kind"]) for item in result["criteria"]] == [
        (7, "exception_observation")
    ]


def test_python_without_oracle_emits_candidate_events_and_dynamic_clues():
    text = '''def test_selected(expected):
    fn = load_function_from_source_ast("module.py")
    fn(expected)
'''
    result = extract_criteria(text, "python", "tests/test_sample.py::test_selected")
    assert result["criteria"] == [{
        "line": 3,
        "kind": "candidate_event",
        "confidence": "candidate",
        "reason": "visible call in a selected test with no explicit observation; it is not an oracle",
        "call": "fn",
    }]
    assert result["dynamic_loading"] == [
        {"line": 2, "name": "load_function_from_source_ast", "precision": "python_ast_call_name"}
    ]
    assert result["diagnostics"][0]["code"] == "no_explicit_observation"


def test_java_limits_lexical_observations_to_selected_method():
    text = '''class TargetTest {
  void other() { assertEquals(1, wrong()); }
  @Test void selected() {
    assertThrows(IllegalStateException.class, () -> target());
  }
}
'''
    result = extract_criteria(text, "java", "example.TargetTest#selected")
    assert result["target_scope"]["status"] == "located"
    assert [(item["line"], item["kind"]) for item in result["criteria"]] == [
        (4, "exception_observation")
    ]


def test_java_without_oracle_marks_calls_as_candidates_only():
    text = '''class TargetTest {
  void selected() {
    service.refresh();
  }
}
'''
    result = extract_criteria(text, "java", "example.TargetTest#selected")
    assert result["criteria"][0]["kind"] == "candidate_event"
    assert result["criteria"][0]["confidence"] == "candidate"
    assert result["diagnostics"][0]["code"] == "no_explicit_observation"


def test_missing_selector_does_not_fall_back_to_whole_file():
    text = "def test_other():\n    assert target()\n"
    result = extract_criteria(text, "python", "tests/test_sample.py::test_missing")
    assert result["target_scope"]["status"] == "not_located"
    assert result["criteria"] == []
    assert result["diagnostics"] == [
        {"code": "target_not_found", "message": "selector resolved to 0 functions"}
    ]
