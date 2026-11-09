"""Re-admit bounded source, then serialize it with the shared Spoon frontend."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from prepare import CASES  # noqa: E402
from mock_spec.adapters.java import load_task_with_repository  # noqa: E402
from mock_spec.java_analysis import _backend_paths  # noqa: E402
from mock_spec.validation import SourceStore  # noqa: E402


def write(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')


def run(old_root):
    common_path = HERE.parent/'path-pilot/inputs/common.json'
    common = json.loads(common_path.read_text(encoding='utf-8'))
    _, registry, dataset, snapshot, case = CASES['cloudfoundry']
    task, repository = load_task_with_repository(old_root/registry/'formal-snapshots'/dataset/snapshot,
                                                case_id=case)
    try:
        if task.to_dict() != common['tasks']['cloudfoundry']:
            raise ValueError('reviewed task changed')
        store = SourceStore(task, repository=repository)
        search = store.search(path_pattern='src/main/**/*.java', max_output_chars=30000)
        for source in common['visible_sources']['cloudfoundry']:
            locator = source['source_id'] if source['source_id'] in ('test','mut') else source['logical_path']
            fresh = store.read(locator, source['start_line'],
                               source['start_line']+len(source['text'].splitlines())-1)
            if fresh.text.rstrip('\n') != source['text'].rstrip('\n'):
                raise ValueError('reviewed source changed')
    finally:
        repository.close()
    backend = _backend_paths()
    if backend is None:
        raise RuntimeError('Pinned Spoon/JDK unavailable')
    jar, java, javac = backend
    metrics = {'common_sha256': hashlib.sha256(common_path.read_bytes()).hexdigest(),
               'extractor_sha256': hashlib.sha256((HERE/'GoalIR.java').read_bytes()).hexdigest(),
               'admission': 'fresh task and all retained source projections match',
               'source_search': {k:search.get(k) for k in ('status','complete','limit_reason')}}
    with tempfile.TemporaryDirectory(prefix='mockspec-goal-ir-') as tmp:
        start = time.perf_counter()
        compiled = subprocess.run([str(javac),'-cp',str(jar),'-d',tmp,str(HERE/'GoalIR.java')],
                                  check=True,capture_output=True,text=True,encoding='utf-8',timeout=60)
        metrics['compile_seconds'] = time.perf_counter()-start
        (HERE/'compile.stderr.txt').write_text(compiled.stderr,encoding='utf-8')
        start = time.perf_counter()
        result = subprocess.run([str(java),'-Xmx768m','-cp',f'{tmp}:{jar}','GoalIR'],
                                input=json.dumps({'sources':common['visible_sources']['cloudfoundry']}),
                                check=True,capture_output=True,text=True,encoding='utf-8',timeout=60)
        metrics['analysis_seconds'] = time.perf_counter()-start
        raw = json.loads(result.stdout)
        write(HERE/'ir.json',raw)
        metrics['serialized_bytes'] = (HERE/'ir.json').stat().st_size
        metrics['types'] = len(raw['types'])
        metrics['unsupported_nodes'] = len(raw['unsupported'])
        (HERE/'analysis.stderr.txt').write_text(result.stderr,encoding='utf-8')
    write(HERE/'ir-metrics.json',metrics)
    print(json.dumps(metrics,ensure_ascii=False))


if __name__ == '__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--old-root',type=Path,required=True)
    run(parser.parse_args().old_root)
