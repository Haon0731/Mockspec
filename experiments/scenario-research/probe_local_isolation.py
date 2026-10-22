"""Host-only mechanism probes; no target code or agent-selected executable."""
import argparse
import asyncio
import json
from pathlib import Path
from local_solver import _run_worker_for_test, check_local_model


async def probe():
    results = {}
    for mode, options in [('parse_error', {}), ('crash', {}),
                          ('hang', {'timeout_seconds': .1}),
                          ('output', {'max_output_bytes': 1024}), ('memory', {}), ('worker_error', {})]:
        failure = await _run_worker_for_test(mode, **options)
        after = await check_local_model(
            {'sort':'Bool','operator':'is_none','literal':None,'bounds':None},
            [{'operator':'is_none','literal':None}])
        results[mode] = {'failure': failure, 'host_survived': True,
                         'subsequent_check': after['status'],
                         'subsequent_check_sufficient': after['locally_sufficient']}
    return {'scope': 'synthetic process isolation probes only', 'probes': results}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = asyncio.run(probe())
    with args.output.open('x') as stream:
        json.dump(result, stream, indent=2)
        stream.write('\n')
    print(json.dumps({k:v['failure']['status'] for k,v in result['probes'].items()}))
