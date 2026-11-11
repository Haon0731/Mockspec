#!/usr/bin/env python3
"""Losslessly package completed issue-32 run evidence, never source snapshots."""
from __future__ import annotations
import argparse
import gzip
import hashlib
import json
from pathlib import Path
import shutil
import tarfile


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def package(run: Path, output: Path) -> None:
    if not (run / 'results.json').is_file() or not (run / 'run-manifest.json').is_file():
        raise ValueError('completed results and manifest required')
    if output.exists():
        raise FileExistsError(output)
    paths = sorted(p for p in run.rglob('*') if p.is_file())
    allowed = {'.json', '.md', '.stdout', '.stderr', '.csv', '.log', '.bqrs', '.ql', '.yml'}
    for path in paths:
        if path.is_symlink() or path.suffix not in allowed:
            raise ValueError(f'unexpected evidence file; review before packaging: {path}')
        if any(part in {'database', 'db', 'src', 'sources', 'classes', 'probe-classes', 'backend-classes'} for part in path.relative_to(run).parts):
            raise ValueError(f'possible materialized source/database: {path}')
    output.mkdir(parents=True)
    manifest = [{'path': str(p.relative_to(run)), 'bytes': p.stat().st_size, 'sha256': digest(p)} for p in paths]
    archive = output / 'run-evidence.tar.gz'
    with archive.open('wb') as raw:
        with gzip.GzipFile(filename='', fileobj=raw, mode='wb', mtime=0, compresslevel=6) as compressed:
            with tarfile.open(fileobj=compressed, mode='w|', format=tarfile.PAX_FORMAT) as tar:
                for path in paths:
                    info = tar.gettarinfo(str(path), arcname=str(path.relative_to(run)))
                    info.uid = info.gid = info.mtime = 0
                    info.uname = info.gname = ''
                    with path.open('rb') as stream:
                        tar.addfile(info, stream)
    expected = {r['path']: r for r in manifest}
    checked = set()
    with tarfile.open(archive, 'r|gz') as tar:
        for info in tar:
            if not info.isfile() or info.name not in expected or info.name in checked:
                raise ValueError(f'unexpected archive member: {info.name}')
            member_stream = tar.extractfile(info)
            assert member_stream is not None
            h = hashlib.sha256()
            count = 0
            for block in iter(lambda: member_stream.read(1024 * 1024), b''):
                count += len(block)
                h.update(block)
            if count != expected[info.name]['bytes'] or h.hexdigest() != expected[info.name]['sha256']:
                raise ValueError(f'archive verification failed: {info.name}')
            checked.add(info.name)
    assert checked == set(expected)
    archive_sha = digest(archive)
    archive_bytes = archive.stat().st_size
    parts = []
    if archive_bytes > 48 * 1024 * 1024:
        with archive.open('rb') as stream:
            index = 0
            while block := stream.read(48 * 1024 * 1024):
                path = output / f'run-evidence.tar.gz.part{index:03}'
                path.write_bytes(block)
                parts.append({'path': path.name, 'bytes': len(block), 'sha256': digest(path)})
                index += 1
        archive.unlink()
    else:
        parts = [{'path': archive.name, 'bytes': archive_bytes, 'sha256': archive_sha}]
    result = {'schema': 'mockspec.issue32-evidence-archive/v1', 'original_run': str(run),
              'archive': {'sha256': archive_sha, 'bytes': archive_bytes, 'parts': parts},
              'files': manifest, 'verification': {'status': 'passed', 'members': len(checked),
              'method': 'stream every decompressed member and compare original byte count and SHA256'},
              'scope': 'Analysis evidence only; no dataset sources, compiled application, dependencies or CodeQL databases retained.'}
    (output / 'archive-manifest.json').write_text(json.dumps(result, indent=2) + '\n')
    for name in ('summary.json', 'run-manifest.json', 'pre-registration.json', 'report.md'):
        if (run / name).is_file():
            shutil.copyfile(run / name, output / name)
    print(json.dumps({'files': len(paths), 'raw_bytes': sum(x['bytes'] for x in manifest), 'archive_bytes': archive_bytes, 'parts': len(parts)}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--run', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    package(args.run, args.output)
