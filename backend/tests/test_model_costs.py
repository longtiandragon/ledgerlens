import json

import pytest
from benchmarks.costs import DEFAULT_PRICES, estimate_batch, estimate_request, load_prices


def request(index=1, **usage):
    return {'request_index': index, 'provider_model': 'deepseek-flash',
            'request_usage': {'prompt_tokens': 1_000_000, 'prompt_cache_hit_tokens': 250_000,
                              'prompt_cache_miss_tokens': 750_000, 'completion_tokens': 100_000,
                              'total_tokens': 1_100_000, **usage}}


def test_exact_decimal_list_price_interval():
    # Independent handwritten calculation: .00075 + .1125 + .06 = .17325 USD.
    value = estimate_request(request(), load_prices(DEFAULT_PRICES))
    assert value['minimum_usd'] == '0.17325'
    assert value['maximum_usd'] == '0.3465'


def test_explicit_zero_is_known_zero():
    value = estimate_request(request(prompt_tokens=0, prompt_cache_hit_tokens=0,
                                     prompt_cache_miss_tokens=0, completion_tokens=0, total_tokens=0),
                             load_prices(DEFAULT_PRICES))
    assert value['status'] == 'estimated' and value['maximum_usd'] == '0.000'


@pytest.mark.parametrize('usage', [
    {'prompt_cache_hit_tokens': None}, {'prompt_tokens': True}, {'completion_tokens': -1},
    {'prompt_cache_miss_tokens': 800_000}, {'total_tokens': 1}, {'prompt_tokens': 1.0},
])
def test_missing_invalid_or_inconsistent_usage_has_no_price(usage):
    value = estimate_request(request(**usage), load_prices(DEFAULT_PRICES))
    assert value['status'] == 'unknown' and 'minimum_usd' not in value


def test_alias_or_unknown_provider_model_not_silently_assigned_rates():
    value = request()
    value['provider_model'] = 'third-party-flash'
    assert estimate_request(value, load_prices(DEFAULT_PRICES))['reason'] == 'unpriced_provider_model'


def test_failures_unscorable_and_unknown_retries_count_in_costs():
    value = estimate_batch({'status': 'completed_with_failures', 'paid_requests': 3, 'cases': [
        {'case_id': 'a', 'method': 'single', 'repeat': 1, 'paid_requests': 2,
         'error': {'code': 'network'}, 'scorable': False, 'model_requests': [request()]},
        {'case_id': 'b', 'method': 'single', 'repeat': 1, 'paid_requests': 1, 'model_requests': [request()]},
    ]}, load_prices(DEFAULT_PRICES))
    group = value['groups']['single']
    assert group['physical_attempts'] == 3 and group['estimated_responses'] == 2
    assert group['unknown_attempts'] == 1 and not group['complete_cost_coverage']
    assert group['known_usage_minimum_usd'] == '0.34650'
    assert value['samples'][0]['failed']


def test_running_request_is_not_dropped_or_priced_as_zero():
    value = estimate_batch({'status': 'running', 'paid_requests': 1, 'cases': [],
                            'current_sample': {'method': 'active', 'paid_requests': 1, 'model_requests': []}},
                           load_prices(DEFAULT_PRICES))
    assert value['samples'][0]['in_flight']
    assert value['groups']['active']['unknown_attempts'] == 1


@pytest.mark.parametrize('changes', [{'paid_requests': 0}, {'model_requests': [request(), request()]},
                                     {'model_requests': [request(index=0)]}])
def test_conflicting_attempt_accounting_is_rejected(changes):
    row = {'method': 'active', 'paid_requests': 2, 'model_requests': [request()], **changes}
    with pytest.raises(ValueError):
        estimate_batch({'paid_requests': 2, 'cases': [row]}, load_prices(DEFAULT_PRICES))


def test_numeric_or_nonfinite_price_rejected(tmp_path):
    path = tmp_path / 'prices.json'
    for bad in ['NaN', 0.3]:
        value = json.loads(DEFAULT_PRICES.read_text(encoding='utf-8'))
        value['rates']['deepseek-flash']['peak']['output'] = bad
        path.write_text(json.dumps(value), encoding='utf-8')
        with pytest.raises(ValueError):
            load_prices(path)
