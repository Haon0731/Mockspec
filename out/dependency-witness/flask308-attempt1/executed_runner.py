"""Two isolated executions of one reviewed row; no model calls or dataset cloning."""
from __future__ import annotations
import argparse
import ast
import difflib
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess

from mock_spec.adapters.python import load_task
from mock_spec.validation import SourceStore

HERE = Path(__file__).resolve().parent
OLD_ROOT = Path('/home/haon/projects/Stub-Tester')
REGISTRY = OLD_ROOT / '.omx/developer-realistic-registry-max-fix23'
SNAPSHOT = REGISTRY / 'formal-snapshots/python-benchmark-new/241ef457f8d071c23e6acd658b684b11b54f13fa955e097d6bebe35dc755f47d'
IMAGE = 'sha256:04a57de0d4ccb65b7f73a5880277b19c65ca23c5b677d89d3a4673706f55866b'
REGION = re.compile(r'(?m)^(?P<indent>[ \t]*)# === PY-SLOT-START:(?P<slot>\w+) ===\n.*?^[ \t]*# === PY-SLOT-END:(?P=slot) ===(?:\n|$)', re.S)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def expected_candidate(masked, fragments):
    """Independent exact outside-slot preservation check against injector output."""
    def replace(match):
        return ''.join(match['indent'] + line + '\n' for line in fragments[match['slot']].splitlines())
    return REGION.sub(replace, masked)


def candidate_fragments(throws):
    # Human-authored behavior, based only on mut:282/285 and test:126.
    response = 'side_effect=RuntimeError(f"Werkzeug abort {code}")' if throws else 'return_value=None'
    fragments = {f'mock_stub_{n:03}': 'pass' for n in range(1, 7)}
    fragments.update({f'verify_{n:03}': 'pass' for n in range(1, 5)})
    fragments['mock_stub_001'] = '\n'.join([
        'mock_query = mocker.patch("flask.helpers._cv_app")',
        'mock_query.get.return_value = None',
        f'mock_fallback = mocker.patch("flask.helpers._wz_abort", {response})',
    ])
    return fragments


def slot_text(fragments, prefix):
    return '\n\n'.join(f'# === PY-SLOT-START:{key} ===\n{body}\n# === PY-SLOT-END:{key} ==='
                        for key, body in fragments.items() if key.startswith(prefix)) + '\n'


def visible_row(task):
    tree = ast.parse(task.masked_test.text)
    function = next(n for n in tree.body if isinstance(n, ast.FunctionDef)
                    and n.name == 'test_abort_scenarios')
    decorator = next(d for d in function.decorator_list if isinstance(d, ast.Call)
                     and isinstance(d.func, ast.Attribute) and d.func.attr == 'parametrize')
    names = ast.literal_eval(decorator.args[0]).split(',')
    rows = ast.literal_eval(decorator.args[1])
    selected = [dict(zip(names, row)) for row in rows
                if row[1] is False and type(row[2]) is int and row[2] == 403
                and row[3] == () and row[4] == {}]
    if len(selected) != 1 or len(rows) != 5:
        raise ValueError('Reviewed row is no longer uniquely selectable')
    return selected[0]


def prepare_inputs(output, task, row):
    guidance = {
        'origin': 'human_curated_from_reviewed_visible_input',
        'not_scenario_agent_output': True,
        'scope': row,
        'scenario': [
            'The context query returns normally with None.',
            'The MUT then reaches its fallback dependency call with code, args and kwargs unchanged.',
            'The fallback raises an Exception satisfying the original message assertion for this row.',
        ],
        'expected_observation': 'The original exception and message assertions hold for the selected row.',
        'alternatives': 'This is one proposed behavior, not the unique valid configuration or a recovered hidden mock.',
        'evidence': ['mut:282-285', 'imports:12,16', 'test:26-32,102-103,126'],
    }
    for variant in ('A', 'B'):
        directory = output/'future-inputs'/variant
        directory.mkdir(parents=True)
        write_json(directory/'task.json', task.to_dict())
        if variant == 'B':
            write_json(directory/'guidance.json', guidance)
    write_json(output/'future-inputs'/'protocol.json', {
        'status': 'prepared_only_no_generation_requests',
        'shared_input': 'A/task.json and B/task.json are byte-identical reviewed UnifiedTask',
        'B_guidance_origin': guidance['origin'],
        'guidance_contains': 'proposed behavior only; no Mock implementation, patch instructions or execution results',
        'future_isolation': [
            'Run A and B in separate fresh contexts with identical generation settings and no shared history.',
            'Expose only the selected variant directory, not the repository or this experiment output.',
            'No access to manual witnesses, analyses, reports, old candidates or reference answers.',
            'Keep execution, injection and result verification host-controlled outside generator context.',
        ],
        'budget': 'No old budget reused; future calls require separate authorization.',
        'conclusion_limit': 'Benefit from human guidance would not prove automatic Scenario Agent effectiveness.',
    })


def validate_execution(execution, throws, returncode):
    """Only observed runtime evidence can establish this witness outcome."""
    runtime = execution.get('runtime', {})
    phases = {p['when']: p for p in execution.get('phases', [])}
    observations = execution.get('observations', [])
    reasons = []
    expected_status = 0 if throws else 1
    if returncode != expected_status or execution.get('pytest_exitstatus') != expected_status:
        reasons.append('unexpected process or pytest exit status')
    if not runtime.get('mut_code_matches_admitted_slice') or not runtime.get('original_test_binding_is_mut'):
        reasons.append('runtime MUT identity not verified')
    if not runtime.get('python', '').startswith('3.10.'):
        reasons.append('runtime is not frozen CPython 3.10 ABI')
    selected = execution.get('selection', {})
    if len(selected.get('selected', [])) != 1 or len(selected.get('deselected', [])) != 4:
        reasons.append('row selection not verified')
    if phases.get('setup', {}).get('outcome') != 'passed' or phases.get('teardown', {}).get('outcome') != 'passed':
        reasons.append('setup/teardown failed')
    call = phases.get('call', {})
    if call.get('outcome') != ('passed' if throws else 'failed'):
        reasons.append('ordinary assertion outcome differs from prediction')
    if not throws and 'DID NOT RAISE' not in call.get('failure_message', ''):
        reasons.append('diagnostic did not fail at original exception expectation')
    if len(observations) != 1:
        reasons.append('expected one observation')
    else:
        observation = observations[0]
        for key in ('mut_identity_preserved', 'mut_globals_are_helpers',
                    'context_binding_replaced', 'fallback_binding_replaced', 'context_query_return_is_none'):
            if observation.get(key) is not True:
                reasons.append(key + ' not established')
        if observation.get('context_query_calls') != [{'args':[None], 'kwargs':{}}]:
            reasons.append('unexpected context query arguments or count')
        if observation.get('fallback_calls') != [{'args':[403], 'kwargs':{}}]:
            reasons.append('unexpected fallback arguments or count')
        if throws:
            if not observation.get('raised_exception_has_mut_traceback'):
                reasons.append('exception lacks actual MUT frame')
            if observation.get('configured_exception') != {'type':'RuntimeError','message':'Werkzeug abort 403'}:
                reasons.append('unexpected configured exception')
        elif observation.get('configured_exception') is not None or observation.get('fallback_return_is_none') is not True:
            reasons.append('diagnostic response differs from normal None return')
    return {'status': 'observed_as_predicted' if not reasons else 'not_established', 'reasons': reasons,
            'human_assisted_witness': True, 'whole_case_verified': False,
            'scenario_agent_effectiveness_verified': False}


def run(output):
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    task = load_task(SNAPSHOT, case_id='flask_308')
    store = SourceStore(task)
    row = visible_row(task)
    prepare_inputs(output, task, row)  # Construct guidance before any execution observation.
    source_hashes = {sid: digest(store.get(sid).text.encode()) for sid in store.ids()}
    runtime = json.loads((SNAPSHOT/'control/runtime-lock.json').read_text())
    bundle = dict(runtime['environment'])['STUBTESTER_PYTHON_DEPENDENCY_BUNDLE']
    if not re.fullmatch('[0-9a-f]{64}', bundle):
        raise ValueError('invalid pinned runtime dependency identity')
    dependencies = REGISTRY/'formal-snapshots/runtime-dependencies/python'/bundle/'site-packages'
    if not dependencies.is_dir() or dependencies.is_symlink():
        raise ValueError('frozen dependency runtime is unavailable')
    summary = {'reference_commit': '54a75f04b2b163ebe4fed3a487ad494ed7a377d3',
               'case_id': task.case_id, 'selected_row': row, 'source_sha256': source_hashes,
               'input_admission': 'reviewed_adapter', 'candidate_origin': 'human_assisted_witness',
               'runtime_lock_digest': runtime['digest'], 'dependency_bundle': bundle,
               'dependency_rehash': False, 'image_id': IMAGE,
               'guidance_prepared_before_execution': True, 'runs': {}}
    for name, throws in [('witness', True), ('diagnostic', False)]:
        work = output/name
        (work/'repo/tests').mkdir(parents=True)
        (work/'repo/tests/test_308abort.py').write_text(store.get('test').text)
        injector = (SNAPSHOT/'tree/apply_regions.py').read_bytes()  # Already checked by Adapter.
        (work/'apply_regions.py').write_bytes(injector)
        fragments = candidate_fragments(throws)
        (work/'mock_stub.txt').write_text(slot_text(fragments, 'mock_stub_'))
        (work/'verify.txt').write_text(slot_text(fragments, 'verify_'))
        inject = subprocess.run(['/usr/bin/python3.10', '-B', 'apply_regions.py'], cwd=work,
                                env={'PATH':'/usr/bin:/bin', 'LANG':'C.UTF-8'},
                                capture_output=True, text=True, timeout=15)
        preview = work/'control_outputs/candidate_preview/candidate_test.py'
        if inject.returncode or not preview.is_file():
            raise RuntimeError('approved injector failed; no target execution')
        candidate = preview.read_text()
        if candidate != expected_candidate(store.get('test').text, fragments):
            raise ValueError('injector modified content outside allowed slots')
        shutil.copyfile(HERE/'witness_observer.py', work/'witness_observer.py')
        write_json(work/'approved_mut.json', store.get('mut').to_dict())
        write_json(work/'selected_row.json', row)
        (work/'runtime').mkdir()
        container = 'mockspec-witness-' + digest(str(work).encode())[:12]
        command = ['docker','run','--rm','--name',container,'--network=none','--read-only',
                   '--cap-drop=ALL','--security-opt=no-new-privileges','--pids-limit=128',
                   '--memory=768m','--cpus=1','--tmpfs','/tmp:rw,nosuid,nodev,size=64m',
                   '--user',f'{os.getuid()}:{os.getgid()}',
                   '-v',f'{SNAPSHOT}/tree/repo:/target:ro',
                   '-v',f'{dependencies}:/deps:ro', '-v',f'{work}:/workspace:ro',
                   '-v',f'{work}/runtime:/outputs:rw',
                   '-v',f'{preview}:/target/tests/test_308abort.py:ro',
                   '-e','PYTHONPATH=/target/src/src:/target/src:/deps:/workspace',
                   '-e','PYTHONDONTWRITEBYTECODE=1','-e','PYTHONNOUSERSITE=1',
                   '-e','HOME=/tmp','-w','/workspace','--entrypoint','python3',IMAGE,
                   '-B','-m','pytest','-p','witness_observer','-p','no:cacheprovider',
                   '--basetemp=/outputs/pytest-tmp','--tb=no','-v',
                   '/target/tests/test_308abort.py']
        write_json(work/'command.json', command)
        with (work/'stdout.txt').open('w') as stdout, (work/'stderr.txt').open('w') as stderr:
            try:
                process = subprocess.run(command, stdout=stdout, stderr=stderr, timeout=90)
                returncode = process.returncode
            except subprocess.TimeoutExpired:
                subprocess.run(['docker','rm','-f',container], capture_output=True, timeout=15)
                returncode = 'timeout'
        execution = json.loads((work/'runtime/execution.json').read_text()) if (work/'runtime/execution.json').exists() else {}
        verdict = validate_execution(execution, throws, returncode)
        summary['runs'][name] = {'exit_code':returncode, 'verdict':verdict,
                                 'injector_sha256':digest(injector), 'candidate_sha256':digest(candidate.encode()),
                                 'outside_slots_preserved':True, 'evidence':f'{name}/runtime/execution.json'}
        write_json(output/'summary.json', summary)  # Retain incomplete/failed attempts.
    witness = (output/'witness/mock_stub.txt').read_text()
    diagnostic = (output/'diagnostic/mock_stub.txt').read_text()
    (output/'candidate.diff').write_text(''.join(difflib.unified_diff(
        witness.splitlines(keepends=True), diagnostic.splitlines(keepends=True),
        fromfile='witness/mock_stub.txt', tofile='diagnostic/mock_stub.txt')))
    after = load_task(SNAPSHOT, case_id='flask_308')
    summary['reviewed_input_unchanged_after_execution'] = after == task
    summary['implementation_sha256'] = {p.name:digest(p.read_bytes()) for p in
                                        (HERE/'run_witness.py', HERE/'witness_observer.py')}
    write_json(output/'summary.json', summary)
    return summary


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    result = run(args.output)
    print(json.dumps({name: info['verdict'] for name,info in result['runs'].items()}, indent=2))
    raise SystemExit(0 if all(info['verdict']['status']=='observed_as_predicted'
                             for info in result['runs'].values()) else 1)
