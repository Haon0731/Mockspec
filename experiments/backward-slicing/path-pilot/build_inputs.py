"""Freeze common reviewed source for every arm, then mechanically ablate summaries."""
import hashlib
import json
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from prepare import CASES, write  # noqa: E402
from mock_spec.adapters import java, python  # noqa: E402
from mock_spec.validation import SourceStore  # noqa: E402


def common(root):
    data = {'tasks': {}, 'visible_sources': {}, 'limitations': [
        'Sources are a bounded reviewed projection; unavailable dependency implementations remain unknown.',
        'All generation arms receive exactly this source, including inherited methods.']}
    for name in ('flask','cloudfoundry'):
        language, registry, dataset, snapshot, case = CASES[name]
        task, repository = (java if language=='java' else python).load_task_with_repository(
            root/registry/'formal-snapshots'/dataset/snapshot, case_id=case)
        store = SourceStore(task, repository=repository)
        retained = json.loads((HERE.parent/'results'/name/'sources.json').read_text())['sources']
        if name == 'cloudfoundry':
            store.search(path_pattern='src/main/**/*.java', max_output_chars=30000)
        for source in retained:
            start = source['start_line']
            locator = source['source_id'] if source['source_id'] in ('test','mut','imports','processor') else source['logical_path']
            fresh = store.read(locator, start, start+len(source['text'].splitlines())-1)
            if fresh.text.rstrip('\n') != source['text'].rstrip('\n'):
                raise ValueError('reviewed source projection changed')
        data['tasks'][name] = task.to_dict()
        data['visible_sources'][name] = retained
        repository.close()
    folder = HERE/'inputs'
    folder.mkdir(exist_ok=True)
    target = folder/'common.json'
    if target.exists():
        raise FileExistsError(target)
    write(target, data)
    write(folder/'S.json', {'condition': 'source_only', 'extra_evidence': None})
    write(folder/'common-manifest.json', {'sha256':hashlib.sha256(target.read_bytes()).hexdigest(),
                                         'bytes':target.stat().st_size, 'fresh_adapter_and_source_check':True})


def summaries():
    """Remove values and effects mechanically for G, retaining all of them for R."""
    folder = HERE/'inputs'
    py = json.loads((HERE/'python-raw.json').read_text())
    java_raw = json.loads((HERE/'java-raw.json').read_text())
    guards = {'flask': {'backend': py['backend'], 'limitations': py['limitations'],
                        'paths': [{'path_id': p['path_id'], 'guards': p['guards']}
                                  for p in py['paths']]},
              'cloudfoundry': {'backend': java_raw['backend'],
                              'limitations': java_raw['limitations'], 'methods': []}}
    for method in java_raw['methods']:
        guards['cloudfoundry']['methods'].append({
            'qualified_signature': method['qualified_signature'], 'location': method['location'],
            'guard_sites': [{'location': site.get('location', method['location']),
                             'guards': site['guards']}
                            for kind in ('returns', 'calls') for site in method[kind]],
            'unsupported': method['unsupported']})
    for arm, value in [('G', guards), ('R', {'flask': py, 'cloudfoundry': java_raw})]:
        target = folder/f'{arm}.json'
        if target.exists():
            raise FileExistsError(target)
        write(target, {'condition': arm, 'extra_evidence': value})
    write(folder/'manifest.json', {p.name: {'sha256': hashlib.sha256(p.read_bytes()).hexdigest(),
                                          'bytes': p.stat().st_size}
                                   for p in [folder/'common.json', folder/'S.json', folder/'G.json',
                                             folder/'R.json', HERE/'python-raw.json', HERE/'java-raw.json']})

if __name__ == '__main__':
    import argparse
    parser=argparse.ArgumentParser()
    parser.add_argument('--old-root',type=Path)
    parser.add_argument('--summaries',action='store_true')
    args=parser.parse_args()
    if args.summaries:
        summaries()
    elif args.old_root:
        common(args.old_root)
    else:
        parser.error('--old-root or --summaries is required')
