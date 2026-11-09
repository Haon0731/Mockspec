"""One finite comparison of local summaries with the actual frozen library."""
import argparse
import json
from pathlib import Path

from library_oracle import evaluate as runtime_evaluate
from local_check import Endpoint, Unknown, evaluate


HERE=Path(__file__).resolve().parent


def lit(value):
    return {'kind':'literal','value':value}


def call(owner,name,args=(),target=None):
    return {'kind':'call','owner':owner,'name':name,'target':target,'arguments':list(args)}


def make_queries():
    uri='org.springframework.web.util.UriComponentsBuilder'
    queries=[]
    for base in ('http://example/Base/','https://host/x'):
        for suffix in ('health','/actuator/health','actuator/health','/actuator//health',
                       'actuator/%68ealth','actuator/{id}','','/','café/health'):
            # Include a nested builder-to-string result as the outer base, as in the composed source.
            inner=call(uri,'toUriString',target=call(uri,'path',[lit('/')],call(uri,'fromUriString',[lit(base)])))
            outer=call(uri,'path',[lit(suffix)],call(uri,'path',[lit('/')],call(uri,'fromHttpUrl',[inner])))
            queries.append({'id':f'uri_{len(queries)}','expr':call(uri,'toUriString',target=outer)})
    for name in ('hasText','isEmpty'):
        for value in (None,'',' ','\t','health',' \t health','é','\u00a0'):
            queries.append({'id':f'string_{len(queries)}','expr':call('org.springframework.util.StringUtils',name,[lit(value)])})
    queries.append({'id':'endpoint','expr':call('org.springframework.boot.actuate.endpoint.EndpointId','of',[lit('health')])})
    return queries


def run(old_root):
    request=HERE/'library-request.json'
    if request.exists():
        raise FileExistsError(request)
    queries=make_queries()
    request.write_text(json.dumps({'queries':queries},ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    runtime=runtime_evaluate(request,HERE/'library-validation',old_root)
    if runtime['status']!='ok':
        raise RuntimeError('library oracle failed: '+runtime['status'])
    actual={row['id']:row for row in runtime['results']}
    comparisons=[]
    for query in queries:
        record={'id':query['id'],'runtime':actual[query['id']]}
        try:
            local=evaluate(query['expr'])
            local=local.value if isinstance(local,Endpoint) else local
            record.update(local_status='value',local_value=local,
                          agrees=actual[query['id']]['status']=='value' and actual[query['id']]['value']==local)
        except Unknown as error:
            record.update(local_status='unknown',reason=str(error),agrees=None)
        comparisons.append(record)
    result={'query_count':len(comparisons),
            'supported':sum(r['local_status']=='value' for r in comparisons),
            'disagreements':sum(r['agrees'] is False for r in comparisons),
            'unknown':sum(r['local_status']=='unknown' for r in comparisons),
            'comparisons':comparisons,
            'scope':'finite differential check of declared summaries; not a proof over all library inputs'}
    (HERE/'library-validation/comparison.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({k:result[k] for k in ('query_count','supported','disagreements','unknown')}))


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--old-root',type=Path,required=True)
    run(parser.parse_args().old_root)
