"""Read-only HTTP access to verified process snapshots; separate from books."""
from datetime import UTC, datetime
import json as _json
import re as _re
import uuid

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query
from fastapi.responses import PlainTextResponse

from .config import settings
from .process_events import (ProcessSnapshot, get_process_trace, query_process_events,
                             resolve_process_evidence)
from .process_rules import (events_by_trace, evaluate_trace, review_trace, rule_catalog, rule_pack_identity,
                            summarize_snapshot, trace_workpaper, workpaper_markdown)

router = APIRouter(prefix='/api/v1/process-datasets', tags=['process evidence'])


def load_scoped(dataset_scope_id: str, snapshot_id: str) -> ProcessSnapshot:
    from .process_store import load_process_snapshot
    try:
        snapshot = load_process_snapshot(settings.process_datasets_dir, snapshot_id)
    except FileNotFoundError:
        raise HTTPException(404, '流程数据快照不存在') from None
    except (ValueError, OSError):
        raise HTTPException(409, '流程证据的来源、文件或内容校验失败') from None
    if snapshot.dataset.dataset_scope_id != dataset_scope_id:
        raise HTTPException(404, '数据集与快照不匹配')
    return snapshot


@router.get('/rule-catalog')
def process_rule_catalog():
    return {'rules': rule_catalog(), 'namespace': 'BPI2019_PROCESS',
            'notice': '流程观察规则依据数据集公开流程说明，独立于CAS／SCAS会计规则，均为实验性。'}


@router.get('')
def list_datasets():
    from .process_store import list_process_snapshots
    try:
        snapshots = list_process_snapshots(settings.process_datasets_dir)
    except (ValueError, OSError):
        raise HTTPException(409, '已导入流程资料校验失败，请核对本地采集记录') from None
    return {'snapshots': snapshots, 'read_only': True, 'scope': 'process_events',
            'notice': '匿名采购流程证据独立于会计账套；完整日志不表示业务已完成。'}


@router.get('/{dataset_scope_id}/snapshots/{snapshot_id}')
def snapshot_detail(dataset_scope_id: str, snapshot_id: str):
    return load_scoped(dataset_scope_id, snapshot_id).dataset.model_dump(mode='json')


@router.get('/{dataset_scope_id}/snapshots/{snapshot_id}/traces')
def traces(dataset_scope_id: str, snapshot_id: str, q: str = Query('', max_length=200),
           conclusion: str | None = Query(None, pattern='^(compatible|needs_review|insufficient|out_of_scope)$'),
           offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=200)):
    snapshot = load_scoped(dataset_scope_id, snapshot_id)
    index = events_by_trace(snapshot)
    conclusions = {trace.source_trace_id: evaluate_trace(snapshot, trace, index=index)['conclusion']
                   for trace in snapshot.traces}
    selected = [trace for trace in snapshot.traces if (not q or q.casefold() in
                (trace.trace_alias + ' ' + trace.source_trace_id).casefold())
                and (conclusion is None or conclusions[trace.source_trace_id] == conclusion)]
    return {'dataset_scope_id': dataset_scope_id, 'snapshot_id': snapshot_id,
            'total': len(selected), 'offset': offset, 'limit': limit,
            'next_offset': offset + limit if offset + limit < len(selected) else None,
            'items': [{'source_trace_id': trace.source_trace_id, 'trace_alias': trace.trace_alias,
                       'evidence_id': trace.evidence_id, 'event_count': len(trace.event_ids),
                       'classification': trace.policy.classification, 'policy_status': trace.policy.status,
                       'business_completion': trace.business_completion,
                       'rule_conclusion': conclusions[trace.source_trace_id]} for trace in selected[offset:offset + limit]]}


@router.get('/{dataset_scope_id}/snapshots/{snapshot_id}/trace')
def trace_detail(dataset_scope_id: str, snapshot_id: str, source_trace_id: str = Query(..., max_length=500),
                 offset: int = Query(0, ge=0), limit: int = Query(100, ge=1, le=200)):
    snapshot = load_scoped(dataset_scope_id, snapshot_id)
    try:
        return get_process_trace(snapshot, dataset_scope_id=dataset_scope_id, snapshot_id=snapshot_id,
                                 source_trace_id=source_trace_id, offset=offset, limit=limit)
    except KeyError:
        raise HTTPException(404, '当前快照没有该采购流程') from None


@router.get('/{dataset_scope_id}/snapshots/{snapshot_id}/events')
def events(dataset_scope_id: str, snapshot_id: str, source_trace_id: str | None = Query(None, max_length=500),
           activity_raw: str | None = Query(None, max_length=200), event_type: str | None = Query(None, max_length=200),
           occurred_from: datetime | None = None, occurred_to: datetime | None = None,
           offset: int = Query(0, ge=0), limit: int = Query(100, ge=1, le=200)):
    snapshot = load_scoped(dataset_scope_id, snapshot_id)
    try:
        return query_process_events(snapshot, dataset_scope_id=dataset_scope_id, snapshot_id=snapshot_id,
                                    source_trace_id=source_trace_id, activity_raw=activity_raw, event_type=event_type,
                                    occurred_from=occurred_from, occurred_to=occurred_to, offset=offset, limit=limit)
    except ValueError:
        raise HTTPException(422, '时间范围需包含时区且起点不得晚于终点') from None


@router.get('/{dataset_scope_id}/snapshots/{snapshot_id}/evidence/{evidence_id}')
def evidence(dataset_scope_id: str, snapshot_id: str, evidence_id: str):
    snapshot = load_scoped(dataset_scope_id, snapshot_id)
    try:
        return resolve_process_evidence(snapshot, dataset_scope_id=dataset_scope_id,
                                        snapshot_id=snapshot_id, evidence_id=evidence_id)
    except KeyError:
        raise HTTPException(404, '证据不属于当前流程快照') from None


@router.get('/{dataset_scope_id}/snapshots/{snapshot_id}/rule-summary')
def rule_summary(dataset_scope_id: str, snapshot_id: str):
    return summarize_snapshot(load_scoped(dataset_scope_id, snapshot_id),
                              dataset_scope_id=dataset_scope_id, snapshot_id=snapshot_id)


@router.get('/{dataset_scope_id}/snapshots/{snapshot_id}/trace-review')
def trace_review(dataset_scope_id: str, snapshot_id: str, source_trace_id: str = Query(..., max_length=500)):
    snapshot = load_scoped(dataset_scope_id, snapshot_id)
    try:
        return review_trace(snapshot, dataset_scope_id=dataset_scope_id, snapshot_id=snapshot_id,
                            source_trace_id=source_trace_id)
    except KeyError:
        raise HTTPException(404, '当前快照没有该采购流程') from None


@router.get('/{dataset_scope_id}/snapshots/{snapshot_id}/trace-review/export')
def trace_review_export(dataset_scope_id: str, snapshot_id: str, source_trace_id: str = Query(..., max_length=500),
                        format: str = Query('json', pattern='^(json|markdown)$')):
    snapshot = load_scoped(dataset_scope_id, snapshot_id)
    try:
        paper = trace_workpaper(snapshot, dataset_scope_id=dataset_scope_id, snapshot_id=snapshot_id,
                                source_trace_id=source_trace_id)
    except KeyError:
        raise HTTPException(404, '当前快照没有该采购流程') from None
    except ValueError:
        raise HTTPException(409, '底稿引用无法在当前流程快照中定位') from None
    if format == 'markdown':
        return PlainTextResponse(workpaper_markdown(paper), media_type='text/markdown; charset=utf-8')
    return paper


# --- Model explanation runs (explicit, paid, append-only) --------------------
_RUN_ID = _re.compile(r"process-run-[0-9a-f]{32}")
_active: set[str] = set()


def _runs_dir():
    path = settings.data_dir / "process-investigations"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _write_run(record: dict) -> None:
    path = _runs_dir() / f"{record['id']}.json"
    temporary = path.with_suffix(".tmp")
    temporary.write_text(_json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def _settle(record: dict) -> dict:
    # A run left "running" by a restarted server is marked, never shown as live.
    if record.get("status") == "running" and record.get("source_trace_id") not in _active:
        record.update(status="interrupted", completed_at=datetime.now(UTC).isoformat())
        _write_run(record)
    return record


def _read_run(run_id: str) -> dict:
    if not _RUN_ID.fullmatch(run_id):
        raise HTTPException(404, '调查记录不存在')
    path = _runs_dir() / f"{run_id}.json"
    if not path.exists():
        raise HTTPException(404, '调查记录不存在')
    return _settle(_json.loads(path.read_text(encoding="utf-8")))


async def _execute_run(record: dict) -> None:
    from .model_gateway import DeepSeekGateway
    from .process_investigation import ProcessScope, investigate_trace
    try:
        snapshot = load_scoped(record["dataset_scope_id"], record["snapshot_id"])
        scope = ProcessScope(snapshot, dataset_scope_id=record["dataset_scope_id"], snapshot_id=record["snapshot_id"],
                             source_trace_id=record["source_trace_id"])
        gateway = DeepSeekGateway(settings.deepseek_api_key, base_url=settings.deepseek_base_url,
                                  model=settings.deepseek_model)
        result = await investigate_trace(scope, gateway)
        record.update(status=result["status"], result=result)
    except Exception as exc:  # recorded as a failure, never as success
        record.update(status="failed", result=None, error={"code": "execution_error", "message": type(exc).__name__})
    finally:
        record["completed_at"] = datetime.now(UTC).isoformat()
        _write_run(record)
        _active.discard(record["source_trace_id"])


@router.post('/{dataset_scope_id}/snapshots/{snapshot_id}/trace-investigations', status_code=202)
def start_trace_investigation(dataset_scope_id: str, snapshot_id: str, background: BackgroundTasks,
                              source_trace_id: str = Query(..., max_length=500)):
    snapshot = load_scoped(dataset_scope_id, snapshot_id)
    if not any(trace.source_trace_id == source_trace_id for trace in snapshot.traces):
        raise HTTPException(404, '当前快照没有该采购流程')
    if not settings.deepseek_api_key:
        raise HTTPException(409, '尚未配置 DEEPSEEK_API_KEY；流程规则观察仍可使用')
    if _active:
        raise HTTPException(409, '已有流程模型解释正在运行，请稍后再试')
    _active.add(source_trace_id)
    record = {"id": "process-run-" + uuid.uuid4().hex, "status": "running",
              "created_at": datetime.now(UTC).isoformat(), "dataset_scope_id": dataset_scope_id,
              "snapshot_id": snapshot_id, "source_trace_id": source_trace_id,
              "snapshot_binding": {"source_subset_sha256": snapshot.dataset.source_subset_sha256,
                                   "adapter_version": snapshot.dataset.adapter_version,
                                   "rule_pack": rule_pack_identity()},
              "notice": "模型解释为未经核验的意见；程序流程规则观察是事实依据。启动会调用付费模型服务。"}
    _write_run(record)
    background.add_task(_execute_run, record)
    return record


@router.get('/process-investigations/{run_id}')
def read_trace_investigation(run_id: str):
    return _read_run(run_id)


@router.get('/{dataset_scope_id}/snapshots/{snapshot_id}/trace-investigations')
def list_trace_investigations(dataset_scope_id: str, snapshot_id: str, source_trace_id: str = Query(..., max_length=500)):
    load_scoped(dataset_scope_id, snapshot_id)
    runs = []
    for path in sorted(_runs_dir().glob("process-run-*.json")):
        record = _json.loads(path.read_text(encoding="utf-8"))
        if (record.get("dataset_scope_id"), record.get("snapshot_id"), record.get("source_trace_id")) == (
                dataset_scope_id, snapshot_id, source_trace_id):
            runs.append(_settle(record))
    return {"runs": sorted(runs, key=lambda r: r["created_at"], reverse=True)}
