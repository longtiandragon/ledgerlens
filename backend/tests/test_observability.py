"""Offline SDK export, exact costs, missing usage and real worker boundaries."""
import hashlib
import json

import pytest

from app import investigation, jobs, observability
from app.config import settings
from test_runtime_ownership import worker_db, queued  # noqa: F401


def response(index=1, model='deepseek-flash', prompt=1000, output=100):
    return {'event': 'model_usage', 'request_index': index, 'provider_model': model,
            'request_usage': {'prompt_tokens': prompt, 'completion_tokens': output}}


def test_cost_is_decimal_peak_estimate_with_timeout_still_unknown():
    result = observability.usage_summary({'calls': 2, 'tokens': 1100, 'reserved_tokens': 9000}, [response()])
    assert result['known_usage_peak_estimate_cny'] == '0.0028'
    assert result['unknown_cost_attempts'] == 1 and result['cost_status'] == 'partial'
    assert result['reserved_tokens'] == 9000


@pytest.mark.parametrize('steps', [[], [response(model='unpriced-model')],
                                 [response(prompt=None)], [response(prompt=True)]])
def test_missing_usage_or_unknown_model_is_not_a_zero_cost(steps):
    result = observability.usage_summary({'calls': 1}, steps)
    assert result['cost_status'] == 'unknown'
    assert 'known_usage_peak_estimate_cny' not in result


def test_zero_calls_dedup_and_inconsistent_counter():
    assert observability.usage_summary({'calls': 0}, [])['known_usage_peak_estimate_cny'] == '0'
    assert observability.usage_summary({'calls': 1}, [response(), response()])['priced_responses'] == 1
    for usage, steps in [({'calls': 0}, [response()]), ({'calls': True}, []),
                         ({'calls': 1}, [response(), response(output=99)])]:
        assert observability.usage_summary(usage, steps)['usage_status'] == 'invalid'


def test_sdk_exports_span_without_text_or_environment_resource(tmp_path, monkeypatch):
    monkeypatch.setenv('OTEL_RESOURCE_ATTRIBUTES', 'customer.secret=do-not-export')
    trace = observability.RunTrace('private-run', 'private-lease', tmp_path)
    trace.seed({'calls': 0}, [])
    trace.observe({**response(), 'usage': {'calls': 1}, 'content': 'secret-invoice',
                   'reasoning_content': 'secret-thought', 'api_key': 'secret-key'})
    trace.status = 'complete'
    trace.finish()
    trace.finish()
    files = list(tmp_path.glob('*.json'))
    assert len(files) == 1
    raw = files[0].read_text()
    for secret in ('private-run', 'private-lease', 'secret-invoice', 'secret-thought', 'secret-key', 'do-not-export'):
        assert secret not in raw
    span = json.loads(raw)
    assert span['duration_ns'] > 0 and span['status'] == 'OK'
    assert span['attributes']['ledgerlens.known_usage_peak_estimate_cny'] == '0.0028'
    assert span['attributes']['ledgerlens.run_sha256'] == hashlib.sha256(b'private-run').hexdigest()
    assert 'events' not in span and 'resource' not in span


def test_export_failure_is_visible_and_does_not_raise(tmp_path, caplog):
    occupied = tmp_path / 'file'
    occupied.write_text('keep')
    trace = observability.RunTrace('run', 'lease', occupied)
    trace.finish()
    assert trace.exporter.failures == 1
    assert 'export failed' in caplog.text
    assert occupied.read_text() == 'keep'


@pytest.mark.asyncio
@pytest.mark.parametrize('fail', [False, True])
async def test_worker_persists_failure_cost_and_does_not_trace_duplicate_delivery(worker_db, tmp_path, monkeypatch, fail):  # noqa: F811
    sessions, _ = worker_db
    monkeypatch.setattr(settings, 'data_dir', tmp_path)
    monkeypatch.setattr(settings, 'telemetry_enabled', True)
    identity = queued(sessions)

    async def offline(*args, on_step, **kwargs):
        await on_step({**response(), 'usage': {'calls': 1}, 'secret': 'private-ledger-text'})
        if fail:
            await on_step({'event': 'model_transport_error', 'usage': {'calls': 2}})
            raise RuntimeError('private-error-body')
        return {'status': 'normal', 'usage': {'calls': 1}}

    monkeypatch.setattr(investigation, 'investigate', offline)
    await jobs._execute_owned(identity)
    await jobs._execute_owned(identity)
    files = list((tmp_path / 'telemetry').glob('*.json'))
    assert len(files) == 1
    span = json.loads(files[0].read_text())
    attributes = span['attributes']
    assert attributes['ledgerlens.outcome'] == ('failed' if fail else 'complete')
    assert attributes['ledgerlens.model_calls'] == (2 if fail else 1)
    assert attributes['ledgerlens.unknown_cost_attempts'] == int(fail)
    assert 'private-' not in files[0].read_text()


def test_disabled_does_not_initialize_sdk(monkeypatch):
    monkeypatch.setattr(settings, 'telemetry_enabled', False)
    monkeypatch.setattr(observability, 'RunTrace', lambda *args: pytest.fail('disabled trace initialized'))
    assert observability.start_run('r', 'l') is None


@pytest.mark.parametrize('order', [('first', 'resumed'), ('resumed', 'first')])
def test_summary_orders_leases_from_one_clock_tick_by_cumulative_progress(tmp_path, monkeypatch, order):
    # Windows time_ns() can return the same value for back-to-back leases; the span id is random.
    from scripts.summarize_telemetry import summarize
    monkeypatch.setattr(observability, 'time_ns', lambda: 1_800_000_000_000_000_000)
    leases = {'first': (1, [response()]), 'resumed': (2, [response(), response(index=2)])}
    for lease in order:
        calls, steps = leases[lease]
        trace = observability.RunTrace('tick-run', lease, tmp_path)
        trace.seed({'calls': calls}, steps)
        trace.status = 'complete'
        trace.finish()
    latest = summarize(tmp_path)['runs'][0]['latest_cumulative']
    assert latest['model_calls'] == 2 and latest['known_usage_peak_estimate_cny'] == '0.0056'


def test_summary_does_not_add_cumulative_resumed_costs_and_reports_corrupt_files(tmp_path):
    from scripts.summarize_telemetry import summarize
    for lease, calls, steps in [('first', 1, [response()]), ('resumed', 2, [response(), response(index=2)])]:
        trace = observability.RunTrace('same-run', lease, tmp_path)
        trace.seed({'calls': calls}, steps)
        trace.status = 'complete'
        trace.finish()
    summary = summarize(tmp_path)
    assert len(summary['runs']) == 1
    assert summary['runs'][0]['leases'] == 2
    assert summary['runs'][0]['latest_cumulative']['known_usage_peak_estimate_cny'] == '0.0056'
    (tmp_path / 'interrupted.json').write_text('{')
    summary = summarize(tmp_path)
    assert summary['status'] == 'invalid_files' and len(summary['failures']) == 1
    assert summarize(tmp_path / 'absent')['status'] == 'no_data'
