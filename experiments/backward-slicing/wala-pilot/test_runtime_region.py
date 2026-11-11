import json
from pathlib import Path
import subprocess


HERE = Path(__file__).resolve().parent
MAIN = HERE / "backend/src/main/java/org/mockspec/research"
FIXTURE = HERE / "backend/src/testFixtures/java/sample/RuntimeRegionFixture.java"


def test_runtime_region_is_conservative_and_keeps_application_fields_precise(tmp_path):
    classpath_file = Path("/tmp/wala-cp.txt")
    assert classpath_file.is_file()
    classpath = classpath_file.read_text().strip()
    fixture_classes = tmp_path / "fixture-classes"
    probe_classes = tmp_path / "probe-classes"
    fixture_classes.mkdir()
    probe_classes.mkdir()
    subprocess.run(
        ["javac", "-g", "-d", str(fixture_classes), str(FIXTURE)],
        check=True,
    )
    fixture_jar = tmp_path / "fixture.jar"
    subprocess.run(
        ["jar", "cf", str(fixture_jar), "-C", str(fixture_classes), "sample"],
        check=True,
    )
    subprocess.run(
        [
            "javac", "-g", "-cp", classpath, "-d", str(probe_classes),
            str(MAIN / "RuntimeRegionModRef.java"),
            str(MAIN / "RuntimeRegionProbe.java"),
        ],
        check=True,
    )
    completed = subprocess.run(
        [
            "java", "-Xmx1g", "-cp", f"{probe_classes}:{classpath}",
            "org.mockspec.research.RuntimeRegionProbe", str(fixture_jar),
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=90,
    )
    result = json.loads(completed.stdout)
    assert result["runtime_key_recognized"] is True
    assert result["runtime_key_uses_weak_update"] is True
    assert result["four_runtime_forms_merged"] is True
    assert result["application_fields_distinct"] is True
    assert result["entry_context_candidates"] == 1
    assert result["all_method_contexts"] > 1

    runtime_get_sources = {
        edge["from"] for edge in result["heap_edges"] if "StreamTokenizer, nval" in edge["to"]
    }
    assert any("StreamTokenizer, nval" in source for source in runtime_get_sources)
    assert any("StreamTokenizer, ttype" in source for source in runtime_get_sources)
    assert any("arraystore" in source for source in runtime_get_sources)

    app_get_sources = {
        edge["from"] for edge in result["heap_edges"]
        if "RuntimeRegionFixture$Box, x" in edge["to"]
    }
    assert len(app_get_sources) == 1
    assert "RuntimeRegionFixture$Box, x" in next(iter(app_get_sources))
    assert all("RuntimeRegionFixture$Box, y" not in source for source in app_get_sources)

    ci_instructions = result["ci_slice_instructions"]
    assert any("StreamTokenizer, nval" in instruction and "putfield" in instruction
               for instruction in ci_instructions)
    assert any("StreamTokenizer, ttype" in instruction and "putfield" in instruction
               for instruction in ci_instructions)
    assert any("arraystore" in instruction for instruction in ci_instructions)
    assert any("RuntimeRegionFixture$Box, x" in instruction and "putfield" in instruction
               for instruction in ci_instructions)
    assert all(not ("RuntimeRegionFixture$Box, y" in instruction and "putfield" in instruction)
               for instruction in result["ci_heap_only_instructions"])
