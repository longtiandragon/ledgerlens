"""Optional offline OCR and append-only human field review. No automatic posting."""
import io
import json
import math
import os
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
from threading import Lock

from fastapi import HTTPException
from PIL import Image
from sqlalchemy import func, select, update

from .config import settings
from .db import Session, Book, Document, OcrRun, OcrReview, ImportBatch, Audit, uid, serialize
from .ocr_observations import canonical, normalize, sha
from .ocr_contract import OcrReviewInput, OcrBatchReviewInput

ROOT = Path(__file__).resolve().parents[2]
GATE = Lock()
UPLOAD_GATE = Lock()
SUPPORTED = {'.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.pdf': 'application/pdf'}


def available():
    return bool(settings.task_mode == 'local' and settings.ocr_python and settings.ocr_python.is_file()
                and settings.ocr_models_dir and settings.ocr_models_dir.is_dir())


def get(s, cls, identity):
    item = s.get(cls, identity)
    if item is None:
        raise HTTPException(404, 'OCR任务、来源或账套不存在')
    return item


def source_bytes(doc):
    path = Path(doc.storage_path)
    with path.open('rb') as handle:
        data = handle.read(settings.max_upload_bytes + 1)
    if len(data) > settings.max_upload_bytes or sha(data) != doc.sha256:
        raise ValueError('原始文件内容校验失败')
    return data


def upload(s, book_id, filename, data):
    # Optional local mode is single-process; serialize admission through commit.
    with UPLOAD_GATE:
        return _upload(s, book_id, filename, data)


def _upload(s, book_id, filename, data):
    book = get(s, Book, book_id)
    if not available():
        raise HTTPException(503, '尚未配置本地OCR环境；请设置OCR_PYTHON和OCR_MODELS_DIR')
    if s.scalar(select(func.count()).select_from(OcrRun).where(OcrRun.status.in_(['queued', 'running']))) >= 4:
        raise HTTPException(429, '本地OCR队列已满，请等待已有任务完成')
    suffix = Path(filename).suffix.lower()
    if suffix not in SUPPORTED or len(data) > settings.max_upload_bytes:
        raise HTTPException(422, '支持20MB以内的PNG、JPEG或PDF扫描件')
    if suffix == '.pdf':
        if not data.startswith(b'%PDF-'):
            raise HTTPException(422, '文件不是PDF')
    else:
        try:
            with Image.open(io.BytesIO(data)) as image:
                if image.format not in {'PNG', 'JPEG'} or image.width * image.height > 16_000_000:
                    raise ValueError('图像格式或像素数不支持')
                image.verify()
        except (ValueError, OSError) as exc:
            raise HTTPException(422, '图像损坏或尺寸超限') from exc
    digest = sha(data)
    path = settings.data_dir / 'sources' / digest[:2] / digest
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open('xb') as handle:
            handle.write(data)
    except FileExistsError:
        if sha(path.read_bytes()) != digest:
            raise HTTPException(409, '已有原件哈希冲突')
    doc = Document(book_id=book_id, name=Path(filename).name, sha256=digest,
                   storage_path=str(path.resolve()), media_type=SUPPORTED[suffix], extracted=[])
    s.add(doc)
    s.flush()
    run = OcrRun(book_id=book_id, document_id=doc.id, book_version=book.version, period=book.period)
    s.add(run)
    s.flush()
    s.add(Audit(book_id=book_id, action='ocr.queued', details={'run_id': run.id, 'document_id': doc.id}))
    s.commit()
    return run


def observations(result):
    if result.get('schema_version') == 'local-ocr-observations-v1':
        return result.get('observations', [])
    return [row for page in result.get('pages', []) if page.get('status') != 'failed'
            for row in page.get('observations', [])]


def validate_result(result, source_hash):
    if result.get('source_sha256') != source_hash or result.get('confirmation') != 'unconfirmed' or result.get('ledger_writes') != 0:
        raise ValueError('OCR结果的来源或未确认边界无效')
    if result.get('schema_version') == 'local-ocr-observations-v1':
        pages = [{**result, 'page': 1}]
    elif result.get('schema_version') == 'local-pdf-ocr-observations-v1':
        pages = result['pages']
        if not 1 <= len(pages) == result['page_count'] <= 20:
            raise ValueError('OCR页数无效')
        if result.get('failed_pages') != sum(p['status'] == 'failed' for p in pages):
            raise ValueError('OCR失败页计数不一致')
    else:
        raise ValueError('未知OCR结果版本')
    ids, total_pixels = set(), 0
    for number, page in enumerate(pages, 1):
        if page['page'] != number or page.get('confirmation') != 'unconfirmed':
            raise ValueError('OCR页序不一致')
        if page['status'] == 'failed':
            if 'error' not in page or 'observations' in page:
                raise ValueError('失败页伪装成功')
            continue
        lines = page['observations']
        width, height = page['image']['width'], page['image']['height']
        if any(type(n) is not int or n <= 0 for n in (width, height)) or width * height > 16_000_000:
            raise ValueError('OCR图像尺寸无效')
        total_pixels += width * height
        if total_pixels > 80_000_000 or page['status'] != ('requires_field_verification' if lines else 'no_text_detected'):
            raise ValueError('OCR页状态或像素预算无效')
        expected = normalize({'res': {'rec_texts': [r['text'] for r in lines], 'rec_scores': [r['confidence'] for r in lines],
                            'rec_polys': [r['locator']['polygon_px'] for r in lines]}},
                  source_hash=source_hash, width=width, height=height, engine=result['engine'])
        for index, row in enumerate(lines):
            if row['confirmation'] != 'unconfirmed' or row['locator']['page'] != number or row['observation_id'] in ids:
                raise ValueError('OCR观察引用或确认状态无效')
            if result['schema_version'] == 'local-ocr-observations-v1':
                if row != expected['observations'][index]:
                    raise ValueError('OCR文字定位与内容指纹不一致')
            else:
                locator = row['locator']
                normalized = [[x / width, y / height] for x, y in locator['polygon_px']]
                transform = page['bitmap_to_pdf']
                origin, x_edge, y_edge = (transform[k] for k in ('origin', 'x_edge', 'y_edge'))
                points = [[origin[d] + x * (x_edge[d] - origin[d]) + y * (y_edge[d] - origin[d]) for d in (0, 1)]
                          for x, y in normalized]
                identity = 'ocr-pdf-' + sha(canonical({'source': source_hash, 'rendered_source': page['rendered_rgb_sha256'],
                    'engine': result['engine'], 'geometry': page['geometry'], 'transform': transform,
                    'text': row['text'], 'locator': locator}))
                if (locator['line'] != index + 1 or normalized != locator['polygon_normalized']
                        or points != locator['polygon_pdf_user_space'] or row['observation_id'] != identity
                        or any(not math.isfinite(n) for p in points for n in p)):
                    raise ValueError('PDF文字定位与内容指纹不一致')
            ids.add(row['observation_id'])
    failed = sum(page['status'] == 'failed' for page in pages)
    expected_status = ('failed' if failed == len(pages) else 'completed_with_failures' if failed
                       else 'requires_field_verification' if ids else 'no_text_detected')
    if result['status'] != expected_status:
        raise ValueError('OCR汇总状态不一致')


def infer(doc):
    data = source_bytes(doc)
    suffix = Path(doc.name).suffix.lower()
    command = ROOT / 'scripts' / ('ocr_pdf.py' if suffix == '.pdf' else 'ocr_image.py')
    # Only fixed scripts and server-configured paths; no request-supplied command or Shell.
    environment = {k: v for k, v in os.environ.items() if k.upper() in
                   {'SYSTEMROOT', 'WINDIR', 'PATH', 'TEMP', 'TMP', 'USERPROFILE', 'LOCALAPPDATA', 'APPDATA'}}
    environment['PYTHONIOENCODING'] = 'utf-8'
    with TemporaryDirectory(prefix='ocr-', dir=settings.data_dir) as folder:
        input_path, output_path = Path(folder) / ('input' + suffix), Path(folder) / 'result.json'
        input_path.write_bytes(data)
        process = subprocess.run([str(settings.ocr_python.resolve()), str(command), '--input', str(input_path.resolve()),
                        '--models', str(settings.ocr_models_dir.resolve()), '--output', str(output_path.resolve()),
                        '--cache', str((settings.data_dir / 'ocr-cache').resolve())], cwd=ROOT, env=environment,
                       timeout=min(max(settings.ocr_timeout_seconds, 10), 600), shell=False,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
                       creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        with output_path.open('rb') as handle:
            raw = handle.read(16 * 1024 * 1024 + 1)
        if len(raw) > 16 * 1024 * 1024:
            raise ValueError('OCR输出超限')
        result = json.loads(raw)
        if process.returncode != (1 if result.get('status') in {'failed', 'completed_with_failures'} else 0):
            raise ValueError('OCR进程退出状态与输出不一致')
    if result.get('status') == 'failed' and not result.get('pages'):
        raise ValueError('本地OCR运行失败：' + str(result.get('error', {}).get('type', 'Unknown')))
    validate_result(result, doc.sha256)
    return result


def execute(identity):
    with GATE:
        token = uid()
        with Session() as s:
            claimed = s.execute(update(OcrRun).where(OcrRun.id == identity, OcrRun.status == 'queued')
                                .values(status='running', worker_token=token)).rowcount
            s.commit()
            if not claimed:
                return
            run = s.get(OcrRun, identity)
            doc = s.get(Document, run.document_id)
            try:
                result = infer(doc)
                validate_result(result, doc.sha256)
                source_bytes(doc)  # Recheck after inference; changed originals invalidate this result.
                status = 'failed' if result['status'] == 'failed' else 'partial' if result['status'] == 'completed_with_failures' else 'complete'
                values = {'status': status, 'result': result, 'result_digest': sha(canonical(result)), 'error': None}
            except Exception as exc:
                values = {'status': 'failed', 'error': 'OCR未完成：' + type(exc).__name__, 'result': {}, 'result_digest': None}
            s.execute(update(OcrRun).where(OcrRun.id == identity, OcrRun.worker_token == token,
                                          OcrRun.status == 'running').values(**values))
            s.commit()


def recover(s):
    s.execute(update(OcrRun).where(OcrRun.status.in_(['queued', 'running'])).values(
        status='failed', worker_token=None, error='服务重启中断了OCR；原件保留，可重新识别'))
    s.commit()


def verified(s, run):
    doc = get(s, Document, run.document_id)
    try:
        if doc.book_id != run.book_id:
            raise ValueError('跨账套来源')
        source_bytes(doc)
        if run.result_digest != sha(canonical(run.result)):
            raise ValueError('OCR结果已改变')
        validate_result(run.result, doc.sha256)
    except (ValueError, OSError, KeyError, TypeError, IndexError) as exc:
        raise HTTPException(409, 'OCR原件或识别结果校验失败，不能继续确认') from exc
    return doc


def detail(s, run):
    book = get(s, Book, run.book_id)
    doc = get(s, Document, run.document_id)
    result = {k: v for k, v in serialize(run).items() if k != 'worker_token'}
    if run.result:
        verified(s, run)
    reviews = s.scalars(select(OcrReview).where(OcrReview.run_id == run.id).order_by(OcrReview.version)).all()
    result.update(document_name=doc.name, media_type=doc.media_type, book_revision=book.revision,
                  stale=book.version != run.book_version or book.period != run.period,
                  reviews=[{**serialize(r), 'import_status': get(s, ImportBatch, r.import_id).status} for r in reviews])
    return result


def reviewed_rows(body, book, run, doc, review_id):
    """Validate the whole review before any preview is persisted; preserve old payloads."""
    batch_mode = isinstance(body, OcrBatchReviewInput)
    entries = body.entries if batch_mode else [body]
    refs = {r['observation_id']: r for r in observations(run.result)}
    context_fields = {'source_id', 'kind', 'period', 'currency'}
    payload, rows, identities = body.model_dump(mode='json'), [], set()
    for index, entry in enumerate(entries, 1):
        record = entry.record.model_dump(mode='json', exclude_none=True)
        if record['period'] != book.period or record['currency'] != book.currency:
            raise HTTPException(422, f'第{index}条记录的期间和币种必须属于当前账套')
        identity = record['kind'], record['source_id']
        if identity in identities:
            raise HTTPException(422, f'第{index}条记录的类型与来源编号在本批重复，未生成预览')
        identities.add(identity)
        required = {k for k, v in record.items() if v not in ('', None) and k not in context_fields}
        if (not required or set(entry.field_evidence) != required
                or any(not ids or len(ids) > 20 or len(set(ids)) != len(ids)
                       or any(identity not in refs for identity in ids) for ids in entry.field_evidence.values())):
            raise HTTPException(422, f'第{index}条记录的每个填写字段必须选择当前识别结果的原文行；不接受其他任务或失败页的引用')
        locator = {'ocr_run_id': run.id, 'ocr_review_id': review_id, 'source_sha256': doc.sha256,
                   'result_digest': run.result_digest, 'confirmation': 'human_confirmed',
                   'field_evidence': {k: [{'observation_id': identity, **refs[identity]['locator']}
                                          for identity in ids] for k, ids in entry.field_evidence.items()}}
        if batch_mode:
            locator['review_record_index'] = index
            payload['entries'][index - 1]['record'] = record
        else:
            payload['record'] = record
        rows.append({'record': record, 'locator': locator})
    return payload, rows


def review(s, run, body):
    s.execute(update(Book).where(Book.id == run.book_id).values(revision=Book.revision))
    book = get(s, Book, run.book_id)
    s.refresh(book)
    s.refresh(run)
    if (run.status not in {'complete', 'partial'} or book.version != run.book_version or book.period != run.period
            or body.expected_book_revision != book.revision or body.expected_result_digest != run.result_digest):
        raise HTTPException(409, '识别结果或账套版本已改变，请重新查看并核实')
    doc = verified(s, run)
    previous = s.scalar(select(OcrReview).where(OcrReview.run_id == run.id).order_by(OcrReview.version.desc()))
    if body.expected_parent_id != (previous.id if previous else None):
        raise HTTPException(409, '字段复核已有新版本，请刷新')
    if previous and get(s, ImportBatch, previous.import_id).status == 'confirmed':
        raise HTTPException(409, '已导入记录不能覆盖；请使用新的源资料修订，调整只进入模拟分支')
    batch_id, review_id = uid(), uid()
    payload, rows = reviewed_rows(body, book, run, doc, review_id)
    batch = ImportBatch(id=batch_id, book_id=book.id, document_id=doc.id, rows=rows,
                        errors=[], columns=[], mapping={'_origin': 'ocr_human_review'})
    saved = OcrReview(id=review_id, book_id=book.id, run_id=run.id, version=previous.version + 1 if previous else 1,
                      parent_id=previous.id if previous else None, book_revision=book.revision, book_version=book.version,
                      payload=payload, digest=sha(canonical(payload)), import_id=batch_id)
    s.add_all([batch, saved, Audit(book_id=book.id, action='ocr.fields_confirmed',
                                 details={'review_id': review_id, 'run_id': run.id, 'import_id': batch_id, 'record_count': len(rows)})])
    s.commit()
    return {'review': serialize(saved), 'preview': {**serialize(batch), 'record_count': len(rows)}}


def require_current_review(s, batch):
    saved = s.scalar(select(OcrReview).where(OcrReview.import_id == batch.id))
    if saved is None:
        if batch.mapping.get('_origin') == 'ocr_human_review':
            raise HTTPException(409, 'OCR导入缺少人工确认来源')
        return
    run, book = get(s, OcrRun, saved.run_id), get(s, Book, saved.book_id)
    verified(s, run)
    latest = s.scalar(select(OcrReview).where(OcrReview.run_id == run.id).order_by(OcrReview.version.desc()))
    if latest.id != saved.id or book.version != saved.book_version or book.revision != saved.book_revision:
        raise HTTPException(409, '字段确认后账套或复核版本已改变，请重新核实后建立新预览')
    try:
        if (saved.digest != sha(canonical(saved.payload)) or batch.book_id != saved.book_id
                or batch.document_id != run.document_id or run.book_id != saved.book_id):
            raise ValueError('Review or import scope changed')
        contract = OcrBatchReviewInput if 'schema_version' in saved.payload else OcrReviewInput
        body = contract.model_validate(saved.payload)
        if body.expected_result_digest != run.result_digest or body.expected_book_revision != saved.book_revision:
            raise ValueError('Review context changed')
        doc = get(s, Document, run.document_id)
        _, expected_rows = reviewed_rows(body, book, run, doc, saved.id)
        if canonical(batch.rows) != canonical(expected_rows):
            raise ValueError('Review records or field locators changed')
    except (ValueError, TypeError, KeyError, HTTPException) as exc:
        raise HTTPException(409, '人工确认记录、字段原文定位与导入预览不一致') from exc
