import hashlib
import json
from pathlib import Path
import subprocess

import pytest


HERE = Path(__file__).resolve().parent
MAIN = HERE / "backend/src/main/java/org/mockspec/research/WalaSliceProbe.java"
EFFECT_ANALYZER = HERE / "backend/src/main/java/org/mockspec/research/ObservationEffectAnalyzer.java"
FIXTURE = HERE / "backend/src/testFixtures/java/sample/GenericProductEvidenceFixture.java"


def test_generic_slice_keeps_product_return_and_dominating_guard_without_heap_claim(tmp_path):
    classpath_file = Path("/tmp/wala-cp.txt")
    if not classpath_file.is_file():
        pytest.skip("WALA 1.7.2 test classpath is not installed")
    classpath = classpath_file.read_text().strip()
    fixture_classes = tmp_path / "fixture-classes"
    probe_classes = tmp_path / "probe-classes"
    fixture_classes.mkdir()
    probe_classes.mkdir()
    subprocess.run(
        [
            "javac", "-g", "-proc:none", "-d", str(fixture_classes),
            str(FIXTURE),
        ],
        check=True,
    )
    fixture_jar = tmp_path / "fixture.jar"
    subprocess.run(
        ["jar", "cf", str(fixture_jar), "-C", str(fixture_classes), "sample"],
        check=True,
    )
    subprocess.run(
        ["javac", "-g", "-proc:none", "-cp", classpath, "-d", str(probe_classes),
         str(MAIN), str(EFFECT_ANALYZER)],
        check=True,
    )
    query = "SOURCE_LINE:22:-1:.assertEquals("
    criteria_file = tmp_path / "criteria.json"
    criteria_file.write_text(json.dumps([query]))
    checkpoint_directory = tmp_path / "root-checkpoints"

    completed = subprocess.run(
        [
            "java", "-Xmx1g", "-cp", f"{probe_classes}:{classpath}",
            "org.mockspec.research.WalaSliceProbe", str(fixture_jar), "-",
            "sample.GenericProductEvidenceFixture", "observe(I)V",
            "CRITERIA_FILE:" + str(criteria_file),
            "GENERIC_NO_HEAP",
            "ROOT_OUTPUT_DIR:" + str(checkpoint_directory),
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=90,
    )
    multi = json.loads(completed.stdout)
    assert multi["schema_version"] == "mockspec.wala-multi-root/v2"
    assert [row["criterion_query"] for row in multi["root_manifest"]] == [query]
    checkpoint = json.loads((checkpoint_directory / "root-00000.json").read_text())
    assert checkpoint["criterion_query"] == query
    assert multi["root_manifest"][0]["checkpoint_sha256"] == hashlib.sha256(
        (checkpoint_directory / "root-00000.json").read_bytes()).hexdigest()
    result = {**multi["shared"], **checkpoint["root"]}
    assert result["configuration"]["data_dependence"] == "NO_HEAP"
    assert result["configuration"]["control_dependence"] == "FULL"

    product = [
        row for row in result["product_body_evidence"]
        if row["method"].startswith("sample.GenericProductEvidenceFixture.product(")
    ]
    assert any(row["kind"] == "value_return" and row["line"] == 14 for row in product)
    guard_lines = {row["line"] for row in product if row["kind"] == "control_guard"}
    assert 6 in guard_lines
    assert 11 not in guard_lines
    assert all(row["kind"] != "retained_state_write_witness" for row in product)
    assert "other reachable state effects remain unknown" in result["generic_mode_boundary"]
