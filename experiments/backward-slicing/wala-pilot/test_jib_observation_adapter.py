from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from build_jib_skeleton import build
from jib_observation_adapter import (
    JibObservationAdapterError,
    adapt_jib_observations,
    validate_adapter_observations,
)


HERE = Path(__file__).parent
RESULTS = HERE.parent / "results/jib/sources.json"
EXPECTATIONS = HERE / "jib_observation_expectations.json"
R6_ENTRY_SHA256 = "29669fbd7d53f0a1252d591690202ff2afa64bd3a4c52c3797dd0028389b3fb7"


def visible_test() -> str:
    data = json.loads(RESULTS.read_text(encoding="utf-8"))
    return next(item["text"] for item in data["sources"] if item["source_id"] == "test")


def test_visible_masked_test_generates_all_observations_and_exact_r6_entry():
    result = adapt_jib_observations(visible_test())
    expected = json.loads(EXPECTATIONS.read_text(encoding="utf-8"))["observations"]

    validate_adapter_observations(result, expected)
    assert len(result["criteria"]) == 19
    assert result["generated_entry_sha256"] == R6_ENTRY_SHA256
    assert hashlib.sha256(result["entry_source"].encode()).hexdigest() == R6_ENTRY_SHA256
    assert result["mut_call"] == {
        "line": 63,
        "callee": "JarFiles.toJibContainerBuilder",
        "receiver_local": "containerBuilder",
        "argument_locals": [
            "mockStandardExplodedProcessor", "mockJarCommand", "mockCommonCliOptions",
            "mockCommonContainerConfigCliOptions", "mockLogger",
        ],
    }
    assert result["plan_binding"]["local"] == "buildPlan"
    assert result["parse_provenance"]["expected_literals_used"] is False


def test_build_records_automatic_adapter_mapping(tmp_path, monkeypatch):
    source = tmp_path / "JarFiles.java"
    test = tmp_path / "JarFilesTest.java"
    source.write_text("package com.google.cloud.tools.jib.cli.jar; class JarFiles {}")
    test.write_text(visible_test(), encoding="utf-8")
    expected = json.loads(EXPECTATIONS.read_text(encoding="utf-8"))["observations"]
    monkeypatch.setattr(
        "build_jib_skeleton.subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(returncode=1, stdout=b"", stderr=b"x"),
    )

    receipt = build(source, "", tmp_path / "output", test_source=test, observations=expected)

    mapping = next(
        item for item in receipt["mappings"]
        if item["generated_path"].endswith("JibObservationEntry.java")
    )
    assert mapping["origin"] == "automatic_source_derived_observation_adapter"
    assert mapping["sha256"] == R6_ENTRY_SHA256
    assert receipt["observation_adapter"]["generated_entry_sha256"] == R6_ENTRY_SHA256
    assert "generator_sha256" in receipt["observation_adapter"]


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (lambda text: text.replace("JarFiles.toJibContainerBuilder", "JarFiles.wrong", 1),
         "expected one exact JarFiles MUT call"),
        (lambda text: text.replace("containerBuilder.toContainerBuildPlan()",
                                   "other.toContainerBuildPlan()", 1),
         "exact plan local declaration"),
        (lambda text: text.replace("buildPlan.getUser()", '"literal"', 1),
         "unsupported observed actual expression"),
    ],
)
def test_wrong_source_call_or_observed_actual_fails_closed(change, message):
    with pytest.raises(JibObservationAdapterError, match=message):
        adapt_jib_observations(change(visible_test()))


def test_assertion_under_branch_is_not_flattened_into_source_order():
    source = visible_test().replace(
        '    assertNull(buildPlan.getUser());',
        '    if (enabled) { assertNull(buildPlan.getUser()); }',
        1,
    )
    with pytest.raises(JibObservationAdapterError, match="branches or nested blocks"):
        adapt_jib_observations(source)


def test_braceless_conditional_assertion_is_not_a_top_level_assertion_statement():
    source = visible_test().replace(
        '    assertNull(buildPlan.getUser());',
        '    if (enabled) assertNull(buildPlan.getUser());',
        1,
    )
    with pytest.raises(JibObservationAdapterError, match="complete top-level statement"):
        adapt_jib_observations(source)


def test_builder_rebinding_between_mut_and_plan_is_rejected():
    source = visible_test().replace(
        '    ContainerBuildPlan buildPlan = containerBuilder.toContainerBuildPlan();',
        '    containerBuilder = otherBuilder;\n'
        '    ContainerBuildPlan buildPlan = containerBuilder.toContainerBuildPlan();',
        1,
    )
    with pytest.raises(JibObservationAdapterError, match="between MUT result and plan binding"):
        adapt_jib_observations(source)


def test_plan_rebinding_anywhere_in_observation_region_is_rejected():
    source = visible_test().replace(
        '    assertEquals("adoptopenjdk:8-jre", buildPlan.getBaseImage());',
        '    buildPlan = otherPlan;\n'
        '    assertEquals("adoptopenjdk:8-jre", buildPlan.getBaseImage());',
        1,
    )
    with pytest.raises(JibObservationAdapterError, match="rebinding after plan binding"):
        adapt_jib_observations(source)


def test_arbitrary_executable_statement_after_plan_is_rejected():
    source = visible_test().replace(
        '    assertNull(buildPlan.getUser());',
        '    audit(buildPlan);\n    assertNull(buildPlan.getUser());',
        1,
    )
    with pytest.raises(JibObservationAdapterError, match="executable statement"):
        adapt_jib_observations(source)


def test_changed_assertion_order_fails_post_generation_validation():
    source = visible_test()
    first = '    assertEquals("adoptopenjdk:8-jre", buildPlan.getBaseImage());'
    second = ('    assertEquals(ImmutableSet.of(new Platform("amd64", "linux")), '
              'buildPlan.getPlatforms());')
    source = source.replace(first, "__FIRST__", 1).replace(second, first, 1).replace(
        "__FIRST__", second, 1
    )
    result = adapt_jib_observations(source)
    expected = json.loads(EXPECTATIONS.read_text(encoding="utf-8"))["observations"]
    with pytest.raises(JibObservationAdapterError, match="differ from validation"):
        validate_adapter_observations(result, expected)


def test_duplicate_mut_call_is_ambiguous():
    source = visible_test().replace(
        "    ContainerBuildPlan buildPlan = containerBuilder.toContainerBuildPlan();",
        """    JibContainerBuilder duplicate =
        JarFiles.toJibContainerBuilder(
            mockStandardExplodedProcessor, mockJarCommand, mockCommonCliOptions,
            mockCommonContainerConfigCliOptions, mockLogger);
    ContainerBuildPlan buildPlan = containerBuilder.toContainerBuildPlan();""",
        1,
    )
    with pytest.raises(JibObservationAdapterError, match="found 2"):
        adapt_jib_observations(source)
