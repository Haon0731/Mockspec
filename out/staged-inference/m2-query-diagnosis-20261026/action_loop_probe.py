"""Directed real-model tool protocol check; NOT autonomous exploration evidence.

Uses only the existing QUERY stage and QueryExecutor on the reviewed task.
Each expected tool is specified by this diagnostic test, never by production policy.
"""
import asyncio
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path('experiments/staged-inference').resolve()))
from controller import QueryResult, QueryAssessment, StagedRunConfig, _call_stage, canonical_json
from query import QueryExecutor
from agents.models.openai_responses import OpenAIResponsesModel
from openai import AsyncOpenAI
from mock_spec.adapters.python import load_task
from mock_spec.budget import InferenceBudget, UsageLedger

ROOT = Path(sys.argv[1])
ROOT.mkdir(parents=True, exist_ok=False)
SNAPSHOT = Path('/home/haon/projects/Stub-Tester/.omx/developer-realistic-registry-max-fix23/formal-snapshots/python-benchmark-new/241ef457f8d071c23e6acd658b684b11b54f13fa955e097d6bebe35dc755f47d')


def save(name, value):
    text = json.dumps(value, ensure_ascii=False, indent=2)
    text = text.replace(os.environ['OPENAI_API_KEY'], '<REDACTED>')
    with (ROOT / name).open('x') as f:
        f.write(text + '\n')


async def main():
    task = load_task(SNAPSHOT, case_id='flask_308')
    extract = json.loads(Path('out/staged-inference/m1-live-20261026/run/inference_record.json').read_text())['stages'][0]['result']
    config = StagedRunConfig(run_mode='live_inference', model_name=os.environ['MOCKSPEC_MODEL'],
        variant='C', max_model_calls=4, max_output_tokens=8192, max_total_tokens=60000,
        timeout_seconds=300, parsing_timeout_seconds=90, max_cost_usd=Decimal('1'),
        input_cost_per_million=Decimal('.30'), output_cost_per_million=Decimal('1.20'))
    ledger = UsageLedger(InferenceBudget(max_turns=4, max_tool_calls=3,
        max_output_tokens=8192, max_total_tokens=60000,
        max_cost_usd=Decimal('1'), input_cost_per_million=Decimal('.30'), output_cost_per_million=Decimal('1.20')))
    started = time.monotonic()
    deadline = started + 300
    executor = QueryExecutor(task, max_attempts=3, max_output_chars=16000,
        analysis_timeout_seconds=15, deadline=deadline)
    calls, responses, results, assessments = [], [], [], []
    expected = ['inspect_source', 'inspect_program_structure', 'inspect_dependency_usage', None]
    save('preflight.json', {'purpose':'Directed protocol smoke; tools specified by tester, not autonomous selection',
        'model':config.model_name, 'case_id':task.case_id, 'tools':expected[:-1], 'max_model_calls':4,
        'max_queries':3, 'max_output_tokens':8192, 'max_total_tokens':60000, 'timeout_seconds':300,
        'stage_timeout_seconds':90, 'max_cost_usd':'1.00','input_rate':'0.30','output_rate':'1.20',
        'automatic_retries':0, 'input_hash':hashlib.sha256(canonical_json(task.to_dict())).hexdigest(),
        'controller_sha256':hashlib.sha256(Path('experiments/staged-inference/controller.py').read_bytes()).hexdigest()})
    passed = False
    error = None
    try:
        async with AsyncOpenAI(max_retries=0) as client:
            model = OpenAIResponsesModel(model=config.model_name, openai_client=client)
            for index, tool in enumerate(expected):
                instruction = (
                    'This is an explicitly directed engineering test of the existing tool protocol, not scenario quality evaluation. '
                    'Assess the previous receipt if present, accurately describing what the returned evidence establishes and its limitations. '
                )
                if tool:
                    instruction += f'Request {tool} on source_id mut to inspect the actual conditional aborter delegation and fallback. '
                    if tool == 'inspect_source':
                        instruction += 'Use start_line 281 and end_line 285. '
                    instruction += 'Use an existing candidate ID; state a concrete dependency-behavior question. Do not finish yet.'
                else:
                    instruction += 'All requested tool checks are done. Assess the last receipt and finish_queries with query_attempt=null.'
                result = await _call_stage('QUERY', QueryResult,
                    {'stage':'QUERY', 'visible_task':task.to_dict(), 'extract':extract,
                     'query_receipts':executor.attempts, 'engineering_protocol_check':instruction},
                    model=model, config=config, ledger=ledger, responses=responses,
                    model_calls=calls, deadline=deadline)
                results.append(result.model_dump(mode='json'))
                assert ledger.usage_complete, 'Unknown model usage; stop before further calls'
                if index:
                    assessment = QueryAssessment.model_validate(result.previous_receipt_assessment)
                    assert assessment.attempt_sequence == executor.attempts[-1]['sequence']
                    executor.attempts[-1]['assessment'] = assessment.model_dump(mode='json')
                    executor.attempts[-1]['assessment_status'] = 'complete'
                    assessments.append(assessment.model_dump(mode='json'))
                else:
                    assert result.previous_receipt_assessment is None
                if tool:
                    assert result.decision == 'request_query'
                    receipt = executor.register(result.query_attempt)
                    assert receipt['tool'] == tool
                    receipt = await executor.execute_registered(receipt,
                        candidate_ids={c['scenario_id'] for c in extract['candidates']})
                    assert receipt['status'] in {'ok','incomplete','unresolved'}, receipt['status']
                    assert receipt['result'], 'Missing actual tool observation'
                else:
                    assert result.decision == 'finish_queries' and result.query_attempt is None
            passed = True
    except Exception as exc:
        error = {'type':type(exc).__name__, 'message':str(exc)}
    finally:
        save('trace.json', {'passed':passed, 'directed_protocol_check':True,
             'autonomous_exploration_validated':False, 'semantic_quality_evaluated':False,
             'input_snapshot':task.to_dict(), 'model_calls':calls, 'model_responses':responses,
             'query_log':executor.attempts, 'query_results':results, 'assessments':assessments,
             'usage':ledger.to_dict(), 'elapsed_seconds':time.monotonic()-started, 'error':error})
    assert passed, 'Directed action loop failed; inspect trace.json'

asyncio.run(main())
