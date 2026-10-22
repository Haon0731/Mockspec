"""Code and environment provenance; no provider or dataset calls."""

import hashlib
import subprocess

from mock_spec import execution_metadata


def test_git_identity_is_anchored_when_cwd_is_an_unrelated_repo(
    tmp_path, monkeypatch
) -> None:
    expected_identity = execution_metadata.collect_execution_metadata().code_commit
    expected_dirty = execution_metadata.collect_execution_metadata().code_dirty
    unrelated = tmp_path / "unrelated"
    unrelated.mkdir()
    subprocess.run(
        ["git", "init", "-q", str(unrelated)], check=True, capture_output=True
    )
    monkeypatch.chdir(unrelated)

    assert execution_metadata.collect_execution_metadata().code_commit == expected_identity
    assert execution_metadata.collect_execution_metadata().code_dirty == expected_dirty


def test_installed_source_outside_project_checkout_has_no_git_identity(
    tmp_path, monkeypatch
) -> None:
    installed = tmp_path / "site-packages/mock_spec/execution_metadata.py"
    installed.parent.mkdir(parents=True)
    installed.write_text("# installed wheel\n")
    monkeypatch.setattr(execution_metadata, "__file__", str(installed))

    assert execution_metadata.collect_execution_metadata().code_commit is None
    assert execution_metadata.collect_execution_metadata().code_dirty is None


def test_runtime_identity_marks_unavailable_spoon_toolchain(monkeypatch) -> None:
    from mock_spec import java_analysis

    monkeypatch.setattr(java_analysis, "_backend_paths", lambda: None)

    identity = execution_metadata.collect_execution_metadata().runtime_identity

    assert identity["python"]["implementation"]
    assert identity["python"]["version_detail"]
    assert identity["spoon_toolchain"]["available"] is False
    assert identity["spoon_toolchain"]["java"] is None
    assert identity["spoon_toolchain"]["limitations"]


def test_code_fingerprint_tracks_implementation_changes(tmp_path, monkeypatch) -> None:
    package = tmp_path / 'mock_spec'
    package.mkdir()
    implementation = package / 'inference_v02.py'
    implementation.write_text('before')
    monkeypatch.setattr(execution_metadata, '__file__', str(package / 'execution_metadata.py'))
    before = execution_metadata.collect_execution_metadata().code_files_sha256
    implementation.write_text('after')
    after = execution_metadata.collect_execution_metadata().code_files_sha256
    assert before['inference_v02.py'] != after['inference_v02.py']
    assert after['inference_v02.py'] == hashlib.sha256(b'after').hexdigest()


def test_git_probe_failure_remains_unknown(monkeypatch) -> None:
    def unavailable(*args, **kwargs):
        raise subprocess.TimeoutExpired('git', 2)

    monkeypatch.setattr(execution_metadata.subprocess, 'run', unavailable)
    assert execution_metadata.collect_execution_metadata().code_commit is None
    assert execution_metadata.collect_execution_metadata().code_dirty is None
