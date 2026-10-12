from decimal import Decimal

import pytest

from app.model_gateway import BeforeDispatchRejected, BudgetExceeded
from benchmarks.paid_budget import PricedRequestBudget

ATTEMPT = {'event': 'model_attempt', 'context_bytes': 1000, 'response_token_limit': 1000}


@pytest.mark.asyncio
async def test_fee_reserved_before_dispatch_and_unknown_attempts_remain_reserved():
    budget = PricedRequestBudget(10, '0.03', 'deepseek-flash')
    await budget.before_attempt('a', ATTEMPT)
    assert budget.used == 1
    assert budget.public()['unknown_reserved_cny'] == '0.018192'
    with pytest.raises(BeforeDispatchRejected):
        await budget.before_attempt('a', ATTEMPT)
    assert budget.used == 1
    budget.settle('a', {'event': 'model_usage', 'request_usage': {'total_tokens': 20}})
    assert Decimal(budget.public()['unknown_reserved_cny']) == Decimal('0.018192')


@pytest.mark.asyncio
async def test_usage_settles_at_conservative_peak_price_without_cache_credit():
    budget = PricedRequestBudget(2, '0.03', 'deepseek-flash')
    await budget.before_attempt('a', ATTEMPT)
    budget.settle('a', {'event': 'model_usage', 'request_usage': {'prompt_tokens': 100, 'completion_tokens': 20}})
    assert budget.settled == Decimal('0.00036')
    await budget.before_attempt('b', ATTEMPT)
    assert budget.used == 2
    with pytest.raises(BeforeDispatchRejected):
        await budget.before_attempt('b', ATTEMPT)


@pytest.mark.asyncio
async def test_unexpected_provider_usage_is_retained_and_reported_as_failure():
    budget = PricedRequestBudget(2, '0.03', 'deepseek-flash')
    await budget.before_attempt('a', ATTEMPT)
    with pytest.raises(BudgetExceeded):
        budget.settle('a', {'event': 'model_usage', 'request_usage': {'prompt_tokens': 6000, 'completion_tokens': 1000}})
    assert budget.settled == Decimal('0.02')
    with pytest.raises(BeforeDispatchRejected):
        await budget.before_attempt('a', ATTEMPT)


@pytest.mark.parametrize('cap,model', [('0', 'deepseek-flash'), ('NaN', 'deepseek-flash'), ('1', 'unknown')])
def test_invalid_limits_fail_closed(cap, model):
    with pytest.raises(ValueError):
        PricedRequestBudget(1, cap, model)
