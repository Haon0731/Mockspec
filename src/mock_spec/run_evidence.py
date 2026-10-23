"""Persist completed run evidence and its ledger outcome in the required order.

Pending evidence precedes ledger completion; publication comes last. This is not
an atomic transaction: a publication failure retains pending evidence even when
the ledger has completed. Unexpected ledger exceptions propagate unchanged.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from .experiment_ledger import complete_attempt
from .inference import InferenceError


def write_artifact(output_dir: Path, artifact: dict) -> bool:
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


def finish_run(
    output_dir: Path,
    artifact: dict,
    *,
    exit_code: int,
    technical_timeout: bool = False,
) -> int:
    """Persist a live-run artifact, returning its exit code or 2 on handled failure.

    The runner supplies its existing experiment and usage records and classifies
    its version's host deadline. Ledger errors update the artifact in place;
    inference status and ledger timeout classification retain distinct meanings.
    """
    experiment = artifact["experiment"]
    ledger_path = None if experiment["ledger"] is None else Path(experiment["ledger"])
    usage = artifact["usage"]
    if ledger_path is not None:
        pending_artifact = _write_pending_artifact(output_dir, artifact)
        if pending_artifact is None:
            print("output error: could not persist pending run evidence", file=sys.stderr)
            return 2
        outcome = artifact["status"]
        if technical_timeout:
            outcome = "technical_timeout"
        try:
            complete_attempt(
                ledger_path,
                attempt_id=experiment["attempt_id"],
                outcome_status=outcome,
                valid_contract=artifact["contract"] is not None,
                usage_complete=usage["usage_complete"],
                rate_estimated_cost_usd=usage["rate_estimated_cost_usd"],
                diagnostic_type=artifact.get("diagnostic_type"),
            )
        except InferenceError:
            artifact["status"] = "ledger_error"
            artifact["contract"] = None
            exit_code = 2
        if not _finalize_pending_artifact(
            pending_artifact, artifact if artifact["status"] == "ledger_error" else None
        ):
            print("output error: could not finalize run evidence", file=sys.stderr)
            return 2
    elif not write_artifact(output_dir, artifact):
        print("output error: could not persist run evidence", file=sys.stderr)
        return 2
    return exit_code
