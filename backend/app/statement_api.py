"""Read-only public-statement workspace, never a ledger or an Agent tool."""
from typing import Literal

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response

from .config import settings
from .statement_store import (StatementDataset, StatementReport, list_statement_snapshots,
                              load_statement_snapshot, source_pdf, verify_locator, verify_context,
                              StatementModel, StatementObservation, StatementContext, ColumnHeader)

router = APIRouter(prefix='/api/v1/statement-datasets', tags=['public statement evidence'])


class StatementDatasetList(StatementModel):
    snapshots: list[StatementDataset]
    read_only: Literal[True] = True
    scope: Literal['public_consolidated_balance_sheet'] = 'public_consolidated_balance_sheet'
    notice: str


class StatementReportsPage(StatementModel):
    items: list[StatementReport]
    total: int
    offset: int
    limit: int
    next_offset: int | None


class StatementSourceIdentity(StatementModel):
    report_id: str
    company_name: str
    report_year: int
    raw_sha256: str
    url: str


class StatementPageGeometry(StatementModel):
    width: float
    height: float
    rotation: int


class StatementEvidenceDetail(StatementModel):
    observation: StatementObservation
    report: StatementSourceIdentity
    context: StatementContext
    column_header: ColumnHeader
    page: StatementPageGeometry
    read_only: Literal[True] = True
    semantic_status: Literal['unverified_extraction'] = 'unverified_extraction'


def load_scoped(dataset_scope_id: str, snapshot_id: str):
    try:
        snapshot, probe, coverage = load_statement_snapshot(settings.statement_datasets_dir, snapshot_id)
    except FileNotFoundError:
        raise HTTPException(404, '年报快照不存在') from None
    except (ValueError, OSError, KeyError, IndexError, TypeError, ArithmeticError):
        raise HTTPException(409, '年报快照的来源、内容或版本校验失败') from None
    if snapshot.dataset.dataset_scope_id != dataset_scope_id:
        raise HTTPException(404, '数据集与快照不匹配')
    return snapshot, probe, coverage


@router.get('', response_model=StatementDatasetList)
def datasets():
    try:
        snapshots = list_statement_snapshots(settings.statement_datasets_dir)
    except (ValueError, OSError, KeyError, IndexError, TypeError, ArithmeticError):
        raise HTTPException(409, '已导入年报快照校验失败，请检查本地来源记录') from None
    return {'snapshots': snapshots, 'read_only': True, 'scope': 'public_consolidated_balance_sheet',
            'notice': '公开合并报表的抽取观察，尚未独立复核；不属于会计账套或可执行准则覆盖。'}


@router.get('/{dataset_scope_id}/snapshots/{snapshot_id}', response_model=StatementDataset)
def detail(dataset_scope_id: str, snapshot_id: str):
    return load_scoped(dataset_scope_id, snapshot_id)[0].dataset


@router.get('/{dataset_scope_id}/snapshots/{snapshot_id}/reports', response_model=StatementReportsPage)
def reports(dataset_scope_id: str, snapshot_id: str, q: str = Query('', max_length=200),
            status: Literal['all', 'observed', 'rejected'] = 'all',
            offset: int = Query(0, ge=0), limit: int = Query(30, ge=1, le=100)):
    snapshot = load_scoped(dataset_scope_id, snapshot_id)[0]
    selected = [r for r in snapshot.reports if (status == 'all' or r.status == status) and (not q or
                q.casefold() in f'{r.company_name} {r.company_code} {r.report_year} {r.report_id}'.casefold())]
    return {'items': selected[offset:offset + limit], 'total': len(selected), 'offset': offset, 'limit': limit,
            'next_offset': offset + limit if offset + limit < len(selected) else None}


def get_report(snapshot, report_id: str) -> StatementReport:
    report = next((r for r in snapshot.reports if r.report_id == report_id), None)
    if report is None:
        raise HTTPException(404, '年报不属于当前快照')
    return report


@router.get('/{dataset_scope_id}/snapshots/{snapshot_id}/reports/{report_id}/file')
def original_file(dataset_scope_id: str, snapshot_id: str, report_id: str):
    snapshot, _, coverage = load_scoped(dataset_scope_id, snapshot_id)
    report = get_report(snapshot, report_id)
    try:
        data = source_pdf(settings.annual_reports_dir, report, coverage)
    except FileNotFoundError:
        raise HTTPException(404, '本地原始 PDF 不存在') from None
    except (ValueError, OSError, KeyError):
        raise HTTPException(409, '年报原文件哈希校验失败') from None
    # Return the bytes that were verified, avoiding a second path read/TOCTOU.
    return Response(data, media_type='application/pdf', headers={
        'Content-Disposition': f'inline; filename="{report.report_id}.pdf"',
        'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff'})


@router.get('/{dataset_scope_id}/snapshots/{snapshot_id}/evidence/{evidence_id}', response_model=StatementEvidenceDetail)
def evidence(dataset_scope_id: str, snapshot_id: str, evidence_id: str):
    snapshot, probe, coverage = load_scoped(dataset_scope_id, snapshot_id)
    found = next(((r, o) for r in snapshot.reports for o in r.observations if o.evidence_id == evidence_id), None)
    if found is None:
        raise HTTPException(404, '证据不属于当前年报快照')
    report, observation = found
    raw = next(r for r in probe['reports'] if r['id'] == report.report_id)
    try:
        data = source_pdf(settings.annual_reports_dir, report, coverage)
        page = verify_locator(data, observation, report_row=raw)
        context, header = verify_context(data, raw)
    except FileNotFoundError:
        raise HTTPException(404, '本地原始 PDF 不存在') from None
    except (ValueError, OSError, KeyError, IndexError, TypeError):
        raise HTTPException(409, '年报证据与原文件的定位校验失败') from None
    return {'observation': observation, 'report': {'report_id': report.report_id, 'company_name': report.company_name,
            'report_year': report.report_year, 'raw_sha256': report.raw_sha256, 'url': report.url},
            'context': context, 'column_header': header, 'page': page,
            'read_only': True, 'semantic_status': 'unverified_extraction'}
