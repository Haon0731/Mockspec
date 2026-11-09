import importlib.util
from pathlib import Path
import sys

HERE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('goal_local_check',HERE/'local_check.py')
module=importlib.util.module_from_spec(spec)
sys.modules[spec.name]=module
spec.loader.exec_module(module)


def literal(v):
    return {'kind':'literal','value':v}


def call(owner,name,args=(),target=None):
    return {'kind':'call','owner':owner,'name':name,'arguments':list(args),'target':target}


def test_uri_summary_appends_paths_but_abstains_on_unmodeled_encoding():
    owner='org.springframework.web.util.UriComponentsBuilder'
    base=call(owner,'fromHttpUrl',[literal('http://host/Base/')])
    path=call(owner,'path',[literal('/part//item')],base)
    assert module.evaluate(call(owner,'toUriString',target=path))=='http://host/Base/part/item'
    import pytest
    with pytest.raises(module.Unknown):
        module.evaluate(call(owner,'path',[literal('a%2Fb')],base))


def test_prefix_guard_excludes_unreached_unconfigured_call_and_results_are_fresh():
    symbol=lambda n:{'kind':'symbol','id':n}
    event=lambda n:{'call_id':n,'receiver':'dep','name':'next','arguments':[],
                    'outcome':'normal','result_symbol':n}
    guard={'condition':call('org.springframework.util.StringUtils','hasText',[symbol('one')]),'polarity':False}
    goal={'goal_id':'g','expected':'second','paths':[
        {'path_id':'p','events':[event('one'),event('two')],
         'guards':[],'outcome':'return','value':symbol('two'),'unknowns':[]},
        {'path_id':'q','events':[event('one'),dict(event('three'),name='missing')],
         'guards':[guard],'outcome':'return','value':symbol('three'),'unknowns':[]}]}
    candidate={'bindings':[{'receiver':'dep','name':'next','arguments':[],
                             'returns':['first','second']}],'verifications':[],'unsupported':[]}
    result=module.check(goal,candidate)
    assert result['verdict']=='candidate_consistent'
    assert result['paths'][0]['response_symbols']=={'one':'first','two':'second'}
    assert result['paths'][1]['verdict']=='excluded'
