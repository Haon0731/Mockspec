"""Regenerate a bounded WALA return-boundary fixture without executing target code."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile


def _run(command: list[str], timeout: int, output: Path, stage: str) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
    (output / f"{stage}.stdout").write_text(result.stdout)
    (output / f"{stage}.stderr").write_text(result.stderr)
    (output / f"{stage}.command.json").write_text(json.dumps({
        "command": command, "returncode": result.returncode,
    }, indent=2) + "\n")
    result.check_returncode()
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--fixture-dir", type=Path, required=True)
    parser.add_argument("--wala-classpath-file", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--criterion", default="SOURCE_LINE:7:-1:.observeValue(")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    sources = sorted(args.fixture_dir.glob("*.java"))
    if not sources:
        raise SystemExit("fixture-dir has no Java sources")
    classpath = args.wala_classpath_file.read_text().strip()
    probe_dir = (args.repo / "experiments/backward-slicing/wala-pilot/backend/src/main/java"
                 / "org/mockspec/research")
    probe_sources = [
        probe_dir / "WalaSliceProbe.java",
        probe_dir / "ObservationEffectAnalyzer.java",
    ]
    with tempfile.TemporaryDirectory(prefix="mockspec-wala-boundary-") as temporary:
        work = Path(temporary)
        fixture_classes = work / "fixture-classes"
        probe_classes = work / "probe-classes"
        checkpoints = work / "checkpoints"
        fixture_classes.mkdir()
        probe_classes.mkdir()
        compile_fixture = [
            "javac", "-g", "-proc:none", "-d", str(fixture_classes),
            *map(str, sources),
        ]
        _run(compile_fixture, 30, args.output, "fixture-compile")
        jar = work / "fixture.jar"
        make_jar = ["jar", "cf", str(jar), "-C", str(fixture_classes), "sample"]
        _run(make_jar, 30, args.output, "fixture-jar")
        compile_probe = [
            "javac", "-g", "-proc:none", "-cp", classpath,
            "-d", str(probe_classes), *map(str, probe_sources),
        ]
        _run(compile_probe, 60, args.output, "probe-compile")
        criteria = work / "criteria.json"
        criteria.write_text(json.dumps([args.criterion]))
        analyze = [
            "java", "-Xmx1g", "-cp", f"{probe_classes}:{classpath}",
            "org.mockspec.research.WalaSliceProbe", str(jar), "-",
            "sample.Entry", "observe(II)V", "CRITERIA_FILE:" + str(criteria),
            "GENERIC_NO_HEAP", "ROOT_OUTPUT_DIR:" + str(checkpoints),
        ]
        result = _run(analyze, 90, args.output, "wala")
        manifest = json.loads(result.stdout)
        checkpoint_path = checkpoints / "root-00000.json"
        checkpoint = json.loads(checkpoint_path.read_text())
        checkpoint_hash = hashlib.sha256(checkpoint_path.read_bytes()).hexdigest()
        assert checkpoint_hash == manifest["root_manifest"][0]["checkpoint_sha256"]
        raw = {**manifest["shared"], **checkpoint["root"]}
    (args.output / "raw.json").write_text(json.dumps(raw, indent=2) + "\n")
    (args.output / "generation.json").write_text(json.dumps({
        "commands": [compile_fixture, make_jar, compile_probe, analyze],
        "criterion": args.criterion,
        "checkpoint_sha256": checkpoint_hash,
        "source_sha256": {
            str(path.relative_to(args.fixture_dir)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sources
        },
        "probe_sha256": {
            str(path.relative_to(args.repo)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in probe_sources
        },
        "summary": {
            "statements": raw["slice_statement_count"],
            "edges": raw["slice_edge_count"],
            "transitions": len(raw["interprocedural_transitions"]),
            "truncated": raw["traversal_truncated"],
        },
    }, indent=2) + "\n")


if __name__ == "__main__":
    main()
