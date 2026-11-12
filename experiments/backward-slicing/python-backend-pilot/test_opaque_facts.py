from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

import pytest


HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("opaque_facts", HERE / "opaque_facts.py")
assert SPEC and SPEC.loader
facts = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = facts
SPEC.loader.exec_module(facts)


SOURCE = """def test_effect(dep, fill):
    box = object()
    dep.fill(box)
    fill(box)
    known = box
    known.fill(box)
    dep = known
    dep.fill(box)
"""


def test_only_approved_unresolved_unrebound_parameter_call_is_emitted():
    result = facts.build_parameter_boundary_facts(
        SOURCE, function_name="test_effect", function_start_line=1,
    )
    assert result == [
        facts.OpaqueCallFact(3, 5, "unresolved_parameter_boundary"),
        facts.OpaqueCallFact(4, 5, "unresolved_parameter_boundary"),
    ]


def test_injected_free_function_call_is_authenticated():
    result = facts.build_parameter_boundary_facts(
        SOURCE, function_name="test_effect", function_start_line=1,
    )
    assert facts.OpaqueCallFact(4, 5, "unresolved_parameter_boundary") in result


def test_visible_resolved_call_site_is_excluded():
    result = facts.build_parameter_boundary_facts(
        SOURCE, function_name="test_effect", function_start_line=1,
        resolved_call_sites=[(3, 5), (4, 5)],
    )
    assert result == []


def test_missing_selected_function_is_rejected():
    with pytest.raises(facts.OpaqueFactError, match="selected function matches"):
        facts.build_parameter_boundary_facts(
            SOURCE, function_name="missing", function_start_line=1,
        )


def test_conditional_or_nested_scope_rebind_does_not_erase_may_effect():
    source = """def test_effect(dep, flag):
    if flag:
        dep = object()
    def nested():
        dep = object()
    dep.fill(object())
"""
    result = facts.build_parameter_boundary_facts(
        source, function_name="test_effect", function_start_line=1,
    )
    assert result == [facts.OpaqueCallFact(6, 5, "unresolved_parameter_boundary")]


def test_same_line_definite_rebind_is_detected_by_column_order():
    source = "def test_effect(dep, other): dep = other; dep.fill(object())\n"
    result = facts.build_parameter_boundary_facts(
        source, function_name="test_effect", function_start_line=1,
    )
    assert result == []


@pytest.mark.parametrize("body", [
    "    for dep in values:\n        dep.fill(box)\n",
    "    with manager() as dep:\n        dep.fill(box)\n",
    "    try:\n        operation()\n    except Error as dep:\n        dep.fill(box)\n",
    "    values = [dep.fill(box) for dep in dependencies]\n",
])
def test_scope_binders_do_not_authenticate_shadowed_parameter_calls(body):
    source = "def test_effect(dep, box, values, dependencies):\n" + body
    result = facts.build_parameter_boundary_facts(
        source, function_name="test_effect", function_start_line=1)
    assert result == []


@pytest.mark.parametrize("source, expected", [
    ("""def test_effect(dep, box):
    try:
        dep.fill(box)
    except Error as dep:
        recover(dep)
""", facts.OpaqueCallFact(3, 9, "unresolved_parameter_boundary")),
    ("""def test_effect(dep):
    with dep() as dep:
        consume(dep)
""", facts.OpaqueCallFact(2, 10, "unresolved_parameter_boundary")),
    ("""def test_effect(dep):
    values = [item for item in dep()]
""", facts.OpaqueCallFact(2, 32, "unresolved_parameter_boundary")),
])
def test_binder_evaluation_before_alias_still_authenticates_original_parameter(
        source, expected):
    result = facts.build_parameter_boundary_facts(
        source, function_name="test_effect", function_start_line=1)
    assert expected in result


@pytest.mark.parametrize("body", [
    "    if flag:\n        dep = local\n        dep.fill(box)\n",
    "    try:\n        dep = local\n        dep.fill(box)\n    except Error:\n        pass\n",
    "    while flag:\n        dep = local\n        dep.fill(box)\n",
    "    with manager():\n        dep = local\n        dep.fill(box)\n",
])
def test_ordered_rebind_in_same_nested_block_rejects_parameter_boundary(body):
    source = "def test_effect(dep, local, box, flag):\n" + body
    assert facts.build_parameter_boundary_facts(
        source, function_name="test_effect", function_start_line=1) == []


def test_earlier_with_item_alias_shadows_later_context_expression():
    source = """def test_effect(dep, manager):
    with manager() as dep, dep.fill(object()):
        pass
"""
    result = facts.build_parameter_boundary_facts(
        source, function_name="test_effect", function_start_line=1)
    assert facts.OpaqueCallFact(2, 28, "unresolved_parameter_boundary") not in result
