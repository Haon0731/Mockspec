"""Host evaluation hook: identity-check the real MUT, then apply declared mutants."""
import __future__
import json
import os
from pathlib import Path
import types

import pytest

CURRENT = None
FACTS = {'phases': [], 'identity': [], 'post_call_identity': [], 'mutant': os.environ.get('SLICE_MUTANT', 'original')}


def code_facts(code):
    return (code.co_code.hex(), tuple(code_facts(c) if isinstance(c, types.CodeType) else repr(c)
                                    for c in code.co_consts), code.co_names, code.co_varnames,
            code.co_argcount, code.co_kwonlyargcount, code.co_flags)


@pytest.fixture(autouse=True)
def check_and_mutate(request):
    global CURRENT
    import flask.helpers as helpers
    approved = json.loads(Path('/workspace/task.json').read_text())['mut_source']
    source = approved['text']
    compiled = compile(source, '<approved-mut>', 'exec', flags=__future__.annotations.compiler_flag,
                       dont_inherit=True)
    expected = next(c for c in compiled.co_consts if isinstance(c, types.CodeType) and c.co_name == 'abort')
    original_function = helpers.abort
    original = helpers.abort.__code__
    matching = code_facts(expected) == code_facts(original) and request.module.abort is helpers.abort
    FACTS['identity'].append(matching)
    if not matching:
        pytest.fail('MUT mismatch', pytrace=False)
    mutant = FACTS['mutant']
    if mutant in ('wrong_code', 'drop_kwargs') and source.count('(code, *args, **kwargs)') != 2:
        raise ValueError('mutation sites changed')
    if mutant == 'duplicate_lookup' and source.count('if (ctx :=') != 1:
        raise ValueError('mutation site changed')
    if mutant == 'wrong_code':
        source = source.replace('(code, *args, **kwargs)', '(code + 1, *args, **kwargs)')
    elif mutant == 'drop_kwargs':
        source = source.replace('(code, *args, **kwargs)', '(code, *args)')
    elif mutant == 'duplicate_lookup':
        source = source.replace('if (ctx :=', '_cv_app.get(None)\n    if (ctx :=')
    elif mutant != 'original':
        raise ValueError('unknown mutant')
    if mutant != 'original':
        modified = compile(source, '<declared-interaction-mutant>', 'exec',
                           flags=__future__.annotations.compiler_flag, dont_inherit=True)
        helpers.abort.__code__ = next(c for c in modified.co_consts
                                     if isinstance(c, types.CodeType) and c.co_name == 'abort')
    CURRENT = (helpers, request.module, original_function, original_function.__code__)
    try:
        yield
    finally:
        original_function.__code__ = original
        CURRENT = None


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    report = outcome.get_result()
    if report.when == 'call' and CURRENT is not None:
        helpers, module, function, expected_code = CURRENT
        FACTS['post_call_identity'].append(helpers.abort is function and module.abort is function
                                           and function.__code__ is expected_code)
    entry = {'nodeid': report.nodeid, 'when': report.when, 'outcome': report.outcome}
    if report.failed:
        entry['failure'] = str(getattr(report.longrepr, 'reprcrash', 'failure'))
    FACTS['phases'].append(entry)


def pytest_sessionfinish(session, exitstatus):
    FACTS['exitstatus'] = int(exitstatus)
    FACTS['collected'] = session.testscollected
    Path('/outputs/execution.json').write_text(json.dumps(FACTS, indent=2) + '\n')
