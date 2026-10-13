import asyncio
import json
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI, HTTPException, Depends, UploadFile, File, Form, BackgroundTasks, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import Response, FileResponse, StreamingResponse
from sqlalchemy import select, func, update
from sqlalchemy.exc import IntegrityError
from pdfminer.pdfexceptions import PDFException
from pdfplumber.utils.exceptions import PdfminerException
from .config import settings
from .db import (Base, engine, Session, session, serialize, Book, Document, ImportBatch,
                 Record, Snapshot, CheckRun, Case, Run, Simulation, Review, Audit, Evaluation)
from .schemas import BookInput, SnapshotInput, CheckInput, InvestigateInput, SimulationInput, ReviewInput, LedgerInput
from .imports import parse_file, confirm_import, make_snapshot, snapshot_records, policy_for, digest
from .domain import check_records, balances, simulate, rule_catalog, ensure_rule_compatibility, rule_identity_key
from .business_chain import business_chain
from .process_api import router as process_router
from .numeric_evaluation import router as numeric_evaluation_router
from .root_cause_results import router as root_cause_results_router
from .evidence_requests import router as evidence_requests_router
from .statement_api import router as statement_router
from .policy_api import router as policy_router
from .policy_context_contract import InvestigationPolicyContextResponse
from .ocr_api import router as ocr_router
from .reference_api import router as reference_router


def get(s, cls, identity):
    obj = s.get(cls, identity)
    if obj is None:
        raise HTTPException(404, f'{cls.__name__} 不存在')
    return obj


def case_data(item):
    return {**item.finding, 'id': item.id, 'book_id': item.book_id, 'snapshot_id': item.snapshot_id,
            'created_at': item.created_at, 'check_run_id': item.check_run_id}


def require_case_rules(s, item):
    snap, check = get(s, Snapshot, item.snapshot_id), get(s, CheckRun, item.check_run_id)
    if snap.book_id != item.book_id or check.book_id != item.book_id or check.snapshot_id != snap.id:
        raise HTTPException(409, '案件、快照和检查记录不属于同一账套版本')
    try:
        ensure_rule_compatibility(snap.policy, check.rule_version)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return snap


@asynccontextmanager
async def lifespan(app):
    Base.metadata.create_all(engine)
    from .ocr_workflow import recover
    with Session() as s:
        recover(s)
        from .reference_workflow import recover as recover_reference_searches
        recover_reference_searches(s)
    # Recover queued/interrupted local work after a single-process local server restart.
    recovery = []
    if settings.task_mode == 'local':
        from . import jobs
        jobs.local_gate = asyncio.Semaphore(1)
        from .jobs import execute_run
        with Session() as s:
            for run in s.scalars(select(Run).where(Run.status.in_(['queued', 'running']))):
                run.status, run.worker_token = 'queued', None
                recovery.append(run.id)
            s.commit()
        app.state.recovery_tasks = [asyncio.create_task(execute_run(identity)) for identity in recovery]
    yield
    for task in getattr(app.state, 'recovery_tasks', []):
        if not task.done():
            task.cancel()


app = FastAPI(title='账证智核 LedgerLens', version='0.1.0', lifespan=lifespan,
              description='本地会计复核研究与辅助平台；原账只读，调整仅限模拟。')
app.include_router(process_router)
app.include_router(numeric_evaluation_router)
app.include_router(root_cause_results_router)
app.include_router(evidence_requests_router)
app.include_router(statement_router)
app.include_router(policy_router)
app.include_router(ocr_router)
app.include_router(reference_router)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=['127.0.0.1', 'localhost', 'testserver', 'api'])
app.add_middleware(CORSMiddleware, allow_origins=['http://127.0.0.1:5188', 'http://localhost:5188',
                                                'http://127.0.0.1:8080', 'http://localhost:8080'],
                   allow_methods=['GET', 'POST', 'PUT'], allow_headers=['Content-Type'])


@app.middleware('http')
async def local_origin_guard(request: Request, call_next):
    origin = request.headers.get('origin')
    if request.method not in ['GET', 'HEAD', 'OPTIONS'] and origin:
        from urllib.parse import urlparse
        if urlparse(origin).hostname not in ['localhost', '127.0.0.1']:
            return Response('Untrusted origin', status_code=403)
    response = await call_next(request)
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Referrer-Policy'] = 'no-referrer'
    return response


@app.get('/api/v1/health')
def health():
    return {'status': 'ok', 'mode': settings.task_mode, 'model_configured': bool(settings.deepseek_api_key)}


@app.get('/api/v1/settings')
def read_settings():
    return {**health(), 'model': settings.deepseek_model, 'frameworks': ['CAS', 'SCAS'],
            'limits': {'rounds': 3, 'model_calls': 24, 'tool_calls': 60},
            'disclaimer': '工程与仿真验证；不执行正式记账。模型调用会向配置的服务发送必要资料片段。'}


@app.get('/api/v1/books')
def books(s=Depends(session)):
    return [serialize(b) for b in s.scalars(select(Book).order_by(Book.created_at))]


@app.post('/api/v1/books', status_code=201)
def create_book(body: BookInput, s=Depends(session)):
    book = Book(**body.model_dump())
    s.add(book)
    s.commit()
    return serialize(book)


@app.put('/api/v1/books/{book_id}')
def update_book(book_id: str, body: BookInput, s=Depends(session)):
    book = get(s, Book, book_id)
    if book.framework != body.framework:
        raise HTTPException(409, '已建账套不能混用制度；不同制度请创建独立账套')
    for key, value in body.model_dump().items():
        setattr(book, key, value)
    book.version += 1
    book.revision += 1
    snap = make_snapshot(s, book)
    s.add(Audit(book_id=book.id, action='policy.versioned', details={'version': book.version, 'snapshot_id': snap.id}))
    s.commit()
    return serialize(book)


@app.get('/api/v1/policies')
def policies():
    return rule_catalog()


@app.get('/api/v1/knowledge/search')
def search_knowledge(book_id: str, query: str, s=Depends(session)):
    from .knowledge import load_verified_corpus, search
    book = get(s, Book, book_id)
    if len(query) > 1000:
        raise HTTPException(422, '查询过长')
    path = settings.data_dir / 'knowledge' / 'corpus-v2.json'
    has_corpus = path.exists() or path.is_symlink()
    reference_status = 'verified' if has_corpus else (
        'legacy_unverified' if (path.parent / 'corpus.json').exists() else 'not_configured')
    try:
        corpus = load_verified_corpus(path, source_root=settings.data_dir / 'public-sources') if has_corpus else []
    except (ValueError, OSError) as exc:
        raise HTTPException(409, '参考语料来源校验失败；请重新核验原件并构建语料，未使用失败内容') from exc
    return {'results': search(corpus, query, book.framework, book.period),
            'backend': 'local_lexical', 'reference_status': reference_status,
            'notice': '参考资料不自动转成执行规则；调查只使用快照内来源绑定片段。未配置或旧版未验证语料不参与检索'}


@app.get('/api/v1/books/{book_id}/overview')
def overview(book_id: str, s=Depends(session)):
    book = get(s, Book, book_id)
    records = [r for r in s.scalars(select(Record).where(Record.book_id == book_id)) if r.payload.get('period') == book.period]
    kinds = {r.kind for r in records}
    snaps = s.scalars(select(Snapshot).where(Snapshot.book_id == book_id).order_by(Snapshot.created_at.desc())).all()
    latest = snaps[0] if snaps else None
    cases = s.scalars(select(Case).where(Case.book_id == book_id).order_by(Case.created_at.desc())).all()
    requirements = [('凭证与期间复核', {'journal'}), ('账表余额核对', {'journal', 'opening', 'trial_balance'}),
                    ('银行往来核对', {'journal', 'bank'}), ('采购证据链', {'order', 'receipt', 'invoice', 'journal'})]
    return {'book': serialize(book), 'counts': {'records': len(records), 'cases': len(cases), 'snapshots': len(snaps),
            'documents': s.scalar(select(func.count()).select_from(Document).where(Document.book_id == book_id))},
            'capabilities': [{'name': name, 'available': required <= kinds, 'missing': sorted(required - kinds)}
                             for name, required in requirements],
            'balances': balances([r.payload for r in records]), 'latest_snapshot_id': latest.id if latest else None,
            'cases': [case_data(c) for c in cases if latest and c.snapshot_id == latest.id]}


def save_import(s, book_id, filename, content, mapping=None, synthetic=False):
    book = get(s, Book, book_id)
    rows, errors, columns, inferred, extracted = parse_file(content, filename, mapping)
    for row in rows:
        if row['record']['period'] != book.period:
            errors.append({'row': row['locator']['row'], 'field': 'period', 'message': '记账期间不属于当前账套期间；业务日期可跨期，记账期间需要显式选择'})
    sha = digest(content)
    storage = settings.data_dir / 'sources' / sha[:2] / sha
    storage.parent.mkdir(parents=True, exist_ok=True)
    if not storage.exists():
        storage.write_bytes(content)
    doc = Document(book_id=book_id, name=Path(filename).name, sha256=sha,
                   storage_path=str(storage.resolve()), synthetic=synthetic, extracted=extracted,
                   media_type='application/pdf' if filename.lower().endswith('.pdf') else 'application/octet-stream')
    s.add(doc)
    s.flush()
    batch = ImportBatch(book_id=book_id, document_id=doc.id, rows=rows, errors=errors, columns=columns, mapping=inferred)
    s.add(batch)
    s.commit()
    return batch


@app.post('/api/v1/imports')
async def upload(book_id: str = Form(...), file: UploadFile = File(...), mapping: str = Form('{}'), s=Depends(session)):
    content = await file.read(settings.max_upload_bytes + 1)
    if len(content) > settings.max_upload_bytes:
        raise HTTPException(413, '上传文件超过 20 MB')
    try:
        batch = save_import(s, book_id, file.filename or 'data.csv', content, json.loads(mapping))
    except (ValueError, UnicodeError, zipfile_error(), PDFException, PdfminerException) as exc:
        raise HTTPException(422, str(exc)) from exc
    return {**serialize(batch), 'record_count': len(batch.rows), 'rows': batch.rows[:100]}


def zipfile_error():
    from zipfile import BadZipFile
    return BadZipFile


@app.put('/api/v1/imports/{identity}/mapping')
def remap(identity: str, body: dict, s=Depends(session)):
    batch = get(s, ImportBatch, identity)
    from .db import OcrReview
    if s.scalar(select(OcrReview.id).where(OcrReview.import_id == batch.id)) is not None:
        raise HTTPException(409, 'OCR人工核实预览不能重新映射；请新增字段复核版本')
    if batch.status == 'confirmed':
        raise HTTPException(409, '已确认导入不可重写')
    doc = get(s, Document, batch.document_id)
    try:
        rows, errors, columns, mapping, _ = parse_file(Path(doc.storage_path).read_bytes(), doc.name, body)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    batch.rows, batch.errors, batch.columns, batch.mapping = rows, errors, columns, mapping
    s.commit()
    return {**serialize(batch), 'record_count': len(rows), 'rows': rows[:100]}


@app.post('/api/v1/imports/{identity}/confirm')
def confirm(identity: str, s=Depends(session)):
    return confirm_import(s, get(s, ImportBatch, identity))


@app.get('/api/v1/books/{book_id}/imports')
def imports(book_id: str, s=Depends(session)):
    get(s, Book, book_id)
    return [{**serialize(b), 'rows': b.rows[:100], 'record_count': len(b.rows)}
            for b in s.scalars(select(ImportBatch).where(ImportBatch.book_id == book_id).order_by(ImportBatch.created_at.desc()))]


def demo_import(book_id, supplement, s):
    from datasets.generate import demo_bundle
    book = get(s, Book, book_id)
    bundle = demo_bundle(book.framework)
    source = bundle['supplemental_records' if supplement else 'records']
    fields = set(LedgerInput.model_fields)
    rows = [{k: v for k, v in row.items() if k in fields} for row in source]
    # Demo dates are explicit simulation inputs, not a modification of source accounting.
    for row in rows:
        row['period'] = book.period
        row['business_date'] = f'{book.period}-{row["business_date"][-2:]}'
    batch = save_import(s, book_id, 'synthetic-supplement.json' if supplement else 'synthetic-demo.json',
                        json.dumps(rows, ensure_ascii=False).encode(), synthetic=True)
    result = confirm_import(s, batch)
    return {**result, 'import_id': batch.id, 'records_count': result['imported'], 'synthetic': True}


@app.post('/api/v1/books/{book_id}/demo')
def seed_demo(book_id: str, s=Depends(session)):
    return demo_import(book_id, False, s)


@app.post('/api/v1/books/{book_id}/supplement-demo')
def supplement_demo(book_id: str, s=Depends(session)):
    return demo_import(book_id, True, s)


@app.post('/api/v1/snapshots')
def snapshot(body: SnapshotInput, s=Depends(session)):
    snap = make_snapshot(s, get(s, Book, body.book_id))
    s.commit()
    return serialize(snap)


@app.get('/api/v1/snapshots/{identity}')
def read_snapshot(identity: str, s=Depends(session)):
    return serialize(get(s, Snapshot, identity))


@app.post('/api/v1/check-runs')
def check(body: CheckInput, s=Depends(session)):
    snap = get(s, Snapshot, body.snapshot_id)
    if snap.book_id != body.book_id:
        raise HTTPException(403, '快照不属于当前账套')
    s.execute(update(Book).where(Book.id == body.book_id).values(revision=Book.revision))
    try:
        ensure_rule_compatibility(snap.policy)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    old = s.scalar(select(CheckRun).where(CheckRun.snapshot_id == snap.id, CheckRun.rule_version == rule_identity_key()))
    if old:
        return {**serialize(old), 'cases': [case_data(c) for c in s.scalars(select(Case).where(Case.check_run_id == old.id))]}
    findings = check_records(snapshot_records(s, snap), snap.policy)
    run = CheckRun(book_id=body.book_id, snapshot_id=snap.id, rule_version=rule_identity_key())
    s.add(run)
    s.flush()
    cases = [Case(book_id=body.book_id, snapshot_id=snap.id, check_run_id=run.id, finding=f) for f in findings]
    s.add_all(cases)
    s.add(Audit(book_id=body.book_id, action='checks.completed', details={'snapshot_id': snap.id, 'count': len(cases)}))
    s.commit()
    return {**serialize(run), 'cases': [case_data(c) for c in cases]}


@app.get('/api/v1/cases')
def cases(book_id: str, s=Depends(session)):
    get(s, Book, book_id)
    return [case_data(c) for c in s.scalars(select(Case).where(Case.book_id == book_id).order_by(Case.created_at.desc()))]


@app.get('/api/v1/cases/{identity}')
def read_case(identity: str, s=Depends(session)):
    item = get(s, Case, identity)
    snap, book = get(s, Snapshot, item.snapshot_id), get(s, Book, item.book_id)
    compatibility_error = None
    try:
        require_case_rules(s, item)
    except HTTPException as exc:
        compatibility_error = exc.detail
    runs = [serialize(r) for r in s.scalars(select(Run).where(Run.case_id == identity).order_by(Run.created_at.desc()))]
    return {**case_data(item), 'stale': snap.revision != book.revision or compatibility_error is not None,
            'rules_compatible': compatibility_error is None, 'rule_compatibility_error': compatibility_error,
            'runs': runs, 'investigations': runs,
            'reviews': [serialize(r) for r in s.scalars(select(Review).where(Review.case_id == identity))]}


@app.post('/api/v1/cases/{identity}/investigate')
def start_investigation(identity: str, body: InvestigateInput, background: BackgroundTasks, s=Depends(session)):
    item = get(s, Case, identity)
    snap = require_case_rules(s, item)
    if body.mode != 'rules':
        from .reference_selection import verify_snapshot_references
        try:
            verify_snapshot_references(snap.policy, snap.book_id)
        except (ValueError, OSError) as exc:
            raise HTTPException(409, '该快照的参考片段未通过来源绑定校验；请创建新快照重新检查，历史底稿仍可查看') from exc
    if body.mode != 'rules' and not settings.deepseek_api_key:
        raise HTTPException(409, '尚未配置 DEEPSEEK_API_KEY；可先运行确定性规则调查')
    # Share the book lock with imports and policy annotations. The run and its
    # captured policy results become visible in one transaction before dispatch.
    from .policy_runtime import lock_book
    from .investigation_policy import capture_policy_context
    lock_book(s, item.book_id)
    existing = s.scalar(select(Run).where(Run.active_slot == identity))
    if existing:
        return serialize(existing)
    run = Run(case_id=item.id, mode=body.mode, active_slot=identity)
    s.add(run)
    try:
        s.flush()
        context = capture_policy_context(s, run, snap)
        s.add(Audit(book_id=item.book_id, action='investigation.policy_context.captured',
                    details={'run_id': run.id, 'snapshot_id': snap.id, 'context_digest': context.digest}))
        s.commit()
    except IntegrityError:
        s.rollback()
        existing = s.scalar(select(Run).where(Run.active_slot == identity))
        if existing is None:
            raise HTTPException(409, '调查版本未能原子保存，请重新启动调查') from None
        return serialize(existing)
    from .jobs import dispatch
    dispatch(run.id, background)
    return serialize(run)


@app.get('/api/v1/runs/{identity}')
def read_run(identity: str, s=Depends(session)):
    return serialize(get(s, Run, identity))


@app.get('/api/v1/runs/{identity}/policy-context', response_model=InvestigationPolicyContextResponse)
def run_policy_context(identity: str, s=Depends(session)):
    from .investigation_policy import read_policy_context
    run = get(s, Run, identity)
    case = get(s, Case, run.case_id)
    snap = get(s, Snapshot, case.snapshot_id)
    if snap.book_id != case.book_id:
        raise HTTPException(409, '调查案件与政策上下文账套不一致')
    return read_policy_context(s, run, snap)


@app.get('/api/v1/runs/{identity}/events')
async def events(identity: str):
    with Session() as s:
        get(s, Run, identity)
    async def stream():
        previous = None
        for _ in range(300):
            with Session() as s:
                data = serialize(get(s, Run, identity))
            text = json.dumps(data, ensure_ascii=False)
            if text != previous:
                yield f'data: {text}\n\n'
                previous = text
            else:
                yield ': heartbeat\n\n'
            if data['status'] not in ['queued', 'running']:
                break
            await asyncio.sleep(1)
    return StreamingResponse(stream(), media_type='text/event-stream', headers={'Cache-Control': 'no-cache'})


def evidence_rows(s, item):
    snap = get(s, Snapshot, item.snapshot_id)
    if snap.book_id != item.book_id:
        raise HTTPException(403, '案件与证据快照不属于同一账套')
    all_records = snapshot_records(s, snap)
    selected = set(item.finding.get('record_ids', []))
    selected.update(r['id'] for r in all_records if r.get('evidence_id') in item.finding.get('evidence_ids', []))
    # Only original same-field identifiers with complete matching identities
    # expand automatically. Model citations below remain explicit references.
    chain = business_chain(all_records, seed_record_ids=selected, limit=max(1, len(all_records)),
                           include_metadata=False)
    selected.update(r['id'] for r in chain['records'])
    # A scoped investigation can legitimately cite another business chain in
    # the same snapshot. Include those locators without treating the suggested
    # association as a confirmed business relationship. Read only explicit
    # citation fields; arbitrary model text is never an evidence selector.
    cited = set()

    def collect_statements(value):
        if not isinstance(value, dict):
            return
        for field_name in ('claims', 'proposed_claims', 'hypotheses'):
            for statement in value.get(field_name, []) if isinstance(value.get(field_name), list) else []:
                if isinstance(statement, dict) and isinstance(statement.get('evidence_ids'), list):
                    cited.update(ref for ref in statement['evidence_ids'] if isinstance(ref, str))
        for selection in value.get('evidence_selections', []) if isinstance(value.get('evidence_selections'), list) else []:
            execution = selection.get('execution') if isinstance(selection, dict) else None
            if isinstance(execution, dict) and execution.get('performed') is True:
                refs = execution.get('record_evidence_ids', [])
                if isinstance(refs, list):
                    cited.update(ref for ref in refs if isinstance(ref, str))

    for run in s.scalars(select(Run).where(Run.case_id == item.id)):
        from .investigation_policy import load_policy_context
        captured = load_policy_context(s, run, snap)
        if captured:
            for saved in captured['checks']:
                cited.update(locator['evidence_id'] for locator in saved['binding']['evidence_locators'])
        collect_statements(run.result)
        if isinstance(run.result, dict) and isinstance(run.result.get('proposals'), list):
            for proposal in run.result['proposals']:
                collect_statements(proposal)
    selected.update(r['id'] for r in all_records if r.get('evidence_id') in cited)
    output = []
    for row in all_records:
        if row['id'] not in selected:
            continue
        obj = get(s, Record, row['id'])
        doc = get(s, Document, obj.document_id)
        output.append({'id': obj.evidence_id, 'record_id': obj.id, 'source_id': obj.source_id, 'kind': obj.kind,
                       'locator': obj.locator, 'document_id': doc.id, 'document_name': doc.name,
                       'synthetic': doc.synthetic, 'record': row})
    return output


@app.get('/api/v1/cases/{identity}/evidence')
def evidence(identity: str, s=Depends(session)):
    return evidence_rows(s, get(s, Case, identity))


@app.get('/api/v1/books/{book_id}/records')
def records(book_id: str, kind: str | None = None, s=Depends(session)):
    get(s, Book, book_id)
    query = select(Record).where(Record.book_id == book_id)
    if kind:
        query = query.where(Record.kind == kind)
    return [r.payload for r in s.scalars(query.order_by(Record.created_at).limit(50000))]


@app.get('/api/v1/books/{book_id}/documents')
def documents(book_id: str, s=Depends(session)):
    get(s, Book, book_id)
    return [{k: v for k, v in serialize(d).items() if k != 'storage_path'}
            for d in s.scalars(select(Document).where(Document.book_id == book_id))]


@app.get('/api/v1/books/{book_id}/matches')
def matches(book_id: str, flow: str = 'payable', s=Depends(session)):
    if flow not in ['payable', 'receivable']:
        raise HTTPException(422, 'flow must be payable or receivable')
    from .matching import match_transactions
    book = get(s, Book, book_id)
    rows = [r.payload for r in s.scalars(select(Record).where(Record.book_id == book_id)) if r.payload.get('period') == book.period]
    return match_transactions([r for r in rows if r['kind'] == 'invoice'], [r for r in rows if r['kind'] == 'bank'], flow=flow)


@app.get('/api/v1/books/{book_id}/bank-reconciliation')
def reconcile_bank(book_id: str, s=Depends(session)):
    from .domain import bank_reconciliation
    book = get(s, Book, book_id)
    rows = [r.payload for r in s.scalars(select(Record).where(Record.book_id == book_id)) if r.payload.get('period') == book.period]
    return bank_reconciliation(rows, policy_for(book))


@app.get('/api/v1/documents/{identity}/file')
def document_file(identity: str, s=Depends(session)):
    doc = get(s, Document, identity)
    path = Path(doc.storage_path)
    if not path.exists() or digest(path.read_bytes()) != doc.sha256:
        raise HTTPException(409, '来源文件缺失或内容校验不一致')
    return FileResponse(path, media_type=doc.media_type, filename=doc.name)


@app.post('/api/v1/simulations')
def simulation(body: SimulationInput, s=Depends(session)):
    item = get(s, Case, body.case_id)
    snap = require_case_rules(s, item)
    adjustments = []
    from pydantic import ValidationError
    for index, row in enumerate(body.adjustments):
        try:
            value = LedgerInput.model_validate({**row, 'source_id': f'simulation-{index}', 'kind': 'journal',
                                               'currency': snap.policy['currency']}).model_dump(mode='json', exclude_none=True)
        except ValidationError as exc:
            raise HTTPException(422, json.loads(exc.json())) from exc
        adjustments.append(value)
    result = simulate(snapshot_records(s, snap), adjustments, snap.policy)
    sim = Simulation(case_id=item.id, snapshot_id=snap.id, adjustments=adjustments, result=result)
    s.add(sim)
    s.add(Audit(book_id=item.book_id, action='simulation.created', details={'case_id': item.id, 'snapshot_id': snap.id}))
    s.commit()
    return {**serialize(sim), **result}


@app.get('/api/v1/simulations')
def simulations(case_id: str, s=Depends(session)):
    get(s, Case, case_id)
    return [{**serialize(x), **x.result} for x in s.scalars(select(Simulation).where(Simulation.case_id == case_id))]


@app.post('/api/v1/reviews')
def review(body: ReviewInput, s=Depends(session)):
    item = get(s, Case, body.case_id)
    require_case_rules(s, item)
    # Serialize with imports to prevent accepting a version that changed during review.
    s.execute(update(Book).where(Book.id == item.book_id).values(revision=Book.revision))
    book, snap = get(s, Book, item.book_id), get(s, Snapshot, item.snapshot_id)
    if item.snapshot_id != body.expected_snapshot_id or snap.revision != book.revision:
        raise HTTPException(409, '数据或政策已更新，请在最新快照重新检查和复核')
    row = Review(case_id=item.id, snapshot_id=snap.id, decision=body.decision, comment=body.comment)
    s.add(row)
    s.add(Audit(book_id=book.id, action='review.recorded', details={'case_id': item.id, 'decision': body.decision}))
    s.commit()
    return serialize(row)


@app.get('/api/v1/exports/cases/{identity}')
def export_case(identity: str, format: str = 'markdown', s=Depends(session)):
    if format not in ['markdown', 'json']:
        raise HTTPException(422, '支持 markdown 或 json')
    item = get(s, Case, identity)
    report = read_case(identity, s)
    report['evidence'] = evidence_rows(s, item)
    report['snapshot'] = serialize(get(s, Snapshot, item.snapshot_id))
    # Export each run's captured context, never today's policy results grafted
    # onto yesterday's model workpaper. Legacy runs remain explicitly unbound.
    report['investigation_policy_contexts'] = {
        run['id']: run_policy_context(run['id'], s) for run in report.get('runs', [])}
    report['simulations'] = simulations(identity, s)
    report['disclaimer'] = '会计复核辅助底稿；原始账簿未修改。规则仅覆盖声明场景，模型建议不是正式会计结论。'
    if format == 'json':
        content, media, extension = json.dumps(report, ensure_ascii=False, indent=2), 'application/json', 'json'
    else:
        lines = ['# 账证智核 · 复核底稿', '', report['disclaimer'], '', f"案件：{report['title']}",
                 f"快照：{item.snapshot_id}", f"规则：{report['rule_id']}", f"状态：{report['status']}",
                 f"当前是否过期：{report['stale']}", '', '## 发现', '', report['explanation'], '', '## 来源证据', '']
        for e in report['evidence']:
            lines.append(f"- {e['id']}：{e['document_name']} {json.dumps(e['locator'], ensure_ascii=False)}；来源编号 {e['source_id']}")
        lines += ['', '## 完整机器可读结果', '', '```json', json.dumps(report, ensure_ascii=False, indent=2), '```']
        content, media, extension = '\n'.join(lines), 'text/markdown', 'md'
    return Response(content, media_type=media, headers={'Content-Disposition': f'attachment; filename="case-{identity}.{extension}"'})


@app.get('/api/v1/evaluations')
def evaluations(s=Depends(session)):
    return {'runs': [serialize(e) for e in s.scalars(select(Evaluation).order_by(Evaluation.created_at.desc()))],
            'limitations': ['规则基线不代表 AI 评测', '仿真结果不代表真实企业业务有效性', '尚未实施专业会计复核'],
            'dataset_summary': {'target_families': 48, 'target_tasks': 480, 'truth_isolated': True}}


@app.post('/api/v1/evaluations/run')
def evaluate(body: dict, s=Depends(session)):
    if body.get('mode', 'rules') != 'rules':
        raise HTTPException(422, 'AI 对照实验请使用评测 CLI 显式配置模型与预算')
    from benchmarks.evaluate import run_evaluation
    result = run_evaluation()
    row = Evaluation(result=result)
    s.add(row)
    s.commit()
    return {**serialize(row), **result}
