"""Durable run ownership; local execution or Celery delivery, never two workflows."""
import asyncio
from datetime import datetime, timezone, timedelta
from contextlib import asynccontextmanager
from sqlalchemy import select, update
from celery import Celery
from .config import settings
from .db import Session, Run, Case, Snapshot, CheckRun, uid, now
from .imports import snapshot_records
from .domain import ensure_rule_compatibility

celery_app = Celery('ledgerlens', broker=settings.redis_url)
celery_app.conf.update(task_serializer='json', accept_content=['json'], task_acks_late=True,
                       task_reject_on_worker_lost=True, worker_prefetch_multiplier=1,
                       broker_transport_options={'visibility_timeout': 1800})
celery_app.conf.beat_schedule = {'recover-stale-runs': {'task': 'ledgerlens.recover', 'schedule': 60.0}}
local_gate = None


@asynccontextmanager
async def saver_context():
    if settings.database_url.startswith('postgresql'):
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
        url = settings.database_url.replace('postgresql+psycopg://', 'postgresql://')
        async with AsyncPostgresSaver.from_conn_string(url) as saver:
            await saver.setup()
            yield saver
    else:
        from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
        async with AsyncSqliteSaver.from_conn_string(str(settings.data_dir / 'checkpoints.sqlite')) as saver:
            await saver.setup()
            yield saver


async def execute_run(identity: str):
    global local_gate
    if settings.task_mode == 'local':
        if local_gate is None:
            local_gate = asyncio.Semaphore(1)
        async with local_gate:
            return await _execute_owned(identity)
    return await _execute_owned(identity)


async def _execute_owned(identity: str):
    from .investigation import investigate
    from .model_gateway import DeepSeekGateway
    from .observability import start_run
    token = uid()
    with Session() as s:
        # Compare-and-set run ownership. Redelivery of terminal/running tasks is a no-op.
        changed = s.execute(update(Run).where(Run.id == identity, Run.status == 'queued')
                            .values(status='running', worker_token=token, heartbeat=now())).rowcount
        s.commit()
        if not changed:
            return

    trace = start_run(identity, token)

    async def persist(step):
        with Session() as s:
            run = s.get(Run, identity)
            if not run or run.worker_token != token or run.status != 'running':
                raise RuntimeError('运行租约已经失效')
            values = {'steps': [*run.steps, {**step, 'recorded_at': now()}], 'heartbeat': now()}
            if 'usage' in step:
                values['usage'] = dict(step['usage'])
            # The lease must still be current at write time, not just when read.
            changed = s.execute(update(Run).where(Run.id == identity, Run.worker_token == token,
                                                  Run.status == 'running').values(**values)).rowcount
            if not changed:
                raise RuntimeError('运行租约已经失效')
            s.commit()

        if trace:
            trace.observe(step)

    try:
        # Loading is part of the owned task: absent/malformed data must release
        # its active slot as a failure rather than leave a permanent running row.
        with Session() as s:
            run = s.get(Run, identity)
            if trace:
                trace.seed(run.usage, run.steps)
            case = s.get(Case, run.case_id)
            if case is None:
                raise ValueError('调查案件不存在')
            snap = s.get(Snapshot, case.snapshot_id)
            if snap is None or snap.book_id != case.book_id:
                raise ValueError('调查快照不存在或不属于当前账套')
            check = s.get(CheckRun, case.check_run_id)
            if check is None or check.book_id != case.book_id or check.snapshot_id != snap.id:
                raise ValueError('检查记录与案件快照不一致')
            ensure_rule_compatibility(snap.policy, check.rule_version)
            records = snapshot_records(s, snap)
            from .investigation_policy import load_policy_context
            policy = {**snap.policy, 'book_id': snap.book_id, 'snapshot_id': snap.id,
                      'snapshot_digest': snap.digest, 'snapshot_cutoff': snap.created_at,
                      'investigation_policy_context': load_policy_context(s, run, snap)}
            finding, mode, usage = case.finding, run.mode, run.usage
        if mode != 'rules':
            from .reference_selection import verify_snapshot_references
            verify_snapshot_references(policy)
        gateway = DeepSeekGateway(settings.deepseek_api_key, base_url=settings.deepseek_base_url,
                                  model=settings.deepseek_model) if mode != 'rules' else None
        async with saver_context() as saver:
            graph_config = {'configurable': {'thread_id': identity}}
            checkpoint = await saver.aget_tuple(graph_config)
            result = await investigate(records, policy, finding, mode=mode, gateway=gateway, on_step=persist,
                                       checkpoint_config={'checkpointer': saver, 'config': graph_config,
                                                          'resume': checkpoint is not None, 'usage': usage})
        if result.get('status') not in {'supported', 'normal', 'insufficient', 'unsupported',
                                        'failed', 'budget_exhausted'}:
            raise ValueError('调查结果缺少有效完成状态')
        with Session() as s:
            status = result['status'] if result.get('status') in ['failed', 'budget_exhausted'] else 'complete'
            changed = s.execute(update(Run).where(Run.id == identity, Run.worker_token == token,
                                        Run.status == 'running').values(
                result=result, usage=result.get('usage', usage), status=status,
                error=json_error(result.get('error')), active_slot=None, worker_token=None)).rowcount
            s.commit()
            if trace:
                trace.status = status if changed else 'lease_lost'
                if changed:
                    trace.usage = result.get('usage', usage)
    except Exception as exc:
        with Session() as s:
            changed = s.execute(update(Run).where(Run.id == identity, Run.worker_token == token,
                                        Run.status == 'running').values(
                status='failed', error=f'{type(exc).__name__}: {str(exc)[:500]}',
                active_slot=None, worker_token=None)).rowcount
            s.commit()
            if trace:
                trace.status = 'failed' if changed else 'lease_lost'
    finally:
        if trace:
            trace.finish()


def json_error(error):
    import json
    return json.dumps(error, ensure_ascii=False) if isinstance(error, (dict, list)) else error


@celery_app.task(name='ledgerlens.investigate')
def celery_investigate(identity):
    asyncio.run(execute_run(identity))


@celery_app.task(name='ledgerlens.recover')
def recover_stale_runs():
    cutoff = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
    with Session() as s:
        candidates = s.scalars(select(Run).where(Run.status == 'running', Run.heartbeat < cutoff)).all()
        recovered = []
        for run in candidates:
            changed = s.execute(update(Run).where(Run.id == run.id, Run.heartbeat < cutoff,
                                                  Run.status == 'running')
                                .values(status='queued', worker_token=None)).rowcount
            if changed:
                recovered.append(run.id)
        queued = list(s.scalars(select(Run.id).where(Run.status == 'queued')))
        s.commit()
    for identity in sorted(set([*recovered, *queued])):
        celery_investigate.delay(identity)
    return {'recovered': len(recovered), 'queued': len(queued)}


def dispatch(identity, background):
    if settings.task_mode == 'celery':
        try:
            celery_investigate.delay(identity)
        except Exception as exc:
            with Session() as s:
                # A broker may accept delivery before returning a transport error.
                # Do not overwrite a run that a worker already claimed.
                s.execute(update(Run).where(Run.id == identity, Run.status == 'queued').values(
                    status='failed', error='后台队列不可用，请检查 Redis/Worker', active_slot=None))
                s.commit()
            raise RuntimeError('任务队列派发失败') from exc
    else:
        background.add_task(execute_run, identity)
