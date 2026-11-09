"""Reproduce bounded analyses from frozen inputs without executing target code."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time

from mock_spec.java_analysis import _backend_paths

HERE = Path(__file__).resolve().parent


def write(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n')


def run(output):
    output.mkdir(parents=True, exist_ok=False)
    common = json.loads((HERE/'inputs/common.json').read_text())
    backend = _backend_paths()
    if backend is None:
        raise RuntimeError('Pinned Spoon/JDK unavailable')
    jar, java, javac = backend
    metrics = {'timing_scope': 'one cold external process per language, not repeated mean',
               'target_code_executed': False, 'peak_rss_measured': False}
    payloads = {
        'python': {'sources': common['visible_sources']['flask'],
                   'entry': {'source_id': 'mut', 'function': 'abort'}},
        'java': {'sources': common['visible_sources']['cloudfoundry'],
                 'boundary_types': ['PathMappedEndpoints'], 'max_depth': 2,
                 'entry_methods': ['DefaultApplicationFactory.createApplication()'],
                 'mode': 'value_effect'}}
    with tempfile.TemporaryDirectory(prefix='mockspec-path-analysis-') as directory:
        started = time.perf_counter()
        compile_command = [str(javac), '-Xlint', '-cp', str(jar), '-d', directory,
                           str(HERE/'JavaPaths.java')]
        compiled = subprocess.run(compile_command, capture_output=True, text=True,
                                  check=True, timeout=60)
        metrics['java_compile_seconds'] = time.perf_counter() - started
        (output/'compile.stderr.txt').write_text(compiled.stderr)
        commands = {'python': [sys.executable, str(HERE/'python_paths.py')],
                    'java': [str(java), '-Xmx768m', '-cp', f'{directory}:{jar}', 'JavaPaths']}
        for language, command in commands.items():
            write(output/f'{language}-request.json', payloads[language])
            started = time.perf_counter()
            result = subprocess.run(command, input=json.dumps(payloads[language]),
                                    capture_output=True, text=True, check=True, timeout=60)
            elapsed = time.perf_counter()-started
            raw = json.loads(result.stdout)
            write(output/f'{language}-raw.json', raw)
            (output/f'{language}.stderr.txt').write_text(result.stderr)
            frozen = json.loads((HERE/f'{language}-raw.json').read_text())
            metrics[language] = {'elapsed_seconds': elapsed, 'status': raw['status'],
                                 'matches_frozen_structure': raw == frozen,
                                 'input_source_units': len(payloads[language]['sources']),
                                 'compact_output_bytes': len(result.stdout.encode()),
                                 'paths_or_methods': len(raw.get('paths', raw.get('methods', [])))}
    metrics['implementation_sha256'] = {name: hashlib.sha256((HERE/name).read_bytes()).hexdigest()
                                       for name in ('JavaPaths.java', 'python_paths.py', 'run_analysis.py')}
    write(output/'metrics.json', metrics)
    return metrics


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', required=True, type=Path)
    print(json.dumps(run(parser.parse_args().output.resolve()), indent=2))
