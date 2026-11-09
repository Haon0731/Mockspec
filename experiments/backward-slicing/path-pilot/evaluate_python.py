"""Pilot batch evaluator; reuse original slot injector and identity observer."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from evaluate import IMAGE, inject  # noqa: E402
from prepare import CASES, write  # noqa: E402
from mock_spec.adapters.python import load_task  # noqa: E402


def classify_run(facts, exit_code, mutant):
    """Accept only consistent process/observer results for the requested program."""
    calls = [p for p in facts.get('phases', []) if p['when']=='call']
    valid = (facts.get('collected')==5 and len(calls)==5
             and len(facts.get('identity', []))==5 and all(facts['identity'])
             and len(facts.get('post_call_identity', []))==5 and all(facts['post_call_identity'])
             and all(p['outcome'] in ('passed','failed') for p in calls)
             and all(p['outcome']=='passed' for p in facts.get('phases', []) if p['when']!='call')
             and exit_code in (0, 1) and facts.get('exitstatus')==exit_code
             and (exit_code==0)==all(p['outcome']=='passed' for p in calls)
             and facts.get('mutant')==mutant)
    return {'valid': valid, 'exit_code': exit_code, 'observer_exitstatus': facts.get('exitstatus'),
            'passed': sum(p['outcome']=='passed' for p in calls),
            'failed': sum(p['outcome']=='failed' for p in calls),
            'failures': [p.get('failure') for p in calls if p['outcome']=='failed']}


def evaluate(root, candidates, output):
    output.mkdir(parents=True, exist_ok=False)
    _, registry, dataset, snapshot_id, case = CASES['flask']
    snapshot = root / registry / 'formal-snapshots' / dataset / snapshot_id
    task = load_task(snapshot, case_id=case)
    runtime = json.loads((snapshot/'control/runtime-lock.json').read_text())
    bundle = dict(runtime['environment'])['STUBTESTER_PYTHON_DEPENDENCY_BUNDLE']
    if not re.fullmatch('[0-9a-f]{64}', bundle):
        raise ValueError('invalid dependency identity')
    dependencies = root / registry / 'formal-snapshots/runtime-dependencies/python' / bundle / 'site-packages'
    summary = {'case_id': case, 'runtime_lock_digest': runtime['digest'], 'image': IMAGE,
               'dependency_rehash': False, 'candidates': {}}
    for candidate_file in candidates:
        name = candidate_file.stem
        candidate = json.loads(candidate_file.read_text())['flask']
        work = output/name
        work.mkdir()
        write(work/'task.json', task.to_dict())
        entry = {'candidate_sha256': hashlib.sha256(candidate_file.read_bytes()).hexdigest(), 'runs': {}}
        summary['candidates'][name] = entry
        try:
            (work/'candidate.py').write_text(inject(task.masked_test.text, candidate['slots']))
        except (ValueError, SyntaxError, KeyError, TypeError) as exc:
            entry['generation_failure'] = type(exc).__name__ + ': ' + str(exc)
            continue
        for mutant in ('original', 'wrong_code', 'drop_kwargs', 'duplicate_lookup'):
            target = work/mutant
            target.mkdir()
            container = 'mockspec-path-' + hashlib.sha256(str(target).encode()).hexdigest()[:12]
            command = ['docker','run','--rm','--name',container,'--network=none','--read-only',
                       '--cap-drop=ALL','--security-opt=no-new-privileges','--pids-limit=128',
                       '--memory=768m','--cpus=1','--tmpfs','/tmp:rw,nosuid,nodev,size=64m',
                       '--user',f'{os.getuid()}:{os.getgid()}',
                       '-v',f'{snapshot}/tree/repo:/target:ro','-v',f'{dependencies}:/deps:ro',
                       '-v',f'{work}:/workspace:ro','-v',f'{target}:/outputs:rw',
                       '-v',f'{HERE.parent}/eval_observer.py:/observer/eval_observer.py:ro',
                       '-v',f'{work}/candidate.py:/target/tests/test_308abort.py:ro',
                       '-e','PYTHONPATH=/target/src/src:/target/src:/deps:/observer',
                       '-e','PYTHONDONTWRITEBYTECODE=1','-e','PYTHONNOUSERSITE=1',
                       '-e',f'SLICE_MUTANT={mutant}','-e','HOME=/tmp','-w','/workspace',
                       '--entrypoint','python3',IMAGE,'-B','-m','pytest','-p','eval_observer',
                       '-p','no:cacheprovider','--basetemp=/outputs/pytest-tmp','--tb=no','-q',
                       '/target/tests/test_308abort.py']
            write(target/'command.json', command)
            started = time.perf_counter()
            try:
                result = subprocess.run(command, capture_output=True, text=True, timeout=60)
            except subprocess.TimeoutExpired:
                subprocess.run(['docker','rm','-f',container], capture_output=True, timeout=15)
                entry['runs'][mutant] = {'valid': False, 'reason': 'timeout'}
                continue
            (target/'stdout.txt').write_text(result.stdout)
            (target/'stderr.txt').write_text(result.stderr)
            facts = json.loads((target/'execution.json').read_text()) if (target/'execution.json').exists() else {}
            entry['runs'][mutant] = {**classify_run(facts, result.returncode, mutant),
                                    'elapsed_seconds': time.perf_counter()-started}
            write(output/'metrics.json', summary)
        base = entry['runs'].get('original', {})
        entry['original_passed'] = (base.get('valid') and base.get('passed') == 5
                                    and base.get('exit_code') == 0
                                    and base.get('observer_exitstatus') == 0)
        entry['mutants_detected'] = [m for m,r in entry['runs'].items() if m!='original'
                                     and entry['original_passed'] and r.get('valid') and r.get('failed',0)>0]
    summary['reviewed_input_unchanged'] = task == load_task(snapshot, case_id=case)
    summary['implementation_sha256'] = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in
                                        (Path(__file__), HERE.parent/'evaluate.py', HERE.parent/'eval_observer.py')}
    write(output/'metrics.json', summary)
    return summary

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--old-root', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('candidates', type=Path, nargs='+')
    args = parser.parse_args()
    result = evaluate(args.old_root, args.candidates, args.output.resolve())
    print(json.dumps({k:{'original_passed':v.get('original_passed'), 'mutants_detected':v.get('mutants_detected')}
                      for k,v in result['candidates'].items()}, indent=2))
