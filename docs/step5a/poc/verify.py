"""Offline assertions for recorded capability evidence and installed SDK wrapper."""
import asyncio
import json
from pathlib import Path
from agents import function_tool, set_tracing_disabled
from agents.tool_context import ToolContext

set_tracing_disabled(True)

root = Path(__file__).resolve().parents[1]
r = json.loads((root / 'results.json').read_text())
python = next(c for c in r['cases'] if c['language'] == 'python')
by_source = {a['source_id']: a for a in python['analyses']}
assert all('text' not in f for a in by_source.values() for f in a['facts'])
mut = by_source['mut']['facts']
calls = {f['callee']: f for f in mut if f['kind'] == 'Call'}
assert set(calls) == {'_cv_app.get', 'ctx.app.aborter', '_wz_abort'}
assert calls['_cv_app.get']['start_line'] == 282
assert calls['ctx.app.aborter']['guards'] == [{'if_line': 282, 'polarity': True}]
assert calls['_wz_abort']['start_line'] == 285
assert calls['_wz_abort']['guards'] == []  # sequential continuation, not else
assert any(f['kind'] == 'NamedExpr' and f['assigned_name'] == 'ctx' for f in mut)
assert {f['start_line'] for f in by_source['test']['facts'] if f['kind'] == 'Assert'} == {120, 126}
assert r['python_parse_failure']['status'] == 'parse_error'
assert r['spoon_startup']['exit_code'] != 0
assert 'UnsupportedClassVersionError' in r['spoon_startup']['stderr']

@function_tool(failure_error_function=None)
async def get_recorded_facts(source_id: str) -> str:
    """Return only recorded facts for one admitted source ID."""
    return json.dumps(by_source[source_id])

async def check_tool():
    context = ToolContext(context=None, tool_name="get_recorded_facts",
                          tool_call_id="offline-probe", tool_arguments='{"source_id":"mut"}')
    value = await get_recorded_facts.on_invoke_tool(context, '{"source_id":"mut"}')
    assert json.loads(value)['source_id'] == 'mut'
    try:
        await get_recorded_facts.on_invoke_tool(context, '{"source_id":"../../private"}')
    except KeyError:
        pass
    else:
        raise AssertionError('Unknown source ID must fail')

asyncio.run(check_tool())
assert get_recorded_facts.params_json_schema['additionalProperties'] is False
print('PASS: call spans, lexical guards, named expression, assertions, parse failure, runtime failure, SDK invocation and rejected unknown ID')
