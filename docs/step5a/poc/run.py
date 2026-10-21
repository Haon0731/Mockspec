"""Step 5A bounded capability probe, not an Agent or general analysis engine.

Run from repository root: PYTHONPATH=src .venv/bin/python -B docs/step5a/poc/run.py
Only reviewed Adapter inputs are materialized; legacy tools are never imported.
"""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
from zipfile import ZipFile

from mock_spec.adapters.java import load_task as load_java
from mock_spec.adapters.python import load_task as load_python

REPO = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
OLD = Path('/home/haon/projects/Stub-Tester')
JAR = OLD / 'scripts/test_support_metadata/spoon_backend/target/spoon-metadata-helper-0.1.0.jar'


def invoke(argv, cwd):
    start = time.perf_counter()
    result = subprocess.run(argv, cwd=cwd, env={'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8'},
                            text=True, capture_output=True, timeout=30)
    return {'exit_code': result.returncode, 'seconds': round(time.perf_counter() - start, 6),
            'stdout': result.stdout, 'stderr': result.stderr}


def main():
    output = HERE.parent / 'results.json'
    if output.exists():
        raise SystemExit('Refusing to overwrite existing evidence; move it explicitly before rerun')
    report = {'kind': 'step5a_capability_probe', 'python_version': sys.version,
              'isolation': 'temporary source directory; no OS sandbox; no analyzed code execution',
              'cases': [], 'joern': {'status': 'not_run', 'path_available': shutil.which('joern'),
                                    'reason': 'not installed on PATH; no download/build attempted'}}
    with tempfile.TemporaryDirectory(prefix='mockspec-step5a-') as temp:
        root = Path(temp)
        for review in json.loads((REPO / 'src/mock_spec/reviewed_inputs.json').read_text()):
            lang = review['language']
            suffix = 'max' if lang == 'java' else 'max-fix23'
            snap = OLD / f'.omx/developer-realistic-registry-{suffix}' / 'formal-snapshots' / review['dataset'] / review['snapshot_id']
            task = (load_java if lang == 'java' else load_python)(snap, case_id=review['case_id'])
            case = {'language': lang, 'case_id': task.case_id, 'sources': [], 'analyses': []}
            for source in (task.mut_source, task.masked_test, *task.supporting_sources):
                # Fixed synthetic filenames, not task-provided filesystem paths.
                directory = root / lang / source.source_id
                directory.mkdir(parents=True)
                path = directory / ('Input.java' if lang == 'java' else 'input.py')
                padded = '\n' * (source.start_line - 1) + source.text
                path.write_text(padded)
                path.chmod(0o444)
                case['sources'].append({'source_id': source.source_id, 'start_line': source.start_line,
                                        'end_line': source.end_line, 'bytes': path.stat().st_size,
                                        'text_sha256': hashlib.sha256(source.text.encode()).hexdigest()})
                if lang == 'python':
                    result = invoke([sys.executable, '-B', str(HERE / 'python_facts.py'), str(path), source.source_id], root)
                    assert result['exit_code'] == 0, result['stderr']
                    facts = json.loads(result.pop('stdout'))
                    assert facts['status'] == 'parsed'
                    for fact in facts['facts']:
                        assert source.start_line <= fact['start_line'] <= fact['end_line'] <= source.end_line
                    case['analyses'].append({**result, **facts})
            if lang == 'java':
                case['analyses'].append({'status': 'not_run', 'backend': 'Spoon',
                                         'reason': 'available Spoon artifact requires Java 17; installed runtime Java 11'})
            report['cases'].append(case)
        broken = root / 'broken.py'
        broken.write_text('def broken(:\n')
        failure = invoke([sys.executable, '-B', str(HERE / 'python_facts.py'), str(broken), 'synthetic-negative'], root)
        report['python_parse_failure'] = json.loads(failure['stdout'])
        assert report['python_parse_failure']['status'] == 'parse_error'
        report['java_version'] = invoke(['/usr/bin/java', '-version'], root)
        if JAR.is_file():
            # Copy only the tool binary, never a legacy workspace or dependency tree.
            local_jar = root / 'spoon-helper.jar'
            shutil.copyfile(JAR, local_jar)
            with ZipFile(local_jar) as jar:
                launcher = jar.read('spoon/Launcher.class')
                report['spoon_artifact'] = {
                    'bytes': local_jar.stat().st_size,
                    'class_major_version': int.from_bytes(launcher[6:8], 'big'),
                    'maven_properties': jar.read('META-INF/maven/fr.inria.gforge.spoon/spoon-core/pom.properties').decode()}
            report['spoon_startup'] = invoke(['/usr/bin/java', '-jar', str(local_jar), '--help'], root)
        # Preserve facts, not a duplicate dataset; TemporaryDirectory removes inputs.
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print(output)


if __name__ == '__main__':
    main()
