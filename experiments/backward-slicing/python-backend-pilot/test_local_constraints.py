from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest


_spec = importlib.util.spec_from_file_location(
    "local_constraints_subject", Path(__file__).with_name("local_constraints.py")
)
assert _spec is not None and _spec.loader is not None
local = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(local)


TEST = '''import pytest
@pytest.mark.parametrize("scenario,args_input", [
    ("empty", ()),
    ("single", ("hello",)),
    ("tuple", (("ok", 200),)),
    ("multiple", ("missing", 404)),
])
def test_case(scenario, args_input):
    pass
    result = make_response(*args_input)
    assert result is not None
    assert hasattr(result, "headers")
    assert result.status_code == 200
'''

PRODUCT = '''def make_response(*args):
    if not args:
        return current_app.response_class()
    if len(args) == 1:
        args = args[0]
    return current_app.make_response(args)
'''


def analyze(
    test_source: str = TEST, product_source: str = PRODUCT, *, product_line: int = 1,
):
    return local.analyze_local_constraints(
        test_source=test_source,
        product_source=product_source,
        test_function="test_case",
        test_function_line=8,
        product_function="make_response",
        product_function_line=product_line,
        call_line=10,
    )


def test_parametrized_rows_remain_separate_and_normalize_variadic_arguments():
    result = analyze()

    assert result["schema_version"] == local.SCHEMA_VERSION
    assert result["binding"]["origin"] == "task_provided"
    assert [item["branch_id"] for item in result["branches"]] == [
        "row:0:empty", "row:1:single", "row:2:tuple", "row:3:multiple",
    ]
    assert result["branches"][2]["normalized_call"]["actual_variadic_arguments"] == [
        ["ok", 200]
    ]
    assert result["branches"][3]["normalized_call"]["actual_variadic_arguments"] == [
        "missing", 404
    ]
    assert result["branches"][0]["boundary_return"]["target_expression"] == (
        "current_app.response_class"
    )
    assert result["branches"][1]["boundary_return"]["target_expression"] == (
        "current_app.make_response"
    )
    assert result["branches"][1]["boundary_return"]["arguments"] == ["hello"]
    assert result["branches"][2]["boundary_return"]["arguments"] == [["ok", 200]]
    assert result["branches"][2]["boundary_return"]["argument_literals"] == [{
        "kind": "tuple",
        "items": [{"kind": "str", "value": "ok"}, {"kind": "int", "value": 200}],
    }]
    assert result["branches"][3]["boundary_return"]["arguments"] == [
        ["missing", 404]
    ]
    assert result["branches"][0]["conditions"][0]["polarity"] is True
    assert result["branches"][1]["conditions"] == [
        {**result["branches"][1]["conditions"][0], "polarity": False},
        {**result["branches"][1]["conditions"][1], "polarity": True},
    ]
    assert result["common_constraints"] == []
    json.dumps(result)


def test_typed_literals_distinguish_nested_list_from_tuple():
    test = TEST.replace(
        '("tuple", (("ok", 200),)),',
        '("tuple", (["ok", 200],)),',
    )

    result = analyze(test_source=test)

    boundary = result["branches"][2]["boundary_return"]
    assert boundary["arguments"] == [["ok", 200]]
    assert boundary["argument_literals"][0]["kind"] == "list"
    assert result["branches"][2]["normalized_call"][
        "actual_variadic_arguments_literal"
    ]["kind"] == "tuple"


def test_only_supported_assertions_become_return_shape_requirements():
    result = analyze()
    requirements = result["branches"][0]["boundary_return"]["requirements"]

    assert [item["kind"] for item in requirements] == [
        "return_non_null", "return_has_attribute",
    ]
    assert requirements[1]["attribute"] == "headers"
    assert result["observations"][2]["projection"] == "unsupported"
    assert "status_code" not in json.dumps(requirements)
    assert result["branches"][0]["boundary_return"]["authorization"] == "not_established"


def test_assignment_overwrite_removes_old_opaque_call():
    product = '''def make_response(*args):
    value = dependency.fetch()
    value = ("fixed",)
    return value
'''

    result = analyze(product_source=product)

    boundary = result["branches"][0]["boundary_return"]
    assert boundary["kind"] == "visible_value_return"
    assert boundary["value"] == ["fixed"]
    assert "target_expression" not in boundary


def test_early_return_excludes_later_call():
    product = '''def make_response(*args):
    if not args:
        return first()
    return later()
'''

    result = analyze(product_source=product)

    assert result["branches"][0]["boundary_return"]["target_expression"] == "first"
    assert all(
        branch["boundary_return"]["target_expression"] == "later"
        for branch in result["branches"][1:]
    )


@pytest.mark.parametrize(
    "product, blocker, product_line",
    [
        ('''def make_response(*args):
    if external(args):
        return first()
    return second()
''', "product_condition_unresolved", 1),
        ('''def make_response(*args):
    for value in args:
        return value
    return other()
''', "unsupported_product_statement", 1),
        ('''len = lambda value: 1
def make_response(*args):
    if len(args) == 1:
        return first()
    return second()
''', "product_condition_unresolved", 2),
    ],
)
def test_unsupported_or_shadowed_constructs_fail_closed_per_row(
    product: str, blocker: str, product_line: int,
):
    result = analyze(product_source=product, product_line=product_line)

    assert all(branch["status"]["realization"] == "blocked" for branch in result["branches"])
    assert all(branch["blockers"] == [blocker] for branch in result["branches"])


def test_overwritten_observed_result_does_not_transfer_later_assertion():
    test = TEST.replace(
        "    assert result is not None\n",
        "    result = object()\n    assert result is not None\n",
    )

    result = analyze(test_source=test)

    requirements = result["branches"][0]["boundary_return"]["requirements"]
    assert [item["kind"] for item in requirements] == []
    assert all(item["result_version"] == "overwritten" for item in result["observations"])


def test_two_candidate_cut_calls_are_rejected():
    test = TEST.replace(
        "    result = make_response(*args_input)\n",
        "    ignored = make_response(*args_input)\n    result = make_response(*args_input)\n",
    )

    with pytest.raises(local.LocalConstraintError, match="selected_product_call_not_unique"):
        analyze(test_source=test)


def test_same_predicate_in_different_rows_is_not_merged():
    result = analyze()

    false_not_args = [
        branch["conditions"][0]
        for branch in result["branches"][1:]
    ]
    assert len(false_not_args) == 3
    assert all(item["expression"] == "not args" and item["polarity"] is False
               for item in false_not_args)
    assert len({branch["branch_id"] for branch in result["branches"]}) == 4


def test_opaque_call_value_cannot_choose_a_condition():
    product = '''def make_response(*args):
    value = dependency.fetch()
    if not value:
        return first()
    return second()
'''

    result = analyze(product_source=product)

    assert all(branch["blockers"] == ["product_condition_unresolved"]
               for branch in result["branches"])


def test_setup_alias_of_actual_arguments_is_rejected():
    test = TEST.replace(
        "    pass\n    result = make_response(*args_input)\n",
        "    alias = args_input\n    result = make_response(*args_input)\n",
    )

    with pytest.raises(local.LocalConstraintError, match="setup_may_use_or_mutate"):
        analyze(test_source=test)


def test_shadowed_hasattr_does_not_create_attribute_requirement():
    test = TEST.replace(
        "    pass\n    result = make_response(*args_input)\n",
        "    hasattr = lambda value, name: True\n    result = make_response(*args_input)\n",
    )

    result = analyze(test_source=test)

    requirements = result["branches"][0]["boundary_return"]["requirements"]
    assert [item["kind"] for item in requirements] == ["return_non_null"]
    assert result["observations"][1]["projection"] == "unsupported"


def test_indirect_parametrize_is_rejected():
    test = TEST.replace(
        "])",
        "], indirect=True)",
        1,
    )

    with pytest.raises(local.LocalConstraintError, match="indirect_parametrize"):
        analyze(test_source=test)


@pytest.mark.parametrize(
    "intervening",
    ["decorate(result)", "result.headers = {}", "return"],
)
def test_intervening_effect_invalidates_only_later_observations(intervening: str):
    test = TEST.replace(
        "    assert result is not None\n",
        f"    assert result is not None\n    {intervening}\n",
    )

    result = analyze(test_source=test)

    requirements = result["branches"][0]["boundary_return"]["requirements"]
    assert [item["kind"] for item in requirements] == ["return_non_null"]
    assert result["observations"][1]["projection"] == "unsupported"
    assert any(item["kind"] == "intervening_post_call_effect" for item in result["unknowns"])


@pytest.mark.parametrize(
    "returned, support",
    [("None", "refuted"), ('"literal"', "unresolved")],
)
def test_visible_literal_return_does_not_fabricate_dependency_requirements(
    returned: str, support: str,
):
    result = analyze(product_source=f'''def make_response(*args):
    return {returned}
''')

    boundary = result["branches"][0]["boundary_return"]
    assert boundary["requirements"] == []
    assert boundary["test_obligations"]
    assert result["branches"][0]["status"]["support"] == support
    assert result["branches"][0]["status"]["realization"] == "blocked"


@pytest.mark.parametrize("where", ["test", "product"])
def test_generators_are_rejected_even_when_yield_is_unreachable(where: str):
    test = TEST
    product = PRODUCT
    if where == "test":
        test = test.replace("    pass\n", "    if False:\n        yield None\n", 1)
    else:
        product = product.replace(
            "def make_response(*args):\n",
            "def make_response(*args):\n    if False:\n        yield None\n",
        )

    with pytest.raises(local.LocalConstraintError, match=f"generator_{where}_function"):
        analyze(test_source=test, product_source=product)


@pytest.mark.parametrize(
    "test, message",
    [
        (TEST.replace('"scenario,args_input"', '"scenario,missing"'),
         "parametrize_name_not_explicit"),
        (TEST.replace("def test_case(scenario, args_input):",
                      "def test_case(scenario, args_input=()):"),
         "parametrize_parameter_has_default"),
        (TEST.replace('(\"single\", (\"hello\",)),', '(\"single\", (1e309,)),'),
         "parametrize_non_finite_literal"),
    ],
)
def test_parametrize_binding_and_literals_fail_closed(test: str, message: str):
    with pytest.raises(local.LocalConstraintError, match=message):
        analyze(test_source=test)


def test_test_parameter_cannot_shadow_task_provided_product_binding():
    test = TEST.replace(
        '"scenario,args_input"',
        '"make_response,args_input"',
    ).replace(
        '("empty", ()),',
        '(None, ()),',
    ).replace(
        "def test_case(scenario, args_input):",
        "def test_case(make_response, args_input):",
    )

    with pytest.raises(local.LocalConstraintError, match="shadowed_by_test_parameter"):
        analyze(test_source=test)


def test_assignment_after_call_still_shadows_product_for_entire_function_scope():
    test = TEST.replace(
        "    assert result.status_code == 200\n",
        "    assert result.status_code == 200\n    make_response = replacement\n",
    )

    with pytest.raises(local.LocalConstraintError, match="shadowed_in_test_scope"):
        analyze(test_source=test)


def test_explicit_global_product_binding_is_rejected_as_unsupported():
    test = TEST.replace(
        "    pass\n    result = make_response(*args_input)\n",
        "    global make_response\n    result = make_response(*args_input)\n",
    )

    with pytest.raises(local.LocalConstraintError, match="scope_declaration_unsupported"):
        analyze(test_source=test)
