"""Java frozen task adapter, restricted to reviewed cases."""

from pathlib import Path

from ..schemas import UnifiedTask
from ..source_repository import ReviewedRepository
from ._frozen import load_reviewed_task, load_reviewed_task_with_repository


def load_task(snapshot_root: Path, *, case_id: str) -> UnifiedTask:
    return load_reviewed_task(snapshot_root, case_id=case_id, language="java")


def load_task_with_repository(
    snapshot_root: Path, *, case_id: str
) -> tuple[UnifiedTask, ReviewedRepository]:
    return load_reviewed_task_with_repository(
        snapshot_root, case_id=case_id, language="java"
    )
