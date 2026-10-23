"""Acquire pinned public FinLongEval samples and route queries without answer labels."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import unicodedata
from urllib.parse import quote
from urllib.request import urlopen

REVISION = 'b2cb7b11d850a17381f82ac7f8950970e7b9b955'
BASE = f'https://raw.githubusercontent.com/valuesimplex/FinLongEval/{REVISION}/'
ROOT = Path(__file__).resolve().parents[1]


def canonical(name):
    return re.sub(r'[\s_"“”\-—]+', '', unicodedata.normalize('NFKC', Path(name).stem))


def prepare(raw, output):
    output.mkdir(parents=True, exist_ok=False)
    tree = json.loads((raw / 'tree.json').read_text(encoding='utf-8'))
    files = [item for item in tree['tree'] if item['type'] == 'blob'
             and (item['path'].startswith('data/sample_document/')
                  or item['path'] in {'data/sample_data.jsonl', 'LICENSE', 'README.md'})]
    pins = {'repository': 'https://github.com/valuesimplex/FinLongEval', 'revision': REVISION,
            'repository_license': 'Apache-2.0',
            'redistribution': 'Do not redistribute third-party source PDFs; local evaluation only.', 'files': {}}
    lookup = {}
    for item in files:
        remote = item['path']
        target = raw / remote
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            with urlopen(BASE + quote(remote, safe='/'), timeout=60) as response:
                data = response.read(item['size'] + 1)
            if len(data) != item['size']:
                raise ValueError('Source size differs')
            target.write_bytes(data)
        data = target.read_bytes()
        blob_hash = hashlib.sha1(b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest()
        if blob_hash != item['sha']:
            raise ValueError('Git blob changed: ' + remote)
        pins['files'][remote] = {'sha256': hashlib.sha256(data).hexdigest(), 'bytes': len(data)}
        if remote.startswith('data/sample_document/'):
            key = canonical(Path(remote).name)
            if key in lookup:
                raise ValueError('Ambiguous document filename')
            lookup[key] = remote
        print(remote, len(data), flush=True)
    original = raw / 'data/sample_data.jsonl'
    records = [json.loads(line) for line in original.read_text(encoding='utf-8').splitlines() if line.strip()]
    if len(records) != 80:
        raise ValueError('Unexpected public sample size')
    queries = [{'id': f'fle-{i:03d}', 'question': row['question'],
                'document_name': row['document_name'], 'source_path': lookup.get(canonical(row['document_name']))}
               for i, row in enumerate(records, 1)]
    # No question type or reference answer in runtime query artifact.
    (output / 'queries.json').write_text(json.dumps(queries, ensure_ascii=False, indent=2), encoding='utf-8')
    (output / 'source-pin.json').write_text(json.dumps(pins, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'queries': len(queries), 'unresolved_document_queries': sum(q['source_path'] is None for q in queries)}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--raw', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    prepare(args.raw, args.output)
