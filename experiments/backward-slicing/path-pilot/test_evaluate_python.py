import copy
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from evaluate_python import classify_run  # noqa: E402


def receipt():
    return {'collected': 5, 'identity': [True]*5, 'post_call_identity': [True]*5,
            'exitstatus': 0, 'mutant': 'original',
            'phases': [{'when': when, 'outcome': 'passed'}
                       for _ in range(5) for when in ('setup', 'call', 'teardown')]}


def test_process_observer_error_does_not_count_as_valid_pass():
    facts = receipt()
    assert classify_run(facts, 0, 'original')['valid']
    facts['exitstatus'] = 1
    assert not classify_run(facts, 1, 'original')['valid']
    assert not classify_run(facts, 3, 'original')['valid']
    facts['exitstatus'] = 3
    assert not classify_run(facts, 3, 'original')['valid']


def test_wrong_mutant_or_changed_mut_identity_is_rejected():
    facts = receipt()
    assert not classify_run(facts, 0, 'wrong_code')['valid']
    changed = copy.deepcopy(facts)
    changed['post_call_identity'][0] = False
    assert not classify_run(changed, 0, 'original')['valid']
