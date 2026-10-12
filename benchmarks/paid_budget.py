"""Pre-dispatch conservative CNY reservation for the sequential research runner.

Official CNY peak prices verified 2026-10-12:
https://api-docs.deepseek.com/zh-cn/quick_start/pricing/
Even off-peak runs reserve at peak prices. This is a list-price bound, not a bill.
"""
from decimal import Decimal

from app.model_gateway import BeforeDispatchRejected, BudgetExceeded
from benchmarks.run_agents import PaidRequestBudget

PRICE_SOURCE = 'https://api-docs.deepseek.com/zh-cn/quick_start/pricing/'
RATES = {'deepseek-flash': (Decimal('2'), Decimal('8')),
         'deepseek-v4-pro': (Decimal('9'), Decimal('27'))}


class PricedRequestBudget(PaidRequestBudget):
    def __init__(self, maximum, cap_cny, model):
        super().__init__(maximum)
        self.cap = Decimal(cap_cny)
        if not self.cap.is_finite() or self.cap <= 0 or model not in RATES:
            raise ValueError('A positive CNY cap and explicitly priced model are required')
        self.model = model
        self.rates = RATES[model]
        self.settled = Decimal(0)
        self.pending = {}
        self.last = {}

    def amount(self, inputs, outputs):
        return (Decimal(inputs) * self.rates[0] + Decimal(outputs) * self.rates[1]) / Decimal(1_000_000)

    async def before_attempt(self, sample_id, event):
        if event.get('event') != 'model_attempt':
            return
        inputs, outputs = event.get('context_bytes'), event.get('response_token_limit')
        if type(inputs) is not int or inputs < 0 or type(outputs) is not int or outputs <= 0:
            raise BeforeDispatchRejected('请求缺少费用预留上界；未发送')
        # UTF-8 request bytes bound ordinary text tokens; add framing headroom.
        inputs += 4096
        reservation = self.amount(inputs, outputs)
        if self.settled + sum(self.pending.values()) + reservation > self.cap:
            raise BeforeDispatchRejected('本批次费用预算不足以预留下一次请求；未发送')
        await super().before_attempt(sample_id, event)
        key = (sample_id, self.calls_by_sample[sample_id])
        self.pending[key] = reservation
        self.last[sample_id] = (key, inputs, outputs)

    def settle(self, sample_id, event):
        if event.get('event') != 'model_usage':
            return
        usage = event.get('request_usage') or {}
        inputs, outputs = usage.get('prompt_tokens'), usage.get('completion_tokens')
        if type(inputs) is not int or inputs < 0 or type(outputs) is not int or outputs < 0:
            return  # Unknown usage, timeout and HTTP failures keep their full reservations.
        key, input_bound, output_bound = self.last[sample_id]
        if key not in self.pending:
            raise BudgetExceeded('同一费用预留已结算，停止批次')
        self.pending.pop(key)
        self.settled += self.amount(inputs, outputs)  # All input charged as cache-miss at peak.
        if inputs > input_bound or outputs > output_bound:
            raise BudgetExceeded('服务端用量超过请求预留上界，停止批次并保留真实用量')

    def public(self):
        return {'cap_cny': str(self.cap), 'settled_peak_upper_cny': str(self.settled),
                'unknown_reserved_cny': str(sum(self.pending.values(), Decimal(0))),
                'model': self.model, 'price_source': PRICE_SOURCE, 'verified_on': '2026-10-12',
                'tariff': 'peak_no_cache_discount', 'not_a_provider_bill': True}
