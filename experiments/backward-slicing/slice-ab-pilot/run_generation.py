"""Run one admitted slice A/B generation arm through Stub-Tester's broker."""
from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import sys
from typing import Any, Callable


ROOT = Path(__file__).resolve().parents[3]
DEFAULT_DOTENV = Path("/home/haon/projects/Stub-Tester/.env")
DEFAULT_STUB_TESTER_ROOT = Path("/home/haon/projects/Stub-Tester")
MODEL = "deepseek-v4-pro"
RUNNER_MODEL = "openai/deepseek-v4-pro"
TIMEOUT_SECONDS = 600


class GenerationError(ValueError):
    """Raised before generation when an admission or identity check fails."""


AgentRunner = Callable[..., None]


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _read_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise GenerationError(f"cannot read {label}") from exc
    if not isinstance(value, dict):
        raise GenerationError(f"{label} must be an object")
    return value


def _safe_relative_path(value: object, label: str) -> Path:
    if not isinstance(value, str) or not value:
        raise GenerationError(f"{label} must be a non-empty relative path")
    pure = PurePosixPath(value)
    if pure.is_absolute() or ".." in pure.parts or not pure.parts:
        raise GenerationError(f"unsafe {label}")
    return Path(*pure.parts)


def _resolve_repo_path(relative: object, label: str) -> Path:
    path = _safe_relative_path(relative, label)
    resolved = (ROOT / path).resolve()
    try:
        resolved.relative_to(ROOT.resolve())
    except ValueError as exc:
        raise GenerationError(f"{label} escapes the repository") from exc
    return resolved


def _validate_workspace_sources(workspace: Path, manifest: dict[str, Any]) -> None:
    source_hashes = manifest.get("source_hashes")
    if not isinstance(source_hashes, dict) or not source_hashes:
        raise GenerationError("paired manifest has no source hashes")
    repo = workspace / "repo"
    if repo.is_symlink() or not repo.is_dir():
        raise GenerationError("prepared workspace repository is missing")

    expected: dict[str, str] = {}
    for raw_path, raw_digest in source_hashes.items():
        relative = _safe_relative_path(raw_path, "source path")
        if not isinstance(raw_digest, str) or not re.fullmatch(r"[0-9a-f]{64}", raw_digest):
            raise GenerationError("paired manifest has an invalid source hash")
        logical = relative.as_posix()
        if logical in expected:
            raise GenerationError("paired manifest repeats a source path")
        source = repo / relative
        if source.is_symlink() or not source.is_file():
            raise GenerationError(f"prepared source is missing: {logical}")
        if _digest(source.read_bytes()) != raw_digest:
            raise GenerationError(f"prepared source changed: {logical}")
        expected[logical] = raw_digest

    actual = {
        path.relative_to(repo).as_posix()
        for path in repo.rglob("*")
        if path.is_file() or path.is_symlink()
    }
    if actual != set(expected):
        raise GenerationError("prepared workspace source set differs from paired manifest")

    target = manifest.get("target_path")
    if not isinstance(target, str) or target not in expected:
        raise GenerationError("paired target is not bound to a workspace source hash")


def _validate_admission(gate_path: Path, pair_root: Path, arm: str) -> tuple[Path, Path, str]:
    gate = _read_json(gate_path, "gate")
    manifest_path = pair_root / "manifest.json"
    manifest = _read_json(manifest_path, "paired manifest")
    if gate.get("approved") is not True:
        raise GenerationError("local reliability gate is not approved")
    if _digest(gate_path.read_bytes()) != manifest.get("gate_sha256"):
        raise GenerationError("gate differs from the gate bound to the paired manifest")

    case_id = manifest.get("case_id")
    cases = gate.get("cases")
    rows = (
        [row for row in cases if isinstance(row, dict) and row.get("case_id") == case_id]
        if isinstance(cases, list)
        else []
    )
    if len(rows) != 1 or rows[0].get("approved") is not True:
        raise GenerationError("paired case is not independently approved")
    row = rows[0]
    if row.get("snapshot_id") != manifest.get("snapshot_id"):
        raise GenerationError("paired snapshot differs from the approved case")

    slice_sha256 = row.get("slice_sha256")
    if not isinstance(slice_sha256, str) or slice_sha256 != manifest.get("slice_sha256"):
        raise GenerationError("paired slice hash differs from the approved case")
    slice_path = _resolve_repo_path(row.get("slice_path"), "approved slice path")
    if slice_path.is_symlink() or not slice_path.is_file():
        raise GenerationError("approved slice is missing")
    if _digest(slice_path.read_bytes()) != slice_sha256:
        raise GenerationError("approved slice changed")

    gate_sources = row.get("sources")
    source_hashes = manifest.get("source_hashes")
    if not isinstance(gate_sources, list) or not isinstance(source_hashes, dict):
        raise GenerationError("approved source identities are missing")
    for source in gate_sources:
        if not isinstance(source, dict):
            raise GenerationError("approved source identity is invalid")
        if source_hashes.get(source.get("logical_path")) != source.get("sha256"):
            raise GenerationError("approved source differs from the paired manifest")

    if arm not in {"A", "B"} or arm not in manifest.get("order", []):
        raise GenerationError("arm is not part of the paired design")
    arm_root = pair_root / arm
    workspace = arm_root / "workspace"
    task_path = arm_root / "task.txt"
    if task_path.is_symlink() or not task_path.is_file():
        raise GenerationError("prepared task is missing")
    _validate_workspace_sources(workspace, manifest)
    workspace_slice = workspace / "slice.json"
    if arm == "B":
        if workspace_slice.is_symlink() or not workspace_slice.is_file():
            raise GenerationError("slice arm is missing its accepted slice")
        if _digest(workspace_slice.read_bytes()) != slice_sha256:
            raise GenerationError("workspace slice differs from the paired manifest")
    elif workspace_slice.exists() or workspace_slice.is_symlink():
        raise GenerationError("source-only arm unexpectedly contains slice evidence")

    if manifest.get("model") != MODEL or manifest.get("timeout_seconds") != TIMEOUT_SECONDS:
        raise GenerationError("paired model or timeout differs from the fixed design")
    if manifest.get("attempts_per_arm") != 1 or manifest.get("mutation_feedback") is not False:
        raise GenerationError("paired attempt policy differs from the fixed design")
    candidate_name = _safe_relative_path(manifest.get("candidate_path"), "candidate path")
    if len(candidate_name.parts) != 1:
        raise GenerationError("candidate path must be a workspace-root filename")
    return workspace, task_path, candidate_name.as_posix()


def _read_deepseek_key(dotenv_path: Path) -> str:
    try:
        lines = dotenv_path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError) as exc:
        raise GenerationError("authorized credential file is unavailable") from exc
    matches: list[str] = []
    for line in lines:
        match = re.fullmatch(r"\s*(?:export\s+)?DEEPSEEK_API_KEY\s*=\s*(.*?)\s*", line)
        if not match:
            continue
        value = match.group(1)
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        matches.append(value)
    if len(matches) != 1 or not matches[0] or any(character.isspace() for character in matches[0]):
        raise GenerationError("authorized credential is unavailable or ambiguous")
    return matches[0]


def _load_stub_runner(stub_tester_root: Path) -> AgentRunner:
    root = stub_tester_root.resolve()
    if not (root / "scripts/test_support_metadata/candidate_runner.py").is_file():
        raise GenerationError("Stub-Tester runner is unavailable")
    sys.dont_write_bytecode = True
    root_text = str(root)
    if root_text not in sys.path:
        sys.path.insert(0, root_text)
    module = importlib.import_module("scripts.test_support_metadata.candidate_runner")
    return module.run_feedback_repo_cli_agent_case


def _base_environment() -> dict[str, str]:
    allowed = {"PATH", "LANG", "LANGUAGE", "TZ", "DOCKER_HOST", "SSL_CERT_FILE"}
    environment = {key: value for key, value in os.environ.items() if key in allowed or key.startswith("LC_")}
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    return environment


def run_generation(
    gate_path: Path,
    pair_root: Path,
    arm: str,
    output: Path,
    *,
    dotenv_path: Path = DEFAULT_DOTENV,
    stub_tester_root: Path = DEFAULT_STUB_TESTER_ROOT,
    agent_command: str | None = None,
    agent_runner: AgentRunner | None = None,
) -> dict[str, str]:
    gate_path = gate_path.resolve()
    pair_root = pair_root.resolve()
    output = output.resolve()
    workspace, task_path, candidate_name = _validate_admission(gate_path, pair_root, arm)
    candidate_path = workspace / candidate_name
    if candidate_path.exists() or candidate_path.is_symlink():
        raise GenerationError("refusing to overwrite an existing candidate")
    if output.exists() or output.is_symlink():
        raise GenerationError("refusing to overwrite an existing generation output")

    credential = _read_deepseek_key(dotenv_path)
    runner = agent_runner or _load_stub_runner(stub_tester_root)
    command = agent_command or shutil.which("claude")
    if not command:
        raise GenerationError("Claude Code executable is unavailable")

    output.mkdir(parents=True, exist_ok=False)
    trajectory_path = output / "trajectory.jsonl"
    task_sha256 = _digest(task_path.read_bytes())
    runner(
        agent_command=command,
        agent_model=RUNNER_MODEL,
        agent_timeout_sec=TIMEOUT_SECONDS,
        task_path=task_path,
        trajectory_path=trajectory_path,
        workspace_root=workspace,
        env_overrides={
            "ANTHROPIC_AUTH_TOKEN": credential,
            "DEEPSEEK_API_BASE": "https://api.deepseek.com",
            "PYTHONDONTWRITEBYTECODE": "1",
        },
        base_environment=_base_environment(),
        agent_frame="claudecode",
        writable_files=(candidate_name,),
    )
    _validate_admission(gate_path, pair_root, arm)
    if _digest(task_path.read_bytes()) != task_sha256:
        raise GenerationError("prepared task changed during generation")
    if candidate_path.is_symlink() or not candidate_path.is_file():
        raise GenerationError("agent did not produce the complete candidate file")
    try:
        candidate_text = candidate_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise GenerationError("candidate is not readable UTF-8 source") from exc
    if not candidate_text.strip():
        raise GenerationError("candidate source is empty")
    return {
        "arm": arm,
        "candidate_path": str(candidate_path),
        "candidate_sha256": _digest(candidate_path.read_bytes()),
        "trajectory_path": str(trajectory_path),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gate", type=Path, required=True)
    parser.add_argument("--pair", type=Path, required=True)
    parser.add_argument("--arm", choices=("A", "B"), required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--dotenv", type=Path, default=DEFAULT_DOTENV)
    parser.add_argument("--stub-tester-root", type=Path, default=DEFAULT_STUB_TESTER_ROOT)
    parser.add_argument("--agent-command")
    args = parser.parse_args()
    run_generation(
        args.gate,
        args.pair,
        args.arm,
        args.output,
        dotenv_path=args.dotenv,
        stub_tester_root=args.stub_tester_root,
        agent_command=args.agent_command,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
