"""Persistent host-side limits for the four-run Step 6 comparison."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import fcntl
import hashlib
import json
import os
from pathlib import Path
import tempfile
from typing import Iterator

from .inference import InferenceError


LEDGER_VERSION = "mock-spec.step6-experiment/v1"
TECHNICAL_DIAGNOSTICS = frozenset({
    "APIConnectionError",
    "APITimeoutError",
    "InternalServerError",
    "RateLimitError",
})


def _positive_decimal(value: object, name: str) -> Decimal:
    if isinstance(value, bool):
        raise InferenceError(f"{name} must be positive")
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise InferenceError(f"{name} must be positive") from None
    if not parsed.is_finite() or parsed <= 0:
        raise InferenceError(f"{name} must be positive")
    return parsed


def _identifier(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 200:
        raise InferenceError(f"{name} must be a non-empty identifier")
    return value.strip()


def _initial_state() -> dict:
    return {
        "schema_version": LEDGER_VERSION,
        "policy": {
            "target_cost_usd": "5",
            "max_run_keys": 4,
            "max_technical_retries": 2,
            "quality_retries_allowed": False,
            "strict_hard_cap": False,
            "limitation": (
                "Reservations and reported usage gate later attempts; provider charges can "
                "arrive after local cancellation or exceed an estimate."
            ),
        },
        "accounting_complete": True,
        "configuration_sha256": None,
        "configuration": None,
        "run_key_task_identities": {},
        "attempts": [],
    }


@contextmanager
def _locked_state(path: Path) -> Iterator[dict]:
    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_name(path.name + ".lock")
    with lock_path.open("a+", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        if path.exists():
            try:
                state = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                raise InferenceError("experiment ledger is unreadable") from None
            if state.get("schema_version") != LEDGER_VERSION:
                raise InferenceError("experiment ledger version is invalid")
        else:
            state = _initial_state()
        yield state
        descriptor, temporary = tempfile.mkstemp(
            prefix=path.name + ".", suffix=".tmp", dir=path.parent
        )
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                json.dump(state, stream, ensure_ascii=False, indent=2, allow_nan=False)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)


def reserve_attempt(
    path: Path,
    *,
    attempt_id: str,
    run_key: str,
    max_cost_usd: object,
    configuration: dict,
    task_identity: str,
    retry_of: str | None = None,
) -> dict:
    """Reserve one primary run or eligible technical retry under the shared $5 target."""

    attempt_id = _identifier(attempt_id, "attempt_id")
    run_key = _identifier(run_key, "run_key")
    reservation = _positive_decimal(max_cost_usd, "max_cost_usd")
    task_identity = _identifier(task_identity, "task_identity")
    if not isinstance(configuration, dict):
        raise InferenceError("experiment configuration must be an object")
    try:
        configuration_json = json.dumps(
            configuration,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError):
        raise InferenceError("experiment configuration is not JSON serializable") from None
    configuration_sha256 = hashlib.sha256(configuration_json.encode()).hexdigest()
    retry_of = None if retry_of is None else _identifier(retry_of, "retry_of")
    with _locked_state(path) as state:
        attempts = state["attempts"]
        if not state["accounting_complete"]:
            raise InferenceError(
                "experiment ledger has unknown prior charges", "budget_exhausted"
            )
        if any(item["attempt_id"] == attempt_id for item in attempts):
            raise InferenceError("experiment attempt_id already exists")
        frozen_hash = state.get("configuration_sha256")
        if frozen_hash is None:
            state["configuration_sha256"] = configuration_sha256
            state["configuration"] = configuration
        elif frozen_hash != configuration_sha256:
            raise InferenceError("experiment configuration differs from the frozen plan")
        identities = state.setdefault("run_key_task_identities", {})
        frozen_identity = identities.get(run_key)
        if frozen_identity is None:
            identities[run_key] = task_identity
        elif frozen_identity != task_identity:
            raise InferenceError("experiment run_key task identity mismatch")
        if any(item["status"] == "reserved" for item in attempts):
            raise InferenceError(
                "experiment has an unfinished attempt", "budget_exhausted"
            )
        policy = state["policy"]
        primary_keys = {item["run_key"] for item in attempts if item["retry_of"] is None}
        if retry_of is None:
            if run_key in primary_keys:
                raise InferenceError("quality or duplicate retries are not allowed")
            if len(primary_keys) >= policy["max_run_keys"]:
                raise InferenceError(
                    "experiment valid-run plan is exhausted", "budget_exhausted"
                )
        else:
            predecessors = [item for item in attempts if item["attempt_id"] == retry_of]
            if not predecessors:
                raise InferenceError("technical retry predecessor is missing")
            predecessor = predecessors[0]
            if predecessor["run_key"] != run_key:
                raise InferenceError("technical retry run_key does not match predecessor")
            run_attempts = [item for item in attempts if item["run_key"] == run_key]
            if run_attempts[-1]["attempt_id"] != retry_of:
                raise InferenceError("technical retry must follow the latest run attempt")
            retryable = (
                predecessor.get("outcome_status") == "technical_timeout"
                or (
                    predecessor.get("outcome_status") == "model_call_failure"
                    and predecessor.get("diagnostic_type") in TECHNICAL_DIAGNOSTICS
                )
            )
            if not retryable:
                raise InferenceError("retry is allowed only after a technical failure")
            retry_count = sum(item["retry_of"] is not None for item in attempts)
            if retry_count >= policy["max_technical_retries"]:
                raise InferenceError(
                    "experiment technical retry budget exhausted", "budget_exhausted"
                )

        committed = sum(
            Decimal(item["rate_estimated_cost_usd"])
            for item in attempts
            if item.get("rate_estimated_cost_usd") is not None
        )
        pending = sum(
            Decimal(item["reserved_max_cost_usd"])
            for item in attempts
            if item["status"] == "reserved"
        )
        if committed + pending + reservation > Decimal(policy["target_cost_usd"]):
            raise InferenceError(
                "experiment cost target would be exceeded", "budget_exhausted"
            )
        record = {
            "attempt_id": attempt_id,
            "run_key": run_key,
            "retry_of": retry_of,
            "status": "reserved",
            "reserved_max_cost_usd": str(reservation),
            "outcome_status": None,
            "valid_contract": False,
            "usage_complete": None,
            "rate_estimated_cost_usd": None,
            "diagnostic_type": None,
            "configuration_sha256": configuration_sha256,
            "task_identity": task_identity,
            "reserved_at": datetime.now(timezone.utc).isoformat(),
            "completed_at": None,
        }
        attempts.append(record)
        return dict(record)


def complete_attempt(
    path: Path,
    *,
    attempt_id: str,
    outcome_status: str,
    valid_contract: bool,
    usage_complete: bool,
    rate_estimated_cost_usd: object | None,
    diagnostic_type: str | None = None,
) -> dict:
    """Finalize a reservation; unknown charges close the ledger to later attempts."""

    attempt_id = _identifier(attempt_id, "attempt_id")
    outcome_status = _identifier(outcome_status, "outcome_status")
    with _locked_state(path) as state:
        matches = [item for item in state["attempts"] if item["attempt_id"] == attempt_id]
        if not matches or matches[0]["status"] != "reserved":
            raise InferenceError("experiment attempt is not reserved")
        record = matches[0]
        if usage_complete and rate_estimated_cost_usd is not None:
            try:
                cost = Decimal(str(rate_estimated_cost_usd))
            except (InvalidOperation, ValueError):
                raise InferenceError("experiment attempt cost is invalid") from None
            if not cost.is_finite() or cost < 0:
                raise InferenceError("experiment attempt cost is invalid")
            cost_value = str(cost)
        else:
            cost_value = None
            state["accounting_complete"] = False
        if diagnostic_type is not None:
            diagnostic_type = _identifier(diagnostic_type, "diagnostic_type")
        record.update(
            status="completed",
            outcome_status=outcome_status,
            valid_contract=bool(valid_contract),
            usage_complete=bool(usage_complete),
            rate_estimated_cost_usd=cost_value,
            diagnostic_type=diagnostic_type,
            completed_at=datetime.now(timezone.utc).isoformat(),
        )
        return dict(record)


__all__ = [
    "LEDGER_VERSION",
    "TECHNICAL_DIAGNOSTICS",
    "complete_attempt",
    "reserve_attempt",
]
