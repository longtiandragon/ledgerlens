"""Build, verify or query a local versioned index. No evaluator input or runtime SQL."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
from time import perf_counter

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))


def run(args):
    from app.knowledge import freeze_reference, load_verified_corpus
    from app.reference_sources import _json, _read, canonical
    from app.retrieval_index import PersistentIndex, digest, manifest_for, validate_manifest, validate_segments
    from app.retrieval_vectors import OfflineBGE
    import psycopg

    if not 1024 <= args.port <= 65535:
        raise ValueError('Invalid local database port')
    protected = [args.corpus, args.registry, args.manifest]
    if (args.output.suffix != '.json' or args.output.resolve() in {p.resolve() for p in protected if p}
            or any(args.output.resolve().is_relative_to(p.resolve()) for p in (args.source_root, args.model_root) if p)):
        raise ValueError('Output must be a new report, not an input artifact')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    report = {'schema_version': 'persistent-reference-index-operation-v1', 'operation': args.command,
              'status': 'running', 'started_at': datetime.now(timezone.utc).isoformat(),
              'ledger_writes': 0, 'paid_model_requests': 0, 'default_retrieval_changed': False}
    with args.output.open('x', encoding='utf-8') as handle:
        json.dump(report, handle)
    started = perf_counter()
    try:
        hashes = {k: digest(_read(getattr(args, k))) for k in ('corpus', 'registry')}
        parents = load_verified_corpus(args.corpus, registry=args.registry, source_root=args.source_root)
        if any(hashes[k] != digest(_read(getattr(args, k))) for k in hashes):
            raise ValueError('Reference artifacts changed during verification')
        if args.command == 'build':
            model = OfflineBGE(args.model_root)
            segments = model.segments(parents)
            validate_segments(segments, parents)  # Enforce the storage budget before costly encoding.
            vectors = model.encode([s['text'] for s in segments])
            manifest = manifest_for(segments, vectors, parents=parents, engine=model.engine,
                                    corpus_sha256=hashes['corpus'], registry_sha256=hashes['registry'])
        else:
            retained = _json(args.manifest)
            if retained.get('status') != 'completed' or retained.get('operation') != 'build':
                raise ValueError('Requires an independently retained successful build manifest')
            manifest = retained['manifest']
            validate_manifest(manifest)
            if (manifest['corpus_sha256'] != hashes['corpus'] or manifest['registry_sha256'] != hashes['registry']
                    or manifest['parents_sha256'] != digest(canonical(parents))):
                raise ValueError('Index does not match current verified reference corpus')
        report['manifest'] = manifest
        with psycopg.connect(host='127.0.0.1', port=args.port, user='retrieval', dbname='retrieval',
                             password=os.environ.get('RETRIEVAL_DB_PASSWORD', 'local-retrieval-only'),
                             connect_timeout=5, autocommit=True) as connection:
            connection.execute("SET statement_timeout='30s'")
            connection.execute("SET lock_timeout='10s'")
            index = PersistentIndex(connection)
            if args.command == 'build':
                index.initialize()
                report['created'] = index.publish(manifest, segments, vectors)
            else:
                segments = list(index.verify(manifest).values())
                validate_segments(segments, parents)
                if args.command == 'query':
                    if not args.query or len(args.query) > 2000:
                        raise ValueError('Query must contain 1-2000 characters')
                    model = OfflineBGE(args.model_root)
                    report['hits'] = index.search(manifest, model.encode([args.query], queries=True)[0],
                                                  engine=model.engine, framework=args.framework,
                                                  period=args.period, limit=args.limit)
                    lookup = {p['chunk_id']: p for p in parents}
                    for hit in report['hits']:
                        hit['parent_reference'] = freeze_reference(lookup[hit['parent_chunk_id']], registry=args.registry)
                    report['query'] = {'text': args.query, 'framework': args.framework, 'period': args.period, 'limit': args.limit}
            report['verified_segments'] = len(index.verify(manifest))
            report['postgres_version'] = connection.execute('SHOW server_version').fetchone()[0]
            report['pgvector_version'] = connection.execute("SELECT extversion FROM pg_extension WHERE extname='vector'").fetchone()[0]
        report['status'] = 'completed'
    except Exception as exc:
        report['status'] = 'failed'
        report['error'] = {'type': type(exc).__name__, 'message': str(exc)[:300]}
    report['duration_seconds'] = perf_counter() - started
    report['source_sha256'] = {p: digest((ROOT / p).read_bytes()) for p in (
        'scripts/retrieval_index.py', 'backend/app/retrieval_index.py', 'backend/app/retrieval_vectors.py',
        'backend/app/knowledge.py', 'backend/app/reference_sources.py', 'docs/retrieval-model-v1.json')}
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['build', 'verify', 'query'])
    for name in ('corpus', 'registry', 'source-root', 'output'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--manifest', type=Path)
    parser.add_argument('--model-root', type=Path)
    parser.add_argument('--port', type=int, default=55439)
    parser.add_argument('--query')
    parser.add_argument('--framework', default='CAS')
    parser.add_argument('--period', default='2026-09')
    parser.add_argument('--limit', type=int, default=6)
    args = parser.parse_args()
    if args.command != 'build' and args.manifest is None:
        parser.error('--manifest is required for verify/query')
    if args.command != 'verify' and args.model_root is None:
        parser.error('--model-root is required for build/query')

    def offline(event, arguments):
        if event == 'socket.connect':
            address = arguments[1]
            if not isinstance(address, tuple) or address[:2] != ('127.0.0.1', args.port):
                raise RuntimeError('Only the configured loopback index database is permitted')
        if event == 'socket.getaddrinfo' and arguments[0] != '127.0.0.1':
            raise RuntimeError('External name resolution prohibited')
    sys.addaudithook(offline)
    report = run(args)
    print(json.dumps({'status': report['status'], 'operation': args.command,
                      'verified_segments': report.get('verified_segments'), 'created': report.get('created'),
                      'error': report.get('error')}, ensure_ascii=False))
    return int(report['status'] != 'completed')


if __name__ == '__main__':
    raise SystemExit(main())
