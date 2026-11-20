"""Execute generated slots against original Flask test, in a read-only container."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import time
from prepare import HERE, CASES, write
from mock_spec.adapters.python import load_task

IMAGE = 'sha256:04a57de0d4ccb65b7f73a5880277b19c65ca23c5b677d89d3a4673706f55866b'
SLOT = re.compile(r'(?m)^(?P<indent>[ \t]*)# === PY-SLOT-START:(?P<slot>\w+) ===\n.*?^[ \t]*# === PY-SLOT-END:(?P=slot) ===', re.S)


def inject(text, slots):
    expected = [m['slot'] for m in SLOT.finditer(text)]
    if set(expected) != set(slots) or len(expected) != 10:
        raise ValueError('slot mismatch')
    def replace(match):
        return '\n'.join(match['indent'] + line for line in slots[match['slot']].splitlines())
    candidate = SLOT.sub(replace, text)
    compile(candidate, '<candidate>', 'exec')
    return candidate


MUTANTS = ['wrong_code', 'drop_kwargs', 'duplicate_lookup']


def generation_failure_receipt(trace_path):
    try:
        trace = json.loads(trace_path.read_text())
    except Exception as error:
        raise ValueError(f'unverifiable generation failure trace {trace_path}: {error}') from error
    turns = trace.get('turns')
    terminal = turns[-1] if isinstance(turns, list) and turns else {}
    prompt_sha256 = trace.get('prompt_sha256', '')
    valid = (trace.get('status') == 'failed'
             and isinstance(trace.get('error_type'), str) and bool(trace['error_type'])
             and re.fullmatch('[0-9a-f]{64}', prompt_sha256) is not None
             and terminal.get('status') == 'incomplete' and terminal.get('text') == ''
             and isinstance(terminal.get('response_id'), str) and bool(terminal['response_id'])
             and isinstance(terminal.get('resolved_model'), str) and bool(terminal['resolved_model']))
    if not valid:
        raise ValueError(f'unverifiable generation failure trace {trace_path}')
    return {
        'failure_category': 'generation_failure',
        'trace_path': str(trace_path.resolve()),
        'trace_sha256': hashlib.sha256(trace_path.read_bytes()).hexdigest(),
        'prompt_sha256': prompt_sha256,
        'error_type': trace['error_type'],
        'status_code': trace.get('status_code'),
        'source_calls': trace.get('source_calls'),
        'elapsed_seconds': trace.get('elapsed_seconds'),
        'terminal_turn': {
            'response_id': terminal['response_id'],
            'resolved_model': terminal['resolved_model'],
            'status': terminal['status'],
            'text_empty': True,
        },
    }


def candidate_variants(candidate_dir, explicit_candidate_dir=False):
    """Return stable variant names while preserving the historical A/B default."""
    if not explicit_candidate_dir:
        return ['a', 'b']
    variants = ['a', 'b', 'c']
    missing = []
    for variant in variants:
        if (candidate_dir/f'generation-{variant}.json').is_file():
            continue
        trace_path = candidate_dir/f'trace-{variant}.json'
        if trace_path.is_file():
            generation_failure_receipt(trace_path)
        else:
            missing.append(f'generation-{variant}.json or verified trace-{variant}.json')
    if missing:
        raise ValueError(f'missing candidates in {candidate_dir}: {", ".join(missing)}')
    return variants


def prepare_output_dir(output_dir, variants, explicit_output_dir=False):
    if explicit_output_dir:
        output_dir.mkdir(parents=True, exist_ok=False)
        return
    existing = [path for path in [output_dir/'generation-metrics.json',
                                  *(output_dir/f'eval-{variant}' for variant in variants)]
                if path.exists()]
    if existing:
        names = ', '.join(path.name for path in existing)
        raise FileExistsError(f'historical evaluation evidence exists in {output_dir}: {names}')


def summarize_execution(facts, exit_code):
    calls = [phase for phase in facts.get('phases', []) if phase.get('when') == 'call']
    failed_calls = [phase for phase in calls if phase.get('outcome') == 'failed']
    expected_exit_code = 1 if failed_calls else 0
    exit_code_consistent = (facts.get('exitstatus') == exit_code == expected_exit_code)
    valid = (facts.get('collected') == 5 and len(calls) == 5
             and len(facts.get('identity', [])) == 5 and all(facts['identity'])
             and len(facts.get('post_call_identity', [])) == 5
             and all(facts['post_call_identity'])
             and all(phase.get('outcome') in ('passed', 'failed') for phase in calls)
             and all(phase.get('outcome') == 'passed'
                     for phase in facts.get('phases', []) if phase.get('when') != 'call')
             and exit_code_consistent)
    if valid and not failed_calls:
        category = 'passed'
    elif valid and all(phase.get('exception_type') == 'AssertionError' for phase in failed_calls):
        category = 'assertion_failure'
    elif valid:
        category = 'configuration_failure'
    else:
        category = 'container_or_framework_failure'
    return {
        'exit_code': exit_code,
        'pytest_exitstatus': facts.get('exitstatus'),
        'exit_code_consistent': exit_code_consistent,
        'valid_execution': valid,
        'failure_category': category,
        'passed': sum(phase.get('outcome') == 'passed' for phase in calls),
        'failed': sum(phase.get('outcome') == 'failed' for phase in calls),
        'parameter_rows': [
            {key: phase[key] for key in ('nodeid', 'outcome', 'failure', 'exception_type') if key in phase}
            for phase in calls
        ],
        'row_events': facts.get('row_events', []),
    }


def subprocess_text(value):
    if value is None:
        return ''
    return value.decode(errors='replace') if isinstance(value, bytes) else value


def observer_summary(execution_path, exit_code):
    try:
        facts = json.loads(execution_path.read_text())
    except Exception as error:
        return {'exit_code': exit_code, 'valid_execution': False,
                'failure_category': 'observer_output_failure',
                'error_type': type(error).__name__, 'error_message': str(error),
                'passed': 0, 'failed': 0, 'parameter_rows': []}
    return summarize_execution(facts, exit_code)


def cleanup_timed_out_container(container):
    try:
        subprocess.run(['docker', 'rm', '-f', container], capture_output=True, timeout=15)
    except Exception as error:
        return {'type': type(error).__name__, 'message': str(error)}
    return None


def docker_output(command, timeout):
    return subprocess.run(command, capture_output=True, text=True, timeout=timeout)


def run_with_separate_timeouts(command, container, output,
                               startup_timeout=120, pytest_timeout=60):
    """Create/start a container, then begin the pytest budget at observer startup."""
    create_command = ['docker', 'create', *command[2:]]
    create_command.remove('--rm')
    started = time.perf_counter()
    startup_deadline = time.monotonic() + startup_timeout
    timings = {'startup_timeout_seconds': startup_timeout,
               'pytest_timeout_seconds': pytest_timeout}
    lifecycle_path = output/'lifecycle-commands.json'
    lifecycle = []

    def invoke(actual_command, timeout):
        lifecycle.append({'command': actual_command, 'timeout_seconds': timeout})
        lifecycle_path.write_text(json.dumps(lifecycle, indent=2) + '\n')
        return docker_output(actual_command, timeout)

    try:
        created = invoke(create_command, startup_timeout)
        if created.returncode != 0:
            return ({'exit_code': created.returncode, 'valid_execution': False,
                     'failure_category': 'container_launch_failure', 'passed': 0, 'failed': 0,
                     'parameter_rows': [], 'timings': timings}, created.stdout, created.stderr)
        remaining = startup_deadline - time.monotonic()
        if remaining <= 0:
            raise subprocess.TimeoutExpired(create_command, startup_timeout)
        launched = invoke(['docker', 'start', container], remaining)
        if launched.returncode != 0:
            return ({'exit_code': launched.returncode, 'valid_execution': False,
                     'failure_category': 'container_launch_failure', 'passed': 0, 'failed': 0,
                     'parameter_rows': [], 'timings': timings}, launched.stdout, launched.stderr)

        partial = output/'execution.partial.json'
        stopped_before_marker = False
        while not partial.is_file() and time.monotonic() < startup_deadline:
            state = invoke(
                ['docker', 'inspect', '--format', '{{.State.Running}}', container], 5)
            if state.returncode == 0 and state.stdout.strip() == 'false':
                stopped_before_marker = True
                break
            time.sleep(0.1)
        timings['startup_seconds'] = time.perf_counter() - started
        if not partial.is_file():
            if stopped_before_marker:
                logs = invoke(['docker', 'logs', container], 15)
                return ({'exit_code': None, 'valid_execution': False,
                         'failure_category': 'container_startup_failure',
                         'passed': 0, 'failed': 0, 'parameter_rows': [],
                         'timings': timings}, logs.stdout, logs.stderr)
            return ({'exit_code': None, 'valid_execution': False,
                     'failure_category': 'container_startup_timeout', 'passed': 0, 'failed': 0,
                     'parameter_rows': [], 'timings': timings}, '', '')

        pytest_started = time.perf_counter()
        try:
            waited = invoke(['docker', 'wait', container], pytest_timeout)
        except subprocess.TimeoutExpired:
            timings['pytest_seconds'] = time.perf_counter() - pytest_started
            summary = observer_summary(partial, None)
            summary.update(failure_category='pytest_timeout', valid_execution=False,
                           exit_code=None, timings=timings)
            return summary, '', ''
        timings['pytest_seconds'] = time.perf_counter() - pytest_started
        exit_code = int(waited.stdout.strip()) if waited.returncode == 0 else None
        logs = invoke(['docker', 'logs', container], 15)
        summary = observer_summary(output/'execution.json', exit_code)
        summary['timings'] = timings
        return summary, logs.stdout, logs.stderr
    except subprocess.TimeoutExpired as error:
        timings['startup_seconds'] = time.perf_counter() - started
        return ({'exit_code': None, 'valid_execution': False,
                 'failure_category': 'container_startup_timeout', 'passed': 0, 'failed': 0,
                 'parameter_rows': [], 'timings': timings},
                subprocess_text(error.stdout), subprocess_text(error.stderr))
    finally:
        lifecycle.append({'command': ['docker', 'rm', '-f', container],
                          'timeout_seconds': 15})
        lifecycle_path.write_text(json.dumps(lifecycle, indent=2) + '\n')
        cleanup_timed_out_container(container)


def run(old_root, candidate_dir=None, output_dir=None, separate_timeouts=False):
    explicit_candidate_dir = candidate_dir is not None
    explicit_output_dir = output_dir is not None
    if explicit_candidate_dir and not explicit_output_dir:
        raise ValueError('--candidate-dir requires an independent --output-dir')
    candidate_dir = candidate_dir or HERE/'results'
    output_dir = output_dir or HERE/'results'
    candidate_dir = candidate_dir.resolve()
    output_dir = output_dir.resolve()
    variants = candidate_variants(candidate_dir, explicit_candidate_dir)
    prepare_output_dir(output_dir, variants, explicit_output_dir)
    _, registry, dataset, snapshot_id, case = CASES['flask']
    snapshot = old_root / registry / 'formal-snapshots' / dataset / snapshot_id
    task = load_task(snapshot, case_id=case)
    runtime = json.loads((snapshot/'control/runtime-lock.json').read_text())
    bundle = dict(runtime['environment'])['STUBTESTER_PYTHON_DEPENDENCY_BUNDLE']
    if not re.fullmatch('[0-9a-f]{64}', bundle):
        raise ValueError('invalid runtime identity')
    dependencies = old_root / registry / 'formal-snapshots/runtime-dependencies/python' / bundle / 'site-packages'
    summary = {'case_id': case, 'image': IMAGE, 'runtime_lock_digest': runtime['digest'],
               'dependency_rehash': False, 'candidate_dir': str(candidate_dir),
               'output_dir': str(output_dir), 'variants': variants, 'runs': {},
               'candidate_errors': {}, 'mutants': MUTANTS,
               'timing_mode': ('startup_120s_then_pytest_60s'
                               if separate_timeouts else 'docker_run_total_60s')}
    metrics_path = output_dir/'generation-metrics.json'
    for variant in variants:
        candidate_path = candidate_dir/f'generation-{variant}.json'
        work = output_dir/f'eval-{variant}'
        if not candidate_path.is_file():
            summary['candidate_errors'][variant] = generation_failure_receipt(
                candidate_dir/f'trace-{variant}.json')
            write(metrics_path, summary)
            continue
        try:
            candidate = json.loads(candidate_path.read_text())['flask']
            work.mkdir(exist_ok=False)
            candidate_text = inject(task.masked_test.text, candidate['slots'])
        except Exception as error:
            category = 'syntax_failure' if isinstance(error, SyntaxError) else 'configuration_failure'
            summary['candidate_errors'][variant] = {
                'failure_category': category,
                'type': type(error).__name__, 'message': str(error),
            }
            write(metrics_path, summary)
            continue
        write(work/'task.json', task.to_dict())
        (work/'candidate.py').write_text(candidate_text)
        summary.setdefault('candidate_evidence', {})[variant] = {
            'sha256': hashlib.sha256((work/'candidate.py').read_bytes()).hexdigest(),
            'masked_outside_regions_sha256': hashlib.sha256(SLOT.sub('<slot>', task.masked_test.text).encode()).hexdigest(),
            'injection': 'callback substitution of exactly ten regions; no outside-region rewrites'}
        for mutant in ('original', *summary['mutants']):
            output = work/mutant
            output.mkdir(exist_ok=False)
            container = 'mockspec-slice-' + hashlib.sha256(str(output).encode()).hexdigest()[:12]
            command = ['docker', 'run', '--rm', '--name', container, '--network=none', '--read-only',
                       '--cap-drop=ALL', '--security-opt=no-new-privileges', '--pids-limit=128',
                       '--memory=768m', '--cpus=1', '--tmpfs', '/tmp:rw,nosuid,nodev,size=64m',
                       '--user', f'{os.getuid()}:{os.getgid()}',
                       '-v', f'{snapshot}/tree/repo:/target:ro', '-v', f'{dependencies}:/deps:ro',
                       '-v', f'{work}:/workspace:ro', '-v', f'{output}:/outputs:rw',
                       '-v', f'{HERE}/eval_observer.py:/observer/eval_observer.py:ro',
                       '-v', f'{work}/candidate.py:/target/tests/test_308abort.py:ro',
                       '-e', 'PYTHONPATH=/target/src/src:/target/src:/deps:/observer',
                       '-e', 'PYTHONDONTWRITEBYTECODE=1', '-e', 'PYTHONNOUSERSITE=1',
                       '-e', f'SLICE_MUTANT={mutant}', '-e', 'HOME=/tmp', '-w', '/workspace',
                       '--entrypoint', 'python3', IMAGE, '-B', '-m', 'pytest', '-p', 'eval_observer',
                       '-p', 'no:cacheprovider', '--basetemp=/outputs/pytest-tmp', '--tb=no', '-q',
                       '/target/tests/test_308abort.py']
            start = time.perf_counter()
            write(output/'command.json', command)
            try:
                if separate_timeouts:
                    run_summary, stdout, stderr = run_with_separate_timeouts(
                        command, container, output)
                    result = None
                else:
                    result = subprocess.run(command, capture_output=True, text=True, timeout=60)
            except subprocess.TimeoutExpired as error:
                cleanup_error = cleanup_timed_out_container(container)
                run_summary = {'exit_code': None, 'valid_execution': False,
                               'failure_category': 'timeout', 'passed': 0, 'failed': 0,
                               'parameter_rows': []}
                if cleanup_error:
                    run_summary['cleanup_error'] = cleanup_error
                stdout, stderr = error.stdout, error.stderr
            except Exception as error:
                run_summary = {'exit_code': None, 'valid_execution': False,
                               'failure_category': 'container_launch_failure',
                               'error_type': type(error).__name__, 'error_message': str(error),
                               'passed': 0, 'failed': 0, 'parameter_rows': []}
                stdout, stderr = '', ''
            else:
                if separate_timeouts:
                    pass
                else:
                    stdout, stderr = result.stdout, result.stderr
                    run_summary = observer_summary(output/'execution.json', result.returncode)
            elapsed = time.perf_counter()-start
            (output/'stdout.txt').write_text(subprocess_text(stdout))
            (output/'stderr.txt').write_text(subprocess_text(stderr))
            run_summary['elapsed_seconds'] = elapsed
            summary['runs'][f'{variant}/{mutant}'] = run_summary
            write(metrics_path, summary)
    summary['reviewed_input_unchanged'] = load_task(snapshot, case_id=case) == task
    summary['evaluator_sha256'] = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in
                                  (HERE/'evaluate.py', HERE/'eval_observer.py')}
    write(metrics_path, summary)
    print(json.dumps(summary, indent=2))

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Evaluate frozen Flask candidates in isolated containers.')
    parser.add_argument('--old-root', type=Path, required=True)
    parser.add_argument('--candidate-dir', type=Path,
                        help='directory containing generation-*.json; when set, all variants are evaluated')
    parser.add_argument('--output-dir', type=Path,
                        help='new directory for evaluation evidence; must not already exist')
    parser.add_argument('--separate-timeouts', action='store_true',
                        help='allow 120s for container startup, then 60s from pytest startup')
    arguments = parser.parse_args()
    if arguments.candidate_dir is not None and arguments.output_dir is None:
        parser.error('--candidate-dir requires an independent --output-dir')
    run(arguments.old_root, arguments.candidate_dir, arguments.output_dir,
        separate_timeouts=arguments.separate_timeouts)
