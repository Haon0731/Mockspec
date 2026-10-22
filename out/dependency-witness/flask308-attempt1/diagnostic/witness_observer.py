"""Host-owned pytest observer: select one original row, inspect Mock records only.

Executed inside the read-only target container. Does not patch the MUT, catch its
exceptions, inspect dependency source, or change the ordinary test assertions.
"""
import __future__
import importlib.metadata
import json
from pathlib import Path
import sys
import types
from unittest.mock import Mock

import pytest

ROOT = Path('/workspace')
OUTPUT = Path('/outputs')
FACTS = {'origin': 'host_pytest_observer', 'phases': [], 'observations': []}


def _selected(params):
    expected = json.loads((ROOT/'selected_row.json').read_text())
    expected['args'] = tuple(expected['args'])
    return (params == expected and params.get('has_app_context') is False and type(params.get('code')) is int
            and params['code'] == 403 and params.get('args') == ()
            and params.get('kwargs') == {})


def pytest_collection_modifyitems(config, items):
    selected = [item for item in items if hasattr(item, 'callspec')
                and _selected(item.callspec.params)]
    if len(selected) != 1 or len(items) != 5:
        raise pytest.UsageError('Expected exactly one specified parameter row among five original rows')
    deselected = [item for item in items if item not in selected]
    FACTS['selection'] = {'selected': [i.nodeid for i in selected],
                          'deselected': [i.nodeid for i in deselected],
                          'params': selected[0].callspec.params,
                          'criterion': 'exact source-derived row; scenario label used only for identity'}
    items[:] = selected
    config.hook.pytest_deselected(items=deselected)


def _code_facts(code):
    # Compare only the admitted MUT, without inspect.getsource or file reads.
    # Code filename differs between slice compilation and the loaded module.
    def constant(value):
        if isinstance(value, types.CodeType):
            return _code_facts(value)
        return repr(value)
    return {'bytecode': code.co_code.hex(), 'constants': [constant(v) for v in code.co_consts],
            'names': code.co_names, 'varnames': code.co_varnames,
            'argcount': code.co_argcount, 'kwonlyargcount': code.co_kwonlyargcount,
            'posonlyargcount': code.co_posonlyargcount, 'flags': code.co_flags,
            'freevars': code.co_freevars, 'cellvars': code.co_cellvars}


def _calls(mock):
    return [{'args': list(call.args), 'kwargs': call.kwargs} for call in mock.call_args_list]


@pytest.fixture(autouse=True)
def observe_witness(mocker, request):
    import flask.helpers as helpers

    approved = json.loads((ROOT/'approved_mut.json').read_text())
    compiled = compile('\n' * (approved['start_line'] - 1) + approved['text'],
                       '<approved-mut>', 'exec', flags=__future__.annotations.compiler_flag,
                       dont_inherit=True)
    expected = next(c for c in compiled.co_consts
                    if isinstance(c, types.CodeType) and c.co_name == approved['symbol'].split('.')[-1])
    original = helpers.abort
    original_context = helpers._cv_app
    original_fallback = helpers._wz_abort
    matching = _code_facts(original.__code__) == _code_facts(expected)
    FACTS['runtime'] = {'python': sys.version, 'executable': sys.executable,
                        'pytest': importlib.metadata.version('pytest'),
                        'pytest_mock': importlib.metadata.version('pytest-mock'),
                        'flask_module_file': helpers.__file__,
                        'mut_start_line': original.__code__.co_firstlineno,
                        'mut_code_matches_admitted_slice': matching,
                        'original_test_binding_is_mut': request.module.abort is original,
                        'mock_fixture_type': type(mocker).__module__ + '.' + type(mocker).__name__}
    if not matching or request.module.abort is not original or not helpers.__file__.startswith('/target/'):
        pytest.fail('Runtime MUT identity does not match the approved input', pytrace=False)
    yield
    lookup, fallback = helpers._cv_app, helpers._wz_abort
    observation = {'mut_identity_preserved': helpers.abort is original and request.module.abort is original,
                   'mut_globals_are_helpers': original.__globals__ is vars(helpers),
                   'context_binding_replaced': lookup is not original_context and isinstance(lookup, Mock),
                   'fallback_binding_replaced': fallback is not original_fallback and isinstance(fallback, Mock)}
    if isinstance(lookup, Mock) and isinstance(fallback, Mock):
        query = lookup.get
        exception = fallback.side_effect
        mut_frames = []
        if isinstance(exception, BaseException):
            tb = exception.__traceback__
            while tb:
                if tb.tb_frame.f_code is original.__code__:
                    mut_frames.append({'line': tb.tb_lineno, 'ctx_is_none': tb.tb_frame.f_locals.get('ctx') is None,
                                       'code': tb.tb_frame.f_locals.get('code'),
                                       'args': tb.tb_frame.f_locals.get('args'),
                                       'kwargs': tb.tb_frame.f_locals.get('kwargs')})
                tb = tb.tb_next
        observation.update({'context_query_calls': _calls(query),
                            'context_query_return_is_none': query.return_value is None,
                            'fallback_calls': _calls(fallback),
                            'fallback_return_is_none': fallback.return_value is None if exception is None else None,
                            'configured_exception': None if exception is None else {
                                'type': type(exception).__name__, 'message': str(exception)},
                            'raised_exception_has_mut_traceback': bool(mut_frames),
                            'mut_exception_frames': mut_frames,
                            'context_mock_calls': [str(c) for c in lookup.mock_calls]})
    FACTS['observations'].append(observation)


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    report = outcome.get_result()
    entry = {'when': report.when, 'outcome': report.outcome, 'nodeid': report.nodeid}
    if report.failed:
        # Deliberately avoid formatting traceback source from runtime-only libraries.
        crash = getattr(report.longrepr, 'reprcrash', None)
        entry['failure_message'] = crash.message if crash is not None else 'pytest phase failed'
    FACTS['phases'].append(entry)


def pytest_sessionfinish(session, exitstatus):
    FACTS['pytest_exitstatus'] = int(exitstatus)
    (OUTPUT/'execution.json').write_text(json.dumps(FACTS, indent=2) + '\n')
