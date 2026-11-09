"""Bounded host driver. Never executes analyzed target code."""
import hashlib
import json
import subprocess
import tempfile
import time
from prepare import HERE, write
from mock_spec.java_analysis import _backend_paths


def run():
    backend = _backend_paths()
    if backend is None:
        raise RuntimeError('Pinned Spoon/JDK unavailable')
    jar, java, javac = backend
    metrics = {}
    with tempfile.TemporaryDirectory(prefix='mockspec-slice-') as work:
        start = time.perf_counter()
        subprocess.run([str(javac), '-cp', str(jar), '-d', work, str(HERE/'SliceProbe.java')], check=True, capture_output=True, timeout=60)
        metrics['compile_seconds'] = time.perf_counter() - start
        for name in ('cloudfoundry', 'jib'):
            directory = HERE/'results'/name
            data = json.loads((directory/'sources.json').read_text())
            test = next(s for s in data['sources'] if s['source_id'] == 'test')
            seeds = [{'source_id': 'test', 'line': i} for i, line in enumerate(test['text'].splitlines(), 1)
                     if line.strip().startswith(('assertEquals(', 'assertThat(', 'assertTrue(', 'assertFalse('))]
            payload = {'sources': data['sources'], 'seeds': seeds,
                       'boundary_types': ['PathMappedEndpoints'] if name == 'cloudfoundry' else ['ArtifactProcessor','StandardExplodedProcessor']}
            write(directory/'probe-input.json', payload)
            start = time.perf_counter()
            result = subprocess.run([str(java), '-Xmx768m', '-cp', f'{work}:{jar}', 'SliceProbe'],
                                    input=json.dumps(payload), text=True, capture_output=True, timeout=60)
            elapsed = time.perf_counter()-start
            (directory/'probe-stderr.txt').write_text(result.stderr)
            result.check_returncode()
            graph = json.loads(result.stdout)
            write(directory/'slice.json', graph)
            metrics[name] = {'elapsed_seconds': elapsed, 'input_files': len(data['sources']),
                             'input_bytes': sum(len(s['text'].encode()) for s in data['sources']),
                             'seeds': len(seeds), 'nodes': len(graph['nodes']), 'edges': len(graph['edges']),
                             'slice_sources': sorted({n['source_id'] for n in graph['nodes']}),
                             'edge_kinds': {k:sum(e['kind']==k for e in graph['edges']) for k in sorted({e['kind'] for e in graph['edges']})}}
        metrics['implementation_sha256'] = hashlib.sha256((HERE/'SliceProbe.java').read_bytes()).hexdigest()
        write(HERE/'results'/'probe-metrics.json', metrics)
        print(json.dumps(metrics, indent=2))

if __name__ == '__main__':
    run()
