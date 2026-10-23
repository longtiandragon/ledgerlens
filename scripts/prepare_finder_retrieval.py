"""Prepare a pooled-reference experiment without reading answer columns."""
import argparse
import hashlib
import json
from pathlib import Path


def digest(data):
    return hashlib.sha256(data).hexdigest()


def prepare(source, output):
    import pyarrow.parquet as pq
    raw = source.read_bytes()
    if digest(raw) != '213395beb38a075dcc7b23f8e213ccbd2768df0f5c615479c33e10ce8cc267f1':
        raise ValueError('Unexpected FinDER source')
    rows = pq.read_table(source, columns=['_id', 'text', 'references']).to_pylist()
    if len(rows) != 5703 or len({r['_id'] for r in rows}) != 5703:
        raise ValueError('Unexpected query count or duplicate IDs')
    corpus, queries, labels = {}, [], []
    for row in sorted(rows, key=lambda r: r['_id']):
        if not row['text'].strip() or not row['references']:
            raise ValueError('Empty source record')
        references = []
        for text in row['references']:
            if not text.strip():
                raise ValueError('Empty reference')
            identity = digest(text.encode())
            corpus[identity] = text
            references.append(identity)
        queries.append({'id': row['_id'], 'text': row['text']})
        labels.append({'id': row['_id'], 'references': sorted(set(references))})
    output.mkdir(parents=True, exist_ok=False)
    for name, data in [('queries.json', queries), ('labels.json', labels),
                       ('corpus.json', [{'id': k, 'text': v} for k, v in sorted(corpus.items())])]:
        (output / name).write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
    pin = {'source_sha256': digest(raw), 'queries': len(queries), 'parents': len(corpus),
           'files': {n: digest((output / n).read_bytes())
                     for n in ['queries.json', 'labels.json', 'corpus.json']}}
    (output / 'manifest.json').write_text(json.dumps(pin, indent=2), encoding='utf-8')
    return pin


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(prepare(args.source, args.output), indent=2))
