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


def run(old_root):
    _, registry, dataset, snapshot_id, case = CASES['flask']
    snapshot = old_root / registry / 'formal-snapshots' / dataset / snapshot_id
    task = load_task(snapshot, case_id=case)
    runtime = json.loads((snapshot/'control/runtime-lock.json').read_text())
    bundle = dict(runtime['environment'])['STUBTESTER_PYTHON_DEPENDENCY_BUNDLE']
    if not re.fullmatch('[0-9a-f]{64}', bundle):
        raise ValueError('invalid runtime identity')
    dependencies = old_root / registry / 'formal-snapshots/runtime-dependencies/python' / bundle / 'site-packages'
    summary = {'case_id': case, 'image': IMAGE, 'runtime_lock_digest': runtime['digest'],
               'dependency_rehash': False, 'runs': {}, 'mutants': ['wrong_code','drop_kwargs','duplicate_lookup']}
    for variant in ('a', 'b'):
        candidate = json.loads((HERE/'results'/f'generation-{variant}.json').read_text())['flask']
        work = HERE/'results'/f'eval-{variant}'
        work.mkdir(exist_ok=True)
        write(work/'task.json', task.to_dict())
        (work/'candidate.py').write_text(inject(task.masked_test.text, candidate['slots']))
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
                result = subprocess.run(command, capture_output=True, text=True, timeout=60)
            except subprocess.TimeoutExpired:
                subprocess.run(['docker', 'rm', '-f', container], capture_output=True, timeout=15)
                raise
            elapsed = time.perf_counter()-start
            (output/'stdout.txt').write_text(result.stdout)
            (output/'stderr.txt').write_text(result.stderr)
            facts = json.loads((output/'execution.json').read_text()) if (output/'execution.json').exists() else {}
            calls = [p for p in facts.get('phases', []) if p['when'] == 'call']
            valid = (facts.get('collected') == 5 and len(calls) == 5 and len(facts.get('identity', [])) == 5
                     and all(facts['identity']) and len(facts.get('post_call_identity', [])) == 5
                     and all(facts['post_call_identity']) and all(p['outcome'] in ('passed','failed') for p in calls)
                     and all(p['outcome']=='passed' for p in facts['phases'] if p['when']!='call'))
            summary['runs'][f'{variant}/{mutant}'] = {
                'exit_code': result.returncode, 'elapsed_seconds': elapsed, 'valid_execution': valid,
                'passed': sum(p['outcome']=='passed' for p in calls), 'failed': sum(p['outcome']=='failed' for p in calls)}
            write(HERE/'results'/'generation-metrics.json', summary)
    summary['reviewed_input_unchanged'] = load_task(snapshot, case_id=case) == task
    summary['evaluator_sha256'] = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in
                                  (HERE/'evaluate.py', HERE/'eval_observer.py')}
    write(HERE/'results'/'generation-metrics.json', summary)
    print(json.dumps(summary, indent=2))

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--old-root', type=Path, required=True)
    run(parser.parse_args().old_root)
