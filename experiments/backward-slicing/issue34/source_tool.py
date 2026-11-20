"""Audited, read-only source interface shared by all issue #34 groups."""

from __future__ import annotations

import argparse
from dataclasses import asdict
from fnmatch import fnmatchcase
import json
from pathlib import Path
from typing import Any

from mock_spec.adapters.python import load_task_with_repository
from mock_spec.validation import SourceStore


REGISTRY = ".omx/developer-realistic-registry-max-fix23"
DATASET = "python-benchmark-new"
SNAPSHOT = "241ef457f8d071c23e6acd658b684b11b54f13fa955e097d6bebe35dc755f47d"
CASE_ID = "flask_308"
ALLOWED_PATHS = ("tests/test_308abort.py", "src/src/flask/helpers.py")
ALIASES = {"test": ALLOWED_PATHS[0], "mut": ALLOWED_PATHS[1], "imports": ALLOWED_PATHS[1]}


def emit(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--old-root", required=True, type=Path)
    subparsers = parser.add_subparsers(dest="action", required=True)
    read = subparsers.add_parser("read")
    read.add_argument("locator")
    read.add_argument("start_line", type=int)
    read.add_argument("end_line", type=int)
    search = subparsers.add_parser("search")
    search.add_argument("--path-pattern")
    search.add_argument("--text")
    search.add_argument("--max-output-chars", type=int, default=16000)
    arguments = parser.parse_args()

    snapshot = arguments.old_root / REGISTRY / "formal-snapshots" / DATASET / SNAPSHOT
    task, repository = load_task_with_repository(snapshot, case_id=CASE_ID)
    store = SourceStore(task, repository=repository)
    try:
        if arguments.action == "read":
            path = ALIASES.get(arguments.locator, arguments.locator)
            if path not in ALLOWED_PATHS:
                parser.error("read locator is outside the two issue #34 audited source paths")
            source = store.read(path, arguments.start_line, arguments.end_line)
            emit({"status": "ok", "source": asdict(source), "usage": store.last_read_usage})
        else:
            if arguments.path_pattern is None and arguments.text is None:
                parser.error("search requires --path-pattern or --text")
            if not 1 <= arguments.max_output_chars <= 16000:
                parser.error("--max-output-chars must be between 1 and 16000")
            matches = []
            used = 0
            for path in ALLOWED_PATHS:
                if arguments.path_pattern is not None and not fnmatchcase(path, arguments.path_pattern):
                    continue
                source = store.analysis_source(path)
                for line_number, line in enumerate(source.text.splitlines(), start=source.start_line):
                    if arguments.text is not None and arguments.text not in line:
                        continue
                    item = {"logical_path": path, "source_id": source.source_id,
                            "start_line": line_number, "end_line": line_number, "text": line}
                    size = len(json.dumps(item, ensure_ascii=False).encode())
                    if used + size > arguments.max_output_chars:
                        emit({"status": "incomplete", "complete": False,
                              "limit_reason": "max_output_chars", "matches": matches,
                              "next_cursor": None, "policy_id": "issue34-two-source-search/v1"})
                        return
                    matches.append(item)
                    used += size
            emit({"status": "ok", "complete": True, "matches": matches,
                  "next_cursor": None, "policy_id": "issue34-two-source-search/v1",
                  "allowed_paths": list(ALLOWED_PATHS)})
    finally:
        repository.close()


if __name__ == "__main__":
    main()
