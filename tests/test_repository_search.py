from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

from mock_spec.adapters._frozen import _load_approved
from mock_spec.schemas import SourceSlice, UnifiedTask
from mock_spec.source_repository import ReviewedRepository, SearchLimits
from mock_spec.validation import InputValidationError
from test_adapters import _build_snapshot


def _repository(
    tmp_path: Path, *, limits: SearchLimits | None = None,
    excluded_paths: tuple[str, ...] = (),
) -> tuple[ReviewedRepository, Path, UnifiedTask]:
    root, review = _build_snapshot(tmp_path, "python")
    task = UnifiedTask.from_dict(_load_approved(root, review).to_dict())
    files = {
        item["logical_path"]: (f"tree/{item['path']}", item["sha256"])
        for item in review["sources"]
    }
    root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    try:
        repository = ReviewedRepository(
            root_fd,
            snapshot_id=review["snapshot_id"],
            files_by_logical_path=files,
            task=task,
            discovery_root="tree/repo",
            excluded_paths=excluded_paths,
            search_limits=limits,
        )
    finally:
        os.close(root_fd)
    return repository, root, task


def test_reviewed_exclusions_block_files_trees_and_cursor_discovery(
    tmp_path: Path,
) -> None:
    repository, root, _ = _repository(
        tmp_path,
        limits=SearchLimits(max_matches=1),
        excluded_paths=("out", "metadata.yml"),
    )
    (root / "tree/repo/out").mkdir()
    (root / "tree/repo/out/build.txt").write_text("needle hidden\n", encoding="utf-8")
    (root / "tree/repo/metadata.yml").write_text("needle private\n", encoding="utf-8")
    (root / "tree/repo/nearby.txt").write_text(
        "needle one\nneedle two\n", encoding="utf-8"
    )
    try:
        direct_tree = repository.search(path_pattern="out/**", text="needle")
        direct_file = repository.search(path_pattern="metadata.yml", text="needle")
        assert direct_tree["error_code"] == "access_denied"
        assert direct_file["error_code"] == "access_denied"

        first = repository.search(text="needle", run_id="excluded-scope")
        assert [match["logical_path"] for match in first["matches"]] == ["nearby.txt"]
        assert first["usage"]["excluded"] >= 2
        assert first["next_cursor"] is not None
        second = repository.search(
            text="needle", cursor=first["next_cursor"], run_id="excluded-scope"
        )
        assert [match["logical_path"] for match in second["matches"]] == ["nearby.txt"]
        assert all(
            match["logical_path"] not in {"out/build.txt", "metadata.yml"}
            for match in [*first["matches"], *second["matches"]]
        )

        source = repository.read("nearby.txt", 1, 1)
        assert source.text == "needle one"
        with pytest.raises(InputValidationError, match="excluded"):
            repository.read("out/build.txt", 1, 1)
        with pytest.raises(InputValidationError, match="excluded"):
            repository.read_for_analysis("metadata.yml")
    finally:
        repository.close()


def test_default_review_scope_still_discovers_ordinary_out_directory(tmp_path: Path) -> None:
    repository, root, _ = _repository(tmp_path)
    (root / "tree/repo/out").mkdir()
    (root / "tree/repo/out/source.txt").write_text("default visible\n", encoding="utf-8")
    try:
        result = repository.search(path_pattern="out/*.txt", text="default visible")
        assert result["matches"][0]["logical_path"] == "out/source.txt"
    finally:
        repository.close()


def test_reviewed_exclusions_are_part_of_repository_identity(tmp_path: Path) -> None:
    default, _, _ = _repository(tmp_path / "default")
    restricted, _, _ = _repository(
        tmp_path / "restricted", excluded_paths=("out",)
    )
    try:
        assert default._repository_identity != restricted._repository_identity
    finally:
        default.close()
        restricted.close()


def test_search_discovers_path_and_literal_line_then_reads_it(tmp_path: Path) -> None:
    repository, root, _ = _repository(tmp_path)
    added = root / "tree/repo/src/new_helper.py"
    added.write_text("# context\ndef helper():\n    return 'literal needle'\n", encoding="utf-8")
    try:
        paths = repository.search(path_pattern="src/*.py")
        discovered = next(item for item in paths["matches"]
                          if item["logical_path"] == "src/new_helper.py")
        assert discovered["source_id"].startswith("repository:")
        assert discovered["start_line"] is None
        assert discovered["text"] is None

        result = repository.search(path_pattern="src/*.py", text="literal needle")
        assert result["status"] == "ok"
        assert result["complete"] is True
        assert result["matches"] == [{
            "logical_path": "src/new_helper.py",
            "source_id": discovered["source_id"],
            "start_line": 3,
            "end_line": 3,
            "text": "    return 'literal needle'",
        }]
        source = repository.read(discovered["source_id"], 3, 3)
        assert source.text == "    return 'literal needle'"
    finally:
        repository.close()


def test_match_limit_paginates_inside_one_file_and_binds_query(tmp_path: Path) -> None:
    limits = SearchLimits(max_matches=1)
    repository, root, _ = _repository(tmp_path, limits=limits)
    added = root / "tree/repo/src/many.py"
    added.write_text("needle one\nneedle two\n", encoding="utf-8")
    try:
        first = repository.search(path_pattern="src/many.py", text="needle")
        assert first["status"] == "incomplete"
        assert first["limit_reason"] == "match_limit"
        assert [item["start_line"] for item in first["matches"]] == [1]

        bad = repository.search(
            path_pattern="src/many.py", text="different", cursor=first["next_cursor"]
        )
        assert bad["error_code"] == "invalid_cursor"

        second = repository.search(
            path_pattern="src/many.py", text="needle", cursor=first["next_cursor"]
        )
        assert [item["start_line"] for item in second["matches"]] == [2]
        assert second["complete"] is True
        assert second["usage"]["bytes"] == len(added.read_bytes())
    finally:
        repository.close()


def test_denied_and_linked_content_is_never_returned(tmp_path: Path) -> None:
    repository, root, _ = _repository(tmp_path)
    secret = tmp_path / "secret.py"
    secret.write_text("external sentinel needle\n", encoding="utf-8")
    (root / "tree/repo/.env").write_text("needle=secret\n", encoding="utf-8")
    (root / "tree/repo/src/linked.py").symlink_to(secret)
    try:
        denied = repository.search(path_pattern=".env", text="needle")
        assert denied["error_code"] == "access_denied"

        result = repository.search(text="needle")
        assert all(item["logical_path"] != "src/linked.py" for item in result["matches"])
        assert all("external sentinel" not in (item["text"] or "")
                   for item in result["matches"])
    finally:
        repository.close()


def test_bad_cursor_and_finite_limits_are_explicit(tmp_path: Path) -> None:
    repository, _, _ = _repository(tmp_path)
    try:
        bad = repository.search(path_pattern="*.py", cursor="invented")
        assert bad["error_code"] == "invalid_cursor"
        result = repository.search(path_pattern="**/*.py")
        assert result["limits"] == {
            "max_entries": 2000,
            "max_bytes": 2 * 1024 * 1024,
            "max_file_bytes": 256 * 1024,
            "max_matches": 50,
            "timeout_seconds": 2.0,
        }
        assert result["usage"]["entries"] > 0
    finally:
        repository.close()


def test_output_limit_omits_unciteable_line_and_reports_incomplete(tmp_path: Path) -> None:
    repository, root, _ = _repository(tmp_path)
    (root / "tree/repo/src/long.py").write_text(
        f"needle {'x' * 1000}\n", encoding="utf-8"
    )
    try:
        result = repository.search(text="needle", max_output_chars=512)
        assert result["status"] == "incomplete"
        assert result["complete"] is False
        assert result["limit_reason"] == "output_limit"
        assert result["matches"] == []
        assert len(json.dumps(result, ensure_ascii=False)) <= 512
        with pytest.raises(InputValidationError, match="outside the reviewed"):
            repository.read("src/long.py", 1, 1)
        continued = repository.search(
            text="needle", cursor=result["next_cursor"], max_output_chars=4096
        )
        assert continued["matches"][0]["text"].startswith("needle ")
    finally:
        repository.close()


def test_scanned_no_match_does_not_authorize_read(tmp_path: Path) -> None:
    repository, root, _ = _repository(tmp_path)
    (root / "tree/repo/src/no_match.py").write_text(
        "ordinary content\n", encoding="utf-8"
    )
    try:
        result = repository.search(
            path_pattern="src/no_match.py", text="absent literal"
        )
        assert result["matches"] == []
        assert result["complete"] is True
        with pytest.raises(InputValidationError, match="outside the reviewed"):
            repository.read("src/no_match.py", 1, 1)
    finally:
        repository.close()


def test_wildcard_policy_exclusion_is_incomplete_and_counted(tmp_path: Path) -> None:
    repository, root, _ = _repository(tmp_path)
    (root / "tree/repo/.env.secret").write_text("hidden needle\n", encoding="utf-8")
    try:
        result = repository.search(path_pattern=".e*", text="needle")
        assert result["status"] == "incomplete"
        assert result["limit_reason"] == "access_denied"
        assert result["usage"]["excluded"] >= 1
        assert result["matches"] == []
    finally:
        repository.close()


def test_cursor_cache_is_finite_and_oldest_cursor_expires(tmp_path: Path) -> None:
    repository, root, _ = _repository(tmp_path, limits=SearchLimits(max_matches=1))
    (root / "tree/repo/src/cursors.py").write_text(
        "cursor hit one\ncursor hit two\n", encoding="utf-8"
    )
    try:
        cursors = [
            repository.search(text="cursor hit", run_id="bounded")["next_cursor"]
            for _ in range(129)
        ]
        expired = repository.search(
            text="cursor hit", cursor=cursors[0], run_id="bounded"
        )
        assert expired["error_code"] == "invalid_cursor"
        assert len(repository._cursors) == 128
    finally:
        repository.close()


def test_byte_limit_is_hard_and_each_page_reports_actual_reads(tmp_path: Path) -> None:
    repository, root, _ = _repository(tmp_path, limits=SearchLimits(max_bytes=6))
    limited = root / "tree/repo/limited"
    limited.mkdir()
    (limited / "a.txt").write_text("hit-a\n", encoding="utf-8")
    (limited / "b.txt").write_text("hit-b\n", encoding="utf-8")
    try:
        first = repository.search(path_pattern="limited/*.txt", text="hit")
        assert first["limit_reason"] == "byte_limit"
        assert first["usage"]["bytes"] == 6
        assert len(first["matches"]) == 1

        second = repository.search(
            path_pattern="limited/*.txt", text="hit", cursor=first["next_cursor"]
        )
        assert second["usage"]["bytes"] == 6
        assert len(second["matches"]) == 1
        assert second["complete"] is True
    finally:
        repository.close()


def test_cursor_and_authenticated_observation_are_bound_to_run(tmp_path: Path) -> None:
    repository, root, _ = _repository(tmp_path, limits=SearchLimits(max_matches=1))
    (root / "tree/repo/src/run.py").write_text("hit one\nhit two\n", encoding="utf-8")
    try:
        first = repository.search(text="hit", run_id="run-one")
        cross_run = repository.search(
            text="hit", cursor=first["next_cursor"], run_id="run-two"
        )
        assert cross_run["error_code"] == "invalid_cursor"
        match = first["matches"][0]
        source = SourceSlice(
            source_id=match["source_id"], logical_path=match["logical_path"],
            text=match["text"], symbol=None, start_line=match["start_line"],
            end_line=match["end_line"], resolution_status="resolved",
            target_precision="file",
        )
        assert repository.authenticate_observation(source, run_id="run-one") == source
        with pytest.raises(InputValidationError, match="content or identity changed"):
            repository.authenticate_observation(source, run_id="run-two")
    finally:
        repository.close()


def test_discovered_content_change_invalidates_whole_run(tmp_path: Path) -> None:
    repository, root, task = _repository(tmp_path)
    added = root / "tree/repo/src/changing.py"
    added.write_text("needle old\n", encoding="utf-8")
    try:
        result = repository.search(path_pattern="src/changing.py", text="needle")
        logical_path = result["matches"][0]["logical_path"]
        added.write_text("needle new\n", encoding="utf-8")
        with pytest.raises(InputValidationError, match="identity changed"):
            repository.read(logical_path, 1, 1)
        with pytest.raises(InputValidationError, match="evidence is invalid"):
            repository.validate_task(task)
        with pytest.raises(InputValidationError, match="evidence is invalid"):
            repository.search(text="needle")
    finally:
        repository.close()


def test_registered_file_disappearance_invalidates_instead_of_no_match(
    tmp_path: Path,
) -> None:
    repository, root, task = _repository(tmp_path)
    added = root / "tree/repo/src/disappears.py"
    added.write_text(
        "unique disappearance marker one\nunique disappearance marker two\n",
        encoding="utf-8",
    )
    try:
        assert repository.search(text="unique disappearance marker")["matches"]
        added.unlink()
        changed = repository.search(text="unique disappearance marker")
        assert changed["error_code"] == "content_changed"
        with pytest.raises(InputValidationError, match="evidence is invalid"):
            repository.validate_task(task)
    finally:
        repository.close()


def test_unregistered_read_failure_is_incomplete_not_empty_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository, root, _ = _repository(tmp_path)
    added = root / "tree/repo/src/unavailable.py"
    added.write_text("unavailable marker\n", encoding="utf-8")
    import mock_spec.source_repository as module

    original = module._read_regular_file

    def fail_selected(root_fd, relative, **kwargs):
        if relative.endswith("unavailable.py"):
            raise InputValidationError("missing reviewed file")
        return original(root_fd, relative, **kwargs)

    monkeypatch.setattr(module, "_read_regular_file", fail_selected)
    try:
        result = repository.search(
            path_pattern="src/unavailable.py", text="unavailable"
        )
        assert result["status"] == "incomplete"
        assert result["complete"] is False
        assert result["limit_reason"] == "content_unavailable"
        assert result["matches"] == []
    finally:
        repository.close()


@pytest.mark.parametrize(
    "limits",
    [
        SearchLimits(max_entries=1),
        SearchLimits(max_bytes=1),
        SearchLimits(max_file_bytes=1),
    ],
)
def test_scope_limits_never_report_complete(tmp_path: Path, limits: SearchLimits) -> None:
    repository, _, _ = _repository(tmp_path, limits=limits)
    try:
        result = repository.search(text="def")
        assert result["status"] == "incomplete"
        assert result["complete"] is False
        assert result["limit_reason"] in {
            "entry_limit", "byte_limit", "file_size_limit"
        }
    finally:
        repository.close()


def test_path_search_does_not_depend_on_reviewed_file_digest(tmp_path: Path) -> None:
    repository, root, _ = _repository(tmp_path)
    added = root / "tree/repo/docs/note.md"
    added.parent.mkdir(parents=True)
    payload = b"ordinary reviewed documentation\n"
    added.write_bytes(payload)
    try:
        result = repository.search(path_pattern="docs/*.md")
        match = result["matches"][0]
        assert match["logical_path"] == "docs/note.md"
        returned = repository.read("docs/note.md", 1, 1).text.encode()
        assert hashlib.sha256(returned).hexdigest() == hashlib.sha256(
            payload.rstrip()
        ).hexdigest()
    finally:
        repository.close()


def test_small_search_budget_does_not_disable_a_valid_explicit_read(tmp_path: Path) -> None:
    from mock_spec.validation import SourceStore

    repository, _, task = _repository(tmp_path, limits=SearchLimits(max_bytes=1))
    try:
        store = SourceStore(task, repository=repository)
        source = store.read(task.mut_source.logical_path, 1, 1)
        store.record_observation(source, response_id="bounded-read")
        assert store.quote(source.source_id, 1, 1) == "def target():"
        assert store.last_read_usage["bytes"] > 1
    finally:
        repository.close()
