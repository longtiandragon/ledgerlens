"""Integrity inventory for local SQLite backups; no credentials or remote I/O."""
import hashlib
import json
from pathlib import Path

VERSION = 'sqlite-local-backup-v2'


def no_links(path):
    path = Path(path).absolute()
    if any(p.is_symlink() or (hasattr(p, 'is_junction') and p.is_junction()) for p in [path, *path.parents]):
        raise ValueError('Backup paths cannot traverse symbolic links or junctions')
    return path.resolve()


def files(root):
    root = no_links(root)
    selected = [root / 'ledgerlens.sqlite']
    checkpoints = root / 'checkpoints.sqlite'
    no_links(checkpoints)
    if checkpoints.exists():
        selected.append(checkpoints)
    sources = no_links(root / 'sources')
    if sources.exists():
        if not sources.is_dir():
            raise ValueError('Backup sources must be a directory')
        pending = [sources]
        while pending:
            for path in pending.pop().iterdir():
                no_links(path)
                if path.is_dir():
                    pending.append(path)
                elif path.is_file():
                    selected.append(path)
                else:
                    raise ValueError('Unsupported backup source file')
    result = {}
    for path in selected:
        no_links(path)
        if not path.is_file():
            raise ValueError('Backup database or source file is missing')
        with path.open('rb') as stream:
            digest = hashlib.file_digest(stream, 'sha256').hexdigest()
        result[path.relative_to(root).as_posix()] = {'sha256': digest, 'bytes': path.stat().st_size}
    return dict(sorted(result.items()))


def read_metadata(root):
    path = no_links(Path(root) / 'backup.json')

    def unique(pairs):
        result = {}
        for k, v in pairs:
            if k in result:
                raise ValueError('Duplicate backup manifest key')
            result[k] = v
        return result

    value = json.loads(path.read_text(encoding='utf-8'), object_pairs_hook=unique)
    if not isinstance(value, dict) or value.get('type') != 'sqlite-local':
        raise ValueError('Only local SQLite backups are supported')
    version = value.get('schema_version')
    if version is not None and version != VERSION:
        raise ValueError('Unsupported backup manifest version')
    if version is None and ('files' in value or 'checkpoints_included' in value):
        raise ValueError('Versioned backup inventory is missing its schema version')
    return value


def verify(root, metadata):
    if metadata.get('schema_version') != VERSION:
        return
    actual = files(root)
    expected = metadata.get('files')
    if not isinstance(expected, dict) or not expected or actual != expected:
        raise ValueError('Backup manifest file inventory or hashes differ')
    if metadata.get('checkpoints_included') is not ('checkpoints.sqlite' in actual):
        raise ValueError('Backup checkpoint inventory is inconsistent')
