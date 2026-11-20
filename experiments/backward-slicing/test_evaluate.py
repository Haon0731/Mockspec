import json

import pytest
import eval_observer
from evaluate import (candidate_variants, cleanup_timed_out_container,
                      generation_failure_receipt, inject, observer_summary,
                      prepare_output_dir, run_with_separate_timeouts,
                      subprocess_text, run, summarize_execution)


def test_inject_preserves_every_non_slot_character_and_rejects_extra_slot():
    names = [f'mock_stub_{i:03}' for i in range(1, 7)] + [f'verify_{i:03}' for i in range(1, 5)]
    parts = [f'    # === PY-SLOT-START:{name} ===\n    pass\n    # === PY-SLOT-END:{name} ==='
             for name in names]
    source = 'def test_example():\n' + '\n    assert 1 == 1\n'.join(parts) + '\n    assert True\n'
    slots = {name: 'x = 42' for name in names}
    expected = 'def test_example():\n' + '\n    assert 1 == 1\n'.join(['    x = 42']*10) + '\n    assert True\n'
    assert inject(source, slots) == expected
    with pytest.raises(ValueError, match='slot mismatch'):
        inject(source, {**slots, 'extra': 'pass'})


def test_candidate_variants_preserve_default_and_require_three_custom_candidates(tmp_path):
    assert candidate_variants(tmp_path) == ['a', 'b']
    for variant in ('a', 'b'):
        (tmp_path/f'generation-{variant}.json').write_text('{}')
    with pytest.raises(ValueError, match='generation-c.json'):
        candidate_variants(tmp_path, explicit_candidate_dir=True)
    (tmp_path/'generation-c.json').write_text('{}')
    (tmp_path/'generation-metrics.json').write_text('{}')
    assert candidate_variants(tmp_path, explicit_candidate_dir=True) == ['a', 'b', 'c']


def test_failed_generation_trace_can_replace_missing_candidate_without_fabricating_one(tmp_path):
    for variant in ('b', 'c'):
        (tmp_path/f'generation-{variant}.json').write_text('{}')
    trace = {
        'prompt_sha256': 'a' * 64,
        'turns': [{'response_id': 'response-a', 'resolved_model': 'model',
                   'status': 'incomplete', 'text': ''}],
        'status': 'failed', 'error_type': 'ValueError', 'status_code': None,
        'source_calls': 3, 'elapsed_seconds': 12.5,
    }
    (tmp_path/'trace-a.json').write_text(json.dumps(trace))
    assert candidate_variants(tmp_path, explicit_candidate_dir=True) == ['a', 'b', 'c']
    receipt = generation_failure_receipt(tmp_path/'trace-a.json')
    assert receipt['failure_category'] == 'generation_failure'
    assert receipt['terminal_turn']['text_empty'] is True
    assert not (tmp_path/'generation-a.json').exists()


def test_missing_candidate_requires_verified_failure_trace(tmp_path):
    for variant in ('b', 'c'):
        (tmp_path/f'generation-{variant}.json').write_text('{}')
    (tmp_path/'trace-a.json').write_text('{"status":"failed"}')
    with pytest.raises(ValueError, match='unverifiable generation failure trace'):
        candidate_variants(tmp_path, explicit_candidate_dir=True)


def test_summarize_execution_keeps_parameter_rows_and_classifies_candidate_failures():
    calls = [
        {'nodeid': f'test_308abort.py::test_abort[{index}]', 'when': 'call',
         'outcome': 'failed' if index == 2 else 'passed',
         **({'failure': 'assert expected call', 'exception_type': 'AssertionError'}
            if index == 2 else {})}
        for index in range(5)
    ]
    facts = {
        'collected': 5,
        'exitstatus': 1,
        'identity': [True] * 5,
        'post_call_identity': [True] * 5,
        'phases': calls,
    }
    summary = summarize_execution(facts, exit_code=1)
    assert summary['valid_execution'] is True
    assert summary['failure_category'] == 'assertion_failure'
    assert summary['passed'] == 4
    assert summary['failed'] == 1
    assert summary['parameter_rows'][2] == {
        'nodeid': 'test_308abort.py::test_abort[2]',
        'outcome': 'failed',
        'failure': 'assert expected call',
        'exception_type': 'AssertionError',
    }


def test_summarize_execution_distinguishes_invalid_observer_evidence():
    summary = summarize_execution({'collected': 0, 'phases': []}, exit_code=4)
    assert summary['valid_execution'] is False
    assert summary['failure_category'] == 'container_or_framework_failure'
    assert summary['parameter_rows'] == []


def test_summarize_execution_classifies_non_assertion_candidate_failure_as_configuration():
    calls = [
        {'nodeid': f'test_308abort.py::test_abort[{index}]', 'when': 'call',
         'outcome': 'failed' if index == 0 else 'passed',
         **({'failure': 'TypeError: bad mock setup', 'exception_type': 'TypeError'}
            if index == 0 else {})}
        for index in range(5)
    ]
    facts = {'collected': 5, 'identity': [True] * 5,
             'post_call_identity': [True] * 5, 'phases': calls, 'exitstatus': 1}
    assert summarize_execution(facts, exit_code=1)['failure_category'] == 'configuration_failure'


def test_summarize_execution_rejects_exit_code_that_disagrees_with_pytest_rows():
    calls = [{'nodeid': f'test[{index}]', 'when': 'call', 'outcome': 'passed'}
             for index in range(5)]
    facts = {'collected': 5, 'identity': [True] * 5,
             'post_call_identity': [True] * 5, 'phases': calls, 'exitstatus': 0}
    summary = summarize_execution(facts, exit_code=1)
    assert summary['valid_execution'] is False
    assert summary['exit_code_consistent'] is False
    assert summary['failure_category'] == 'container_or_framework_failure'


def test_prepare_output_dir_protects_default_evidence_and_all_explicit_paths(tmp_path):
    default_output = tmp_path/'default'
    default_output.mkdir()
    (default_output/'eval-a').mkdir()
    with pytest.raises(FileExistsError, match='historical evaluation evidence'):
        prepare_output_dir(default_output, ['a', 'b'])

    fresh_explicit = tmp_path/'fresh-explicit'
    prepare_output_dir(fresh_explicit, ['a', 'b'], explicit_output_dir=True)
    assert fresh_explicit.is_dir()
    with pytest.raises(FileExistsError):
        prepare_output_dir(fresh_explicit, ['a', 'b'], explicit_output_dir=True)


def test_custom_candidate_directory_requires_independent_output_directory(tmp_path):
    with pytest.raises(ValueError, match='--candidate-dir requires'):
        run(tmp_path, candidate_dir=tmp_path/'candidates')


@pytest.mark.parametrize('content,error_type', [(None, 'FileNotFoundError'), ('{', 'JSONDecodeError')])
def test_observer_summary_records_missing_or_malformed_evidence(tmp_path, content, error_type):
    execution = tmp_path/'execution.json'
    if content is not None:
        execution.write_text(content)
    summary = observer_summary(execution, exit_code=3)
    assert summary['failure_category'] == 'observer_output_failure'
    assert summary['error_type'] == error_type


def test_timeout_cleanup_failure_is_returned_instead_of_raised(monkeypatch):
    def fail_cleanup(*args, **kwargs):
        raise RuntimeError('docker unavailable')

    monkeypatch.setattr('evaluate.subprocess.run', fail_cleanup)
    assert cleanup_timed_out_container('container-name') == {
        'type': 'RuntimeError', 'message': 'docker unavailable'}


def test_subprocess_text_decodes_timeout_bytes():
    assert subprocess_text(b'partial output') == 'partial output'


def test_separate_timeouts_start_pytest_budget_after_partial_receipt(tmp_path, monkeypatch):
    output = tmp_path/'output'
    output.mkdir()
    partial = {'collected': 1, 'phases': [], 'identity': [],
               'post_call_identity': [], 'row_events': [
                   {'nodeid': 'test[row]', 'event': 'started', 'at': 1.0}]}
    calls = []

    def fake_output(command, timeout):
        calls.append((command, timeout))
        if command[1] == 'start':
            (output/'execution.partial.json').write_text(json.dumps(partial))
            return type('Result', (), {'returncode': 0, 'stdout': 'container\n', 'stderr': ''})()
        if command[1] == 'wait':
            raise __import__('subprocess').TimeoutExpired(command, timeout)
        return type('Result', (), {'returncode': 0, 'stdout': 'container\n', 'stderr': ''})()

    monkeypatch.setattr('evaluate.docker_output', fake_output)
    monkeypatch.setattr('evaluate.cleanup_timed_out_container', lambda container: None)
    command = ['docker', 'run', '--rm', '--name', 'container', 'image', 'pytest']
    summary, stdout, stderr = run_with_separate_timeouts(command, 'container', output)
    assert summary['failure_category'] == 'pytest_timeout'
    assert summary['parameter_rows'] == []
    assert summary['timings']['pytest_timeout_seconds'] == 60
    assert any(call[0][1] == 'wait' and call[1] == 60 for call in calls)


def test_separate_timeouts_reports_startup_timeout_without_pytest_marker(tmp_path, monkeypatch):
    output = tmp_path/'output'
    output.mkdir()

    def fake_output(command, timeout):
        if command[1] == 'inspect':
            return type('Result', (), {'returncode': 0, 'stdout': 'false\n', 'stderr': ''})()
        return type('Result', (), {'returncode': 0, 'stdout': 'container\n', 'stderr': ''})()

    monkeypatch.setattr('evaluate.docker_output', fake_output)
    monkeypatch.setattr('evaluate.cleanup_timed_out_container', lambda container: None)
    command = ['docker', 'run', '--rm', '--name', 'container', 'image', 'pytest']
    summary, stdout, stderr = run_with_separate_timeouts(command, 'container', output)
    assert summary['failure_category'] == 'container_startup_failure'
    commands = json.loads((output/'lifecycle-commands.json').read_text())
    assert commands[0]['command'][:2] == ['docker', 'create']
    assert commands[-1]['command'] == ['docker', 'rm', '-f', 'container']


def test_observer_persists_pytest_and_row_start_finish_events(tmp_path, monkeypatch):
    partial = tmp_path/'execution.partial.json'
    monkeypatch.setattr(eval_observer, 'PARTIAL', partial)
    monkeypatch.setattr(eval_observer, 'FACTS', {
        'phases': [], 'identity': [], 'post_call_identity': [], 'mutant': 'original',
    })
    eval_observer.pytest_configure(None)
    eval_observer.pytest_runtest_logstart('test.py::test_case[row]', None)
    eval_observer.pytest_runtest_logfinish('test.py::test_case[row]', None)
    facts = json.loads(partial.read_text())
    assert not (tmp_path/'.execution.partial.json.tmp').exists()
    assert facts['pytest_started_at'] > 0
    assert [(event['event'], event['nodeid']) for event in facts['row_events']] == [
        ('started', 'test.py::test_case[row]'),
        ('finished', 'test.py::test_case[row]'),
    ]
