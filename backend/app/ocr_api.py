from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy import select
from .db import session, Book, OcrRun
from .config import settings
from .ocr_contract import OcrReviewInput, OcrBatchReviewInput
from . import ocr_workflow as workflow

router = APIRouter(prefix='/api/v1/ocr', tags=['OCR人工核实'])


@router.get('/capabilities')
def capabilities():
    return {'available': workflow.available(), 'max_upload_bytes': settings.max_upload_bytes,
            'notice': '本地识别结果始终待核实；人工确认字段后进入普通导入预览，确认导入才建立快照。'}


@router.post('/runs', status_code=202)
async def start(background: BackgroundTasks, book_id: str = Form(...), file: UploadFile = File(...), s=Depends(session)):
    data = await file.read(settings.max_upload_bytes + 1)
    if len(data) > settings.max_upload_bytes:
        raise HTTPException(413, 'OCR原件超过上传限制')
    run = workflow.upload(s, book_id, file.filename or '', data)
    response = workflow.detail(s, run)
    background.add_task(workflow.execute, run.id)
    return response


@router.get('/runs')
def runs(book_id: str, s=Depends(session)):
    workflow.get(s, Book, book_id)
    return [{'id': r.id, 'status': r.status, 'created_at': r.created_at, 'document_id': r.document_id}
            for r in s.scalars(select(OcrRun).where(OcrRun.book_id == book_id).order_by(OcrRun.created_at.desc()).limit(100))]


@router.get('/runs/{identity}')
def detail(identity: str, book_id: str, s=Depends(session)):
    run = workflow.get(s, OcrRun, identity)
    if run.book_id != book_id:
        raise HTTPException(404, '当前账套中没有该识别任务')
    return workflow.detail(s, run)


@router.post('/runs/{identity}/reviews', status_code=201)
def review(identity: str, book_id: str, body: OcrReviewInput | OcrBatchReviewInput, s=Depends(session)):
    run = workflow.get(s, OcrRun, identity)
    if run.book_id != book_id:
        raise HTTPException(404, '当前账套中没有该识别任务')
    return workflow.review(s, run, body)
