"""Optional local index queries and explicit append-only reference choices."""
from copy import deepcopy
import math
import os
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
from threading import Lock

from fastapi import HTTPException
from sqlalchemy import func, select, update

from .config import settings
from .db import Session, Book, ReferenceSearch, ReferenceChoice, Audit, uid, serialize
from .knowledge import verify_frozen_references
from .reference_sources import _json, canonical, digest
from .reference_selection import ROOT, latest_choice, registry_for
from .retrieval_index import SEGMENT_KEYS, validate_manifest, validate_segments
from .retrieval_vectors import eligible

GATE, ADMISSION = Lock(), Lock()


def get(s, cls, identity):
    item = s.get(cls, identity)
    if item is None:
        raise HTTPException(404, '账套、检索任务或参考选择不存在')
    return item


def catalog():
    if (settings.task_mode != 'local' or not settings.retrieval_python or not settings.retrieval_python.is_file()
            or not settings.reference_index_catalog):
        return []
    value = _json(settings.reference_index_catalog)
    if set(value) != {'schema_version', 'indexes'} or value['schema_version'] != 'reference-index-catalog-v1':
        raise ValueError('Unknown reference index catalog')
    if not isinstance(value['indexes'], list) or not 1 <= len(value['indexes']) <= 8:
        raise ValueError('Invalid reference index catalog size')
    result, seen = [], set()
    for raw in value['indexes']:
        if set(raw) != {'title', 'manifest', 'corpus', 'registry_key', 'source_root', 'model_root'}:
            raise ValueError('Invalid server index entry')
        if not isinstance(raw['title'], str) or not 1 <= len(raw['title']) <= 100:
            raise ValueError('Invalid index display title')
        entry = {**raw, **{key: (ROOT / raw[key]).resolve() for key in ('manifest', 'corpus', 'source_root', 'model_root')}}
        report = _json(entry['manifest'])
        if report.get('status') != 'completed' or report.get('operation') != 'build':
            raise ValueError('Index lacks a successful retained build')
        manifest = report['manifest']
        validate_manifest(manifest)
        entry['registry'] = registry_for(entry['registry_key'], manifest['registry_sha256'])
        entry['index_id'], entry['index_manifest'] = manifest['index_id'], manifest
        if entry['index_id'] in seen:
            raise ValueError('Duplicate server index entry')
        seen.add(entry['index_id'])
        result.append(entry)
    return result


def capabilities():
    try:
        entries = catalog()
        return {'available': bool(entries), 'indexes': [
            {'index_id': e['index_id'], 'title': e['title'], 'segments': e['index_manifest']['segment_count'],
             'model': e['index_manifest']['engine'].get('model')} for e in entries],
             'notice': '实验性向量索引未优于关键词；候选条文需要人工选择并冻结到新快照。数据库可用性在查询时检查。'}
    except (ValueError, OSError, KeyError, TypeError):
        return {'available': False, 'indexes': [], 'error': '参考索引目录或构建清单校验失败，请检查本机配置'}


def entry_for(identity):
    try:
        found = next((entry for entry in catalog() if entry['index_id'] == identity), None)
    except (ValueError, OSError, KeyError, TypeError) as exc:
        raise HTTPException(503, '参考索引配置校验失败') from exc
    if found is None:
        raise HTTPException(503, '所选本地索引未配置或已改变')
    return found


def start(s, body):
    with ADMISSION:
        book = get(s, Book, body.book_id)
        entry = entry_for(body.index_id)
        if s.scalar(select(func.count()).select_from(ReferenceSearch).where(ReferenceSearch.status.in_(['queued', 'running']))) >= 4:
            raise HTTPException(429, '本地参考检索队列已满，请等待已有任务完成')
        run = ReferenceSearch(book_id=book.id, book_version=book.version, book_revision=book.revision,
                              framework=book.framework, period=book.period, query=body.query,
                              registry_key=entry['registry_key'], manifest=deepcopy(entry['index_manifest']))
        s.add(run)
        s.flush()
        s.add(Audit(book_id=book.id, action='reference.search_queued', details={'search_id': run.id, 'index_id': body.index_id}))
        s.commit()
        return run


def validate_result(run, result):
    expected = {'text': run.query, 'framework': run.framework, 'period': run.period, 'limit': 6}
    if (result.get('status') != 'completed' or result.get('operation') != 'query'
            or canonical(result.get('manifest')) != canonical(run.manifest) or result.get('query') != expected
            or result.get('ledger_writes') != 0 or result.get('paid_model_requests') != 0
            or result.get('verified_segments') != run.manifest['segment_count']):
        raise ValueError('Reference search result identity or scope mismatch')
    validate_manifest(run.manifest)
    registry = registry_for(run.registry_key, run.manifest['registry_sha256'])
    hits = result['hits']
    if not isinstance(hits, list) or len(hits) > 6:
        raise ValueError('Invalid reference result count')
    if hits:
        validate_segments([{key: h[key] for key in SEGMENT_KEYS} for h in hits])
    for hit in hits:
        parent = hit['parent_reference']
        verify_frozen_references([parent], registry=registry)
        if (hit['index_id'] != run.manifest['index_id'] or hit['parent_chunk_id'] != parent['source_id']
                or hit['source_id'] != parent['source_binding']['original_source_id']
                or hit['text'] != parent['text'][hit['start']:hit['end']]
                or any(hit[k] != parent[k] for k in ('framework', 'effective_from', 'effective_to'))
                or not eligible(hit, run.framework, run.period)):
            raise ValueError('Retrieved segment does not belong to its verified parent or scope')
        if type(hit['score']) not in (int, float) or not math.isfinite(hit['score']) or not -1.00001 <= hit['score'] <= 1.00001:
            raise ValueError('Invalid cosine similarity')
    if hits != sorted(hits, key=lambda h: (-h['score'], h['segment_id'])):
        raise ValueError('Reference ranking order changed')


def infer(run):
    entry = entry_for(run.manifest['index_id'])
    if canonical(entry['index_manifest']) != canonical(run.manifest) or entry['registry_key'] != run.registry_key:
        raise ValueError('Server index entry changed before execution')
    environment = {k: v for k, v in os.environ.items() if k.upper() in
                   {'SYSTEMROOT', 'WINDIR', 'PATH', 'TEMP', 'TMP', 'USERPROFILE', 'LOCALAPPDATA', 'APPDATA', 'RETRIEVAL_DB_PASSWORD'}}
    environment['PYTHONIOENCODING'] = 'utf-8'
    with TemporaryDirectory(prefix='reference-', dir=settings.data_dir) as folder:
        output = Path(folder) / 'query.json'
        command = [str(settings.retrieval_python.resolve()), str(ROOT / 'scripts/retrieval_index.py'), 'query',
                   '--manifest', str(entry['manifest']), '--corpus', str(entry['corpus']), '--registry', str(entry['registry']),
                   '--source-root', str(entry['source_root']), '--model-root', str(entry['model_root']),
                   '--port', str(settings.retrieval_db_port), '--framework', run.framework, '--period', run.period,
                   '--query', run.query, '--output', str(output.resolve())]
        completed = subprocess.run(command, cwd=ROOT, env=environment, shell=False,
                                   timeout=min(max(settings.retrieval_timeout_seconds, 10), 600), check=False,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                   creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        result = _json(output, limit=4 * 1024 * 1024)
    if completed.returncode or result.get('status') != 'completed':
        raise ValueError('Local reference worker failed')
    validate_result(run, result)
    return result


def execute(identity):
    with GATE, Session() as s:
        token = uid()
        claimed = s.execute(update(ReferenceSearch).where(ReferenceSearch.id == identity, ReferenceSearch.status == 'queued')
                            .values(status='running', worker_token=token)).rowcount
        s.commit()
        if not claimed:
            return
        run = s.get(ReferenceSearch, identity)
        try:
            result = infer(run)
            validate_result(run, result)
            values = {'status': 'complete', 'result': result, 'result_digest': digest(canonical(result)), 'error': None}
        except Exception as exc:
            values = {'status': 'failed', 'result': {}, 'result_digest': None, 'error': '本地参考检索未完成：' + type(exc).__name__}
        s.execute(update(ReferenceSearch).where(ReferenceSearch.id == identity, ReferenceSearch.status == 'running',
                                               ReferenceSearch.worker_token == token).values(**values))
        s.commit()


def recover(s):
    s.execute(update(ReferenceSearch).where(ReferenceSearch.status.in_(['queued', 'running'])).values(
        status='failed', worker_token=None, error='服务重启中断了参考检索；请重新查询，未自动切换检索方法'))
    s.commit()


def verified(run):
    try:
        if run.status != 'complete' or digest(canonical(run.result)) != run.result_digest:
            raise ValueError('Reference result changed or incomplete')
        validate_result(run, run.result)
    except (ValueError, OSError, KeyError, TypeError) as exc:
        raise HTTPException(409, '参考检索结果或来源校验失败，不能选择或冻结') from exc


def detail(s, run):
    if run.result:
        verified(run)
    book = get(s, Book, run.book_id)
    return {**{k: v for k, v in serialize(run).items() if k != 'worker_token'},
            'stale': (book.version, book.revision) != (run.book_version, run.book_revision)}


def choose(s, body):
    from .imports import make_snapshot
    book = get(s, Book, body.book_id)
    s.execute(update(Book).where(Book.id == book.id).values(revision=Book.revision))
    s.refresh(book)
    previous = latest_choice(s, book.id)
    if body.expected_book_revision != book.revision or body.expected_parent_id != (previous.id if previous else None):
        raise HTTPException(409, '账套或参考选择已有新版本，请刷新后重新选择')
    payload = {'mode': 'keyword'}
    if body.mode == 'persistent_bge':
        run = get(s, ReferenceSearch, body.search_id)
        if run.book_id != book.id:
            raise HTTPException(404, '当前账套中没有该参考检索')
        if ((run.book_version, run.book_revision, run.framework, run.period) != (book.version, book.revision, book.framework, book.period)
                or run.result_digest != body.expected_result_digest):
            raise HTTPException(409, '检索后账套或结果已改变，请重新查询')
        verified(run)
        parents = {h['parent_chunk_id']: h['parent_reference'] for h in run.result['hits']}
        selected = body.selected_parent_ids
        if not selected or len(set(selected)) != len(selected) or any(identity not in parents for identity in selected):
            raise HTTPException(422, '请选择本次检索返回的原父条文，不接受其他任务的引用')
        payload = {'mode': body.mode, 'registry_key': run.registry_key, 'manifest': deepcopy(run.manifest),
                   'query': run.query, 'search_id': run.id, 'search_result_digest': run.result_digest,
                   'selected_parent_ids': selected, 'references': [deepcopy(parents[key]) for key in selected],
                   'book_id': book.id, 'book_version': book.version, 'framework': book.framework, 'period': book.period}
    elif body.search_id is not None or body.expected_result_digest is not None or body.selected_parent_ids:
        raise HTTPException(422, '恢复关键词检索时不能附带候选条文')
    choice = ReferenceChoice(book_id=book.id, book_version=book.version,
                             version=previous.version + 1 if previous else 1, parent_id=previous.id if previous else None,
                             payload=payload, digest=digest(canonical(payload)))
    s.add(choice)
    book.revision += 1  # New reference selection invalidates prior review conclusions.
    s.flush()
    snapshot = make_snapshot(s, book)
    choice.snapshot_id = snapshot.id
    s.add(Audit(book_id=book.id, action='reference.choice_frozen', details={'choice_id': choice.id, 'snapshot_id': snapshot.id}))
    s.commit()
    return {'choice': serialize(choice), 'snapshot_id': snapshot.id, 'book_revision': book.revision}


def choices(s, book_id):
    book = get(s, Book, book_id)
    rows = s.scalars(select(ReferenceChoice).where(ReferenceChoice.book_id == book_id)
                    .order_by(ReferenceChoice.version.desc()).limit(100)).all()
    for row in rows:
        if digest(canonical(row.payload)) != row.digest:
            raise HTTPException(409, '参考选择历史校验失败')
    return {'book_revision': book.revision, 'choices': [{**serialize(row), 'stale': row.book_version != book.version} for row in rows]}
