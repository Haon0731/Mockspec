"""Acquire pinned ANALYSIS TOOLS only; never resolve analyzed-project dependencies.

Run before projecting any case. Requires Python 3.12+, curl, and an existing Docker
Ubuntu image matching environment-lock.json. No system Java changes are made.
"""
import hashlib
import json
from pathlib import Path
import shutil
import stat
import subprocess
import tarfile
import zipfile

HERE = Path(__file__).resolve().parents[1]
DEST = Path('/tmp/mockspec-step5b-tools')
lock = json.loads((HERE / 'environment-lock.json').read_text())
DEST.mkdir(exist_ok=True)


def acquire(entry, filename):
    path = DEST / filename
    if not path.exists() or path.stat().st_size != entry['bytes']:
        subprocess.run(['curl', '-L', '--fail', '--retry', '2', '--continue-at', '-',
                        '--max-time', '3600', entry['artifact_url'], '-o', str(path)], check=True)
    assert path.stat().st_size == entry['bytes']
    with path.open('rb') as f:
        assert hashlib.file_digest(f, 'sha256').hexdigest() == entry['sha256'], 'Tool digest mismatch'
    return path


jdk = acquire(lock['jdk'], 'jdk21.tar.gz')
if not (DEST / 'jdk').exists():
    with tarfile.open(jdk) as tar:
        root = tar.getmembers()[0].name.split('/')[0]
        tar.extractall(DEST, filter='data')
    (DEST / root).rename(DEST / 'jdk')
joern = acquire(lock['joern'], 'joern-cli-linux-x86_64.zip')
if not (DEST / 'joern').exists():
    with zipfile.ZipFile(joern) as archive:
        for item in archive.infolist():
            path = Path(item.filename)
            mode = item.external_attr >> 16
            assert not path.is_absolute() and '..' not in path.parts
            assert not stat.S_ISLNK(mode)
            extracted = Path(archive.extract(item, DEST))
            if mode and not item.is_dir():
                extracted.chmod(mode & 0o777)
    (DEST / 'joern-cli').rename(DEST / 'joern')
spoon = DEST / 'spoon.jar'
if not spoon.exists():
    source = Path('/home/haon/projects/Stub-Tester/scripts/test_support_metadata/spoon_backend/target/spoon-metadata-helper-0.1.0.jar')
    shutil.copyfile(source, spoon)  # Tool binary only; no legacy workspace execution.
with spoon.open('rb') as f:
    assert hashlib.file_digest(f, 'sha256').hexdigest() == lock['spoon']['sha256']
print('Pinned tool archive hashes verified; independent tools ready')

# Copy only the standalone interpreter distribution, never the project venv.
python_source = Path(lock['python_ast']['source_path'])
python_dest = DEST / 'python'
if not python_dest.exists():
    shutil.copytree(python_source, python_dest, symlinks=True)
with (python_dest / 'bin/python3.12').open('rb') as f:
    assert hashlib.file_digest(f, 'sha256').hexdigest() == lock['python_ast']['binary_sha256']
