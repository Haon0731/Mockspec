"""Run fixed Step5B stages in a resource-bounded, offline Docker container.

No model-controlled commands. The sole positional argument selects a fixed stage.
Raw transient artifacts remain under /tmp; only audited facts belong in the repo.
"""
import argparse
import hashlib
import xml.etree.ElementTree as ET
import json
from pathlib import Path
import subprocess
import time

REPO = Path(__file__).resolve().parents[3]
TOOLS = Path('/tmp/mockspec-step5b-tools')
INPUTS = Path('/tmp/mockspec-step5b-inputs')
RUNS = Path('/tmp/mockspec-step5b-runs')
IMAGE = 'sha256:9ef2baf83b26485e2a4c36afefd01d8ebe59f9c48c8486f522c20d76c522bcc3'


def docker(*args, check=True):
    return subprocess.run(['docker', *args], text=True, capture_output=True, check=check, timeout=30)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('stage', choices=['preflight', 'spoon', 'python-ast', 'java-build', 'python-build',
                                        'java-query', 'python-query', 'java-slice', 'python-slice',
                                        'java-export', 'python-export', 'help'])
    parser.add_argument('--attempt', default='1', choices=[str(n) for n in range(1, 10)])
    parser.add_argument('--build-attempt', default='1', choices=[str(n) for n in range(1, 10)])
    parser.add_argument('--diagnostic-java-subset', action='store_true',
                        help='Reproduce default exclusion failure mode; NOT full Java acceptance')
    args = parser.parse_args()
    if args.diagnostic_java_subset and not args.stage.startswith('java-'):
        parser.error('diagnostic subset applies only to Java stages')
    stage = args.stage
    output = RUNS / f'{stage}-{args.attempt}'
    output.mkdir(parents=True, exist_ok=False)
    name = f'mockspec-step5b-{stage}-{args.attempt}'
    lang = 'python' if stage.startswith('python') else 'java'
    commands = {
        'preflight': ['/bin/sh', '/probes/preflight.sh'],
        'python-ast': ['/tools/python/bin/python3.12', '-I', '-B', '/probes/python_ast.py'],
        'help': ['/bin/sh', '/probes/help.sh'],
        'spoon': ['/bin/sh', '-c', 'mkdir -p /work/classes && /tools/jdk/bin/javac -cp /tools/spoon.jar -d /work/classes /probes/SpoonProbe.java && /tools/jdk/bin/java -cp /work/classes:/tools/spoon.jar SpoonProbe'],
    }
    for language, frontend in [('java', 'JAVASRC'), ('python', 'PYTHONSRC')]:
        commands[language + '-build'] = ['/tools/joern/joern-parse', '/input', '--language', frontend, '--output', '/work/cpg.bin']
        if language == 'java':
            commands[language + '-build'] += ['--frontend-args', '--no-default-exclude', '--jdk-path', '/tools/jdk', '--delombok-mode', 'no-delombok']
        if language == 'java' and args.diagnostic_java_subset:
            commands[language + '-build'].remove('--no-default-exclude')
        commands[language + '-query'] = ['/tools/joern/joern', '--script', '/probes/facts.sc', '--param', 'cpgFile=/artifacts/cpg.bin']
        commands[language + '-slice'] = ['/bin/sh', '-c', 'cp /artifacts/cpg.bin /work/cpg.bin && /tools/joern/joern-slice data-flow --out /work/slice --parallelism 2 --method-name-filter ' + ('abort' if language == 'python' else '.*') + ' --slice-depth 4']
        commands[language + '-export'] = ['/tools/joern/joern-export', '/artifacts/cpg.bin', '--repr', 'all', '--format', 'graphml', '--out', '/work/export']
    command = commands[stage]
    create = ['create', '--name', name, '--hostname', 'mockspec-analysis', '--add-host', 'mockspec-analysis:127.0.0.1', '--network', 'none', '--read-only', '--cap-drop', 'ALL',
              '--security-opt', 'no-new-privileges', '--user', '1000:1000',
              '--memory', '4g', '--memory-swap', '4g', '--cpus', '2', '--pids-limit', '256',
              '--ulimit', 'core=0', '--ulimit', 'nofile=1024:1024',
              '--tmpfs', '/work:rw,nosuid,nodev,size=1073741824,mode=1777',
              '--tmpfs', '/tmp:rw,exec,nosuid,nodev,size=268435456,mode=1777',
              '--workdir', '/work', '--env', 'HOME=/work/home', '--env', 'JAVA_HOME=/tools/jdk',
              '--env', 'PATH=/tools/jdk/bin:/usr/bin:/bin',
              '--env', 'JAVA_OPTS=-Xmx2g -XX:ActiveProcessorCount=2',
              '--env', 'JAVA_TOOL_OPTIONS=-Xmx2g -XX:ActiveProcessorCount=2',
              '--mount', f'type=bind,src={TOOLS},dst=/tools,readonly',
              '--mount', f'type=bind,src={REPO / "docs/step5b/poc"},dst=/probes,readonly',
              '--mount', f'type=bind,src={INPUTS / lang},dst=/input,readonly']
    provenance = None
    if stage.endswith(('-query', '-slice', '-export')):
        artifact = RUNS / f'{lang}-build-{args.build_attempt}' / 'cpg.bin'
        if not artifact.is_file() or artifact.stat().st_size == 0 or json.loads((artifact.parent / 'metrics.json').read_text())['command_exit'] != 0:
            raise SystemExit('Missing successful build prerequisite')
        build = json.loads((artifact.parent / 'stage.json').read_text())
        if build['status'] != 'command_succeeded':
            raise SystemExit('Build stage was not successful')
        subset = lang == 'java' and '--no-default-exclude' not in build['command']
        if subset and not args.diagnostic_java_subset:
            raise SystemExit('Incomplete Java build requires explicit --diagnostic-java-subset')
        provenance = {'build_run': artifact.parent.name, 'cpg_sha256': hashlib.sha256(artifact.read_bytes()).hexdigest(),
                      'diagnostic_only': subset}
        create += ['--mount', f'type=bind,src={artifact.parent},dst=/artifacts,readonly']
    create += ['--entrypoint', '/bin/sh', IMAGE, '-c',
               'mkdir -p /work/home; /bin/sh /probes/measure.sh "$@"; touch /work/done; sleep 300',
               'stage', *command]
    start = time.monotonic()
    record = {'stage': stage, 'attempt': args.attempt, 'image': IMAGE, 'command': command,
              'limits': {'memory_bytes': 4294967296, 'cpu_cores': 2, 'pids': 256,
                         'wall_seconds': 240, 'work_tmpfs_bytes': 1073741824,
                         'tmp_tmpfs_bytes': 268435456, 'individual_output_bytes': 8388608},
              'status': 'starting', 'diagnostic_java_subset': args.diagnostic_java_subset, 'build_provenance': provenance}
    try:
        docker(*create)
        config = json.loads(docker('inspect', name).stdout)[0]
        h = config['HostConfig']
        assert h['NetworkMode'] == 'none' and h['ReadonlyRootfs']
        assert h['Memory'] == 4294967296 and h['MemorySwap'] == 4294967296
        assert h['NanoCpus'] == 2000000000 and h['PidsLimit'] == 256
        assert config['Config']['User'] == '1000:1000'
        assert h['CapDrop'] == ['ALL'] and 'no-new-privileges' in h['SecurityOpt']
        assert all(not m['RW'] for m in config['Mounts'] if m['Type'] == 'bind')
        record['security_verified'] = {
            'network_mode': h['NetworkMode'], 'read_only_root': h['ReadonlyRootfs'],
            'user': config['Config']['User'], 'cap_drop': h['CapDrop'],
            'security_opt': h['SecurityOpt'],
            'bind_mounts': [{'destination': m['Destination'], 'read_only': not m['RW']}
                            for m in config['Mounts'] if m['Type'] == 'bind']}
        docker('start', name)
        while time.monotonic() - start < 240:
            if docker('exec', name, 'test', '-f', '/work/done', check=False).returncode == 0:
                record['status'] = 'finished'
                break
            state = json.loads(docker('inspect', '--format', '{{json .State}}', name).stdout)
            if not state['Running']:
                record['status'] = 'container_exited_before_metrics'
                record['container_state'] = state
                break
            time.sleep(0.5)
        else:
            record['status'] = 'host_wall_timeout'
        # Copy a fixed set while tmpfs is live; never copy the input tree or tool workspace.
        wanted = ['stdout.txt', 'stderr.txt', 'metrics.json', 'cpg.bin', 'facts.json', 'slice.json']
        if stage.endswith('-slice'):
            listing = docker('exec', name, 'find', '/work', '-maxdepth', '1', '-type', 'f', '-name', 'slice*', check=False)
            wanted += [p.removeprefix('/work/') for p in listing.stdout.splitlines()
                       if p.startswith('/work/slice') and '..' not in Path(p).parts]
        if stage.endswith('-export'):

            listing = docker('exec', name, 'find', '/work/export', '-maxdepth', '2', '-type', 'f', check=False)
            wanted += [p.removeprefix('/work/') for p in listing.stdout.splitlines()
                       if p.startswith('/work/export/') and '..' not in Path(p).parts]
        wanted = list(dict.fromkeys(wanted))
        assert len(wanted) <= 32, 'Too many output files'
        collected = 0
        for filename in wanted:
            # docker cp does not reliably see tmpfs mounts; read through the live namespace.
            result = subprocess.run(['docker', 'exec', name, 'cat', '/work/' + filename],
                                    capture_output=True, timeout=30)
            if result.returncode == 0:
                assert len(result.stdout) <= 8388608, 'Output exceeds per-file cap'
                collected += len(result.stdout)
                assert collected <= 67108864, 'Collected output exceeds 64 MiB'
                target = output / filename
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(result.stdout)
            elif filename in ['stdout.txt', 'stderr.txt', 'metrics.json']:
                record.setdefault('missing_required_outputs', []).append(filename)
                record.setdefault('collection_errors', {})[filename] = result.stderr.decode(errors='replace')[:500]
        record['container_logs'] = docker('logs', name, check=False).stderr[:2000]
        record['container_state'] = json.loads(docker('inspect', '--format', '{{json .State}}', name).stdout)
        metrics = output / 'metrics.json'
        if metrics.exists():
            record['command_exit'] = json.loads(metrics.read_text())['command_exit']
            record['status'] = 'command_succeeded' if record['command_exit'] == 0 else 'command_failed'
            if record['command_exit'] == 0:
                try:
                    errors = (output / 'stderr.txt').read_text()
                    if any(line.startswith('Error:') for line in errors.splitlines()):
                        raise ValueError('CLI diagnostic reports error despite exit zero')
                    if stage.endswith('-slice'):
                        slices = list(output.glob('slice*.json'))
                        if not slices:
                            raise ValueError('Missing slice artifact')
                        for path in slices:
                            data = json.loads(path.read_text())
                            if data.get('$type') != 'DataFlowSlice' or not data.get('nodes') or not data.get('edges'):
                                raise ValueError('Empty or invalid data-flow slice')
                    if stage.endswith('-query'):
                        data = json.loads((output / 'facts.json').read_text())
                        if data.get('status') != 'queried' or not data.get('methods') or not data.get('calls'):
                            raise ValueError('Empty or invalid query facts')
                    if stage.endswith('-export'):
                        data = ET.parse(output / 'export/export.xml')
                        if not data.findall('.//{http://graphml.graphdrawing.org/xmlns}node') or not data.findall('.//{http://graphml.graphdrawing.org/xmlns}edge'):
                            raise ValueError('Empty graph export')
                    if stage.endswith('-build') and (not (output / 'cpg.bin').exists() or not (output / 'cpg.bin').stat().st_size):
                        raise ValueError('Missing CPG')
                except (ValueError, OSError, ET.ParseError) as exc:
                    record['status'] = 'invalid_required_artifact'
                    record['validation_error'] = str(exc)

    except Exception as exc:
        record['status'] = 'orchestration_error'
        record['error'] = str(exc)
        raise
    finally:
        record['host_wall_seconds'] = round(time.monotonic() - start, 3)
        record['files'] = {str(p.relative_to(output)): p.stat().st_size for p in output.rglob('*') if p.is_file()}
        try:
            cleanup = docker('rm', '-f', name, check=False)
            record['cleanup_exit'] = cleanup.returncode
        except subprocess.TimeoutExpired:
            record['cleanup_error'] = 'Docker cleanup client timed out; inspect container separately'
        (output / 'stage.json').write_text(json.dumps(record, indent=2) + '\n')
    print(json.dumps(record, indent=2))
    if record['status'] != 'command_succeeded':
        raise SystemExit(1)


if __name__ == '__main__':
    main()
