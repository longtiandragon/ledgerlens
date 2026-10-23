from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy import select

from .db import session, Book, ReferenceSearch
from .reference_contract import ReferenceSearchInput, ReferenceChoiceInput, ReferenceSupportInput
from . import reference_workflow as workflow

router = APIRouter(prefix='/api/v1/reference-indexes', tags=['实验参考索引'])


@router.get('/capabilities')
def capabilities():
    return workflow.capabilities()


@router.post('/searches', status_code=202)
def start(body: ReferenceSearchInput, background: BackgroundTasks, s=Depends(session)):
    run = workflow.start(s, body)
    result = workflow.detail(s, run)
    background.add_task(workflow.execute, run.id)
    return result


@router.get('/searches')
def searches(book_id: str, s=Depends(session)):
    workflow.get(s, Book, book_id)
    return [{key: getattr(row, key) for key in ('id', 'status', 'created_at', 'query')}
            for row in s.scalars(select(ReferenceSearch).where(ReferenceSearch.book_id == book_id)
                                 .order_by(ReferenceSearch.created_at.desc()).limit(100))]


@router.get('/searches/{identity}')
def detail(identity: str, book_id: str, s=Depends(session)):
    run = workflow.get(s, ReferenceSearch, identity)
    if run.book_id != book_id:
        raise HTTPException(404, '当前账套中没有该参考检索')
    return workflow.detail(s, run)


@router.post('/support-checks')
def support(body: ReferenceSupportInput, s=Depends(session)):
    return workflow.support(s, body)


@router.get('/choices')
def choices(book_id: str, s=Depends(session)):
    return workflow.choices(s, book_id)


@router.post('/choices', status_code=201)
def choose(body: ReferenceChoiceInput, s=Depends(session)):
    return workflow.choose(s, body)
