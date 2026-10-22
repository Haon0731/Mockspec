"""Synthetic mechanism regressions: actual parsers -> host -> cvc5, no dataset score."""
import asyncio
from pathlib import Path
import sys

import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from inputs import _wrap
from local_host import LocalResearchHost
from mock_spec.schemas import Source, SourceIndex, Interface, ExecutionContext, InputPolicy, UnifiedTask


def fixture(language, text, method='readLevel'):
    def source(sid, code):
        return Source(sid, f'{sid}.txt', code, 'Synthetic', 1, len(code.splitlines()),
                      'resolved', 'class' if language == 'java' else 'function')
    mut = source('mut', text)
    test = source('test', '// synthetic only' if language == 'java' else '# synthetic only')
    support = source('support', f'class Dependency {{\n public Integer {method}() {{ throw new UnsupportedOperationException(); }}\n}}')
    supports = (support,) if language == 'java' else ()
    interfaces = (Interface(f'Dependency.{method}', f'public Integer {method}()',
                            'support', 2, 2, 'resolved'),) if language == 'java' else ()
    task = UnifiedTask(UnifiedTask.SCHEMA_VERSION, language, 'synthetic_mechanism_only',
                       mut, test, interfaces, supports,
                       ExecutionContext(language, 'synthetic', 'none', None),
                       InputPolicy('synthetic', 'synthetic', 'synthetic', 'reviewed_frozen_sources'),
                       tuple(SourceIndex(s.source_id, s.logical_path, role)
                             for s, role in [(mut, 'mut'), (test, 'masked_test'),
                                             *[(s, 'supporting') for s in supports]]))
    return _wrap(task)


def execute(reviewed, candidate):
    async def run():
        host = await LocalResearchHost.prepare_synthetic(reviewed)
        catalog = host.catalog()['extraction']
        assert catalog['parser_status'] != 'error', catalog
        pattern = next(p for p in catalog['patterns'] if p['support_status'] == 'supported')
        result = await host.propose({'source_id': 'mut', 'pattern_id': pattern['pattern_id'],
                                     'question': 'check_local_branch', 'goal_branch': True,
                                     'candidate': candidate})
        return host.bind_contract(result['check_id'], host.contract(result['check_id']))
    return asyncio.run(run())


def java(method, variable, op, threshold):
    return fixture('java', 'class Target {\n'
                   f' Object select(Dependency {variable}) {{\n'
                   f'  return ({variable}.{method}() {op} {threshold}) ? "yes" : "no";\n'
                   ' }\n}', method)


def test_real_spoon_source_changes_flow_into_checked_terms():
    from mock_spec.java_analysis import _backend_paths
    if _backend_paths() is None:
        pytest.skip('pinned Spoon unavailable')
    original = execute(java('readLevel', 'service', '<=', 4), [{'kind':'return_integer','value':4}])
    renamed = execute(java('readOther', 'renamed', '<=', 9), [{'kind':'return_integer','value':9}])
    flipped = execute(java('readOther', 'renamed', '>', 9), [{'kind':'return_integer','value':9}])
    assert original['check']['solver']['locally_sufficient']
    assert renamed['check']['solver']['locally_sufficient']
    assert not flipped['check']['solver']['locally_sufficient']
    assert original['contract']['formulas']['goal'] == '(<= response 4)'
    assert renamed['contract']['formulas']['goal'] == '(<= response 9)'
    assert flipped['contract']['formulas']['goal'] == '(> response 9)'
    assert original['contract']['model_id'] != renamed['contract']['model_id']


@pytest.mark.parametrize('op,sufficient', [('is None', True), ('is not None', False)])
def test_real_ast_local_rename_and_identity_reach_solver(op, sufficient):
    reviewed = fixture('python', 'def target(provider):\n'
                       '    renamed = provider.changed_lookup()\n'
                       f'    if renamed {op}:\n        return 1\n    return 0')
    bound = execute(reviewed, [{'kind':'return_is_none','value':True}])
    assert bound['check']['solver']['locally_sufficient'] is sufficient
    assert bound['check']['model']['input']['admission'] == 'synthetic_mechanism_fixture'


def test_complex_call_condition_explicitly_downgrades():
    reviewed = fixture('python', 'def target(provider, ready):\n'
                       '    if provider.lookup() is None and ready:\n        return 1')
    host = asyncio.run(LocalResearchHost.prepare_synthetic(reviewed))
    extraction = host.catalog()['extraction']
    assert extraction['status'] == 'incomplete'
    assert extraction['patterns'] == []
    assert any(d['code'] == 'unsupported_python_condition' for d in extraction['diagnostics'])
