import asyncio
import json
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI, HTTPException, Depends, UploadFile, File, Form, BackgroundTasks, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import Response, FileResponse, JSONResponse, StreamingResponse
from sqlalchemy import select, func, update
from sqlalchemy.exc import IntegrityError
from pdfminer.pdfexceptions import PDFException
from pdfplumber.utils.exceptions import PdfminerException
from .config import settings
from .db import (Base, engine, Session, session, serialize, Book, Document, ImportBatch,
                 Record, Snapshot, CheckRun, Case, Run, Simulation, Review, Audit, Evaluation, Approval, User)
from .schemas import (BookInput, SnapshotInput, CheckInput, InvestigateInput, SimulationInput, ReviewInput,
                      LedgerInput, ApprovalInput)
from .imports import parse_file, confirm_import, make_snapshot, snapshot_records, policy_for, digest, declares_public_example
from .domain import (balances, simulate, rule_catalog, ensure_rule_compatibility, rule_identity_key,
                     month_end_findings, scenario_of)
from .business_chain import business_chain
from . import case_flow, treatment
from .evidence_requests import requests_for_case
from .process_api import router as process_router
from .numeric_evaluation import router as numeric_evaluation_router
from .root_cause_results import router as root_cause_results_router
from .retrieval_results import router as retrieval_results_router
from .evidence_requests import router as evidence_requests_router
from .statement_api import router as statement_router
from .policy_api import router as policy_router
from .policy_context_contract import InvestigationPolicyContextResponse
from .ocr_api import router as ocr_router
from .reference_api import router as reference_router
from .auth_api import router as auth_router
from . import auth

# Initialize native libraries before startup/request threads are created. On
# Windows, loading them concurrently with thread creation can stall the server.
try:
    from ortools.sat.python import cp_model  # noqa: F401
except ImportError:
    pass  # matching retains its bounded pure-Python fallback.


def get(s, cls, identity):
    obj = s.get(cls, identity)
    if obj is None:
        raise HTTPException(404, f'{cls.__name__} 不存在')
    return obj


def case_data(item):
    return {**item.finding, 'id': item.id, 'book_id': item.book_id, 'snapshot_id': item.snapshot_id,
            'created_at': item.created_at, 'check_run_id': item.check_run_id,
            'scenario': item.finding.get('scenario') or scenario_of(item.finding.get('rule_id', ''))}


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
app.include_router(retrieval_results_router)
app.include_router(evidence_requests_router)
app.include_router(statement_router)
app.include_router(policy_router)
app.include_router(ocr_router)
app.include_router(reference_router)
app.include_router(auth_router)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=['127.0.0.1', 'localhost', 'testserver', 'api'])
app.add_middleware(CORSMiddleware, allow_origins=['http://127.0.0.1:5188', 'http://localhost:5188',
                                                'http://127.0.0.1:8080', 'http://localhost:8080'],
                   allow_methods=['GET', 'POST', 'PUT'], allow_headers=['Content-Type', 'Authorization'])


@app.middleware('http')
async def multi_user_guard(request: Request, call_next):
    # Multi-user mode: every API call except the open entry points needs a live
    # session. Role and duty-separation checks happen in the endpoints.
    path = request.url.path
    if auth.multi_user() and path.startswith('/api/') and path not in auth.OPEN_PATHS and request.method != 'OPTIONS':
        with Session() as s:
            if auth.resolve(s, auth.bearer(request)) is None:
                return JSONResponse({'detail': '请先登录'}, status_code=401)
    return await call_next(request)


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
    return {**health(), 'model': settings.deepseek_model, 'frameworks': ['CAS', 'SCAS'], 'auth_mode': settings.auth_mode,
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
    # parse_file has already verified a recognized envelope against its pinned original.
    # This property cannot be removed by an unchecked UI checkbox or synthetic=false.
    if Path(filename).suffix.lower() == '.json' and declares_public_example(json.loads(content)):
        synthetic = True
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
async def upload(book_id: str = Form(...), file: UploadFile = File(...), mapping: str = Form('{}'),
                 synthetic: bool = Form(False), s=Depends(session)):
    content = await file.read(settings.max_upload_bytes + 1)
    if len(content) > settings.max_upload_bytes:
        raise HTTPException(413, '上传文件超过 20 MB')
    try:
        batch = save_import(s, book_id, file.filename or 'data.csv', content, json.loads(mapping), synthetic=synthetic)
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
        result = {**serialize(old), 'cases': [case_data(c) for c in s.scalars(select(Case).where(Case.check_run_id == old.id))]}
        # Release the book write lock before returning. Dependency teardown can
        # otherwise wait behind an async investigator blocked on this same lock.
        s.commit()
        return result
    findings = month_end_findings(snapshot_records(s, snap), snap.policy)
    run = CheckRun(book_id=body.book_id, snapshot_id=snap.id, rule_version=rule_identity_key())
    s.add(run)
    s.flush()
    cases = [Case(book_id=body.book_id, snapshot_id=snap.id, check_run_id=run.id, finding=f) for f in findings]
    s.add_all(cases)
    s.flush()
    links, followups = case_flow.continue_cases(s, run, snap, cases, scenario_of)
    s.add(Audit(book_id=body.book_id, action='checks.completed',
                details={'snapshot_id': snap.id, 'count': len(cases), 'linked_to_prior': len(links),
                         'not_reproduced_followups': len(followups)}))
    s.commit()
    return {**serialize(run), 'cases': [case_data(c) for c in [*cases, *followups]]}


@app.get('/api/v1/cases')
def cases(book_id: str, s=Depends(session)):
    get(s, Book, book_id)
    return [case_data(c) for c in s.scalars(select(Case).where(Case.book_id == book_id).order_by(Case.created_at.desc()))]


def case_draft(s, item, snap):
    """The deterministic treatment draft of a rules-compatible case."""
    run = case_flow.latest_finished_run(s, item.id)
    return treatment.build_draft(item.id, snap.id, item.finding, snapshot_records(s, snap), snap.policy,
                                 serialize(run) if run else None)


def latest_simulation(s, item):
    return s.scalars(select(Simulation).where(Simulation.case_id == item.id, Simulation.snapshot_id == item.snapshot_id)
                     .order_by(Simulation.created_at.desc())).first()


def case_open_items(s, item, snap, simulation_row, compatible: bool):
    draft = None
    if compatible:
        try:
            draft = case_draft(s, item, snap)
        except HTTPException:
            draft = None  # missing snapshot records are reported by the case's own evidence views
    run = case_flow.latest_finished_run(s, item.id)
    return draft, case_flow.open_items(s, item, simulation=simulation_row, draft=draft,
                                       latest_run=serialize(run) if run else None)


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
    stale = snap.revision != book.revision or compatibility_error is not None
    draft, items = case_open_items(s, item, snap, latest_simulation(s, item), compatibility_error is None)
    return {**case_data(item), 'stale': stale,
            'rules_compatible': compatibility_error is None, 'rule_compatibility_error': compatibility_error,
            'runs': runs, 'investigations': runs,
            'reviews': [serialize(r) for r in s.scalars(select(Review).where(Review.case_id == identity)
                                                       .order_by(Review.created_at))],
            'lineage': case_flow.lineage(s, item, stale), 'open_items': items,
            'treatment': {k: draft[k] for k in ('status', 'summary', 'digest')} if draft else None,
            **signoff(s, identity)}


@app.get('/api/v1/cases/{identity}/treatment-draft')
def treatment_draft(identity: str, s=Depends(session)):
    item = get(s, Case, identity)
    return case_draft(s, item, require_case_rules(s, item))


def signoff(s, identity: str) -> dict:
    approvals = [serialize(a) for a in s.scalars(select(Approval).where(Approval.case_id == identity)
                                               .order_by(Approval.created_at))]
    actor_ids = {a['user_id'] for a in approvals}
    actor_ids |= {r.user_id for r in s.scalars(select(Review).where(Review.case_id == identity)) if r.user_id}
    actor_ids |= auth.investigators(s, identity)
    actors = {user.id: {'username': user.username, 'display_name': user.display_name}
              for user in (s.get(User, actor) for actor in sorted(actor_ids)) if user}
    return {'approvals': approvals, 'actors': actors, 'auth_mode': settings.auth_mode}


@app.post('/api/v1/cases/{identity}/investigate')
def start_investigation(identity: str, body: InvestigateInput, background: BackgroundTasks, request: Request,
                        s=Depends(session)):
    user = auth.acting_user(request, s, 'preparer')
    item = get(s, Case, identity)
    snap = require_case_rules(s, item)
    if item.finding.get('rule_id') == case_flow.CLEARED_RULE:
        raise HTTPException(409, '接续核对案件来自前后两次检查的比较，不另行调查；请查看前一案件的调查与补证记录后复核')
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
        result = serialize(existing)
        s.commit()
        return result
    run = Run(case_id=item.id, mode=body.mode, active_slot=identity, created_by=user.id if user else None)
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
    basis = {'source': 'manual'}
    if body.basis is not None:
        # Never trust a client-side copy: recompute the draft and compare.
        draft = case_draft(s, item, snap)
        if draft['digest'] != body.basis.draft_digest:
            raise HTTPException(409, '处理草稿已变化（调查结果、资料或程序版本已更新），请重新生成草稿后再模拟')
        basis = {'source': 'treatment_draft', 'draft_digest': draft['digest'], 'draft_version': draft['version'],
                 'draft_source_sha256': draft['source_sha256'], 'draft_status': draft['status'],
                 'summary': draft['summary'], 'rule_id': draft['rule_id'], 'investigation': draft['investigation'],
                 **treatment.compare_lines(draft, adjustments)}
    result = simulate(snapshot_records(s, snap), adjustments, snap.policy, target=item.finding)
    sim = Simulation(case_id=item.id, snapshot_id=snap.id, adjustments=adjustments, result=result, basis=basis)
    s.add(sim)
    s.add(Audit(book_id=item.book_id, action='simulation.created',
                details={'case_id': item.id, 'snapshot_id': snap.id, 'source': basis['source']}))
    s.commit()
    return {**serialize(sim), **result}


@app.get('/api/v1/simulations')
def simulations(case_id: str, s=Depends(session)):
    get(s, Case, case_id)
    return [{**serialize(x), **x.result} for x in s.scalars(select(Simulation).where(Simulation.case_id == case_id)
                                                           .order_by(Simulation.created_at, Simulation.id))]


@app.post('/api/v1/reviews')
def review(body: ReviewInput, request: Request, s=Depends(session)):
    user = auth.acting_user(request, s, 'reviewer')
    item = get(s, Case, body.case_id)
    auth.require_separate(user, auth.investigators(s, item.id), '发起该案件调查的人员不能复核同一案件（不相容职务分离）')
    require_case_rules(s, item)
    # Serialize with imports to prevent accepting a version that changed during review.
    s.execute(update(Book).where(Book.id == item.book_id).values(revision=Book.revision))
    book, snap = get(s, Book, item.book_id), get(s, Snapshot, item.snapshot_id)
    if item.snapshot_id != body.expected_snapshot_id or snap.revision != book.revision:
        raise HTTPException(409, '数据或政策已更新，请在最新快照重新检查和复核')
    chosen = None
    if body.simulation_id is not None:
        chosen = s.get(Simulation, body.simulation_id)
        if chosen is None or chosen.case_id != item.id or chosen.snapshot_id != snap.id:
            raise HTTPException(409, '只能选择本案件当前快照上的模拟方案')
    # Freeze what was unresolved when the decision was made; later events never rewrite it.
    _, items = case_open_items(s, item, snap, chosen, True)
    row = Review(case_id=item.id, snapshot_id=snap.id, decision=body.decision, comment=body.comment,
                 user_id=user.id if user else None, simulation_id=body.simulation_id, open_items=items)
    s.add(row)
    s.add(Audit(book_id=book.id, action='review.recorded', details={
        'case_id': item.id, 'decision': body.decision, 'user_id': user.id if user else None,
        'simulation_id': body.simulation_id, 'open_items': len(items)}))
    s.commit()
    return serialize(row)


@app.post('/api/v1/cases/{identity}/approvals')
def approve(identity: str, body: ApprovalInput, request: Request, s=Depends(session)):
    if not auth.multi_user():
        raise HTTPException(409, '审批签发需要多用户模式（AUTH_MODE=multi_user）')
    user = auth.acting_user(request, s, 'approver')
    item = get(s, Case, identity)
    require_case_rules(s, item)
    s.execute(update(Book).where(Book.id == item.book_id).values(revision=Book.revision))
    book, snap = get(s, Book, item.book_id), get(s, Snapshot, item.snapshot_id)
    if item.snapshot_id != body.expected_snapshot_id or snap.revision != book.revision:
        raise HTTPException(409, '数据或政策已更新，请在最新快照重新复核后再审批')
    review_row = s.get(Review, body.review_id)
    if not review_row or review_row.case_id != item.id or review_row.snapshot_id != snap.id:
        raise HTTPException(409, '只能审批本案件当前快照上的复核')
    latest = s.scalar(select(Review).where(Review.case_id == item.id, Review.snapshot_id == snap.id)
                      .order_by(Review.created_at.desc(), Review.id.desc()))
    if latest.id != review_row.id:
        raise HTTPException(409, '只能审批该快照上最新的复核意见')
    if review_row.user_id is None:
        raise HTTPException(409, '该复核没有可追溯的复核人（单用户模式记录），不能进入审批')
    if review_row.decision == 'returned':
        raise HTTPException(409, '已退回的复核不能审批签发')
    auth.require_separate(user, {review_row.user_id}, '复核人不能审批自己的复核（不相容职务分离）')
    auth.require_separate(user, auth.investigators(s, item.id), '发起该案件调查的人员不能审批同一案件（不相容职务分离）')
    row = Approval(case_id=item.id, snapshot_id=snap.id, review_id=review_row.id, user_id=user.id,
                   decision=body.decision, comment=body.comment)
    s.add(row)
    s.add(Audit(book_id=book.id, action='approval.recorded', details={
        'case_id': item.id, 'review_id': review_row.id, 'decision': body.decision, 'user_id': user.id}))
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
    report['evidence_requests'] = requests_for_case(s, identity)
    report['treatment_draft'] = None
    if report['rules_compatible']:
        try:
            report['treatment_draft'] = case_draft(s, item, get(s, Snapshot, item.snapshot_id))
        except HTTPException:
            pass
    report['disclaimer'] = '会计复核辅助底稿；原始账簿未修改。规则仅覆盖声明场景，模型建议不是正式会计结论。'
    if format == 'json':
        content, media, extension = json.dumps(report, ensure_ascii=False, indent=2), 'application/json', 'json'
    else:
        lines = ['# 账证智核 · 复核底稿', '', report['disclaimer'], '', f"案件：{report['title']}",
                 f"快照：{item.snapshot_id}", f"规则：{report['rule_id']}", f"状态：{report['status']}",
                 f"当前是否过期：{report['stale']}", '', '## 发现', '', report['explanation'], '', '## 来源证据', '']
        for e in report['evidence']:
            lines.append(f"- {e['id']}：{e['document_name']} {json.dumps(e['locator'], ensure_ascii=False)}；来源编号 {e['source_id']}")
        lineage = report['lineage']
        lines += ['', '## 接续关系', '']
        for prior in lineage['prior']:
            lines.append(f"- 接续自案件 {prior['case_id']}（快照 {prior['snapshot_id']}，{prior['match']}）：调查 {prior['runs']} 次，"
                         f"模拟 {prior['simulations']} 个，复核 {prior['reviews']} 条，补证 {prior['evidence_requests']} 项")
        for later in lineage['successors']:
            lines.append(f"- 已在后续快照 {later['snapshot_id']} 接续为案件 {later['case_id']}")
        if lineage['state'] == 'not_reproduced':
            lines.append(f"- 后续检查（快照 {lineage['next_check']['snapshot_id']}）未再出现该发现；不自动视为已解决")
        if not (lineage['prior'] or lineage['successors'] or lineage['state'] == 'not_reproduced'):
            lines.append('- 无')
        lines += ['', '## 补证事项', '']
        for request in report['evidence_requests']:
            lines.append(f"- {request['kind']}：{request['description']}（{request['status']}）")
            for event in request['events']:
                lines.append(f"  - {event['created_at']} {event['action']}：记录 {', '.join(event['record_ids']) or '无'} {event['comment']}")
        if not report['evidence_requests']:
            lines.append('- 无')
        lines += ['', '## 处理草稿与模拟', '']
        draft = report['treatment_draft']
        if draft:
            lines.append(f"- 当前草稿（{draft['version']}）：{draft['summary']}；状态 {draft['status']}。{draft['reason']}")
            lines.append(f"- 制度路径：{draft['framework_note']}")
        for sim in report['simulations']:
            basis = sim.get('basis') or {}
            target = (sim.get('target') or {}).get('status', '未记录')
            comparison = sim.get('comparison') or {}
            origin = (f"来自处理草稿“{basis.get('summary')}”，{'有人工修改' if basis.get('edited') else '未修改草稿分录'}，"
                      f"补齐 {len(basis.get('completed_inputs', []))} 项必填" if basis.get('source') == 'treatment_draft'
                      else '人工编制' if basis.get('source') == 'manual' else '来源未记录（0.2.0 之前）')
            lines.append(f"- 模拟 {sim['id']}（{sim['created_at']}）：{origin}；本案发现 {target}；"
                         f"消除 {len(comparison.get('resolved', []))} 项，仍存在 {len(comparison.get('persisting', []))} 项，"
                         f"新引入 {len(comparison.get('introduced', []))} 项；{'借贷平衡' if sim['balanced'] else '借贷不平衡'}")
        if not draft and not report['simulations']:
            lines.append('- 无')
        lines += ['', '## 未决事项（当前）', '']
        lines += [f"- {entry['text']}" for entry in report['open_items']] or ['- 无']
        actors = report.get('actors', {})
        lines += ['', '## 复核与审批', '']
        for r in report.get('reviews', []):
            who = actors.get(r.get('user_id'), {}).get('display_name', '单用户本地记录')
            frozen = r.get('open_items')
            lines.append(f"- 复核 {r['created_at']}：{r['decision']}（{who}）快照 {r['snapshot_id']} {r['comment']}"
                         f"；所选模拟 {r.get('simulation_id') or '无'}；"
                         + (f"复核时未决事项 {len(frozen)} 项" if frozen is not None else '复核时未决事项未记录（0.2.0 之前）'))
            for entry in frozen or []:
                lines.append(f"  - {entry['text']}")
        for a in report.get('approvals', []):
            who = actors.get(a['user_id'], {}).get('display_name', a['user_id'])
            lines.append(f"- 审批 {a['created_at']}：{a['decision']}（{who}）复核 {a['review_id']} {a['comment']}")
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
