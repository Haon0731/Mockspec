import hashlib
import json
from pathlib import Path
import subprocess
import time

import pytest


HERE = Path(__file__).resolve().parent
PROBE = HERE / "backend/src/main/java/org/mockspec/research/WalaSliceProbe.java"
ANALYZER = HERE / "backend/src/main/java/org/mockspec/research/ObservationEffectAnalyzer.java"
FIXTURE = HERE / "backend/src/testFixtures/java/sample/ObservationEffectFixture.java"
MECHANISM = HERE / "backend/src/testFixtures/java/sample/MechanismProbe.java"


def _line(source: str, fragment: str, occurrence: int = 0) -> int:
    matches = [index for index, text in enumerate(source.splitlines(), 1) if fragment in text]
    return matches[occurrence]


def test_full_heap_effects_are_rooted_in_original_observations(tmp_path):
    classpath_file = Path("/tmp/wala-cp.txt")
    if not classpath_file.is_file():
        pytest.skip("WALA 1.7.2 test classpath is not installed")
    classpath = classpath_file.read_text().strip()
    fixture_classes = tmp_path / "fixture-classes"
    probe_classes = tmp_path / "probe-classes"
    fixture_classes.mkdir()
    probe_classes.mkdir()
    subprocess.run(
        ["javac", "-g", "-proc:none", "-d", str(fixture_classes),
         str(FIXTURE), str(MECHANISM)], check=True)
    fixture_jar = tmp_path / "fixture.jar"
    subprocess.run(
        ["jar", "cf", str(fixture_jar), "-C", str(fixture_classes), "sample"], check=True)
    subprocess.run(
        ["javac", "-g", "-proc:none", "-cp", classpath, "-d", str(probe_classes),
         str(PROBE), str(ANALYZER)], check=True)

    source = FIXTURE.read_text()
    observe_start = _line(source, "void observeAll")
    observation_lines = [
        line for line, text in enumerate(source.splitlines(), 1)
        if line > observe_start and "assertEquals(1," in text
    ]
    assert len(observation_lines) == 12
    queries = [f"SOURCE_LINE:{line}:-1:.assertEquals(" for line in observation_lines]
    criteria_file = tmp_path / "criteria.json"
    criteria_file.write_text(json.dumps(queries))
    checkpoints = tmp_path / "checkpoints"
    started = time.monotonic()
    completed = subprocess.run(
        ["java", "-Xmx1g", "-cp", f"{probe_classes}:{classpath}",
         "org.mockspec.research.WalaSliceProbe", str(fixture_jar), "-",
         "sample.ObservationEffectFixture",
         "observeAll(Lsample/ObservationEffectFixture$Dependency;ZLsample/ObservationEffectFixture$Box;)V",
         "CRITERIA_FILE:" + str(criteria_file), "OPAQUE_SUMMARIES", "GENERIC_NO_HEAP",
         "OBSERVATION_EFFECTS", "ROOT_OUTPUT_DIR:" + str(checkpoints)],
        check=True, capture_output=True, text=True, timeout=180)
    elapsed = time.monotonic() - started
    multi = json.loads(completed.stdout)
    assert multi["shared"]["configuration"]["heap_effect_analysis_demanded"] is True
    assert multi["shared"]["configuration"]["data_dependence"] == "NO_HEAP"
    assert multi["shared"]["timings_millis"]["observation_effect_full_sdg_initialization"] >= 0
    roots = []
    for index, query in enumerate(queries):
        path = checkpoints / f"root-{index:05d}.json"
        checkpoint = json.loads(path.read_text())
        assert checkpoint["criterion_query"] == query
        assert multi["root_manifest"][index]["checkpoint_sha256"] == hashlib.sha256(
            path.read_bytes()).hexdigest()
        roots.append(checkpoint["root"])

    analyses = [root["observation_effect_evidence"][0]["analysis"] for root in roots[:-1]]
    assert analyses[0]["visible_state"]

    call_fragments = [
        "dependency.fill(opaqueBox)", "dependency.fill(unrelatedFresh)",
        "dependency.fill(killedBox)", "dependency.fill(copiedBox)",
        "dependency.fill(reintroducedBox)", "dependency.fill(conditionalBox)",
        "dependency.fill(exceptionalBox)", "dependency.fill(parameterBox)",
        "dependency.fill(loopBox)", "dependency.fill(cyclicBox)",
    ]
    call_lines = [_line(source, fragment) for fragment in call_fragments]

    def rows_for(analysis, call_line, route="normal"):
        return [row for row in analysis["opaque_state"]
                if row.get("call", {}).get("line") == call_line
                and row.get("route", "normal") == route]

    assert any(row["certainty"] == "potential" for row in rows_for(analyses[1], call_lines[0]))
    fresh = rows_for(analyses[2], call_lines[1])
    assert fresh
    assert all(row["certainty"] == "excluded"
               and row.get("reason") in {
                   "points_to_regions_disjoint",
                   "distinct_ssa_allocation_sites_despite_merged_points_to_region",
               } for row in fresh)
    killed = rows_for(analyses[3], call_lines[2])
    assert any(row.get("reason") == "exact_receiver_field_constant_overwrite_on_all_normal_routes"
               for row in killed)
    assert any(row["certainty"] == "potential" for row in rows_for(analyses[4], call_lines[3]))
    assert any(row["certainty"] == "potential" for row in rows_for(analyses[5], call_lines[4]))
    assert any(row["certainty"] == "potential" for row in rows_for(analyses[6], call_lines[5]))
    exceptional = rows_for(analyses[7], call_lines[6], route="exceptional")
    assert any(row["certainty"] == "potential" for row in exceptional)
    assert analyses[7]["exceptions"]
    assert analyses[7]["exceptions"][0]["read_to_observation"]
    assert any(row["certainty"] in {"potential", "unknown"}
               for row in rows_for(analyses[8], call_lines[7]))
    assert any(row["certainty"] in {"potential", "unknown"}
               for row in rows_for(analyses[9], call_lines[8]))
    assert any(row["certainty"] in {"potential", "unknown"}
               for row in rows_for(analyses[10], call_lines[9]))
    assert all(row["incoming_edge_kind"] == "normal"
               for row in rows_for(analyses[1], call_lines[0])[0]["normal_cfg_path"][1:])

    static_root = roots[-1]
    assert static_root["observation_effect_demand"]["status"] == (
        "not_demanded_static_field_unmodeled")
    assert static_root["observation_effect_demand"]["static_read_count"] == 1

    static_request = tmp_path / "static-criteria.json"
    static_request.write_text(json.dumps([queries[-1]]))
    static_checkpoints = tmp_path / "static-checkpoints"
    static_completed = subprocess.run(
        ["java", "-Xmx1g", "-cp", f"{probe_classes}:{classpath}",
         "org.mockspec.research.WalaSliceProbe", str(fixture_jar), "-",
         "sample.ObservationEffectFixture",
         "observeAll(Lsample/ObservationEffectFixture$Dependency;ZLsample/ObservationEffectFixture$Box;)V",
         "CRITERIA_FILE:" + str(static_request), "OPAQUE_SUMMARIES", "GENERIC_NO_HEAP",
         "OBSERVATION_EFFECT_SCAN", "ROOT_OUTPUT_DIR:" + str(static_checkpoints)],
        check=True, capture_output=True, text=True, timeout=180)
    static_multi = json.loads(static_completed.stdout)
    static_configuration = static_multi["shared"]["configuration"]
    assert static_configuration["observation_effect_scan"] is True
    assert static_configuration["observation_effects"] is False
    assert static_configuration["heap_effect_analysis_demanded"] is False
    assert "observation_effect_full_sdg_initialization" not in static_multi["shared"][
        "timings_millis"]
    static_checkpoint = json.loads((static_checkpoints / "root-00000.json").read_text())
    assert static_checkpoint["root"]["observation_effect_demand"]["status"] == (
        "not_demanded_static_field_unmodeled")
    assert elapsed < 180
