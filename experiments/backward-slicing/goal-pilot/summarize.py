"""Verify frozen identities and aggregate saved experimental evidence."""
import hashlib
import json
from pathlib import Path


HERE=Path(__file__).resolve().parent


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    manifest=read(HERE/'inputs/manifest.json')
    for name,identity in manifest.items():
        if digest(HERE.parent/name)!=identity['sha256']:
            raise ValueError('frozen input or implementation changed: '+name)
    checks=read(HERE/'checks.json')
    binding_entries=read(HERE/'bindings.json')['candidates']
    candidates={Path(item['candidate']).stem:item for item in binding_entries}
    rows={}
    for name,judgments in checks.items():
        if len(judgments)!=1:
            raise ValueError('this experiment selects one goal')
        old=name in ('S1','S2','G1','G2','R1','R2')
        run=read((HERE.parent/'path-pilot/java-generated' if old else HERE/'junit-probes')/name/'result.json')
        if run['candidate_sha256'] != candidates[name]['sha256']:
            raise ValueError('candidate identity mismatch: '+name)
        verdict=judgments[0]['verdict']
        rows[name]={'origin':'prior generated candidate' if old else 'host mechanism probe',
                    'check':verdict,'junit_status':run['status'],
                    'consistent_with_observed_junit':None if verdict=='unknown' else
                        (verdict=='candidate_consistent')==(run['status']=='passed')}
    revisions={}
    provenance={'configured_agent_type':'executor','configured_role_model':'gpt-5.6-sol',
                'runtime_model_id':None,'model_tokens':None,'model_cost':None,
                'fork_turns':'none','launch_order':['C1','E1','E2','C2'],
                'order_randomized':False,'seed_control':False,
                'isolation':'instruction read whitelist and agent self-report, not OS-enforced or exported complete tool audit',
                'attempts_per_context':1,'target_execution_feedback_before_output':False,
                'original_candidate':'path-pilot/candidates/R1.json',
                'conditions':{}}
    revised_checks=read(HERE/'revision-checks.json')
    for name in ('C1','E1','E2','C2'):
        run=read(HERE/'junit-revisions'/name/'result.json')
        candidate=HERE/'revisions'/f'{name}.json'
        if run['candidate_sha256'] != digest(candidate):
            raise ValueError('revision identity mismatch')
        revisions[name]={'junit_status':run['status'],'check':revised_checks[name][0]['verdict'],
                         'compile_seconds':run['compile']['elapsed_seconds'],
                         'test_seconds':run['test']['elapsed_seconds']}
        sources=['goal-pilot/revision-prompt.txt','path-pilot/inputs/common.json',f'goal-pilot/inputs/{name[0]}.json']
        provenance['conditions'][name]={
            'agent_name':'goal_'+name.lower(),
            'read_files_self_report':sources+([f'goal-pilot/revisions/{name}.json'] if name!='C2' else []),
            'own_output_read_purpose':'JSON/schema validation' if name!='C2' else 'metadata-only existence check',
            'input_sha256':{source:digest(HERE.parent/source) for source in sources},
            'input_file_bytes':sum((HERE.parent/source).stat().st_size for source in sources),
            'candidate_sha256':digest(candidate)}
    library=read(HERE/'library-validation/comparison.json')
    result={'frozen_inputs_unchanged':True,'candidate_checks':rows,
            'decided':sum(row['check']!='unknown' for row in rows.values()),
            'unknown':sum(row['check']=='unknown' for row in rows.values()),
            'decided_disagreements':sum(row['consistent_with_observed_junit'] is False for row in rows.values()),
            'revisions':revisions,
            'revision_passes':{arm:sum(v['junit_status']=='passed' for n,v in revisions.items() if n.startswith(arm))
                               for arm in ('C','E')},
            'library_comparison':{key:library[key] for key in ('query_count','supported','disagreements','unknown')},
            'new_target_junit_runs':len(revisions)+len(list((HERE/'junit-probes').glob('*/result.json'))),
            'oracle_batches':{'preparation_attempts_self_reported':4,'formal_retained_batches':1},
            'scope':'one development task, one projected property, constant-return Mockito subset; not held-out generalization or causal component isolation'}
    (HERE/'revision-provenance.json').write_text(json.dumps(provenance,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    (HERE/'summary.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({k:result[k] for k in ('decided','unknown','decided_disagreements','revision_passes','new_target_junit_runs')}))


if __name__=='__main__':
    main()
