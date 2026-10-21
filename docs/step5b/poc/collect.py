"""Persist bounded analysis facts/metrics, never CPGs or complete source trees."""
import hashlib
import json
from pathlib import Path
from graph_facts import read_graph

ROOT = Path(__file__).resolve().parents[1]
RUNS = Path('/tmp/mockspec-step5b-runs')
OUT = ROOT / 'evidence'


def dump(name, data):
    (OUT / name).write_text(json.dumps(data, indent=2) + '\n')


def main():
    OUT.mkdir(exist_ok=True)
    attempts = []
    for folder in sorted(RUNS.iterdir()):
        if not (folder / 'stage.json').exists():
            continue
        stage = json.loads((folder / 'stage.json').read_text())
        entry = {'run': folder.name, 'execution': stage}
        if (folder / 'metrics.json').exists():
            entry['metrics'] = json.loads((folder / 'metrics.json').read_text())
        stderr = (folder / 'stderr.txt').read_text() if (folder / 'stderr.txt').exists() else ''
        # Host source paths must never occur in analysis-tool output.
        for f in folder.rglob('*'):
            if f.is_file() and f.suffix in {'.txt', '.xml', '.json'} and f.name not in {'stage.json'}:
                assert b'/home/haon/' not in f.read_bytes(), f'Host path leakage: {f.name}'
        entry['diagnostic_excerpt'] = stderr[:2200]
        entry['artifacts'] = {str(f.relative_to(folder)): {'bytes': f.stat().st_size,
            'sha256': hashlib.sha256(f.read_bytes()).hexdigest()}
            for f in folder.rglob('*') if f.is_file() and f.name != 'stage.json'}
        entry['assessment'] = stage['status']
        if folder.name == 'java-build-2':
            entry['assessment'] = 'incomplete: default frontend exclusion omitted masked test'
        if 'slice-' in folder.name and not any(folder.glob('slice*')):
            entry['assessment'] = 'failed: no slice artifact (exit zero is not success)'
        if folder.name.startswith('java-') and folder.name in {'java-query-2','java-export-1','java-slice-3'}:
            entry['coverage'] = 'production subset only; no test coverage; build-2'
        attempts.append(entry)
    dump('attempts.json', attempts)
    dump('preflight.json', {name: (RUNS / 'preflight-2' / name).read_text()
                           for name in ('stdout.txt', 'stderr.txt')})
    for lang in ('java', 'python'):
        facts = json.loads((RUNS / f'{lang}-query-2/facts.json').read_text())
        facts['coverage'] = 'test excluded; production subset only' if lang == 'java' else 'both approved projected files'
        dump(f'{lang}-joern.json', facts)
        graph = read_graph(RUNS / f'{lang}-export-1/export/export.xml')
        graph['coverage'] = facts['coverage']
        graph['build_run'] = f'{lang}-build-2'
        graph['cpg_sha256'] = hashlib.sha256((RUNS / f'{lang}-build-2/cpg.bin').read_bytes()).hexdigest()
        dump(f'{lang}-edges.json', graph)
    for run, name in [('spoon-1','java-spoon.json'), ('python-ast-1','python-ast.json')]:
        dump(name, json.loads((RUNS / run / 'stdout.txt').read_text()))
    for lang, run in [('java', 'java-slice-3'), ('python', 'python-slice-4')]:
        data = json.loads((RUNS / run / 'slices.json').read_text())
        assert data['nodes'] and data['edges'], 'Empty slice is not success'
        for node in data['nodes']:
            if 'code' in node:
                node['code'] = node['code'][:240]
        data['coverage'] = 'production subset only' if lang == 'java' else 'both approved files; method filter not effective'
        dump(f'{lang}-slice.json', data)
    dump('probe-hashes.json', {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
         for p in sorted((ROOT / 'poc').iterdir()) if p.is_file()})
    print(f'Collected {len(attempts)} attempts; no host path in tool stdout/stderr/GraphML')


if __name__ == '__main__':
    main()
