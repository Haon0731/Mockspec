import hashlib,json,os,sys
from pathlib import Path
sys.path.insert(0,'src');sys.path.insert(0,'experiments/staged-inference')
from controller import canonical_json
from mock_spec.adapters.java import load_task_with_repository
from mock_spec.validation import SourceStore
from mock_spec.scenario import validate_contract
root=Path('out/staged-inference/issue17-query-recovery-20261027/live');p=root/'run'
r=json.loads((p/'inference_record.json').read_text());m=json.loads((p/'run_manifest.json').read_text());f=json.loads((root/'preflight.json').read_text())
h=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
art={n:(h(p/n) if (p/n).is_file() else None)==digest for n,digest in m['artifact_sha256'].items()}
code={str(Path(base)/n):h(Path(base)/n)==digest for base,key in [('src/mock_spec','files_sha256'),('experiments/staged-inference','research_files_sha256')] for n,digest in m['code'][key].items()}
payloads=[hashlib.sha256(canonical_json(c['payload'])).hexdigest()==c['payload_sha256'] for c in r['model_calls']]
links=[r['model_responses'][ref['response_index']]['response_id']==ref['response_id'] for c in r['model_calls'] for ref in c['response_refs']]
t,repo=load_task_with_repository(Path(f['snapshot']),case_id=f['case_id']);store=SourceStore(t,repository=repo)
observations=[]
try:
 for o in r['source_observations']:
  locator=o['source_id'] if o['source_id'] in {t.masked_test.source_id,t.mut_source.source_id,*[s.source_id for s in t.supporting_sources]} else o['logical_path']
  src=store.read(locator,o['start_line'],o['end_line'])
  match=src.text==o['text'] and src.source_id==o['source_id'] and hashlib.sha256(src.text.encode()).hexdigest()==o['sha256']
  observations.append({'source_id':o['source_id'],'range':[o['start_line'],o['end_line']],'response_id':o['response_id'],'exact_text_identity':match})
  store.record_observation(src,response_id=o['response_id'])
 valid_contract=None
 if (p/'scenario_contract.json').exists():
  validate_contract(json.loads((p/'scenario_contract.json').read_text()),t,source_store=store);valid_contract=True
finally:repo.close()
secret=os.environ.get('OPENAI_API_KEY','');assert secret
no_secret=all(secret.encode() not in file.read_bytes() for file in root.rglob('*') if file.is_file())
checks={'artifact_hashes':art,'code_hashes':code,'input_and_commit_match':m['input_hash']==f['input_hash'] and m['code']['commit']==f['git_commit'],'payload_hashes_match':all(payloads),'response_links_match':all(links),'source_observations':observations,'final_contract_revalidated':valid_contract,'requests_started':m['usage']['requests_started'],'requests_completed':m['usage']['requests_completed'],'usage_complete':m['usage']['usage_complete'],'total_tokens':m['usage']['total_tokens'],'rate_estimated_cost_usd':m['usage']['rate_estimated_cost_usd'],'tool_attempts_registered':len(r['query_log']),'format_repair_allowance':r.get('format_repair_allowance'),'format_repair_entries':len(r['format_repair_log']),'actual_credential_absent':no_secret,'verification_scope':'Post-run read-only reconstruction of only recorded observations; no verification evidence supplied to inference'}
assert all(art.values()) and all(code.values()) and all(payloads) and all(links) and all(o['exact_text_identity'] for o in observations) and checks['input_and_commit_match'] and no_secret
(root/'artifact-checks.json').write_text(json.dumps(checks,ensure_ascii=False,indent=2)+'\n')
print(json.dumps({k:v for k,v in checks.items() if k not in ['code_hashes','source_observations']},ensure_ascii=False))
