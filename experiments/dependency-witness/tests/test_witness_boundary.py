"""Host-boundary regressions; these never execute the target or launch Docker."""
from copy import deepcopy
import json
from pathlib import Path
import sys

import pytest

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
from run_witness import validate_execution, candidate_fragments, expected_candidate, slot_text

RESULTS = HERE/'results'


def evidence(name):
    return json.loads((RESULTS/name/'runtime/execution.json').read_text())


def test_recorded_pass_and_deliberate_failure_have_separate_meanings():
    assert validate_execution(evidence('witness'), True, 0)['status'] == 'observed_as_predicted'
    assert validate_execution(evidence('diagnostic'), False, 1)['status'] == 'observed_as_predicted'
    assert validate_execution(evidence('diagnostic'), True, 1)['status'] == 'not_established'


@pytest.mark.parametrize('mutation', ['wrong_line','wrong_args','wrong_row','mut_replaced','no_calls','wrong_failure'])
def test_incomplete_or_mismatched_evidence_cannot_pass(mutation):
    throws = mutation != 'wrong_failure'
    record = deepcopy(evidence('witness' if throws else 'diagnostic'))
    if mutation == 'wrong_line':
        record['observations'][0]['mut_exception_frames'][0]['line'] = 282
    elif mutation == 'wrong_args':
        record['observations'][0]['fallback_calls'][0]['args'] = [404]
    elif mutation == 'wrong_row':
        record['selection']['params']['code'] = 404
    elif mutation == 'mut_replaced':
        record['observations'][0]['mut_identity_preserved'] = False
    elif mutation == 'no_calls':
        record['observations'][0]['context_query_calls'] = []
    else:
        next(p for p in record['phases'] if p['when']=='call')['failure_message'] = 'unrelated failure'
    assert validate_execution(record, throws, 0 if throws else 1)['status'] == 'not_established'


def test_only_fallback_response_differs_between_candidates():
    witness, diagnostic = candidate_fragments(True), candidate_fragments(False)
    assert witness.keys() == diagnostic.keys()
    differences = [key for key in witness if witness[key] != diagnostic[key]]
    assert differences == ['mock_stub_001']
    assert witness['mock_stub_001'].splitlines()[:2] == diagnostic['mock_stub_001'].splitlines()[:2]
    assert slot_text(witness, 'verify_') == slot_text(diagnostic, 'verify_')


def test_original_input_and_assertions_are_preserved_outside_slots():
    task = json.loads((HERE/'generation-inputs/A/task.json').read_text())
    masked = task['masked_test']['text']
    for name, throws in [('witness', True), ('diagnostic', False)]:
        actual = (RESULTS/name/'candidate.py').read_text()
        assert actual == expected_candidate(masked, candidate_fragments(throws))


def test_future_input_b_has_same_task_and_no_execution_or_patch_code():
    inputs = HERE/'generation-inputs'
    assert (inputs/'A/task.json').read_bytes() == (inputs/'B/task.json').read_bytes()
    guidance = (inputs/'B/guidance.json').read_text()
    assert 'human_curated' in guidance
    for forbidden in ('mocker.patch', 'RuntimeError', 'flask.helpers.', 'observed_as_predicted', '1 passed'):
        assert forbidden not in guidance
