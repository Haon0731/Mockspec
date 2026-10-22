import asyncio
from copy import deepcopy
from pathlib import Path
import sys

import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from inputs import load_python
from local_host import LocalResearchHost


@pytest.fixture(scope='module')
def host():
    return asyncio.run(LocalResearchHost.prepare('python'))


def proposal(host):
    catalog = host.catalog()['extraction']
    pattern = next(p for p in catalog['patterns'] if p['support_status'] == 'supported')
    return {'source_id': catalog['source_id'], 'pattern_id': pattern['pattern_id'],
            'question': 'check_local_branch', 'goal_branch': True,
            'candidate': [{'kind': 'condition', 'value': True}]}


def test_same_terms_bound_and_tampered_formula_rejected(host):
    result = asyncio.run(host.propose(proposal(host)))
    assert result['solver']['locally_sufficient']
    contract = host.contract(result['check_id'])
    assert host.bind_contract(result['check_id'], contract)['binding'] == 'exact_host_model_and_solver_terms'
    bad = deepcopy(contract)
    bad['formulas'] = {'candidate': 'false'}
    with pytest.raises(ValueError, match='mismatch'):
        host.bind_contract(result['check_id'], bad)
    result['model']['term']['operator'] = 'is_none'
    assert host.bind_contract(result['check_id'], contract)['check']['model'] != result['model']


@pytest.mark.parametrize('field', ['oracle', 'mut_output', 'external_axiom', 'path', 'script', 'formula', 'terms'])
def test_arbitrary_fields_rejected(host, field):
    request = proposal(host)
    request[field] = 'favorable'
    with pytest.raises(ValueError):
        asyncio.run(host.propose(request))


@pytest.mark.parametrize('kind', ['oracle', 'mut_output', 'external_semantics', 'throw'])
def test_noncontrollable_candidate_rejected(host, kind):
    request = proposal(host)
    request['candidate'] = [{'kind': kind, 'value': True}]
    with pytest.raises(ValueError):
        asyncio.run(host.propose(request))


def test_contradiction_not_sufficient(host):
    request = proposal(host)
    request['candidate'].append({'kind': 'condition', 'value': False})
    result = asyncio.run(host.propose(request))
    assert result['solver']['consistency']['status'] == 'unsat'
    assert not result['solver']['locally_sufficient']


def test_unknown_reference_and_mock_configuration_rejected(host):
    request = proposal(host)
    request['source_id'] = '/etc/passwd'
    with pytest.raises(ValueError):
        asyncio.run(host.propose(request))
    request = proposal(host)
    request['question'] = 'configure_mock'
    with pytest.raises(ValueError):
        asyncio.run(host.propose(request))


def test_empty_candidate_retains_counterexample(host):
    request = proposal(host)
    request['candidate'] = []
    result = asyncio.run(host.propose(request))
    assert result['solver']['consistency']['status'] == 'sat'
    assert result['solver']['counterexample']['status'] == 'sat'
    assert not result['solver']['locally_sufficient']


def test_admission_cannot_be_claimed_by_supplied_task():
    with pytest.raises((ValueError, TypeError)):
        asyncio.run(LocalResearchHost.prepare(load_python()))
    reviewed = load_python()
    reviewed.source_sha256['mut'] = '0' * 64
    with pytest.raises(ValueError, match='identity'):
        asyncio.run(LocalResearchHost.prepare_synthetic(reviewed))
