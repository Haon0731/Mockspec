import asyncio, json, os, sys, time
from pathlib import Path
from decimal import Decimal
import httpx2 as httpx
from openai import AsyncOpenAI
sys.path.insert(0,str(Path('experiments/staged-inference').resolve()))
from controller import _call_stage, QueryResult, StagedRunConfig, canonical_json
from agents.models.openai_responses import OpenAIResponsesModel
from mock_spec.budget import InferenceBudget, UsageLedger
from mock_spec.adapters.python import load_task

ROOT=Path(sys.argv[1])
ROOT.mkdir(parents=True,exist_ok=False)
SECRET=os.environ['OPENAI_API_KEY']
def safe(value):
    if isinstance(value,str):return value.replace(SECRET,'<REDACTED>')
    if isinstance(value,dict):return {k:safe(v) for k,v in value.items()}
    if isinstance(value,list):return [safe(v) for v in value]
    return value

def save(name,value):
    with (ROOT/name).open('x') as f:json.dump(safe(value),f,ensure_ascii=False,indent=2);f.write('\n')

async def main():
    old=Path('out/staged-inference/issue17-m2-20261026/run')
    task=json.loads((old/'input_snapshot.json').read_text())
    record=json.loads((old/'inference_record.json').read_text())
    snapshot=Path('/home/haon/projects/Stub-Tester/.omx/developer-realistic-registry-max-fix23/formal-snapshots/python-benchmark-new/241ef457f8d071c23e6acd658b684b11b54f13fa955e097d6bebe35dc755f47d')
    reviewed=load_task(snapshot,case_id='flask_308')
    assert canonical_json(reviewed.to_dict())==canonical_json(task)
    save('preflight.json',{'purpose':'Single QUERY provider conformance replay from newly validated EXTRACT; no tool dispatch, not autonomous exploration or full M2','model':os.environ['MOCKSPEC_MODEL'],'max_calls':1,'max_output_tokens':8192,'max_total_tokens':60000,'timeout_seconds':120,'max_cost_usd':'1.00','input_rate':'0.30','output_rate':'1.20','automatic_retries':0,'controller_sha256':__import__('hashlib').sha256(Path('experiments/staged-inference/controller.py').read_bytes()).hexdigest(),'reviewed_input_matches_prior_snapshot':True})
    async def request_hook(request):
        save('request.json',{'method':request.method,'url':str(request.url),'body':json.loads(request.content)})
    async def response_hook(response):
        await response.aread()
        try:body=response.json()
        except ValueError:body={'non_json_body':response.text[:2000]}
        save('response.json',{'http_status':response.status_code,'request_id':response.headers.get('x-request-id'),'body':body})
    async with httpx.AsyncClient(event_hooks={'request':[request_hook],'response':[response_hook]}) as http:
        client=AsyncOpenAI(max_retries=0,http_client=http)
        model=OpenAIResponsesModel(model=os.environ['MOCKSPEC_MODEL'],openai_client=client)
        config=StagedRunConfig(repository_read=True,run_mode='live_inference',model_name=os.environ['MOCKSPEC_MODEL'],variant='C',max_model_calls=1,max_output_tokens=8192,max_total_tokens=60000,timeout_seconds=120,parsing_timeout_seconds=120,max_cost_usd=Decimal('1'),input_cost_per_million=Decimal('.30'),output_cost_per_million=Decimal('1.20'))
        ledger=UsageLedger(InferenceBudget(max_output_tokens=8192,max_total_tokens=60000,max_cost_usd=Decimal('1'),input_cost_per_million=Decimal('.30'),output_cost_per_million=Decimal('1.20')))
        responses=[]
        model_calls=[]
        try:
            result=await _call_stage('QUERY',QueryResult,{'stage':'QUERY','visible_task':task,'extract':record['stages'][0]['result'],'query_receipts':[]},model=model,config=config,ledger=ledger,responses=responses,model_calls=model_calls,deadline=time.monotonic()+120)
            save('outcome.json',{'status':'parsed','result':result.model_dump(mode='json'),'usage':ledger.to_dict(),'model_calls':model_calls})
        except Exception as exc:
            chain=[];current=exc
            while current is not None and len(chain)<5:
                chain.append({'type':type(current).__name__,'message':str(current)[:3000],'status_code':getattr(current,'status_code',None),'request_id':getattr(current,'request_id',None)})
                current=current.__cause__ or current.__context__
            save('outcome.json',{'status':'error','chain':chain,'usage':ledger.to_dict(),'model_calls':model_calls})
        finally:await client.close()
asyncio.run(main())

assert json.loads((ROOT/'outcome.json').read_text())['status']=='parsed', 'QUERY did not parse; inspect diagnostic artifacts'
