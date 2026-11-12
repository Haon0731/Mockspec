"""Run the bounded WALA mechanism probes without executing analysed classes."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import time


HERE = Path(__file__).resolve().parent
BACKEND = HERE / "backend/src/main/java/org/mockspec/research/WalaSliceProbe.java"
EFFECT_ANALYZER = HERE / "backend/src/main/java/org/mockspec/research/ObservationEffectAnalyzer.java"
MECHANISM = HERE / "backend/src/testFixtures/java/sample/MechanismProbe.java"
PROBES = (
    ("visible", "visibleObservation(ILsample/MechanismProbe$Box;)I"),
    ("unknown_write", "unknownObservation(Lsample/MechanismProbe$UnknownDependency;Lsample/MechanismProbe$Box;)I"),
    ("unknown_ready", "unknownReadyObservation(Lsample/MechanismProbe$UnknownDependency;Lsample/MechanismProbe$Box;)I"),
    ("unknown_alias", "unknownAliasObservation(Lsample/MechanismProbe$UnknownDependency;Lsample/MechanismProbe$Box;)I"),
    ("unknown_completion", "unknownCompletionObservation(Lsample/MechanismProbe$UnknownDependency;Lsample/MechanismProbe$Box;)I"),
    ("unknown_exception", "unknownExceptionObservation(Lsample/MechanismProbe$UnknownDependency;Lsample/MechanismProbe$Box;)I"),
    ("unknown_fresh", "unknownFreshObservation(Lsample/MechanismProbe$UnknownDependency;Lsample/MechanismProbe$Box;)I"),
    ("unknown_receiver", "unknownReceiverObservation(Lsample/MechanismProbe$OpaqueCarrier;Lsample/MechanismProbe$Box;)I"),
    ("unknown_reachable", "unknownReachableObservation(Lsample/MechanismProbe$UnknownDependency;Lsample/MechanismProbe$Box;)I"),
    ("unknown_escaped", "unknownEscapedObservation(Lsample/MechanismProbe$UnknownDependency;Lsample/MechanismProbe$Box;Lsample/MechanismProbe$Box;)I"),
    ("unknown_static", "unknownStaticObservation(Lsample/MechanismProbe$UnknownDependency;Lsample/MechanismProbe$Box;)I"),
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _run(command: list[str], *, timeout: int, stdout: Path, stderr: Path) -> dict:
    started = time.monotonic()
    try:
        completed = subprocess.run(command, capture_output=True, timeout=timeout)
        code, out, err, timed_out = (
            completed.returncode, completed.stdout, completed.stderr, False)
    except subprocess.TimeoutExpired as failure:
        code, out, err, timed_out = None, failure.stdout or b"", failure.stderr or b"", True
    stdout.write_bytes(out)
    stderr.write_bytes(err)
    return {
        "command": command,
        "exit_code": code,
        "timed_out": timed_out,
        "seconds": time.monotonic() - started,
        "stdout": stdout.name,
        "stderr": stderr.name,
    }


def run(
    classpath_file: Path,
    output: Path,
    probes: tuple[tuple[str, str], ...] = PROBES,
) -> dict:
    if output.exists():
        raise FileExistsError(f"output must not exist: {output}")
    output.mkdir(parents=True)
    wala_classpath = classpath_file.read_text(encoding="utf-8").strip()
    dependencies = [Path(item) for item in wala_classpath.split(":") if item]
    if not dependencies or any(not item.is_file() for item in dependencies):
        raise ValueError("WALA classpath contains missing dependencies")
    input_hashes = {
        "backend_sha256": _sha256(BACKEND),
        "effect_analyzer_sha256": _sha256(EFFECT_ANALYZER),
        "mechanism_sha256": _sha256(MECHANISM),
        "runner_sha256": _sha256(Path(__file__).resolve()),
        "dependency_sha256": {str(path): _sha256(path) for path in dependencies},
    }
    pre_registration = {
        "schema_version": "mockspec.wala-capability-preregistration/v1",
        "configuration": {
            "wala": "1.7.2", "call_graph": "0-1-CFA", "data_dependence": "FULL",
            "control_dependence": "FULL", "reflection": "NONE",
            "opaque_effect_model": "bounded-effect-token-v1",
            "heap_limit_bytes": 2 * 1024**3, "analysis_timeout_seconds": 180,
            "target_executed": False,
        },
        "selected_probes": [label for label, _ in probes],
        "expectations": {
            "retain": [
                "same-call same-region writes from native unknown scalar tokens",
                "receiver direct fields, one-hop argument reachability, and Application static/escaped regions",
            ],
            "reject": [
                "a different field token or direct-argument write substituted for the selected region origin",
            ],
            "unknown": [
                "deeper heap paths, arrays and collection contents, library/runtime globals, unbounded escaped regions, null, and arbitrary exception types",
            ],
        },
        "inputs": input_hashes,
    }
    (output / "pre-registration.json").write_text(
        json.dumps(pre_registration, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    with tempfile.TemporaryDirectory(prefix="mockspec-issue23-wala-") as temporary:
        root = Path(temporary)
        backend_classes = root / "backend"
        mechanism_classes = root / "mechanism"
        backend_classes.mkdir()
        mechanism_classes.mkdir()
        receipts = {
            "compile_backend": _run(
                ["javac", "-g", "-cp", wala_classpath, "-d", str(backend_classes),
                 str(BACKEND), str(EFFECT_ANALYZER)],
                timeout=90, stdout=output / "compile-backend.stdout.txt",
                stderr=output / "compile-backend.stderr.txt"),
            "compile_mechanism": _run(
                ["javac", "-g", "-d", str(mechanism_classes), str(MECHANISM)],
                timeout=90, stdout=output / "compile-mechanism.stdout.txt",
                stderr=output / "compile-mechanism.stderr.txt"),
        }
        if any(item["exit_code"] != 0 for item in receipts.values()):
            status = "compile_failed"
            attempts: list[dict] = []
        else:
            mechanism_jar = root / "mechanism.jar"
            jar_receipt = _run(
                ["jar", "--create", "--file", str(mechanism_jar), "-C", str(mechanism_classes), "."],
                timeout=30, stdout=output / "jar.stdout.txt", stderr=output / "jar.stderr.txt")
            receipts["package_mechanism"] = jar_receipt
            attempts = []
            for label, selector in probes:
                attempt = _run(
                    ["java", "-Xmx2g", "-cp", f"{backend_classes}:{wala_classpath}",
                     "org.mockspec.research.WalaSliceProbe", str(mechanism_jar), "-",
                     "sample.MechanismProbe", selector, "RETURN", "OPAQUE_SUMMARIES"],
                    timeout=180, stdout=output / f"{label}.json",
                    stderr=output / f"{label}.stderr.txt")
                attempt["label"] = label
                attempt["selector"] = selector
                attempts.append(attempt)
            status = "analysed" if all(item["exit_code"] == 0 for item in attempts) else "analysis_failed"
    manifest = {
        "schema_version": "mockspec.wala-capability-run/v2",
        "status": status,
        "configuration": {
            "wala": "1.7.2", "call_graph": "0-1-CFA", "data_dependence": "FULL",
            "control_dependence": "FULL", "reflection": "NONE",
            "opaque_effect_model": "bounded-effect-token-v1",
            "heap_limit_bytes": 2 * 1024**3, "analysis_timeout_seconds": 180,
            "target_executed": False,
        },
        "expectations_fixed_before_run": {
            "retain": ["live reaching definition", "matched actual/formal/return",
                       "visible field write through alias", "control and exceptional structure"],
            "exclude": ["overwritten local definition"],
            "unknown": ["opaque return alias", "opaque exception", "writes deeper than one reference field",
                        "arrays, library globals, and unbounded escaped regions"],
        },
        "inputs": input_hashes,
        "receipts": receipts,
        "attempts": attempts,
        "interpretation": (
            "Mechanism probes test WALA relations only. They do not substitute for the real masked Jib input, "
            "automatic entry recovery, or a complete conservative opaque-call effect model. The configured "
            "MethodSummary model is deliberately fixture-scoped to visible int fields of the receiver and "
            "reference arguments, one-hop reachable int fields, Application static/escaped int regions, "
            "one compatible argument-or-fresh return choice, and one RuntimeException alternative."
        ),
    }
    (output / "manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--wala-classpath-file", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--probe", action="append", choices=[label for label, _ in PROBES])
    args = parser.parse_args()
    selected: tuple[tuple[str, str], ...] = PROBES
    if args.probe:
        selected = tuple(probe for probe in PROBES if probe[0] in args.probe)
    print(json.dumps(run(args.wala_classpath_file, args.output, selected), indent=2))


if __name__ == "__main__":
    main()
