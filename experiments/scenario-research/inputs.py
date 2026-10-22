"""Load the two reviewed Scenario Contract research inputs.

This module deliberately delegates admission and file reads to the production
reviewed adapters.  Callers receive only the resulting ``UnifiedTask`` and its
in-memory, source-id-only ``SourceStore``.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path

from mock_spec.adapters.java import load_task as load_java_task
from mock_spec.adapters.python import load_task as load_python_task
from mock_spec.schemas import UnifiedTask
from mock_spec.validation import SourceStore


JAVA_CASE_ID = (
    "com.google.cloud.tools.jib.cli.jar.JarFilesTest#"
    "testToJibContainerBuilder_explodedStandard_basicInfo@69ae66df"
)
PYTHON_CASE_ID = "flask_308"

JAVA_SNAPSHOT_ROOT = Path(
    "/home/haon/projects/Stub-Tester/.omx/developer-realistic-registry-max/"
    "formal-snapshots/java-bechmark-strong/"
    "5b93995c794af0152854a01ddda2fe77948b5ab828b05282b00728f7729f64d4"
)
PYTHON_SNAPSHOT_ROOT = Path(
    "/home/haon/projects/Stub-Tester/.omx/developer-realistic-registry-max-fix23/"
    "formal-snapshots/python-benchmark-new/"
    "241ef457f8d071c23e6acd658b684b11b54f13fa955e097d6bebe35dc755f47d"
)


@dataclass(frozen=True)
class ReviewedInput:
    task: UnifiedTask
    sources: SourceStore
    source_sha256: dict[str, str]


def _wrap(task: UnifiedTask) -> ReviewedInput:
    store = SourceStore(task)
    hashes = {
        source_id: hashlib.sha256(store.get(source_id).text.encode("utf-8")).hexdigest()
        for source_id in store.ids()
    }
    return ReviewedInput(task=task, sources=store, source_sha256=hashes)


def load_java(root: Path = JAVA_SNAPSHOT_ROOT) -> ReviewedInput:
    """Load JarFiles through its reviewed adapter and source-only store."""
    return _wrap(load_java_task(root, case_id=JAVA_CASE_ID))


def load_python(root: Path = PYTHON_SNAPSHOT_ROOT) -> ReviewedInput:
    """Load flask_308 through its reviewed adapter and source-only store."""
    return _wrap(load_python_task(root, case_id=PYTHON_CASE_ID))
