"""Restore into a NEW directory and fix source references; never overwrites live data."""
import argparse
import hashlib
import json
import re
import shutil
import sqlite3
import tempfile
from contextlib import closing
from pathlib import Path


def _check_database(path: Path):
    try:
        with closing(sqlite3.connect(f'{path.as_uri()}?mode=ro', uri=True)) as database:
            if database.execute('PRAGMA integrity_check').fetchall() != [('ok',)]:
                raise ValueError(f'Backup database integrity check failed: {path.name}')
    except sqlite3.DatabaseError as error:
        raise ValueError(f'Backup database is unreadable: {path.name}') from error


def restore(source: Path, destination: Path):
    source, destination = source.resolve(), destination.resolve()
    if destination.exists() or source == destination or source in destination.parents:
        raise ValueError('Restore destination must be a new directory outside the backup')
    if not source.is_dir():
        raise ValueError('Backup directory does not exist')
    # Do not let an archive with symbolic links read files outside its contents.
    if any(path.is_symlink() or path.is_junction() for path in source.rglob('*')):
        raise ValueError('Backup symbolic links and junctions are not supported')
    metadata = json.loads((source / 'backup.json').read_text(encoding='utf-8'))
    if metadata.get('type') != 'sqlite-local':
        raise ValueError('Only local SQLite backups are supported')
    original = source / 'ledgerlens.sqlite'
    if not original.is_file():
        raise ValueError('Backup is missing its business database')
    _check_database(original)
    with closing(sqlite3.connect(f'{original.as_uri()}?mode=ro', uri=True)) as database:
        documents = database.execute('SELECT id, sha256 FROM documents').fetchall()
    for identity, sha in documents:
        if not isinstance(sha, str) or not re.fullmatch(r'[0-9a-f]{64}', sha):
            raise ValueError(f'Backup source has an invalid content hash: {identity}')
        file = source / 'sources' / sha[:2] / sha
        if not file.is_file():
            raise ValueError(f'Backup source is missing/corrupt: {identity}')
        with file.open('rb') as stream:
            digest = hashlib.file_digest(stream, 'sha256').hexdigest()
        if digest != sha:
            raise ValueError(f'Backup source is missing/corrupt: {identity}')
    checkpoints = source / 'checkpoints.sqlite'
    if checkpoints.exists():
        _check_database(checkpoints)
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f'.{destination.name}-restore-', dir=destination.parent)).resolve()
    try:
        # Copy only the files the backup format defines. Operational logs, .env,
        # or other unexpected files are not part of a restored runtime.
        for path in [source / 'backup.json', original, checkpoints]:
            if path.exists():
                shutil.copy2(path, staging / path.name)
        if (source / 'sources').is_dir():
            shutil.copytree(source / 'sources', staging / 'sources')
        with closing(sqlite3.connect(staging / 'ledgerlens.sqlite')) as database:
            for identity, sha in documents:
                database.execute('UPDATE documents SET storage_path=? WHERE id=?',
                                 (str(destination / 'sources' / sha[:2] / sha), identity))
            database.commit()
        if destination.exists():
            raise ValueError('Restore destination appeared during restore; refusing to overwrite')
        staging.rename(destination)
    finally:
        # staging is a directory created by this invocation, inside the verified
        # destination parent; never remove the requested destination or backup.
        if staging.exists() and staging.parent == destination.parent:
            shutil.rmtree(staging)
    return destination


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--backup', type=Path, required=True)
    parser.add_argument('--destination', type=Path, required=True)
    args = parser.parse_args()
    print(restore(args.backup, args.destination))
