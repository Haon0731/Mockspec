"""Audited V0.1/V0.2 inference runner with non-overwriting artifacts."""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
from importlib.metadata import version
import json
import math
import os
from pathlib import Path
import platform
import subprocess
import sys

from .adapters import java, python
from .budget import UsageLedger
from .experiment_ledger import complete_attempt, reserve_attempt
from .inference import InferenceError
from .inference_v02 import InferenceBudget


def _decimal(value: str) -> Decimal:
    try:
        parsed = Decimal(value)
    except InvalidOperation:
        raise argparse.ArgumentTypeError("must be a decimal") from None
    if not parsed.is_finite() or parsed <= 0:
        raise argparse.ArgumentTypeError("must be positive")
    return parsed


def _project_git_root() -> Path | None:
    source = Path(__file__).resolve()
    candidate = source.parents[2]
    if not (
        (candidate / "pyproject.toml").is_file()
        and (candidate / "src/mock_spec/live_v02.py").resolve() == source
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
        package / "program_tools.py",
        package / "analysis_process.py",
        package / "python_analysis.py",
        package / "python_analysis_runner.py",
        package / "java_analysis.py",
        package / "scenario.py",
        package / "schemas.py",
        package / "validation.py",
        package / "experiment_ledger.py",
        package / "live_v02.py",
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
                "The active Python executable could not be hashed; version metadata is "
                "available but the runtime binary identity is incomplete."
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
                "The pinned Spoon JAR or configured Java/Javac toolchain is unavailable; "
                "no executable Java runtime identity could be captured."
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
            "Version output and launcher hashes identify the configured toolchain; "
            "they do not hash every JDK runtime library."
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


def _baseline_usage(responses: list, budget: InferenceBudget, *, failed: bool) -> dict:
    input_tokens = 0
    output_tokens = 0
    total_tokens = 0
    complete = bool(responses)
    reported_requests = 0
    for response in responses:
        usage = response.get("usage") or {}
        reported_requests += usage.get("requests", 0) or 0
        known = bool(
            usage.get("input_tokens")
            or usage.get("output_tokens")
            or usage.get("total_tokens")
        )
        if not known:
            complete = False
            continue
        input_tokens += usage.get("input_tokens", 0)
        output_tokens += usage.get("output_tokens", 0)
        total_tokens += usage.get("total_tokens", 0) or (
            usage.get("input_tokens", 0) + usage.get("output_tokens", 0)
        )
    cost = None
    if (
        complete
        and budget.input_cost_per_million is not None
        and budget.output_cost_per_million is not None
    ):
        cost = str((
            Decimal(input_tokens) * budget.input_cost_per_million
            + Decimal(output_tokens) * budget.output_cost_per_million
        ) / Decimal(1_000_000))
    return {
        "requests_started": None,
        "responses_completed": len(responses),
        "reported_provider_requests": reported_requests or None,
        "input_tokens": input_tokens if complete else None,
        "output_tokens": output_tokens if complete else None,
        "total_tokens": total_tokens if complete else None,
        "usage_complete": complete,
        "known_usage_subtotal": {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": total_tokens,
        },
        "rate_estimated_cost_usd": cost,
        "pricing": {
            "input_cost_per_million": (
                None if budget.input_cost_per_million is None
                else str(budget.input_cost_per_million)
            ),
            "output_cost_per_million": (
                None if budget.output_cost_per_million is None
                else str(budget.output_cost_per_million)
            ),
            "basis": (
                "host_supplied_per_million_token_prices"
                if budget.input_cost_per_million is not None else "unknown"
            ),
        },
        "limitations": [
            "Frozen V0.1 has one model turn and no pre-request token/cost hook.",
            "Provider retries remain at the frozen baseline defaults.",
            "A locally cancelled request may incur charges absent from captured usage.",
        ],
    }


def _write_artifact(output_dir: Path, artifact: dict) -> bool:
    try:
        text = json.dumps(artifact, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
        with (output_dir / "result.json").open("x", encoding="utf-8") as stream:
            stream.write(text)
    except (OSError, TypeError, ValueError):
        return False
    return True


def _write_pending_artifact(output_dir: Path, artifact: dict) -> Path | None:
    pending = output_dir / "result.pending.json"
    try:
        text = json.dumps(artifact, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
        with pending.open("x", encoding="utf-8") as stream:
            stream.write(text)
    except (OSError, TypeError, ValueError):
        return None
    return pending


def _finalize_pending_artifact(pending: Path, artifact: dict | None = None) -> bool:
    try:
        if artifact is not None:
            text = json.dumps(artifact, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
            pending.write_text(text, encoding="utf-8")
        pending.rename(pending.with_name("result.json"))
    except (OSError, TypeError, ValueError):
        return False
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--language", choices=("java", "python"), required=True)
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--case-id", required=True)
    parser.add_argument("--model", default=os.environ.get("MOCKSPEC_MODEL"))
    parser.add_argument("--agent-version", choices=("v0.1", "v0.2"), required=True)
    parser.add_argument("--timeout", type=float, default=120)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--max-turns", type=int, default=6)
    parser.add_argument("--max-tool-calls", type=int, default=8)
    parser.add_argument("--max-output-tokens", type=int, default=4_096)
    parser.add_argument("--max-total-tokens", type=int, default=24_000)
    parser.add_argument("--max-tool-output-chars", type=int, default=16_000)
    parser.add_argument("--analysis-timeout", type=float, default=15)
    parser.add_argument("--max-cost-usd", type=_decimal)
    parser.add_argument("--input-cost-per-million", type=_decimal)
    parser.add_argument("--output-cost-per-million", type=_decimal)
    parser.add_argument("--experiment-ledger", type=Path)
    parser.add_argument("--attempt-id")
    parser.add_argument("--run-key")
    parser.add_argument("--retry-of")
    parser.add_argument(
        "--standalone", action="store_true",
        help="Run one audited call outside the formal four-run experiment ledger.",
    )
    args = parser.parse_args(argv)
    args.model = (args.model or "").strip()

    if not args.model or not os.environ.get("OPENAI_API_KEY", "").strip():
        print("configuration error: model and OPENAI_API_KEY required", file=sys.stderr)
        return 2
    if not math.isfinite(args.timeout) or args.timeout <= 0:
        print("configuration error: timeout must be finite and positive", file=sys.stderr)
        return 2
    if args.experiment_ledger is None and not args.standalone:
        print(
            "configuration error: use --experiment-ledger for formal runs or --standalone",
            file=sys.stderr,
        )
        return 2
    if args.experiment_ledger is not None and args.standalone:
        print("configuration error: --standalone cannot use an experiment ledger", file=sys.stderr)
        return 2
    if args.experiment_ledger is not None and (
        not args.attempt_id or args.max_cost_usd is None
    ):
        print(
            "configuration error: ledger requires attempt-id and max-cost-usd",
            file=sys.stderr,
        )
        return 2
    try:
        budget = InferenceBudget(
            max_turns=args.max_turns,
            max_tool_calls=args.max_tool_calls,
            max_output_tokens=args.max_output_tokens,
            max_total_tokens=args.max_total_tokens,
            max_tool_output_chars=args.max_tool_output_chars,
            analysis_timeout_seconds=args.analysis_timeout,
            max_cost_usd=args.max_cost_usd,
            input_cost_per_million=args.input_cost_per_million,
            output_cost_per_million=args.output_cost_per_million,
        )
        loader = java.load_task if args.language == "java" else python.load_task
        task = loader(args.snapshot, case_id=args.case_id)
    except (InferenceError, ValueError, OSError) as exc:
        print(f"configuration/input error: {type(exc).__name__}", file=sys.stderr)
        return 2

    from . import inference, inference_v02
    expected_run_key = f"{task.language}-{args.agent_version}"
    if args.run_key is not None and args.run_key != expected_run_key:
        print(
            f"configuration error: run-key must be {expected_run_key}", file=sys.stderr
        )
        return 2
    args.run_key = expected_run_key
    selected = inference if args.agent_version == "v0.1" else inference_v02
    payload = json.dumps(task.to_dict(), ensure_ascii=False, separators=(",", ":"))
    task_sha256 = hashlib.sha256(payload.encode()).hexdigest()
    prompt_hashes = {
        "v0.1": hashlib.sha256(inference._PROMPT_PATH.read_bytes()).hexdigest(),
        "v0.2": hashlib.sha256(inference_v02._PROMPT_PATH.read_bytes()).hexdigest(),
    }
    code_files = _code_hashes()
    anchored_git_commit = _git_identity()
    runtime_identity = _runtime_identity()
    packages = {p: version(p) for p in ("openai-agents", "openai", "pydantic")}
    formal_configuration = {
        "model": args.model,
        "timeout_seconds": float(args.timeout),
        "configured_limits": {
            "max_turns": budget.max_turns,
            "max_tool_calls": budget.max_tool_calls,
            "max_output_tokens": budget.max_output_tokens,
            "max_total_tokens": budget.max_total_tokens,
            "max_tool_output_chars": budget.max_tool_output_chars,
            "analysis_timeout_seconds": float(budget.analysis_timeout_seconds),
            "max_cost_usd": (
                None if budget.max_cost_usd is None else str(budget.max_cost_usd)
            ),
        },
        "pricing": {
            "input_cost_per_million": (
                None if budget.input_cost_per_million is None
                else str(budget.input_cost_per_million)
            ),
            "output_cost_per_million": (
                None if budget.output_cost_per_million is None
                else str(budget.output_cost_per_million)
            ),
        },
        "prompt_sha256": prompt_hashes,
        "code_files_sha256": code_files,
        "packages": packages,
        "anchored_git_commit": anchored_git_commit,
        "runtime_identity": runtime_identity,
        "sampling": "SDK/provider defaults; no override",
        "transport_retries": {
            "v0.1": "frozen baseline SDK/provider defaults",
            "v0.2": "disabled (max_retries=0)",
        },
    }
    formal_configuration_sha256 = hashlib.sha256(json.dumps(
        formal_configuration,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()).hexdigest()

    try:
        args.output_dir.mkdir(parents=True, exist_ok=False)
    except OSError:
        print("output error: new output directory required", file=sys.stderr)
        return 2

    prompt_path = selected._PROMPT_PATH
    source_path = Path(selected.__file__)
    raw_responses: list = []
    tool_trace: list = []
    usage_ledger = UsageLedger(budget) if args.agent_version == "v0.2" else None
    artifact = {
        "mode": "live_inference",
        "agent_version": args.agent_version,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "model": args.model,
        "resolved_model_version": None,
        "prompt_version": selected.PROMPT_VERSION,
        "prompt_sha256": hashlib.sha256(prompt_path.read_bytes()).hexdigest(),
        "code_sha256": hashlib.sha256(source_path.read_bytes()).hexdigest(),
        "code_files_sha256": code_files,
        "git_commit": anchored_git_commit,
        "runtime_identity": runtime_identity,
        "git_dirty": _git_dirty(),
        "task_schema_version": task.schema_version,
        "contract_schema_version": "mock-spec.scenario/v0.1",
        "case_id": task.case_id,
        "language": task.language,
        "snapshot_id": args.snapshot.name,
        "task_sha256": task_sha256,
        "packages": packages,
        "parameters": {
            "timeout_seconds": args.timeout,
            "max_turns": 1 if args.agent_version == "v0.1" else budget.max_turns,
            "max_tool_calls": None if args.agent_version == "v0.1" else budget.max_tool_calls,
            "max_output_tokens": (
                None if args.agent_version == "v0.1" else budget.max_output_tokens
            ),
            "max_total_tokens": (
                None if args.agent_version == "v0.1" else budget.max_total_tokens
            ),
            "max_tool_output_chars": (
                None if args.agent_version == "v0.1" else budget.max_tool_output_chars
            ),
            "analysis_timeout_seconds": (
                None if args.agent_version == "v0.1" else budget.analysis_timeout_seconds
            ),
            "max_cost_usd": (
                None if budget.max_cost_usd is None else str(budget.max_cost_usd)
            ),
            "input_cost_per_million": (
                None if budget.input_cost_per_million is None
                else str(budget.input_cost_per_million)
            ),
            "output_cost_per_million": (
                None if budget.output_cost_per_million is None
                else str(budget.output_cost_per_million)
            ),
            "store": False,
            "tracing_disabled": True,
            "sampling": "SDK/provider defaults; no override",
            "transport_retries": (
                "frozen baseline SDK/provider defaults"
                if args.agent_version == "v0.1" else "disabled (max_retries=0)"
            ),
        },
        "experiment": {
            "ledger": None if args.experiment_ledger is None else str(args.experiment_ledger),
            "attempt_id": args.attempt_id,
            "run_key": args.run_key,
            "retry_of": args.retry_of,
            "configuration_sha256": formal_configuration_sha256,
            "application_level_retries": "none; retries require a new explicit attempt",
        },
        "raw_responses": raw_responses,
        "tool_trace": tool_trace,
        "usage": None,
        "validation": "not_run",
        "semantic_review": "pending",
        "reference_answer_evaluation": "not_performed",
        "contract": None,
    }

    if args.experiment_ledger is not None:
        try:
            reserve_attempt(
                args.experiment_ledger,
                attempt_id=args.attempt_id,
                run_key=args.run_key,
                max_cost_usd=args.max_cost_usd,
                configuration=formal_configuration,
                task_identity=task_sha256,
                retry_of=args.retry_of,
            )
        except InferenceError as exc:
            artifact["status"] = exc.category
            artifact["diagnostic_type"] = "ExperimentLedger"
            artifact["finished_at"] = datetime.now(timezone.utc).isoformat()
            if not _write_artifact(args.output_dir, artifact):
                print("output error: could not persist run evidence", file=sys.stderr)
            return 2

    failure: InferenceError | None = None
    interrupted = False
    try:
        kwargs = {
            "model": args.model,
            "timeout_seconds": args.timeout,
            "responses": raw_responses,
        }
        if args.agent_version == "v0.2":
            kwargs.update(
                budget=budget,
                tool_trace=tool_trace,
                usage_ledger=usage_ledger,
            )
        contract = asyncio.run(selected.infer_scenario(task, **kwargs))
        artifact["contract"] = contract.model_dump()
        artifact["validation"] = "schema_and_evidence_locations_passed"
        artifact["status"] = "completed"
        code = 0
    except InferenceError as exc:
        failure = exc
        artifact["status"] = exc.category
        artifact["diagnostic_type"] = exc.diagnostic
        if exc.category in ("structure_error", "evidence_error"):
            artifact["validation"] = exc.category
        code = 2
    except KeyboardInterrupt:
        interrupted = True
        artifact["status"] = "interrupted"
        artifact["diagnostic_type"] = "KeyboardInterrupt"
        code = 130

    if usage_ledger is not None:
        usage = usage_ledger.to_dict()
    else:
        usage = _baseline_usage(
            raw_responses, budget, failed=failure is not None or interrupted
        )
    artifact["usage"] = usage
    artifact["finished_at"] = datetime.now(timezone.utc).isoformat()

    pending_artifact = None
    if args.experiment_ledger is not None:
        pending_artifact = _write_pending_artifact(args.output_dir, artifact)
        if pending_artifact is None:
            print("output error: could not persist pending run evidence", file=sys.stderr)
            return 2
        outcome = artifact["status"]
        if failure is not None and str(failure) == "scenario inference timed out":
            outcome = "technical_timeout"
        try:
            complete_attempt(
                args.experiment_ledger,
                attempt_id=args.attempt_id,
                outcome_status=outcome,
                valid_contract=artifact["contract"] is not None,
                usage_complete=usage["usage_complete"],
                rate_estimated_cost_usd=usage["rate_estimated_cost_usd"],
                diagnostic_type=artifact.get("diagnostic_type"),
            )
        except InferenceError:
            artifact["status"] = "ledger_error"
            artifact["contract"] = None
            code = 2
        if not _finalize_pending_artifact(
            pending_artifact, artifact if artifact["status"] == "ledger_error" else None
        ):
            print("output error: could not finalize run evidence", file=sys.stderr)
            return 2
    elif not _write_artifact(args.output_dir, artifact):
        print("output error: could not persist run evidence", file=sys.stderr)
        return 2
    return code


if __name__ == "__main__":
    raise SystemExit(main())
