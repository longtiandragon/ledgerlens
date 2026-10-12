"""Local-only OpenTelemetry spans for owned runs; no documents or model text.

One span measures one worker lease. Usage is cumulative for its run, not additive
across resumed leases. Unknown provider usage stays unknown, including failures.
There is no network exporter, automatic instrumentation or global SDK provider.
"""
from decimal import Decimal
import hashlib
import json
import logging
from pathlib import Path
from time import perf_counter_ns, time_ns

from opentelemetry.context import Context
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor, SpanExporter, SpanExportResult
from opentelemetry.sdk.trace.sampling import ALWAYS_ON
from opentelemetry.trace import StatusCode

from .config import settings
from .model_gateway import provider_request_usage, GatewayError

log = logging.getLogger(__name__)
# Official CNY peak, cache-miss list prices verified 2026-10-12. Deliberately
# conservative and frozen, not the tariff/bill at the request's actual time.
PRICE_SOURCE = 'https://api-docs.deepseek.com/zh-cn/quick_start/pricing/'
RATES = {'deepseek-flash': ('2', '8'), 'deepseek-v4-pro': ('9', '27')}
PRICE_HASH = hashlib.sha256(json.dumps(RATES, sort_keys=True).encode()).hexdigest()
TERMINAL = {'complete', 'failed', 'budget_exhausted', 'lease_lost', 'interrupted'}


def nonnegative(value):
    return type(value) is int and value >= 0


def usage_summary(usage, steps):
    """Deduplicate persisted response indices; never price a configured model."""
    calls = usage.get('calls') if isinstance(usage, dict) else None
    responses, duplicate = {}, False
    for step in steps:
        if step.get('event') != 'model_usage':
            continue
        index = step.get('request_index')
        if not nonnegative(index) or index == 0:
            duplicate = True
            continue
        if index in responses and responses[index] != step:
            duplicate = True
        responses[index] = step
    if not nonnegative(calls) or duplicate or any(index > calls for index in responses):
        return {'usage_status': 'invalid', 'cost_status': 'unknown'}
    known = 0
    amount = Decimal(0)
    for step in responses.values():
        model = step.get('provider_model')
        try:
            row = provider_request_usage(step.get('request_usage'))
        except GatewayError:
            continue
        inputs, outputs = row['prompt_tokens'], row['completion_tokens']
        if not isinstance(model, str) or model not in RATES or inputs is None or outputs is None:
            continue
        input_rate, output_rate = map(Decimal, RATES[model])
        amount += (inputs * input_rate + outputs * output_rate) / Decimal(1_000_000)
        known += 1
    result = {'usage_status': 'recorded', 'model_calls': calls, 'priced_responses': known,
              'unknown_cost_attempts': calls - known,
              'cost_status': 'estimated' if calls == known else 'partial' if known else 'unknown',
              'cost_currency': 'CNY', 'cost_basis': '2026-10-12_peak_all_input_cache_miss',
              'price_sha256': PRICE_HASH}
    # A known zero-call run costs zero. No response to paid attempts is not zero.
    if known or calls == 0:
        result['known_usage_peak_estimate_cny'] = format(amount, 'f')
    for name in ('tokens', 'reserved_tokens', 'tools'):
        if nonnegative(usage.get(name)):
            result[name] = usage[name]
    return result


class LocalSpanExporter(SpanExporter):
    def __init__(self, directory: Path):
        self.directory = Path(directory)
        self.failures = 0

    def export(self, spans):
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            for span in spans:
                identity = f'{span.context.trace_id:032x}-{span.context.span_id:016x}'
                path = self.directory / (identity + '.json')
                payload = {'schema_version': 'local-run-span-v1', 'name': span.name,
                           'trace_id': f'{span.context.trace_id:032x}',
                           'span_id': f'{span.context.span_id:016x}',
                           'start_time_unix_ns': span.start_time, 'end_time_unix_ns': span.end_time,
                           'duration_ns': span.end_time - span.start_time,
                           'status': span.status.status_code.name, 'attributes': dict(span.attributes),
                           'scope': 'worker_lease_duration_cumulative_run_usage_not_provider_bill'}
                # Exclusive per-span files avoid interleaving between workers.
                # A truncated file after a crash is detectable, never replaced.
                with path.open('x', encoding='utf-8') as handle:
                    json.dump(payload, handle, ensure_ascii=True, allow_nan=False)
                    handle.write('\n')
            return SpanExportResult.SUCCESS
        except (OSError, ValueError, TypeError):
            self.failures += 1
            log.warning('Local telemetry export failed; business result is independent')
            return SpanExportResult.FAILURE

    def shutdown(self):
        pass


class RunTrace:
    def __init__(self, identity, lease, directory):
        self.exporter = LocalSpanExporter(directory)
        self.provider = TracerProvider(resource=Resource({'service.name': 'ledgerlens'}),
                                       sampler=ALWAYS_ON, shutdown_on_exit=False)
        self.provider.add_span_processor(SimpleSpanProcessor(self.exporter))
        self.started_at, self.started_monotonic = time_ns(), perf_counter_ns()
        self.span = self.provider.get_tracer('ledgerlens.owned-run', '1').start_span(
            'ledgerlens.investigation', context=Context(), start_time=self.started_at, attributes={
                'ledgerlens.run_sha256': hashlib.sha256(identity.encode()).hexdigest(),
                'ledgerlens.lease_sha256': hashlib.sha256(lease.encode()).hexdigest(),
                'ledgerlens.usage_scope': 'cumulative_run_not_additive_across_leases'})
        self.usage, self.responses, self.status = {}, [], 'interrupted'
        self.finished = False

    def seed(self, usage, steps):
        try:
            self.usage = dict(usage or {})
            self.responses = [self._response(step) for step in steps if step.get('event') == 'model_usage']
        except (ValueError, TypeError, AttributeError):
            self.usage, self.responses = {}, []

    @staticmethod
    def _response(step):
        return {key: step.get(key) for key in ('event', 'request_index', 'request_usage', 'provider_model')}

    def observe(self, step):
        try:
            if 'usage' in step:
                self.usage = dict(step['usage'])
            if step.get('event') == 'model_usage':
                self.responses.append(self._response(step))
        except (ValueError, TypeError, AttributeError):
            self.usage = {}

    def finish(self):
        if self.finished:
            return
        self.finished = True
        try:
            status = self.status if self.status in TERMINAL else 'failed'
            self.span.set_attribute('ledgerlens.outcome', status)
            self.span.set_attributes({'ledgerlens.' + key: value for key, value in
                                      usage_summary(self.usage, self.responses).items()})
            self.span.set_status(StatusCode.OK if status == 'complete' else StatusCode.ERROR)
            # Wall-clock granularity/adjustments must not erase or reverse elapsed time.
            self.span.end(end_time=self.started_at + perf_counter_ns() - self.started_monotonic)
        except Exception:
            log.warning('Local telemetry finalization failed; business result is independent')
        finally:
            self.provider.shutdown()


def start_run(identity, lease):
    if not settings.telemetry_enabled:
        return None
    try:
        return RunTrace(identity, lease, settings.data_dir / 'telemetry')
    except Exception:
        log.warning('Local telemetry initialization failed; business result is independent')
        return None
