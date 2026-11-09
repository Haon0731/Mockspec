"""Reclassify saved receipts and summarize, without executing any candidate again."""
import hashlib
import json
from pathlib import Path

from evaluate_python import classify_run

HERE = Path(__file__).resolve().parent
NAMES = ('S1', 'S2', 'G1', 'G2', 'R1', 'R2')


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n')


def main():
    manifest = json.loads((HERE/'inputs/manifest.json').read_text())
    for filename, expected in manifest.items():
        path = HERE/filename if filename.endswith('-raw.json') else HERE/'inputs'/filename
        if digest(path) != expected['sha256']:
            raise ValueError('frozen evidence changed: '+filename)
    old = json.loads((HERE/'python-generated/metrics.json').read_text())
    result = {'python': {}, 'java': {}, 'arms': {}, 'all_frozen_inputs_unchanged': True,
              'python_execution_evaluator_sha256': old['implementation_sha256'],
              'current_classifier_sha256': digest(HERE/'evaluate_python.py'),
              'note': 'Original process receipts retained; stricter exit-status/mutant audit does not rerun candidates.'}
    unchanged = True
    for name in NAMES:
        candidate_hash = digest(HERE/'candidates'/f'{name}.json')
        runs = {}
        for mutant, entry in old['candidates'][name]['runs'].items():
            facts = json.loads((HERE/'python-generated'/name/mutant/'execution.json').read_text())
            runs[mutant] = {**classify_run(facts, entry['exit_code'], mutant),
                            'elapsed_seconds': entry['elapsed_seconds']}
        base = runs['original']
        passed = base['valid'] and base['passed']==5 and base['exit_code']==0 and base['observer_exitstatus']==0
        kills = [key for key, value in runs.items() if key!='original'
                 and passed and value['valid'] and value['exit_code']==1 and value['failed']>0]
        failure_sites = []
        lines = (HERE/'python-generated'/name/'candidate.py').read_text().splitlines()
        for mutant in kills:
            for failure in runs[mutant]['failures']:
                line_number = int(failure.split(':')[1])
                line = lines[line_number-1].strip()
                failure_sites.append({'mutant': mutant, 'line': line_number, 'code': line})
        result['python'][name] = {'original_passed': passed, 'mutants_detected': kills,
                                  'runs': runs, 'failure_sites': failure_sites}
        unchanged &= (passed==old['candidates'][name]['original_passed']
                      and kills==old['candidates'][name]['mutants_detected'])
        java = json.loads((HERE/'java-generated'/name/'result.json').read_text())
        if candidate_hash != java['candidate_sha256'] or candidate_hash != old['candidates'][name]['candidate_sha256']:
            raise ValueError('candidate changed: '+name)
        result['java'][name] = {key: java[key] for key in ('status', 'compile', 'test')}
    result['classification_unchanged_after_stricter_audit'] = unchanged
    result['python_run_count'] = sum(len(v['runs']) for v in result['python'].values())
    result['java_generated_run_count'] = len(result['java'])
    result['python_seconds'] = sum(r['elapsed_seconds'] for v in result['python'].values() for r in v['runs'].values())
    result['java_generated_seconds'] = sum(v['compile']['elapsed_seconds']+v['test']['elapsed_seconds'] for v in result['java'].values())
    for arm in ('S', 'G', 'R'):
        names = [n for n in NAMES if n.startswith(arm)]
        result['arms'][arm] = {
            'candidate_count_per_task': len(names),
            'python_passed': sum(result['python'][n]['original_passed'] for n in names),
            'java_passed': sum(result['java'][n]['status']=='passed' for n in names),
            'python_usable_candidate_mutant_pairs_detected': sum(len(result['python'][n]['mutants_detected']) for n in names)}
    provenance = {'common_instruction_file': 'generation-prompt.txt',
                  'common_instruction_sha256': digest(HERE/'generation-prompt.txt'),
                  'context_isolation': 'fork_turns=none and explicit read whitelist; not OS-enforced; reads are agent self-report',
                  'configured_role_model': 'gpt-5.6-sol', 'agent_type': 'executor',
                  'runtime_model_id': None, 'model_tokens': None, 'model_cost': None,
                  'feedback_before_output': False, 'candidate_revisions_after_execution': 0,
                  'generation_order': ['S1', 'S2', 'G1', 'G2', 'R1', 'R2'],
                  'randomized_order': False, 'seed_control': False, 'conditions': {}}
    for name in NAMES:
        arm = name[0]
        provenance['conditions'][name] = {
            'native_agent_name': 'pilot_'+name.lower(), 'attempt_count': 1,
            'read_files_self_report': ['generation-prompt.txt', 'inputs/common.json', f'inputs/{arm}.json'],
            'evidence_sha256': manifest[f'{arm}.json']['sha256'],
            'total_input_file_bytes': manifest['common.json']['bytes']+manifest[f'{arm}.json']['bytes']+(HERE/'generation-prompt.txt').stat().st_size,
            'candidate_sha256': digest(HERE/'candidates'/f'{name}.json')}
    write(HERE/'generation-provenance.json', provenance)
    write(HERE/'summary.json', result)
    print(json.dumps({key: result[key] for key in ('arms', 'classification_unchanged_after_stricter_audit',
                                                 'python_run_count', 'java_generated_run_count',
                                                 'python_seconds', 'java_generated_seconds')}, indent=2))


if __name__ == '__main__':
    main()
