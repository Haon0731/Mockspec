"""Check one composed goal using explicitly bounded, version-specific summaries.

This evaluates a concrete response proposal; it neither solves for new responses
nor proves the complete test or external dependency implementation correct.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
from pathlib import Path
import re


class Unknown(ValueError):
    pass


@dataclass(frozen=True)
class Uri:
    authority: str
    path: str


@dataclass(frozen=True)
class Endpoint:
    value: str


def safe_path(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9._~/-]*', value):
        raise Unknown('URI summary excludes encoding, templates, query, fragment and reserved characters')
    if any(part in ('.', '..') for part in value.split('/')):
        raise Unknown('URI summary excludes dot segments')
    return value


def evaluate(term, symbols=None):
    symbols = symbols or {}
    if not isinstance(term, dict):
        raise Unknown('invalid expression term')
    kind = term.get('kind')
    if kind == 'literal':
        return term['value']
    if kind == 'symbol':
        if term['id'] not in symbols:
            raise Unknown('unbound response '+term['id'])
        return symbols[term['id']]
    if kind == 'binary':
        left = evaluate(term['left'], symbols)
        op = term['operator']
        if op == 'OR' and left is True:
            return True
        if op == 'AND' and left is False:
            return False
        right = evaluate(term['right'], symbols)
        if op == 'PLUS' and isinstance(left, str) and isinstance(right, str):
            return left+right
        if op in ('EQ', 'NE') and all(v is None or isinstance(v, (str, bool, int)) for v in (left,right)):
            equal = type(left) is type(right) and left == right
            return equal if op == 'EQ' else not equal
        if op in ('OR', 'AND') and isinstance(left, bool) and isinstance(right, bool):
            return left or right if op == 'OR' else left and right
        raise Unknown('unsupported binary operation '+op)
    if kind == 'unary' and term.get('operator') == 'NOT':
        operand = evaluate(term['operand'], symbols)
        if isinstance(operand, bool):
            return not operand
        raise Unknown('non-boolean negation')
    if kind != 'call':
        raise Unknown('unsupported term '+str(kind))
    target = evaluate(term['target'], symbols) if term.get('target') is not None else None
    args = [evaluate(arg, symbols) for arg in term.get('arguments', [])]
    owner, name = term.get('owner'), term['name']
    if owner == 'org.springframework.util.StringUtils' and name in ('hasText','isEmpty') and len(args)==1:
        value = args[0]
        if value is not None and not isinstance(value, str):
            raise Unknown('StringUtils summary accepts null and ASCII strings')
        if isinstance(value, str) and (not value.isascii() or any(ord(c)<32 and c not in '\t\n\r\f\v\x1c\x1d\x1e\x1f' for c in value)):
            raise Unknown('StringUtils summary excludes unsupported characters')
        if name == 'isEmpty':
            return value is None or value == ''
        # Java Character.isWhitespace within the admitted ASCII subset.
        return value is not None and any(c not in ' \t\n\r\f\v\x1c\x1d\x1e\x1f' for c in value)
    if owner == 'org.springframework.boot.actuate.endpoint.EndpointId' and name == 'of' and len(args)==1:
        if not isinstance(args[0], str) or not re.fullmatch('[a-z][a-z0-9]*',args[0]):
            raise Unknown('EndpointId summary accepts lowercase alphanumeric ids')
        return Endpoint(args[0])
    if owner == 'org.springframework.web.util.UriComponentsBuilder':
        if name in ('fromHttpUrl','fromUriString') and len(args)==1 and target is None:
            if not isinstance(args[0],str):
                raise Unknown('URI base must be concrete text')
            match = re.fullmatch(r'(https?://[A-Za-z0-9.-]+(?::[0-9]+)?)(/.*)?',args[0])
            if not match:
                raise Unknown('URI summary accepts absolute HTTP(S) bases without query/fragment')
            return Uri(match[1],safe_path(match[2] or ''))
        if name == 'path' and isinstance(target,Uri) and len(args)==1:
            return Uri(target.authority,target.path+safe_path(args[0]))
        if name == 'toUriString' and isinstance(target,Uri) and not args:
            return target.authority+re.sub('/+','/',target.path)
    raise Unknown('unmodeled call '+str(owner)+'.'+name)


def key(receiver,name,arguments,symbols=None):
    values = [evaluate(arg,symbols) for arg in arguments]
    encoded = [({'endpoint_id':v.value} if isinstance(v,Endpoint) else v) for v in values]
    return receiver,name,json.dumps(encoded,sort_keys=True,ensure_ascii=False)


def known_false_guard(guards,symbols):
    for guard in guards:
        try:
            value=evaluate(guard['condition'],symbols)
        except Unknown:
            continue
        if isinstance(value,bool) and value != guard['polarity']:
            return True
    return False


def check(goal, candidate):
    if candidate.get('unsupported'):
        return {'verdict':'unknown','reason':'candidate contains unsupported configuration or assertions',
                'details':candidate['unsupported'],'paths':[]}
    bindings = {}
    try:
        for binding in candidate.get('bindings',[]):
            bound_key = key(binding['receiver'],binding['name'],binding['arguments'])
            if bound_key in bindings or not binding['returns']:
                raise Unknown('duplicate or empty response sequence')
            bindings[bound_key] = binding['returns']
    except Unknown as error:
        return {'verdict':'unknown','reason':str(error),'paths':[]}
    checked=[]
    for path in goal['paths']:
        symbols, counts = {}, {}
        record = {'path_id':path['path_id']}
        try:
            for event in path.get('events',[]):
                if known_false_guard(path.get('guards',[]),symbols):
                    record.update(verdict='excluded',reason='prefix guard false before next event')
                    break
                event_key = key(event['receiver'],event['name'],event['arguments'],symbols)
                if event_key not in bindings:
                    raise Unknown('no explicit response for '+str(event_key))
                if event['outcome']=='raise':
                    record.update(verdict='excluded',reason='configured normal return excludes this exception exit')
                    break
                ordinal = counts.get(event_key,0)
                counts[event_key]=ordinal+1
                responses=bindings[event_key]
                symbol=event['result_symbol']
                symbols[symbol['id'] if isinstance(symbol,dict) else symbol]=responses[min(ordinal,len(responses)-1)]
            else:
                guard_values = [evaluate(g['condition'],symbols) for g in path.get('guards',[])]
                if any(not isinstance(v,bool) for v in guard_values):
                    raise Unknown('non-boolean guard')
                if any(v != g['polarity'] for v,g in zip(guard_values,path.get('guards',[]))):
                    record.update(verdict='excluded',reason='guard false under this response proposal')
                elif path.get('unknowns'):
                    raise Unknown('path contains unsupported semantics: '+str(path['unknowns']))
                elif path['outcome']=='raise':
                    record.update(verdict='conflict',reason='selected path raises before asserted value is available')
                elif path['outcome']!='return':
                    raise Unknown('no supported normal result')
                else:
                    value=evaluate(path['value'],symbols)
                    record.update(actual=value,expected=goal['expected'],response_symbols=symbols)
                    if value != goal['expected']:
                        record.update(verdict='conflict',reason='constructed value differs from original assertion')
                    else:
                        for verification in candidate.get('verifications',[]):
                            expected_key=key(verification['receiver'],verification['name'],verification['arguments'],symbols)
                            if counts.get(expected_key,0)!=verification['count']:
                                record.update(verdict='conflict',reason='interaction count differs on selected path')
                                break
                        else:
                            record.update(verdict='candidate_consistent',reason='selected goal and supported interaction checks agree')
        except Unknown as error:
            record.update(verdict='unknown',reason=str(error))
        checked.append(record)
    verdicts={record['verdict'] for record in checked}
    if 'candidate_consistent' in verdicts:
        verdict='candidate_consistent'
    elif 'unknown' in verdicts or not checked:
        verdict='unknown'
    else:
        verdict='conflict'
    if verdict=='conflict' and goal.get('truncated'):
        verdict='unknown'
    return {'verdict':verdict,'goal_id':goal['goal_id'],'paths':checked,
            'scope':'projected goal under recorded normal-completion and library-summary assumptions; not whole-test validity'}


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--goals',type=Path,required=True)
    parser.add_argument('--bindings',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    goals=json.loads(args.goals.read_text(encoding='utf-8'))['goals']
    bindings=json.loads(args.bindings.read_text(encoding='utf-8'))
    candidates=bindings.get('candidates',bindings)
    if isinstance(candidates,list):
        candidates={Path(candidate['candidate']).stem:candidate for candidate in candidates}
    results={name:[check(goal,candidate) for goal in goals] for name,candidate in candidates.items()}
    args.output.write_text(json.dumps(results,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
