"""Read only admitted cases via existing adapters; retain bounded research evidence."""
import argparse
import json
from pathlib import Path
from mock_spec.adapters import java, python
from mock_spec.validation import SourceStore

HERE = Path(__file__).resolve().parent
CASES = {
    'cloudfoundry': ('java', '.omx/developer-realistic-registry-max', 'java-bechmark-strong', 'ff6812732f1e2779e09005d721f464cebed6b587925130e6a2555a154097214a', 'de.codecentric.boot.admin.client.registration.CloudFoundryApplicationFactoryTest#should_use_application_uri@d0085edf'),
    'jib': ('java', '.omx/developer-realistic-registry-max', 'java-bechmark-strong', '5b93995c794af0152854a01ddda2fe77948b5ab828b05282b00728f7729f64d4', 'com.google.cloud.tools.jib.cli.jar.JarFilesTest#testToJibContainerBuilder_explodedStandard_basicInfo@69ae66df'),
    'flask': ('python', '.omx/developer-realistic-registry-max-fix23', 'python-benchmark-new', '241ef457f8d071c23e6acd658b684b11b54f13fa955e097d6bebe35dc755f47d', 'flask_308'),
}

def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')

def prepare(root, name):
    language, registry, dataset, snapshot, case_id = CASES[name]
    task, repository = (java if language == 'java' else python).load_task_with_repository(
        root / registry / 'formal-snapshots' / dataset / snapshot, case_id=case_id)
    store = SourceStore(task, repository=repository)
    sources = [task.masked_test, task.mut_source, *task.supporting_sources]
    search_receipts = []
    if name == 'cloudfoundry':
        result = store.search(path_pattern='src/main/**/*.java', max_output_chars=30000)
        search_receipts.append(result)
        # Fail instead of silently analyzing a truncated discovery set.
        if result.get('complete') is False and result.get('limit_reason') != 'access_denied':
            raise RuntimeError('incomplete discovery')
        for match in result.get('matches', []):
            path = match.get('logical_path', match.get('path'))
            if path and path not in {s.logical_path for s in sources}:
                full = store.analysis_source(path)
                observed = store.read(path, full.start_line, full.end_line)
                store.record_observation(observed)
                sources.append(observed)
    directory = HERE / 'results' / name
    directory.mkdir(parents=True, exist_ok=True)
    write(directory / 'task.json', task.to_dict())
    write(directory / 'sources.json', {'sources': [
        {'source_id': s.source_id, 'logical_path': s.logical_path, 'text': s.text, 'start_line': s.start_line}
        for s in sources], 'case_id': case_id, 'snapshot': snapshot, 'discovery': search_receipts})
    repository.close()
    return directory

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--old-root', required=True, type=Path)
    args = parser.parse_args()
    for name in CASES:
        print(prepare(args.old_root, name))
