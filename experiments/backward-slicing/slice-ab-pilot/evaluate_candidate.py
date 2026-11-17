"""Evaluate one restored test using frozen injectors/runners in an isolated container."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import runpy
import subprocess
import time
import textwrap


_PYTHON_FORBIDDEN_CALLS = {
    "pytest.skip", "pytest.xfail", "pytest.importorskip", "unittest.skip", "self.skipTest",
    "assume", "pytest.assume", "sys.exit", "os._exit", "os.abort", "exit", "quit",
}


def _call_name(node) -> str | None:
    import ast
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = _call_name(node.value)
        return f"{parent}.{node.attr}" if parent else node.attr
    return None


def _validate_python_fragment(body: str, slot: str) -> None:
    import ast
    wrapped = "def __slot__():\n" + textwrap.indent(body or "pass", "    ")
    tree = ast.parse(wrapped)
    root = tree.body[0]

    class Guard(ast.NodeVisitor):
        def __init__(self) -> None:
            self.function_depth = 0

        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # noqa: N802
            self.function_depth += 1
            if self.function_depth == 1:
                for statement in node.body:
                    self.visit(statement)
            else:
                # Normal returns in local helper functions are allowed; their contents remain checked.
                for child in node.body:
                    self.visit(child)
            self.function_depth -= 1

        def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:  # noqa: N802
            self.visit_FunctionDef(node)  # type: ignore[arg-type]

        def visit_Return(self, node: ast.Return) -> None:  # noqa: N802
            if self.function_depth == 1:
                raise ValueError(f"{slot}: slot-level return is forbidden")
            self.generic_visit(node)

        def visit_Yield(self, node: ast.Yield) -> None:  # noqa: N802
            if self.function_depth == 1:
                raise ValueError(f"{slot}: slot-level yield is forbidden")
            self.generic_visit(node)

        def visit_YieldFrom(self, node: ast.YieldFrom) -> None:  # noqa: N802
            if self.function_depth == 1:
                raise ValueError(f"{slot}: slot-level yield is forbidden")
            self.generic_visit(node)

        def visit_Call(self, node: ast.Call) -> None:  # noqa: N802
            name = _call_name(node.func)
            if name in _PYTHON_FORBIDDEN_CALLS or (name and name.endswith(".assume")):
                raise ValueError(f"{slot}: skip, assume, or process exit is forbidden")
            self.generic_visit(node)

        def visit_Raise(self, node: ast.Raise) -> None:  # noqa: N802
            name = node.exc and _call_name(
                node.exc.func if isinstance(node.exc, ast.Call) else node.exc
            )
            if name and (name.endswith("SystemExit") or name.endswith("SkipTest")):
                raise ValueError(f"{slot}: process exit is forbidden")
            self.generic_visit(node)

    Guard().visit(root)


def _validate_java_fragment(body: str, slot: str) -> None:
    code = re.sub(
        r'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|//[^\n]*|/\*.*?\*/',
        lambda match: " " * len(match.group(0)), body, flags=re.S,
    )
    forbidden = [
        r"\bSystem\.exit\s*\(", r"\bRuntime\.getRuntime\(\)\.(?:exit|halt)\s*\(",
        r"\b(?:Assume\.)?assume(?:True|False|That|NoException)?\s*\(",
    ]
    if any(re.search(pattern, code) for pattern in forbidden):
        raise ValueError(f"{slot}: early return, assume, or process exit is forbidden")
    stack: list[bool] = []
    return_positions = {match.start() for match in re.finditer(r"\breturn\b", code)}
    for position, char in enumerate(code):
        if char == "{":
            prefix = code[:position].rstrip()
            lambda_body = prefix.endswith("->")
            method_match = re.search(
                r"\b([A-Za-z_$][\w$]*)\s*\([^{};]*\)\s*$", prefix,
            )
            method_body = bool(
                method_match
                and method_match.group(1) not in {
                    "if", "for", "while", "switch", "catch", "synchronized",
                }
                and re.search(r"\b(?:public|private|protected|static|final|void|[A-Z]\w*)\b",
                              prefix[max(0, method_match.start() - 80):method_match.start()])
            )
            stack.append(lambda_body or method_body or any(stack))
        elif char == "}" and stack:
            stack.pop()
        elif position in return_positions and not any(stack):
            raise ValueError(f"{slot}: slot-level return is forbidden")


def extract_regions(masked: str, candidate: str, language: str) -> tuple[str, str]:
    """Require exact fixed context, then extract only the original replacement slots."""
    if language == "python":
        pattern = re.compile(
            r"(?m)^(?P<start>[ \t]*# === PY-SLOT-START:(?P<slot>\w+) ===\n)"
            r"(?P<body>.*?)^(?P<end>[ \t]*# === PY-SLOT-END:(?P=slot) ===)$", re.S)
    else:
        pattern = re.compile(
            r"(?m)^(?P<start>[ \t]*// === (?P<slot>MOCK-STUB|VERIFY)-START ===\n)"
            r"(?P<body>.*?)^(?P<end>[ \t]*// === (?P=slot)-END ===)$", re.S)
    original = list(pattern.finditer(masked))
    restored = list(pattern.finditer(candidate))
    if not original or [m['slot'] for m in original] != [m['slot'] for m in restored]:
        raise ValueError("candidate changed replacement region identities")
    def fixed(match: re.Match) -> str:
        return match['start'] + "<REPLACEMENT>\n" + match['end']
    if pattern.sub(fixed, masked) != pattern.sub(fixed, candidate):
        raise ValueError("candidate changed fixed test context outside replacement regions")
    if language == "python":
        compile(candidate, "candidate.py", "exec")
        for match in restored:
            _validate_python_fragment(match["body"], match["slot"])
        parts = ["\n".join(m.group(0) for m in restored if m['slot'].startswith(prefix))
                 for prefix in ("mock_stub_", "verify_")]
        return parts[0], parts[1]
    for match in restored:
        _validate_java_fragment(match["body"], match["slot"])
    return restored[0]['body'], restored[1]['body']


def _failure_category(receipt: dict) -> str:
    stage = receipt.get("stage")
    if stage in {"apply_regions", "compile", "javac"}:
        return "compile"
    if stage in {"import", "collection"}:
        return "import"
    if stage in {"pytest", "test", "junit"}:
        return "test"
    return "unknown"


def _expected_injected_candidate(
    runtime: Path, masked: str, stub: str, verify: str, language: str,
) -> str:
    frozen = runpy.run_path(str(runtime / "apply_regions.py"))
    if language == "python":
        fragments = {}
        fragments.update(frozen["parse_slots"](stub, frozen["MOCK_EXPECTED"]))
        fragments.update(frozen["parse_slots"](verify, frozen["VERIFY_EXPECTED"]))
        expected = masked
        for slot_id in [*frozen["MOCK_EXPECTED"], *frozen["VERIFY_EXPECTED"]]:
            expected = frozen["replace_region"](expected, slot_id, fragments[slot_id])
        return expected
    expected = frozen["_replace_region"](
        masked, frozen["MOCK_STUB_START"], frozen["MOCK_STUB_END"], stub,
    )
    return frozen["_replace_region"](
        expected, frozen["VERIFY_START"], frozen["VERIFY_END"], verify,
    )


def _injection_evidence(
    runtime: Path, language: str, expected_candidate: str,
) -> tuple[bool, str | None]:
    preview = runtime / "control_outputs/candidate_preview" / (
        "candidate_test.py" if language == "python" else "candidate_test.java"
    )
    if not preview.is_file() or not preview.read_text(encoding="utf-8").strip():
        return False, "injected candidate preview is missing"
    if preview.read_text(encoding="utf-8") != expected_candidate:
        return False, "injected candidate differs from frozen region application"
    if language == "python":
        receipt = runtime / "control_outputs/candidate_preview/result.json"
        if not receipt.is_file():
            return False, "apply_regions receipt is missing"
        payload = json.loads(receipt.read_text(encoding="utf-8"))
        if payload.get("ok") is not True or payload.get("candidate_test") != str(
            Path("control_outputs/candidate_preview/candidate_test.py")
        ):
            return False, "apply_regions receipt does not authenticate preview"
        return True, None
    index = runtime / "control_outputs/best_so_far/index.jsonl"
    if not index.is_file():
        return False, "Java apply_regions receipt is missing"
    records = [json.loads(line) for line in index.read_text(encoding="utf-8").splitlines() if line]
    applied = [item for item in records if item.get("source") == "apply_regions"]
    if len(applied) != 1 or applied[0].get("apply_success") is not True:
        return False, "Java apply_regions receipt is invalid"
    return True, None


def evaluate(snapshot: Path, registry: Path, arm: Path, manifest: dict, language: str) -> dict:
    candidate = arm / "workspace" / manifest["candidate_path"]
    output = arm / "evaluation"
    output.mkdir(exist_ok=False)
    result = {"case_id": manifest["case_id"], "status": "generation_missing",
              "dependency_rehash": False, "mutations": False,
              "semantic_correctness": "not_evaluated",
              "defect_detection": "not_evaluated",
              "record_check_scope": "fixed_context_and_frozen_injection_receipts_only"}
    def save() -> dict:
        (output / "result.json").write_text(json.dumps(result, indent=2) + "\n")
        return result
    if not candidate.is_file():
        return save()
    text = candidate.read_text()
    result["candidate_sha256"] = hashlib.sha256(candidate.read_bytes()).hexdigest()
    masked = (snapshot / "tree/repo" / manifest["target_path"]).read_text()
    if hashlib.sha256(masked.encode()).hexdigest() != manifest["source_hashes"][manifest["target_path"]]:
        raise ValueError("frozen mask differs from paired input")
    try:
        stub, verify = extract_regions(masked, text, language)
    except (ValueError, SyntaxError) as exc:
        result.update(status="invalid_candidate", diagnostic=str(exc))
        return save()
    result["fixed_context_unchanged"] = True
    result["region_sha256"] = {
        "mock_stub": hashlib.sha256(stub.encode()).hexdigest(),
        "verify": hashlib.sha256(verify.encode()).hexdigest(),
    }
    runtime = output / "runtime"
    runtime.mkdir()
    tree = json.loads((snapshot / "control/tree-manifest.json").read_text())
    # Copy only public case files; frozen binary dependencies remain a read-only mount.
    for entry in tree["entries"]:
        path = entry["relpath"]
        if entry["kind"] != "file" or path.startswith("repo/libs/"):
            continue
        source = snapshot / "tree" / path
        content = source.read_bytes()
        if hashlib.sha256(content).hexdigest() != entry["sha256"]:
            raise ValueError("frozen runtime source identity changed: " + path)
        target = runtime / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    (runtime / "mock_stub.txt").write_text(stub)
    (runtime / "verify.txt").write_text(verify)
    lock = json.loads((snapshot / "control/runtime-lock.json").read_text())
    result["runtime_lock_digest"] = lock["digest"]
    image = ("stubtester-feedback-python-agent:3.10" if language == "python"
             else "stubtester-feedback-java-agent:11")
    image_id = subprocess.run(["docker", "image", "inspect", "--format", "{{.Id}}", image],
                              capture_output=True, text=True, check=True).stdout.strip()
    result["image_id"] = image_id
    name = "mockspec-slice-ab-" + hashlib.sha256(str(arm).encode()).hexdigest()[:12]
    command = ["docker", "run", "--rm", "--name", name, "--network=none", "--read-only",
               "--cap-drop=ALL", "--security-opt=no-new-privileges", "--pids-limit=256",
               "--memory=2g", "--cpus=2", "--tmpfs", "/tmp:rw,nosuid,nodev,size=128m",
               "--user", f"{os.getuid()}:{os.getgid()}", "-v", f"{runtime.resolve()}:/workspace:rw",
               "-w", "/workspace", "-e", "HOME=/tmp", "-e", "PYTHONDONTWRITEBYTECODE=1",
               "-e", "STUBTESTER_CANDIDATE_RUNTIME=1"]
    if language == "python":
        bundle = dict(lock["environment"])["STUBTESTER_PYTHON_DEPENDENCY_BUNDLE"]
        if not re.fullmatch(r"[0-9a-f]{64}", bundle):
            raise ValueError("invalid dependency bundle identity")
        deps = registry / "formal-snapshots/runtime-dependencies/python" / bundle / "site-packages"
        command += ["-v", f"{deps}:/deps:ro", "-e", "PYTHONPATH=/deps",
                    "-e", "STUBTESTER_PYTHON_EXECUTABLE=python3.10"]
    else:
        command += ["-v", f"{snapshot}/tree/repo/libs:/workspace/repo/libs:ro",
                    "-e", "JAVA_HOME=/usr/lib/jvm/java-11-openjdk-amd64"]
    command += ["--entrypoint", "python3", image_id, "-B"]
    if language == "java":
        command += ["-c", "import subprocess,sys; "
                    "p=subprocess.run([sys.executable,'-B','apply_regions.py']); "
                    "sys.exit(p.returncode or subprocess.run([sys.executable,'-B','run_target_test.py']).returncode)"]
    else:
        command += ["run_target_test.py"]
    (output / "command.json").write_text(json.dumps(command, indent=2) + "\n")
    started = time.monotonic()
    process = None
    try:
        process = subprocess.run(command, capture_output=True, text=True, timeout=120)
        (output / "stdout.txt").write_text(process.stdout)
        (output / "stderr.txt").write_text(process.stderr)
        result["exit_code"] = process.returncode
    except subprocess.TimeoutExpired:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True, timeout=15)
        result.update(status="infrastructure_failure", diagnostic="evaluation timeout")
    result["wall_seconds"] = time.monotonic() - started
    result["frozen_mask_unchanged"] = (
        (snapshot / "tree/repo" / manifest["target_path"]).read_text() == masked
    )
    receipt = runtime / "control_outputs/feedback_compile/result.json"
    if process is None:
        return save()
    if not receipt.is_file():
        result.update(status="infrastructure_failure", diagnostic="runner receipt is missing")
        return save()
    try:
        runner_receipt = json.loads(receipt.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        result.update(status="infrastructure_failure", diagnostic=f"invalid runner receipt: {exc}")
        return save()
    result["runner_receipt"] = runner_receipt
    if runner_receipt.get("returncode") != process.returncode:
        result.update(status="infrastructure_failure",
                      diagnostic="runner receipt return code differs from container")
        return save()
    receipt_target = runner_receipt.get("target", runner_receipt.get("target_test"))
    if isinstance(receipt_target, str) and receipt_target.removeprefix("repo/") != manifest[
        "target_path"
    ]:
        result.update(status="infrastructure_failure",
                      diagnostic="runner receipt target differs from frozen case")
        return save()
    try:
        expected_candidate = _expected_injected_candidate(
            runtime, masked, stub, verify, language,
        )
    except Exception as exc:
        result.update(status="infrastructure_failure",
                      diagnostic=f"frozen injector could not be replayed: {exc}")
        return save()
    injected, injection_error = _injection_evidence(
        runtime, language, expected_candidate,
    )
    if process.returncode == 0:
        if not injected:
            result.update(status="infrastructure_failure", diagnostic=injection_error)
            return save()
        preview = runtime / "control_outputs/candidate_preview" / (
            "candidate_test.py" if language == "python" else "candidate_test.java"
        )
        result.update(
            status="original_test_pass", original_test_pass=True,
            candidate_regions_injected=True,
            injected_candidate_sha256=hashlib.sha256(preview.read_bytes()).hexdigest(),
        )
        return save()
    failure_category = _failure_category(runner_receipt)
    if runner_receipt.get("stage") != "apply_regions" and not injected:
        result.update(status="infrastructure_failure", diagnostic=injection_error)
        return save()
    result.update(status="original_test_failed", original_test_pass=False,
                  failure_stage=failure_category)
    return save()
