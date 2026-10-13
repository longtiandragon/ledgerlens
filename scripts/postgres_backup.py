"""Back up or restore the Compose PostgreSQL deployment, always into new directories.

A backup needs api, worker and scheduler stopped so that database rows and stored
originals describe one moment; postgres keeps running for pg_dump. Before a backup
is published, its dump is restored into a throwaway container of the same Compose
image and every table is compared. A restore builds a new STORAGE_ROOT the same way
and never touches the running deployment's directories. Redis is not included:
queued runs are re-dispatched by the scheduled stale-run recovery. Hashes detect
changed files, not a maliciously rewritten manifest.
"""
from __future__ import annotations

import argparse
from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.backup_manifest import no_links  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
VERSION = 'compose-postgres-backup-v1'
WRITERS = ('api', 'worker', 'scheduler')
DUMP = 'ledgerlens.dump'
# Celery beat rebuilds its schedule file; the OCR cache is re-creatable model output.
EXCLUDED = re.compile(r'celerybeat.*|ocr-cache')
TABLE = re.compile(r'[a-z_][a-z0-9_]*')
SHA = re.compile(r'[0-9a-f]{64}')
# Fixed output settings so both servers render rows identically.
SESSION = '-c TimeZone=UTC -c DateStyle=ISO,YMD -c IntervalStyle=postgres -c extra_float_digits=1'


def run(args, *, stdin=None, stdout=None, env=None, timeout=900):
    result = subprocess.run(args, cwd=ROOT, stdin=stdin, stdout=stdout or subprocess.PIPE,
                            stderr=subprocess.PIPE, env=env, timeout=timeout)
    if result.returncode:
        message = result.stderr.decode('utf-8', 'replace').strip()[-400:]
        raise RuntimeError(f'{" ".join(args[:3])} failed: {message}')
    return result.stdout


def deployment(env=None) -> dict:
    """Effective Compose settings, including STORAGE_ROOT and POSTGRES_PASSWORD overrides."""
    config = json.loads(run(['docker', 'compose', 'config', '--format', 'json'], env=env))

    def bind(service, target):
        found = [Path(volume['source']) for volume in config['services'][service].get('volumes', [])
                 if volume.get('type') == 'bind' and volume.get('target') == target]
        if len(found) != 1:
            raise ValueError(f'{service} must bind exactly one host directory to {target}')
        return found[0]

    postgres = config['services']['postgres']
    environment = postgres['environment']
    return {'project': config['name'], 'image': postgres['image'], 'user': environment['POSTGRES_USER'],
            'database': environment['POSTGRES_DB'], 'password': environment['POSTGRES_PASSWORD'],
            'postgres_dir': bind('postgres', '/var/lib/postgresql/data'), 'app_dir': bind('api', '/data')}


def check_states(states: dict) -> None:
    """states: service -> (state, health) from `docker compose ps --all`."""
    if states.get('postgres') != ('running', 'healthy'):
        raise RuntimeError('postgres must be running and healthy for pg_dump')
    running = [name for name in WRITERS if (states.get(name) or (None, None))[0] == 'running']
    if running:
        raise RuntimeError('Stop writers before a backup: ' + ', '.join(running))


def compose_states(env=None) -> dict:
    rows = [json.loads(line) for line in run(['docker', 'compose', 'ps', '--all', '--format', 'json'],
                                             env=env).decode('utf-8').splitlines() if line.strip()]
    return {row['Service']: (row.get('State'), row.get('Health')) for row in rows}


def fingerprint_sql(table: str) -> str:
    if not TABLE.fullmatch(table):
        raise ValueError('Unexpected table name: ' + table)
    # Order-independent: sort per-row hashes, so physical row order cannot matter.
    return (f"select count(*), md5(coalesce(string_agg(h, '' order by h), '')) "
            f'from (select md5(t::text) as h from public."{table}" t) s')


def snapshot(psql) -> dict:
    tables = {}
    for table in psql("select table_name from information_schema.tables where table_schema='public' "
                      "and table_type='BASE TABLE' order by table_name").split():
        rows, digest = psql(fingerprint_sql(table)).split('|')
        tables[table] = {'rows': int(rows), 'digest': digest}
    documents = [line.split('|') for line in psql(
        "select id, sha256, storage_path from documents order by id").splitlines() if line]
    return {'server_version': psql('show server_version'), 'tables': tables,
            'alembic_revision': psql('select version_num from alembic_version'), 'documents': documents}


def check_documents(documents, app_dir: Path) -> None:
    for identity, sha, storage_path in documents:
        if not SHA.fullmatch(sha) or storage_path != f'/data/sources/{sha[:2]}/{sha}':
            raise ValueError(f'Document is not stored in the container source layout: {identity}')
        path = no_links(app_dir / 'sources' / sha[:2] / sha)
        if not path.is_file():
            raise ValueError(f'Stored original is missing: {identity}')
        with path.open('rb') as stream:
            if hashlib.file_digest(stream, 'sha256').hexdigest() != sha:
                raise ValueError(f'Stored original does not match its hash: {identity}')


def inventory(root: Path) -> dict:
    root = no_links(root)
    result, pending = {}, [root]
    while pending:
        for path in pending.pop().iterdir():
            no_links(path)
            if path.is_dir():
                pending.append(path)
            elif path.is_file():
                if path.relative_to(root).as_posix() == 'backup.json':
                    continue
                with path.open('rb') as stream:
                    digest = hashlib.file_digest(stream, 'sha256').hexdigest()
                result[path.relative_to(root).as_posix()] = {'sha256': digest, 'bytes': path.stat().st_size}
            else:
                raise ValueError('Unsupported file type in backup: ' + str(path))
    return dict(sorted(result.items()))


def copy_app(source: Path, target: Path) -> list[str]:
    """Copy the API data directory except transient entries; links are refused."""
    source, excluded = no_links(source), []
    target.mkdir()
    for path in sorted(source.iterdir()):
        no_links(path)
        if EXCLUDED.fullmatch(path.name):
            excluded.append(path.name)
        elif path.is_dir():
            inventory(path)  # refuses links and special files before copying
            shutil.copytree(path, target / path.name)
        elif path.is_file():
            shutil.copy2(path, target / path.name)
        else:
            raise ValueError('Unsupported file type in data directory: ' + str(path))
    return excluded


def read_manifest(backup: Path) -> dict:
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('Duplicate backup manifest key')
            result[key] = value
        return result

    manifest = json.loads(no_links(backup / 'backup.json').read_text(encoding='utf-8'), object_pairs_hook=unique)
    if not isinstance(manifest, dict) or manifest.get('schema_version') != VERSION \
            or manifest.get('type') != 'compose-postgres':
        raise ValueError('Not a Compose PostgreSQL backup of a supported version')
    return manifest


def open_server(data_dir: Path, settings: dict) -> str:
    """Throwaway server of the deployment image on a new data directory."""
    name = 'ledgerlens-pgbackup-' + uuid.uuid4().hex[:12]
    # Password comes from the environment, so it never appears on a command line.
    env = {**os.environ, 'POSTGRES_PASSWORD': settings['password']}
    run(['docker', 'run', '-d', '--rm', '--name', name, '-e', 'POSTGRES_USER=' + settings['user'],
         '-e', 'POSTGRES_DB=' + settings['database'], '-e', 'POSTGRES_PASSWORD',
         '-v', f'{data_dir}:/var/lib/postgresql/data', settings['image']], env=env)
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        # TCP readiness: the image's initialisation server listens on the socket only.
        if subprocess.run(['docker', 'exec', name, 'pg_isready', '-h', '127.0.0.1', '-U', settings['user'],
                           '-d', settings['database']], capture_output=True, timeout=30).returncode == 0:
            return name
        time.sleep(1)
    subprocess.run(['docker', 'stop', name], capture_output=True, timeout=120)
    raise RuntimeError('Throwaway PostgreSQL did not become ready')


def restore_dump(data_dir: Path, dump: Path, settings: dict) -> dict:
    data_dir.mkdir()  # must be new: the image only initialises an empty directory
    name = open_server(data_dir, settings)
    try:
        with dump.open('rb') as stream:
            run(['docker', 'exec', '-i', name, 'pg_restore', '-U', settings['user'], '-d', settings['database'],
                 '--exit-on-error', '--single-transaction'], stdin=stream)

        def psql(sql):
            return run(['docker', 'exec', '-e', 'PGOPTIONS=' + SESSION, name, 'psql', '-U', settings['user'],
                        '-d', settings['database'], '-tAc', sql]).decode('utf-8').strip()
        return snapshot(psql)
    finally:
        # Stop is part of the result: a still-running server could change the files.
        run(['docker', 'stop', name], timeout=120)


def compose_psql(settings: dict, env=None):
    def psql(sql):
        return run(['docker', 'compose', 'exec', '-T', '-e', 'PGOPTIONS=' + SESSION, 'postgres', 'psql',
                    '-U', settings['user'], '-d', settings['database'], '-tAc', sql], env=env).decode('utf-8').strip()
    return psql


def outside(destination: Path, protected: list[Path]) -> bool:
    return all(destination != path and path not in destination.parents and destination not in path.parents
               for path in protected)


def backup(destination: Path, env=None) -> Path:
    destination = no_links(destination)
    settings = deployment(env)
    if destination.exists() or not outside(destination, [no_links(settings['postgres_dir']),
                                                         no_links(settings['app_dir'])]):
        raise ValueError('Backup destination must be a new directory outside the deployment storage')
    check_states(compose_states(env))
    psql = compose_psql(settings, env)
    before = snapshot(psql)
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f'.{destination.name}-backup-', dir=destination.parent)).resolve()
    verify_dir = Path(tempfile.mkdtemp(prefix=f'.{destination.name}-verify-', dir=destination.parent)).resolve()
    try:
        with (staging / DUMP).open('xb') as stream:
            run(['docker', 'compose', 'exec', '-T', 'postgres', 'pg_dump', '-U', settings['user'],
                 '-d', settings['database'], '-Fc'], stdout=stream, env=env)
        check_states(compose_states(env))
        if snapshot(psql) != before:
            raise RuntimeError('Database changed while the dump was taken; keep writers stopped')
        excluded = copy_app(settings['app_dir'], staging / 'app')
        check_documents(before['documents'], staging / 'app')
        restored = restore_dump(verify_dir / 'postgres', staging / DUMP, settings)
        if {key: value for key, value in restored.items() if key != 'server_version'} != \
                {key: value for key, value in before.items() if key != 'server_version'}:
            raise RuntimeError('Restored dump does not reproduce the source tables')
        manifest = {'schema_version': VERSION, 'type': 'compose-postgres',
                    'created_at': datetime.now(UTC).isoformat(), 'project': settings['project'],
                    'image': settings['image'], **before, 'restore_check_server_version': restored['server_version'],
                    'excluded_app_entries': excluded, 'files': inventory(staging),
                    'notice': 'Writers were stopped. Redis is not included. Restore only into a new STORAGE_ROOT '
                              'with the POSTGRES_PASSWORD the restored stack will use.'}
        (staging / 'backup.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        if read_manifest(staging)['files'] != inventory(staging):
            raise RuntimeError('Backup inventory changed before publishing')
        if destination.exists():
            raise ValueError('Backup destination appeared; refusing to overwrite')
        staging.rename(destination)
    finally:
        for path in (staging, verify_dir):
            if path.exists() and path.parent == destination.parent:
                shutil.rmtree(path)
    return destination


def restore(backup_dir: Path, destination: Path, env=None) -> Path:
    backup_dir, destination = no_links(backup_dir), no_links(destination)
    if destination.exists() or not outside(destination, [backup_dir]):
        raise ValueError('Restore destination must be a new directory outside the backup')
    manifest = read_manifest(backup_dir)
    if inventory(backup_dir) != manifest['files']:
        raise ValueError('Backup file inventory or hashes differ')
    check_documents(manifest['documents'], backup_dir / 'app')
    settings = deployment(env)
    if not outside(destination, [no_links(settings['postgres_dir']), no_links(settings['app_dir'])]):
        raise ValueError('Restore destination must not overlap the configured deployment storage')
    if settings['image'] != manifest['image']:
        raise ValueError('Compose PostgreSQL image differs from the backup image')
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f'.{destination.name}-restore-', dir=destination.parent)).resolve()
    try:
        shutil.copytree(backup_dir / 'app', staging / 'app')
        expected_app = {key[4:]: value for key, value in manifest['files'].items() if key.startswith('app/')}
        if inventory(staging / 'app') != expected_app:
            raise RuntimeError('Copied data files differ from the backup inventory')
        restored = restore_dump(staging / 'postgres', backup_dir / DUMP, settings)
        for key in ('tables', 'alembic_revision', 'documents'):
            if restored[key] != manifest[key]:
                raise RuntimeError(f'Restored {key} differ from the backup manifest')
        shutil.copy2(backup_dir / 'backup.json', staging / 'restored-from-backup.json')
        if destination.exists():
            raise ValueError('Restore destination appeared; refusing to overwrite')
        staging.rename(destination)
    finally:
        if staging.exists() and staging.parent == destination.parent:
            shutil.rmtree(staging)
    return destination


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('backup').add_argument('--output', type=Path, required=True)
    restoring = commands.add_parser('restore')
    restoring.add_argument('--backup', type=Path, required=True)
    restoring.add_argument('--storage-root', type=Path, required=True,
                           help='new directory; start the stack with STORAGE_ROOT set to it')
    options = parser.parse_args()
    if options.command == 'backup':
        print(backup(options.output))
    else:
        print(restore(options.backup, options.storage_root))
