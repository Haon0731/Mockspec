"""Freeze a compact automatic goal/check packet for the revision pilot."""
import hashlib
import json
from pathlib import Path

from local_check import check


HERE=Path(__file__).resolve().parent


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    folder=HERE/'inputs'
    folder.mkdir(exist_ok=False)
    original=HERE.parent/'path-pilot/candidates/R1.json'
    common=HERE.parent/'path-pilot/inputs/common.json'
    raw=json.loads((HERE/'goal-raw.json').read_text(encoding='utf-8'))
    bindings=json.loads((HERE/'bindings.json').read_text(encoding='utf-8'))['candidates']
    candidate=next(b for b in bindings if Path(b['candidate']).stem=='R1')
    goals=[]
    evidence_catalog={}
    assumption_catalog={}
    def intern(items,catalog,prefix):
        keys={json.dumps(value,sort_keys=True):key for key,value in catalog.items()}
        refs=[]
        for item in items:
            fingerprint=json.dumps(item,sort_keys=True)
            if fingerprint not in keys:
                identifier=f'{prefix}{len(catalog)+1}'
                keys[fingerprint]=identifier
                catalog[identifier]=item
            refs.append(keys[fingerprint])
        return refs
    for goal in raw['goals']:
        paths=[]
        for path in goal['paths']:
            paths.append({**{key:path[key] for key in
                          ('path_id','guards','value','events','outcome','unknowns')},
                          'evidence_refs':intern(path['evidence'],evidence_catalog,'ev'),
                          'assumption_refs':intern(path.get('assumptions',[]),assumption_catalog,'a')})
        goals.append({**{k:v for k,v in goal.items() if k!='paths'},'paths':paths})
    extra={'goals':goals,'evidence_catalog':evidence_catalog,'assumption_catalog':assumption_catalog,
           'analysis_limitations':raw['limitations'],
           'check_of_supplied_candidate':[check(goal,candidate) for goal in raw['goals']],
           'library_summary_scope':'Spring 5.1.2.RELEASE; null/ASCII predicates and restricted HTTP(S) URI path construction; no query/fragment/templates/percent-encoding/dot segments',
           'checker_scope':'one projected property with recorded normal-completion obligations, not complete test correctness'}
    base={'candidate':json.loads(original.read_text(encoding='utf-8'))['cloudfoundry']}
    for arm,evidence in [('C',None),('E',extra)]:
        (folder/f'{arm}.json').write_text(json.dumps({**base,'extra_evidence':evidence},ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    files=[common,original,HERE/'GoalIR.java',HERE/'compose.py',HERE/'local_check.py',HERE/'goal-raw.json',
           HERE/'bindings.json',HERE/'revision-prompt.txt',folder/'C.json',folder/'E.json']
    manifest={str(path.relative_to(HERE.parent)):{'sha256':digest(path),'bytes':path.stat().st_size} for path in files}
    (folder/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'common_bytes':common.stat().st_size,'control_packet_bytes':(folder/'C.json').stat().st_size,
                      'evidence_packet_bytes':(folder/'E.json').stat().st_size}))


if __name__=='__main__':
    main()
