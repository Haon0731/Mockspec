import sys
from pathlib import Path
import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from negative_checks import run
from models import java_image
from poc import abduction, bounded_abduction, check_candidate


@pytest.fixture(scope='module')
def results():
    return run()


@pytest.mark.parametrize('name', ['contradictory_h','nullable_java_integer','unused_java_response',
    'no_exception_observed','uncalled_fallback_cannot_supply_message','citation_valid_but_wrong_version_constraint'])
def test_unsound_candidate_not_sufficient(results, name):
    assert not results[name]['locally_sufficient']


@pytest.mark.parametrize('name', ['none_reaches_fallback','abort_throw_blocks_fallback',
    'abort_return_reaches_fallback','falsey_non_none_still_calls_aborter','syntactically_distinct_equivalent'])
def test_relation_has_no_counterexample(results, name):
    assert results[name]['status'] == 'unsat'


@pytest.mark.parametrize('name', ['version_7_also_sufficient','custom_list_can_equal_expected','same_message_from_fallback'])
def test_alternatives_preserved(results, name):
    assert results[name]['locally_sufficient']


def test_unknown_external_semantics_not_source_proof(results):
    r = results['default_not_proven_globally_necessary']
    assert r['status'] == 'sat' and 'abstract_countermodel_only' in r['interpretation']
    assert results['external_path_without_favorable_assumption']['status'] == 'sat'
    assert results['getters_not_implicitly_stable']['status'] == 'sat'


def test_forbidden_grammar_symbols(results):
    for name in ('java8_selected','external_image_matches','image_observation_reached'):
        assert results['forbidden_h_'+name]['status'] == 'rejected'


def test_sequence_and_count_representation(results):
    r = results['synthetic_representation_test']
    assert r['sequence_consistent']['status'] == 'sat'
    assert r['second_call_after_first_throws']['status'] == 'unsat'


def test_unsat_serialization_round_trip(results):
    assert results['unsat_round_trip']['matches']
    assert results['unsat_round_trip']['round_trip'] == 'unsat'


def test_inconsistent_abduction_background_blocked():
    m = java_image()
    m.background.append(m.tm.mkBoolean(False))
    assert abduction(m)['status'] == 'blocked_background_not_sat'


def test_outer_timeout_is_distinct():
    # Actual subprocess timeout forced before solver initialization, not a solver performance claim.
    assert bounded_abduction('java_image', timeout=0.000001)['status'] == 'timeout'


def test_solver_unknown_retains_reason(results):
    assert results['solver_unknown']['status'] == 'unknown'
    assert 'RESOURCEOUT' in results['solver_unknown']['reason']
    assert results['process_timeout']['status'] == 'timeout'


def test_candidate_checker_rejects_direct_goal_assignment():
    m = java_image()
    with pytest.raises(ValueError, match='noncontrollable'):
        check_candidate(m, m.goal)
