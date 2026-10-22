"""Reproduce two reviewed inputs; no target execution or model API requests."""
from __future__ import annotations
import argparse
import asyncio
import hashlib
import json
from pathlib import Path

from local_host import LocalResearchHost

HERE = Path(__file__).resolve().parent


async def run(output):
    output.mkdir(parents=True, exist_ok=False)
    report = {'scope': 'source-bound normal-return local branch predicates',
              'whole_test_verified': False, 'automatic_agent_inference': False,
              'cases': {}, 'implementation_sha256': {}}
    for language in ('java', 'python'):
        try:
            host = await LocalResearchHost.prepare(language)
        except (OSError, ValueError) as exc:
            report['cases'][language] = {'status': 'input_unavailable', 'reason': str(exc)}
            continue
        case = {'catalog': host.catalog(), 'checks': []}
        report['cases'][language] = case
        for pattern in case['catalog']['extraction']['patterns']:
            if pattern['support_status'] != 'supported':
                continue
            for branch in (True, False):
                for question in ('check_local_branch', 'infer_local_branch'):
                    proposal = {'source_id': case['catalog']['extraction']['source_id'],
                                'pattern_id': pattern['pattern_id'], 'question': question,
                                'goal_branch': branch,
                                'candidate': [{'kind': 'condition', 'value': branch}]
                                if question == 'check_local_branch' else []}
                    result = await host.propose(proposal)
                    contract = host.contract(result['check_id'])
                    if result['solver'].get('formulas'):
                        bound = host.bind_contract(result['check_id'], contract)
                        bound['query_exports'] = {}
                        for name, query in result['solver'].get('queries', {}).items():
                            text = '\n'.join(['(set-logic ALL)',
                                *result['solver']['formulas']['declarations'],
                                *[f'(assert {term})' for term in query['assertions']],
                                '(check-sat)', ''])
                            filename = f"{result['check_id']}.{name}.smt2"
                            (output/filename).write_text(text)
                            bound['query_exports'][name] = {
                                'file': filename, 'sha256': hashlib.sha256(text.encode()).hexdigest()}
                        case['checks'].append(bound)
                    else:
                        case['checks'].append({'contract': contract, 'check': result,
                                               'binding': 'not_bound_missing_formula_evidence'})
        results = [item['check']['solver'] for item in case['checks']]
        completed = [result for result in results
                     if result.get('formulas') and result.get('round_trip', {}).get('matches')
                     and len(result.get('queries', {})) == 3
                     and all(q.get('status') in {'sat', 'unsat'}
                             for q in result['queries'].values())]
        case['status'] = ('checked_local_fragments' if completed and len(completed) == len(results)
                          else 'incomplete_checks' if results else 'no_supported_fragment')
    for name in ('local_host.py', 'local_patterns.py', 'local_solver.py', 'local_solver_worker.py', 'run_local.py', 'probe_local_isolation.py'):
        report['implementation_sha256'][name] = hashlib.sha256((HERE/name).read_bytes()).hexdigest()
    (output/'report.json').write_text(json.dumps(report, indent=2, ensure_ascii=False)+'\n')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    report = asyncio.run(run(args.output))
    print(json.dumps({key: value['status'] for key, value in report['cases'].items()}))
