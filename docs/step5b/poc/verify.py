"""Offline consistency checks of persisted evidence; no legacy data access."""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
E = ROOT / 'evidence'
load = lambda name: json.loads((E / name).read_text())
manifest = json.loads((ROOT / 'input-manifest.json').read_text())
attempts = {e['run']: e for e in load('attempts.json')}
assert attempts['java-build-3']['metrics']['command_exit'] == 1
assert 'NoSuchElementException' in attempts['java-build-3']['diagnostic_excerpt']
assert attempts['python-slice-2']['metrics']['command_exit'] == 124
assert '--no-default-exclude' in attempts['java-build-3']['execution']['command']
assert '--no-default-exclude' not in attempts['java-build-2']['execution']['command']
for lang in ('java', 'python'):
    graph = load(f'{lang}-edges.json')
    allowed = {f['path'] for f in manifest[lang]['files']}
    assert set(graph['file_nodes']) <= allowed | {'<unknown>', 'N/A'}
    expected = allowed if lang == 'python' else {p for p in allowed if '/test/' not in p}
    assert expected <= set(graph['file_nodes'])
    if lang == 'java':
        assert not any('/test/' in f for f in graph['file_nodes'])
        assert graph['coverage'] == 'test excluded; production subset only'
    assert graph['cpg_sha256'] == attempts[f'{lang}-build-2']['artifacts']['cpg.bin']['sha256']
    assert all(graph['edge_counts'][key] > 0 for key in ('CFG','CDG','REACHING_DEF'))
    for edge in graph['selected_edges']:
        assert edge['label'] in {'CFG','CDG','REACHING_DEF'}
        for node in (edge['source'],edge['target']):
            if node['file'] in allowed and node['line'] is not None:
                assert any(s['logical_path'] == node['file'] and s['start_line'] <= node['line'] <= s['end_line']
                           for s in manifest[lang]['sources']), node
    facts = load(f'{lang}-joern.json')
    assert facts['calls'] and facts['dataflow_paths'] and not facts['paths_truncated']
    slices = load(f'{lang}-slice.json')
    assert (len(slices['nodes']), len(slices['edges'])) == ((97,125) if lang == 'java' else (237,226))
    run = 'java-slice-3' if lang == 'java' else 'python-slice-4'
    assert '--slice-depth 4' in attempts[run]['execution']['command'][-1]
    assert 'slices.json' in attempts[run]['artifacts']
    if lang == 'python':
        assert len({n['parentMethod'] for n in slices['nodes']}) == 4
    ids = {n['id'] for n in slices['nodes']}
    assert all(e['src'] in ids and e['dst'] in ids and e['label'] == 'REACHING_DEF' for e in slices['edges'])
    assert all(n.get('parentFile') in allowed for n in slices['nodes'])
assert not any(e['label'] == 'REACHING_DEF' and e['source'].get('name') == 'getJavaVersion'
               and e['source']['line'] == 69 and e['target'].get('name') == '<operator>.lessEqualsThan'
               for e in load('java-edges.json')['selected_edges'])
assert load('java-spoon.json')['status'] == 'success'
assert all(f['status'] == 'parsed' for f in load('python-ast.json')['files'])
for p in E.glob('*.json'):
    assert '/home/haon/' not in p.read_text(), p
for name, digest in load('probe-hashes.json').items():
    assert hashlib.sha256((ROOT / 'poc' / name).read_bytes()).hexdigest() == digest, name
prompt = ROOT.parents[1] / 'src/mock_spec/prompts/scenario_v0.1.txt'
assert hashlib.sha256(prompt.read_bytes()).hexdigest() == 'e7bf55a8bb78b52701ab20bbee894513931642b772bc1a08bda8a79a5cb61e6d'
print('PASS: source allowlists/spans, graph and slice structure, failures, probe hashes, frozen prompt')
