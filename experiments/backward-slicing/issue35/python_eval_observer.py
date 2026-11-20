"""Pytest observer for #35 Python reference and candidate executions."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sys
import time
import types

import pytest


OUTPUT = Path("/outputs")
PARTIAL = OUTPUT / "execution.partial.json"
FACTS = {
    "schema": "mock-spec.issue35-python-execution/v1",
    "case_key": os.environ["MOCKSPEC_CASE_KEY"],
    "variant": os.environ["MOCKSPEC_VARIANT"],
    "mutant": os.environ["MOCKSPEC_MUTANT"],
    "test_sha256": os.environ["MOCKSPEC_TEST_SHA256"],
    "mut_sha256": os.environ["MOCKSPEC_MUT_SHA256"],
    "phases": [],
    "row_events": [],
}
TARGET_NAME = os.environ["MOCKSPEC_MUT_FUNCTION"]
TARGET_PATH = os.environ["MOCKSPEC_MUT_PATH"]
FRAME_FILENAME = os.environ["MOCKSPEC_FRAME_FILENAME"]
LOADER = os.environ["MOCKSPEC_MUT_LOADER"]
START_LINE = int(os.environ["MOCKSPEC_MUT_START_LINE"])
END_LINE = int(os.environ["MOCKSPEC_MUT_END_LINE"])
EXPECTED_CODE_SHA256 = None


def _find_code(code: types.CodeType, name: str) -> types.CodeType:
    matches = [
        nested for nested in code.co_consts
        if isinstance(nested, types.CodeType) and nested.co_name == name
    ]
    for nested in (item for item in code.co_consts if isinstance(item, types.CodeType)):
        try:
            matches.append(_find_code(nested, name))
        except LookupError:
            pass
    if len(matches) != 1:
        raise LookupError(name)
    return matches[0]


def _code_sha256(code: types.CodeType) -> str:
    def facts(item: types.CodeType):
        return (
            item.co_code.hex(),
            tuple(facts(value) if isinstance(value, types.CodeType) else repr(value)
                  for value in item.co_consts),
            item.co_names,
            item.co_varnames,
            item.co_argcount,
            item.co_kwonlyargcount,
            item.co_flags,
        )

    encoded = repr(facts(code)).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _sha256(path: str) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _persist(path: Path = PARTIAL) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(json.dumps(FACTS, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def pytest_configure(config: pytest.Config) -> None:
    global EXPECTED_CODE_SHA256
    test_path = os.environ["MOCKSPEC_TEST_PATH"]
    mut_path = os.environ["MOCKSPEC_MUT_PATH"]
    FACTS["identity"] = {
        "test_path": test_path,
        "test_sha256_matches": _sha256(test_path) == FACTS["test_sha256"],
        "mut_path": mut_path,
        "mut_sha256_matches": _sha256(mut_path) == FACTS["mut_sha256"],
    }
    source = Path(mut_path).read_text(encoding="utf-8")
    if LOADER == "module":
        compiled_source = source
    elif LOADER == "slice":
        lines = source.splitlines()
        compiled_source = "\n" * (START_LINE - 1) + "\n".join(lines[START_LINE - 1 : END_LINE])
    else:
        raise ValueError("unknown MUT loader")
    compiled = compile(compiled_source, FRAME_FILENAME, "exec", dont_inherit=True)
    EXPECTED_CODE_SHA256 = _code_sha256(_find_code(compiled, TARGET_NAME))
    FACTS["identity"]["expected_mut_code_sha256"] = EXPECTED_CODE_SHA256
    FACTS["pytest_started_at"] = time.time()
    _persist()


@pytest.fixture(autouse=True)
def observe_mut_frame(request: pytest.FixtureRequest):
    seen: list[dict[str, object]] = []

    def profile(frame, event, arg):
        if event == "call" and frame.f_code.co_name == TARGET_NAME:
            code_sha256 = _code_sha256(frame.f_code)
            seen.append({
                "nodeid": request.node.nodeid,
                "function": frame.f_code.co_name,
                "filename": frame.f_code.co_filename,
                "code_sha256": code_sha256,
                "filename_matches": (
                    frame.f_code.co_filename == FRAME_FILENAME
                    and (Path("/target") / frame.f_code.co_filename).resolve().as_posix()
                    == TARGET_PATH
                    if not Path(frame.f_code.co_filename).is_absolute()
                    else frame.f_code.co_filename == TARGET_PATH
                ),
                "variant_matches": code_sha256 == EXPECTED_CODE_SHA256,
            })
        return profile

    sys.setprofile(profile)
    try:
        yield
    finally:
        sys.setprofile(None)
        FACTS.setdefault("mut_frames", []).extend(seen)
        _persist()


def pytest_runtest_logstart(nodeid: str, location: tuple[str, int | None, str]) -> None:
    FACTS["row_events"].append({"nodeid": nodeid, "event": "started", "at": time.time()})
    _persist()


def pytest_runtest_logfinish(nodeid: str, location: tuple[str, int | None, str]) -> None:
    FACTS["row_events"].append({"nodeid": nodeid, "event": "finished", "at": time.time()})
    _persist()


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item: pytest.Item, call: pytest.CallInfo):
    outcome = yield
    report = outcome.get_result()
    entry = {"nodeid": report.nodeid, "when": report.when, "outcome": report.outcome}
    if report.failed:
        entry["failure"] = str(getattr(report.longrepr, "reprcrash", report.longrepr))
        if call.excinfo is not None:
            entry["exception_type"] = call.excinfo.type.__name__
    FACTS["phases"].append(entry)
    _persist()


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    FACTS["exitstatus"] = int(exitstatus)
    FACTS["collected"] = session.testscollected
    FACTS["pytest_finished_at"] = time.time()
    _persist(OUTPUT / "execution.json")
