"""Materialize only Adapter-admitted sources, retaining logical paths and line ranges."""
import hashlib
import json
from pathlib import Path
from mock_spec.adapters.java import load_task as java
from mock_spec.adapters.python import load_task as python

REPO = Path(__file__).resolve().parents[3]
OLD = Path('/home/haon/projects/Stub-Tester')
DEST = Path('/tmp/mockspec-step5b-inputs')


def main():
    DEST.mkdir(exist_ok=False)
    manifest = {}
    for review in json.loads((REPO / 'src/mock_spec/reviewed_inputs.json').read_text()):
        lang = review['language']
        suffix = 'max' if lang == 'java' else 'max-fix23'
        snap = OLD / f'.omx/developer-realistic-registry-{suffix}' / 'formal-snapshots' / review['dataset'] / review['snapshot_id']
        task = (java if lang == 'java' else python)(snap, case_id=review['case_id'])
        files, spans = {}, []
        for source in (task.mut_source, task.masked_test, *task.supporting_sources):
            path = Path(source.logical_path)
            assert not path.is_absolute() and '..' not in path.parts
            lines = files.setdefault(source.logical_path, {})
            for n, line in enumerate(source.text.splitlines(), source.start_line):
                assert n not in lines or lines[n] == line
                lines[n] = line
            spans.append({'source_id': source.source_id, 'logical_path': source.logical_path,
                          'start_line': source.start_line, 'end_line': source.end_line,
                          'sha256': hashlib.sha256(source.text.encode()).hexdigest()})
        projected = []
        for logical, lines in files.items():
            path = DEST / lang / logical
            path.parent.mkdir(parents=True, exist_ok=True)
            content = '\n'.join(lines.get(n, '') for n in range(1, max(lines) + 1)) + '\n'
            path.write_text(content)
            path.chmod(0o444)
            projected.append({'path': logical, 'bytes': path.stat().st_size,
                              'sha256': hashlib.sha256(content.encode()).hexdigest()})
        manifest[lang] = {'case_id': task.case_id, 'sources': spans, 'files': projected,
                          'gap_policy': 'blank lines only; no unavailable source recovered'}
    (REPO / 'docs/step5b/input-manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    print('Prepared only reviewed sources at', DEST)


if __name__ == '__main__':
    main()
