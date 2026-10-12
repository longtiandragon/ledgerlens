"""Consistent local SQLite backup. Stop writes while copying source artifacts."""
import argparse
import json
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from contextlib import closing

parser = argparse.ArgumentParser()
parser.add_argument('--runtime', type=Path, default=Path('runtime'))
parser.add_argument('--output', type=Path, required=True)
args = parser.parse_args()
source, destination = args.runtime.resolve(), args.output.resolve()
if source == destination or source in destination.parents:
    raise SystemExit('Backup destination must be outside runtime')
if not (source / 'ledgerlens.sqlite').is_file():
    raise SystemExit('Runtime business database does not exist')
destination.mkdir(parents=True, exist_ok=False)
for name in ['ledgerlens.sqlite', 'checkpoints.sqlite']:
    if (source / name).exists():
        with closing(sqlite3.connect(f'{(source / name).as_uri()}?mode=ro', uri=True)) as live, closing(sqlite3.connect(destination / name)) as backup:
            live.backup(backup)
if (source / 'sources').exists():
    shutil.copytree(source / 'sources', destination / 'sources')
(destination / 'backup.json').write_text(json.dumps({'created_at': datetime.now(timezone.utc).isoformat(),
    'type': 'sqlite-local', 'source': str(source), 'notice': 'Stop API writes for cross-file consistency; restore only into a new directory.'}, indent=2), encoding='utf-8')
print(destination)
