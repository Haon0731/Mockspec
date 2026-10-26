"""Collect code provenance and runtime metadata for audited and offline runs."""

from __future__ import annotations

import hashlib
import platform
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ExecutionMetadata:
    code_commit: str | None
    code_dirty: bool | None
    code_files_sha256: dict[str, str]
    runtime_identity: dict[str, object]


def collect_execution_metadata() -> ExecutionMetadata:
    """Inspect the installed code, anchoring Git to its checkout, never the cwd.

    Missing Git/runtime probes remain explicit unknowns. Code file read errors
    propagate; this function neither runs inference nor writes audit records.
    """
    return ExecutionMetadata(
        code_commit=_git_identity(),
        code_dirty=_git_dirty(),
        code_files_sha256=_code_hashes(),
        runtime_identity=_runtime_identity(),
    )


def _project_git_root() -> Path | None:
    source = Path(__file__).resolve()
    candidate = source.parents[2]
    if not (
        (candidate / "pyproject.toml").is_file()
        and (candidate / "src/mock_spec/execution_metadata.py").resolve() == source
    ):
        return None
    try:
        reported = subprocess.run(
            ["git", "-C", str(candidate), "rev-parse", "--show-toplevel"],
            check=True,
            capture_output=True,
            text=True,
            timeout=2,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None
    try:
        return candidate if Path(reported).resolve() == candidate else None
    except OSError:
        return None


def _git_identity() -> str | None:
    root = _project_git_root()
    if root is None:
        return None
    try:
        return subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
            timeout=2,
        ).stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


def _git_dirty() -> bool | None:
    root = _project_git_root()
    if root is None:
        return None
    try:
        return bool(subprocess.run(
            ["git", "-C", str(root), "status", "--porcelain"],
            check=True,
            capture_output=True,
            text=True,
            timeout=2,
        ).stdout)
    except (OSError, subprocess.SubprocessError):
        return None


def _code_hashes() -> dict[str, str]:
    package = Path(__file__).parent
    paths = [
        package / "inference.py",
        package / "inference_v02.py",
        package / "budget.py",
        package / "source_tools.py",
        package / "source_analysis.py",
        package / "source_repository.py",
        package / "analysis_process.py",
        package / "python_analysis.py",
        package / "python_analysis_runner.py",
        package / "java_analysis.py",
        package / "scenario.py",
        package / "schemas.py",
        package / "validation.py",
        package / "experiment_ledger.py",
        package / "live_v02.py",
        package / "audited_run.py",
        package / "execution_metadata.py",
        package / "run_evidence.py",
        package / "reviewed_inputs.json",
    ]
    paths.extend(sorted((package / "adapters").glob("*.py")))
    paths.extend(sorted((package / "tools").glob("**/*")))
    return {
        str(path.relative_to(package)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in paths if path.is_file()
    }


def _file_sha256(path: Path) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def _tool_version(path: Path) -> dict[str, object]:
    try:
        completed = subprocess.run(
            [str(path), "-version"],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
            cwd=path.parent,
            env={"LANG": "C.UTF-8", "LC_ALL": "C.UTF-8"},
        )
        output = (completed.stdout + completed.stderr).strip()
        return {
            "status": "ok" if completed.returncode == 0 and output else "unavailable",
            "returncode": completed.returncode,
            "version_output": output or None,
        }
    except (OSError, subprocess.SubprocessError):
        return {"status": "unavailable", "returncode": None, "version_output": None}


def _runtime_identity() -> dict[str, object]:
    from . import java_analysis

    executable = Path(sys.executable).resolve()
    executable_sha256 = _file_sha256(executable)
    python_identity = {
        "implementation": platform.python_implementation(),
        "version": platform.python_version(),
        "version_detail": sys.version,
        "cache_tag": sys.implementation.cache_tag,
        "compiler": platform.python_compiler(),
        "executable": str(executable),
        "executable_sha256": executable_sha256,
        "limitations": (
            [] if executable_sha256 is not None else [
                (
                    "The active Python executable could not be hashed; version metadata is "
                    "available but the runtime binary identity is incomplete."
                )
            ]
        ),
    }
    backend = java_analysis._backend_paths()
    if backend is None:
        spoon_identity = {
            "available": False,
            "spoon_version": "11.2.1",
            "spoon_jar_sha256": java_analysis._PINNED_SPOON_SHA256,
            "java": None,
            "javac": None,
            "limitations": [
                (
                    "The pinned Spoon JAR or configured Java/Javac toolchain is unavailable; "
                    "no executable Java runtime identity could be captured."
                )
            ],
        }
    else:
        jar, java, javac = backend
        java_identity = {
            "path": str(java.resolve()),
            "sha256": _file_sha256(java.resolve()),
            **_tool_version(java),
        }
        javac_identity = {
            "path": str(javac.resolve()),
            "sha256": _file_sha256(javac.resolve()),
            **_tool_version(javac),
        }
        limitations = [
            (
                "Version output and launcher hashes identify the configured toolchain; "
                "they do not hash every JDK runtime library."
            )
        ]
        if java_identity["status"] != "ok" or javac_identity["status"] != "ok":
            limitations.append(
                "At least one Java tool version probe was unavailable; its executable path "
                "and hash are recorded when readable."
            )
        spoon_identity = {
            "available": True,
            "spoon_version": "11.2.1",
            "spoon_jar": str(jar.resolve()),
            "spoon_jar_sha256": _file_sha256(jar),
            "java": java_identity,
            "javac": javac_identity,
            "limitations": limitations,
        }
    return {"python": python_identity, "spoon_toolchain": spoon_identity}
