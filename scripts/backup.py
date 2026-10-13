"""Publish a validated local SQLite backup. Stop API/worker writes beforehand."""
import argparse
import json
import shutil
import sqlite3
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from contextlib import closing

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.backup_manifest import VERSION, files, no_links, verify  # noqa: E402
from scripts.restore_local import _check_database, _check_sources  # noqa: E402


def backup(source: Path, destination: Path):
    source, destination = no_links(source), no_links(destination)
    if destination.exists() or source == destination or source in destination.parents:
        raise ValueError('Backup destination must be a new directory outside runtime')
    if not (source / 'ledgerlens.sqlite').is_file():
        raise ValueError('Runtime business database does not exist')
    files(source)  # Check link boundaries; final hashes come from SQLite snapshots.
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f'.{destination.name}-backup-', dir=destination.parent)).resolve()
    try:
        for name in ('ledgerlens.sqlite', 'checkpoints.sqlite'):
            path = source / name
            if path.exists():
                with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)) as live:
                    with closing(sqlite3.connect(staging / name)) as target:
                        live.backup(target)
                _check_database(staging / name)
        if (source / 'sources').exists():
            shutil.copytree(source / 'sources', staging / 'sources')
        _check_sources(staging)
        manifest = {'schema_version': VERSION, 'created_at': datetime.now(timezone.utc).isoformat(),
            'type': 'sqlite-local', 'source': str(source), 'files': files(staging),
            'checkpoints_included': (staging / 'checkpoints.sqlite').exists(),
            'notice': 'Stop API/worker writes for cross-file consistency. Hashes detect changed files, not a maliciously rewritten manifest. Restore only to a new directory.'}
        (staging / 'backup.json').write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
        verify(staging, manifest)
        if destination.exists():
            raise ValueError('Backup destination appeared; refusing to overwrite')
        staging.rename(destination)
    finally:
        if staging.exists() and staging.parent == destination.parent.resolve():
            shutil.rmtree(staging)
    return destination


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime', type=Path, default=Path('runtime'))
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    print(backup(args.runtime, args.output))
