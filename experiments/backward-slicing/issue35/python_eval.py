"""Prepare and execute the four #35 Python reference/candidate evaluations."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import time
from typing import Any

import inputs


HERE = Path(__file__).resolve().parent
IMAGE = "sha256:04a57de0d4ccb65b7f73a5880277b19c65ca23c5b677d89d3a4673706f55866b"
DATA_ROOT = Path("/home/haon/data/processed/python-benchmark-new")
SLOT = re.compile(
    r"(?m)^(?P<indent>[ \t]*)# === PY-SLOT-START:(?P<slot>[a-z0-9_]+) ===\n"
    r".*?^[ \t]*# === PY-SLOT-END:(?P=slot) ===",
    re.S,
)

CASES = {
    "ansible_119": {
        "project": "ansible",
        "test": "tests/test_119_get_package_info.py",
        "node": "test_get_package_info",
        "mut": "src/ansible/lib/ansible/modules/pip.py",
        "mut_range": [540, 561],
        "mut_function": "_get_package_info",
        "mut_loader": "slice",
        "frame_filename": "src/ansible/lib/ansible/modules/pip.py",
        "reference_normalization": {
            "old": "src_path = '/home/haon/data/processed/python-benchmark-new/ansible/src/ansible/lib/ansible/modules'",
            "new": "src_path = 'src/ansible/lib/ansible/modules'",
            "reason": "processed reference relocates the source root; frozen ordinary test uses the project-relative path",
        },
        "mutants": {
            "omit_importlib_probe": (
                "importlib_rc = module.run_command([python_bin, '-c', 'import importlib.metadata'])[0]",
                "importlib_rc = 0",
            ),
            "duplicate_importlib_probe": (
                "importlib_rc = module.run_command([python_bin, '-c', 'import importlib.metadata'])[0]",
                "importlib_rc = (module.run_command([python_bin, '-c', 'import importlib.metadata']), "
                "module.run_command([python_bin, '-c', 'import importlib.metadata']))[1][0]",
            ),
            "wrong_version_python": (
                "module.run_command([python_bin, '-c', _SPECIAL_PACKAGE_CHECKERS[discovery_mechanism][package]])",
                "module.run_command(['/usr/bin/false', '-c', _SPECIAL_PACKAGE_CHECKERS[discovery_mechanism][package]])",
            ),
        },
        "reference_contract": {
            "F_stub": [
                "run_command returns the registered importlib probe status",
                "the version probe returns the registered status and stdout for each row",
            ],
            "ordinary_assertions": [
                "return value equals expected_result",
            ],
            "interaction_assertions": [
                "applicable rows issue one importlib probe",
                "applicable rows issue a version probe using python_bin and -c",
            ],
        },
    },
    "click_402": {
        "project": "click",
        "test": "tests/test_402format_filename.py",
        "node": "test_format_filename",
        "mut": "src/click/utils.py",
        "mut_range": [407, 446],
        "mut_function": "format_filename",
        "mut_loader": "module",
        "mutants": {
            "omit_encoding_lookup": (
                'filename.decode(sys.getfilesystemencoding(), "replace")',
                'filename.decode("utf-8", "replace")',
            ),
            "wrong_basename_argument": (
                "filename = os.path.basename(filename)",
                "filename = os.path.basename(os.fspath(filename) + '.wrong')",
            ),
            "duplicate_encoding_lookup": (
                'filename.decode(sys.getfilesystemencoding(), "replace")',
                'filename.decode((sys.getfilesystemencoding(), sys.getfilesystemencoding())[1], "replace")',
            ),
        },
        "reference_contract": {
            "F_stub": ["sys.getfilesystemencoding returns mock_encoding"],
            "ordinary_assertions": [
                "formatted result equals expected_result",
            ],
            "interaction_assertions": [
                "bytes rows call sys.getfilesystemencoding",
            ],
        },
    },
    "click_423": {
        "project": "click",
        "test": "tests/test_423echo_via_pager.py",
        "node": "test_echo_via_pager",
        "mut": "src/click/termui.py",
        "mut_range": [261, 290],
        "mut_function": "echo_via_pager",
        "mut_loader": "module",
        "mutants": {
            "omit_pager": (
                'return pager(itertools.chain(text_generator, "\\n"), color)',
                "return None",
            ),
            "wrong_color": (
                'return pager(itertools.chain(text_generator, "\\n"), color)',
                'return pager(itertools.chain(text_generator, "\\n"), not color)',
            ),
            "duplicate_pager": (
                'return pager(itertools.chain(text_generator, "\\n"), color)',
                'return (pager(itertools.chain(text_generator, "\\n"), color), '
                'pager(itertools.chain(text_generator, "\\n"), color))[1]',
            ),
        },
        "reference_contract": {
            "F_stub": ["click._termui_impl.pager returns None"],
            "ordinary_assertions": [],
            "interaction_assertions": [
                "pager is called once",
                "stream elements equal expected_text",
                "forwarded color is the row color object",
            ],
        },
    },
    "langflow_189": {
        "project": "langflow",
        "test": "tests/test_189is_encrypted.py",
        "node": "test_is_encrypted",
        "mut": "src/src/backend/base/langflow/services/auth/mcp_encryption.py",
        "mut_range": [94, 118],
        "mut_function": "is_encrypted",
        "mut_loader": "slice",
        "mutants": {
            "omit_settings_lookup": (
                "settings_service = get_settings_service()",
                "settings_service = None",
            ),
            "wrong_decrypt_argument": (
                "auth_utils.decrypt_api_key(value, settings_service)",
                "auth_utils.decrypt_api_key(value + 'x', settings_service)",
            ),
            "duplicate_decrypt": (
                "decrypted = auth_utils.decrypt_api_key(value, settings_service)",
                "decrypted = (auth_utils.decrypt_api_key(value, settings_service), "
                "auth_utils.decrypt_api_key(value, settings_service))[1]",
            ),
        },
        "reference_contract": {
            "F_stub": [
                "get_settings_service returns settings_service",
                "decrypt_api_key returns decrypt_result or raises decrypt_error",
            ],
            "ordinary_assertions": [
                "result identity equals expected_result",
                "dynamic InvalidToken identity is preserved",
            ],
            "interaction_assertions": [
                "settings lookup and decrypt are each called once on applicable rows",
                "empty input calls neither dependency",
            ],
        },
    },
}

MUTANT_EFFECTS = {
    "ansible_119": {
        "omit_importlib_probe": (6, "omit the importlib availability call and force the importlib path"),
        "duplicate_importlib_probe": (6, "issue the importlib availability call twice"),
        "wrong_version_python": (6, "change the version-probe executable argument"),
    },
    "click_402": {
        "omit_encoding_lookup": (2, "omit the filesystem-encoding lookup on bytes rows"),
        "wrong_basename_argument": (1, "change the basename argument on the shorten row"),
        "duplicate_encoding_lookup": (2, "issue the filesystem-encoding lookup twice on bytes rows"),
    },
    "click_423": {
        "omit_pager": (3, "omit the pager call"),
        "wrong_color": (3, "invert the forwarded color argument"),
        "duplicate_pager": (3, "issue the pager call twice"),
    },
    "langflow_189": {
        "omit_settings_lookup": (4, "omit settings lookup on non-empty rows"),
        "wrong_decrypt_argument": (4, "change the value passed to decrypt_api_key"),
        "duplicate_decrypt": (4, "issue decrypt_api_key twice on non-empty rows"),
    },
}


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def inject(template: str, slots: dict[str, str]) -> str:
    expected = [match["slot"] for match in SLOT.finditer(template)]
    if set(slots) != set(expected) or len(slots) != len(expected):
        raise ValueError("candidate slot keys differ from the frozen template")

    def replace(match: re.Match[str]) -> str:
        lines = slots[match["slot"]].splitlines()
        return "\n".join(match["indent"] + line if line else "" for line in lines)

    candidate = SLOT.sub(replace, template)
    compile(candidate, "<issue35-candidate>", "exec")
    return candidate


def extract_reference_slots(template: str, reference: str) -> dict[str, str]:
    matches = list(SLOT.finditer(template))
    chunks = [template[: matches[0].start()]]
    chunks.extend(template[left.end() : right.start()] for left, right in zip(matches, matches[1:]))
    chunks.append(template[matches[-1].end() :])
    if not reference.startswith(chunks[0]) or not reference.endswith(chunks[-1]):
        raise ValueError("reference differs outside the first or last masked region")
    cursor = len(chunks[0])
    values: dict[str, str] = {}
    for index, match in enumerate(matches):
        following = chunks[index + 1]
        boundary = (
            len(reference) - len(following)
            if index == len(matches) - 1 else reference.find(following, cursor)
        )
        if boundary < 0:
            raise ValueError(f"reference ordinary region is missing after {match['slot']}")
        region = reference[cursor:boundary]
        indent = match["indent"]
        lines = region.splitlines()
        if any(line and not line.startswith(indent) for line in lines):
            raise ValueError(f"reference indentation differs in {match['slot']}")
        values[match["slot"]] = "\n".join(line[len(indent):] if line else "" for line in lines)
        cursor = boundary + len(following)
    if cursor != len(reference) or inject(template, values) != reference:
        raise ValueError("reference cannot be reconstructed from masked slots")
    return values


def prepare(output: Path, registry_root: Path) -> dict[str, Any]:
    output.mkdir(parents=True, exist_ok=True)
    registrations = []
    for case_key, spec in CASES.items():
        contract, sources = inputs.load_case(case_key, registry_root)
        template = sources["test"]["text"]
        reference_path = DATA_ROOT / spec["project"] / spec["test"]
        raw_reference = reference_path.read_text(encoding="utf-8")
        reference = raw_reference
        normalization = spec.get("reference_normalization")
        if normalization is not None:
            if reference.count(normalization["old"]) != 1:
                raise ValueError(f"{case_key} reference normalization site changed")
            reference = reference.replace(normalization["old"], normalization["new"])
        slots = extract_reference_slots(template, reference)
        envelope = {"slots": slots, "rationale": ["developer reference held by evaluation side"]}
        case_dir = output / case_key
        case_dir.mkdir(exist_ok=True)
        _write_json(case_dir / "reference.json", envelope)
        (case_dir / "reference.py").write_text(reference, encoding="utf-8")
        mut_source = next(
            source for source in sources.values() if source["logical_path"] == spec["mut"]
        )
        mutant_rows = []
        for name, (old, new) in spec["mutants"].items():
            mutated = _apply_mutation(mut_source["text"], spec, name)
            count = 1
            compile(mutated, f"<{case_key}:{name}>", "exec")
            denominator_rows, expected_change = MUTANT_EFFECTS[case_key][name]
            mutant_rows.append({
                "mutant": name,
                "replacement_count": count,
                "source_before_sha256": _sha256(mut_source["text"].encode()),
                "source_after_sha256": _sha256(mutated.encode()),
                "expected_change": expected_change,
                "applicable_reference_rows": denominator_rows,
            })
        registration = {
            "case_key": case_key,
            "identity": contract["identity"],
            "reference_path": str(reference_path),
            "raw_reference_sha256": _sha256(raw_reference.encode()),
            "evaluation_reference_sha256": _sha256(reference.encode()),
            "ordinary_region_audit": (
                "reference reconstructed byte-for-byte from frozen mask plus slots"
                if normalization is None else
                "one registered environment-path relocation normalized, then reconstructed byte-for-byte"
            ),
            "reference_normalization": normalization,
            "slot_ids": list(slots),
            "test_node": f"{reference_path}::{spec['node']}",
            "mut_path": str(DATA_ROOT / spec["project"] / spec["mut"]),
            "reference_contract": spec["reference_contract"],
            "mutants": mutant_rows,
        }
        _write_json(case_dir / "registration.json", registration)
        registrations.append(registration)
    manifest = {
        "schema": "mock-spec.issue35-python-evaluation-registration/v1",
        "image": IMAGE,
        "network": "none",
        "filesystem": "read-only with tmpfs /tmp and one writable output mount",
        "startup_timeout_seconds": 120,
        "pytest_timeout_seconds": 120,
        "cases": registrations,
    }
    _write_json(output / "manifest.json", manifest)
    return manifest


def _mutated_source(case_key: str, mutant: str, registry_root: Path) -> tuple[str, str]:
    spec = CASES[case_key]
    _, sources = inputs.load_case(case_key, registry_root)
    source = next(item for item in sources.values() if item["logical_path"] == spec["mut"])
    if mutant == "original":
        return source["text"], source["logical_path"]
    return _apply_mutation(source["text"], spec, mutant), source["logical_path"]


def _apply_mutation(source: str, spec: dict[str, Any], mutant: str) -> str:
    old, new = spec["mutants"][mutant]
    if old.count("\n") != new.count("\n"):
        raise ValueError(f"mutation changes line count: {mutant}")
    lines = source.splitlines(keepends=True)
    start, end = spec["mut_range"]
    prefix = "".join(lines[: start - 1])
    region = "".join(lines[start - 1 : end])
    suffix = "".join(lines[end:])
    if region.count(old) != 1:
        raise ValueError(f"mutation site changed in registered MUT range: {mutant}")
    mutated_region = region.replace(old, new)
    if len(mutated_region.splitlines()) != len(region.splitlines()):
        raise ValueError(f"mutation shifts registered MUT lines: {mutant}")
    return prefix + mutated_region + suffix


def run_case(
    case_key: str, envelope_path: Path, variant: str, mutant: str,
    output: Path, registry_root: Path,
) -> dict[str, Any]:
    spec = CASES[case_key]
    contract, sources = inputs.load_case(case_key, registry_root)
    envelope = json.loads(envelope_path.read_text(encoding="utf-8"))
    if set(envelope) != {"slots", "rationale"} or not isinstance(envelope["rationale"], list):
        raise ValueError("candidate envelope must contain slots and rationale")
    candidate = inject(sources["test"]["text"], envelope["slots"])
    mut_text, mut_logical_path = _mutated_source(case_key, mutant, registry_root)
    output.mkdir(parents=True, exist_ok=False)
    staged_test = output / "candidate.py"
    staged_mut = output / "mut.py"
    staged_test.write_text(candidate, encoding="utf-8")
    staged_mut.write_text(mut_text, encoding="utf-8")
    snapshot = registry_root / "formal-snapshots" / contract["identity"]["dataset"] / contract["identity"]["snapshot_id"]
    runtime = json.loads((snapshot / "control/runtime-lock.json").read_text(encoding="utf-8"))
    bundle = dict(runtime["environment"])["STUBTESTER_PYTHON_DEPENDENCY_BUNDLE"]
    if re.fullmatch(r"[0-9a-f]{64}", bundle) is None:
        raise ValueError("invalid frozen dependency bundle identity")
    dependencies = registry_root / "formal-snapshots/runtime-dependencies/python" / bundle / "site-packages"
    target_root = Path("/target")
    test_path = target_root / spec["test"]
    mut_path = target_root / mut_logical_path
    container = "mockspec-issue35-" + _sha256(str(output.resolve()).encode())[:16]
    command = [
        "docker", "run", "--rm", "--name", container, "--network", "none", "--read-only",
        "--tmpfs", "/tmp:rw,nosuid,nodev,size=256m", "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges", "--pids-limit", "256",
        "--memory", "1g", "--cpus", "1",
        "--user", f"{os.getuid()}:{os.getgid()}",
        "-v", f"{snapshot / 'tree/repo'}:/target:ro",
        "-v", f"{dependencies}:/deps:ro",
        "-v", f"{staged_test.resolve()}:{test_path}:ro",
        "-v", f"{staged_mut.resolve()}:{mut_path}:ro",
        "-v", f"{output.resolve()}:/outputs:rw",
        "-v", f"{(HERE / 'python_eval_observer.py').resolve()}:/observer/observer.py:ro",
        "-w", "/target",
        "-e", "PYTHONDONTWRITEBYTECODE=1",
        "-e", "PYTHONPATH=/target/src/src:/target/src:/deps:/observer",
        "-e", "PYTHONNOUSERSITE=1", "-e", "HOME=/tmp",
        "-e", f"MOCKSPEC_CASE_KEY={case_key}", "-e", f"MOCKSPEC_VARIANT={variant}",
        "-e", f"MOCKSPEC_MUTANT={mutant}",
        "-e", f"MOCKSPEC_TEST_PATH={test_path}", "-e", f"MOCKSPEC_MUT_PATH={mut_path}",
        "-e", f"MOCKSPEC_TEST_SHA256={_sha256(candidate.encode())}",
        "-e", f"MOCKSPEC_MUT_SHA256={_sha256(mut_text.encode())}",
        "-e", f"MOCKSPEC_MUT_FUNCTION={spec['mut_function']}",
        "-e", f"MOCKSPEC_MUT_LOADER={spec['mut_loader']}",
        "-e", f"MOCKSPEC_MUT_START_LINE={spec['mut_range'][0]}",
        "-e", f"MOCKSPEC_MUT_END_LINE={spec['mut_range'][1]}",
        "-e", f"MOCKSPEC_FRAME_FILENAME={spec.get('frame_filename', str(mut_path))}",
        "--entrypoint", "python3", IMAGE, "-B", "-m", "pytest", "-q",
        "-p", "no:cacheprovider",
        "-p", "observer", "--basetemp=/outputs/pytest-tmp", "--tb=no",
        f"{test_path}::{spec['node']}",
    ]
    _write_json(output / "command.json", {"command": command})
    started = time.monotonic()
    lock_path = Path("/tmp/mockspec-issue35-docker.lock")
    try:
        with lock_path.open("w", encoding="utf-8") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                completed = subprocess.run(command, capture_output=True, text=True, timeout=240)
                exit_code = completed.returncode
                stdout, stderr = completed.stdout, completed.stderr
            except subprocess.TimeoutExpired as error:
                exit_code = None
                stdout = error.stdout or ""
                stderr = error.stderr or ""
                subprocess.run(["docker", "rm", "-f", container], capture_output=True, timeout=15)
    finally:
        if staged_test.exists() or staged_mut.exists():
            subprocess.run(["docker", "rm", "-f", container], capture_output=True, timeout=15)
        staged_test.unlink(missing_ok=True)
        staged_mut.unlink(missing_ok=True)
    (output / "stdout.txt").write_text(stdout, encoding="utf-8")
    (output / "stderr.txt").write_text(stderr, encoding="utf-8")
    execution_path = output / "execution.json"
    observed = json.loads(execution_path.read_text()) if execution_path.is_file() else {}
    call_rows = [row for row in observed.get("phases", []) if row.get("when") == "call"]
    receipt = {
        "case_key": case_key,
        "variant": variant,
        "mutant": mutant,
        "exit_code": exit_code,
        "elapsed_seconds": time.monotonic() - started,
        "collected": observed.get("collected"),
        "row_identity": [row.get("nodeid") for row in call_rows],
        "passed": sum(row.get("outcome") == "passed" for row in call_rows),
        "failed": sum(row.get("outcome") == "failed" for row in call_rows),
        "identity": observed.get("identity"),
        "valid": bool(observed.get("identity", {}).get("test_sha256_matches"))
        and bool(observed.get("identity", {}).get("mut_sha256_matches"))
        and bool(observed.get("mut_frames"))
        and all(
            frame.get("filename_matches") and frame.get("variant_matches")
            for frame in observed.get("mut_frames", [])
        ),
        "mut_frames": observed.get("mut_frames", []),
        "analysis_snapshot_id": contract["identity"]["snapshot_id"],
    }
    _write_json(output / "receipt.json", receipt)
    return receipt


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registry-root", type=Path, default=inputs.DEFAULT_REGISTRY_ROOT)
    subparsers = parser.add_subparsers(dest="command", required=True)
    prepare_parser = subparsers.add_parser("prepare")
    prepare_parser.add_argument("--output", type=Path, required=True)
    run_parser = subparsers.add_parser("run")
    run_parser.add_argument("--case", choices=CASES, required=True)
    run_parser.add_argument("--envelope", type=Path, required=True)
    run_parser.add_argument("--variant", required=True)
    run_parser.add_argument("--mutant", required=True)
    run_parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    if arguments.command == "prepare":
        prepare(arguments.output, arguments.registry_root)
    else:
        allowed = {"original", *CASES[arguments.case]["mutants"]}
        if arguments.mutant not in allowed:
            raise ValueError("mutant is not registered for the case")
        run_case(
            arguments.case, arguments.envelope, arguments.variant, arguments.mutant,
            arguments.output, arguments.registry_root,
        )


if __name__ == "__main__":
    main()
