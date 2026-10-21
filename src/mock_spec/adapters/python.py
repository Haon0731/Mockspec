"""Python frozen task adapter, restricted to reviewed cases."""

from pathlib import Path

from ..schemas import UnifiedTask
from ._frozen import load_reviewed_task


def load_task(snapshot_root: Path, *, case_id: str) -> UnifiedTask:
    return load_reviewed_task(snapshot_root, case_id=case_id, language="python")
