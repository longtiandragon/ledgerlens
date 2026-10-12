import importlib.metadata
import json
from pathlib import Path

rows = []
for package in importlib.metadata.distributions():
    meta = package.metadata
    license_value = meta.get('License-Expression') or meta.get('License') or '; '.join(
        x.removeprefix('License :: ') for x in meta.get_all('Classifier', []) if x.startswith('License :: '))
    rows.append({'name': meta['Name'], 'version': package.version, 'license_metadata': license_value or 'review_required'})
destination = Path('docs/dependencies-python.json')
destination.write_text(json.dumps(sorted(rows, key=lambda x: x['name'].lower()), ensure_ascii=False, indent=2), encoding='utf-8')
print(f'{len(rows)} distributions recorded; metadata is not a substitute for bundled LICENSE review.')
lock = json.loads(Path('frontend/package-lock.json').read_text(encoding='utf-8'))
frontend = [{'package_path': name, 'version': value.get('version'),
             'license_metadata': value.get('license', 'review_required'), 'dev': value.get('dev', False)}
            for name, value in lock['packages'].items() if name]
Path('docs/dependencies-frontend.json').write_text(json.dumps(frontend, ensure_ascii=False, indent=2), encoding='utf-8')
print(f'{len(frontend)} frontend lock entries recorded.')
