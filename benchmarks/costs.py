"""Offline, versioned list-price estimates; never a provider bill or API call."""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import defaultdict
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PRICES = Path(__file__).with_name('deepseek-prices-2026-10-08.json')


def integer(value: object) -> bool:
    return type(value) is int and value >= 0


def load_prices(path: Path) -> dict:
    data = path.read_bytes()
    prices = json.loads(data)
    if prices.get('schema_version') != 'deepseek-price-snapshot-v1' or prices.get('currency') != 'USD':
        raise ValueError('Unsupported price snapshot')
    if prices.get('tokens_per_unit') != 1_000_000 or not prices.get('source_url') or not prices.get('verified_on'):
        raise ValueError('Incomplete price metadata')
    rates = prices.get('rates')
    if not isinstance(rates, dict) or not rates:
        raise ValueError('No model rates')
    for tariffs in rates.values():
        if set(tariffs) != {'off_peak', 'peak'}:
            raise ValueError('Both published tariffs are required')
        for row in tariffs.values():
            if set(row) != {'input_cache_hit', 'input_cache_miss', 'output'}:
                raise ValueError('Incomplete tariff')
            for value in row.values():
                if not isinstance(value, str):
                    raise ValueError('Prices must be decimal strings')
                amount = Decimal(value)
                if not amount.is_finite() or amount < 0:
                    raise ValueError('Invalid price')
    return {**prices, 'sha256': hashlib.sha256(data).hexdigest()}


def estimate_request(request: dict, prices: dict) -> dict:
    usage = request.get('request_usage')
    model = request.get('provider_model')
    if not isinstance(usage, dict):
        return {'status': 'unknown', 'reason': 'usage_not_reported'}
    fields = ('prompt_tokens', 'completion_tokens', 'prompt_cache_hit_tokens', 'prompt_cache_miss_tokens')
    if any(not integer(usage.get(key)) for key in fields):
        return {'status': 'unknown', 'reason': 'missing_or_invalid_usage'}
    prompt, output, hit, miss = (usage[key] for key in fields)
    if hit + miss != prompt or ('total_tokens' in usage and usage['total_tokens'] is not None
                               and (not integer(usage['total_tokens']) or usage['total_tokens'] != prompt + output)):
        return {'status': 'unknown', 'reason': 'inconsistent_usage'}
    if model not in prices['rates']:
        return {'status': 'unknown', 'reason': 'unpriced_provider_model'}
    amounts = []
    for tariff in prices['rates'][model].values():
        amounts.append((Decimal(hit) * Decimal(tariff['input_cache_hit'])
                        + Decimal(miss) * Decimal(tariff['input_cache_miss'])
                        + Decimal(output) * Decimal(tariff['output'])) / Decimal(prices['tokens_per_unit']))
    return {'status': 'estimated', 'provider_model': model,
            'minimum_usd': format(min(amounts), 'f'), 'maximum_usd': format(max(amounts), 'f')}


def estimate_batch(report: dict, prices: dict) -> dict:
    """Include failed/unscorable rows. Pending transport remains unknown."""
    rows = report.get('cases')
    if not isinstance(rows, list):
        raise ValueError('Expected benchmark cases')
    output_rows = []
    groups = defaultdict(lambda: {'samples': 0, 'physical_attempts': 0, 'estimated_responses': 0,
                                  'unknown_attempts': 0, 'minimum': Decimal(0), 'maximum': Decimal(0)})
    pending = report.get('current_sample')
    scopes = [(row, False) for row in rows] + ([(pending, True)] if isinstance(pending, dict) else [])
    for row, in_flight in scopes:
        requests = row.get('model_requests', [])
        paid = row.get('paid_requests', 0)
        if not isinstance(requests, list) or not integer(paid) or len(requests) > paid:
            raise ValueError('Physical attempt and response accounting conflict')
        indices = [request.get('request_index') for request in requests]
        if any(not integer(index) or index < 1 for index in indices) or len(set(indices)) != len(indices):
            raise ValueError('Invalid or duplicate response request index')
        estimates = [estimate_request(request, prices) for request in requests]
        known = [value for value in estimates if value['status'] == 'estimated']
        minimum = sum((Decimal(value['minimum_usd']) for value in known), Decimal(0))
        maximum = sum((Decimal(value['maximum_usd']) for value in known), Decimal(0))
        unknown = paid - len(known)
        group = groups[row['method']]
        for key, value in {'samples': int(not in_flight), 'physical_attempts': paid,
                           'estimated_responses': len(known), 'unknown_attempts': unknown}.items():
            group[key] += value
        group['minimum'] += minimum
        group['maximum'] += maximum
        output_rows.append({'case_id': row.get('case_id'), 'method': row['method'], 'repeat': row.get('repeat'),
                            'in_flight': in_flight, 'failed': bool(row.get('error')),
                            'physical_attempts': paid, 'estimated_responses': len(known),
                            'unknown_attempts': unknown, 'known_usage_minimum_usd': format(minimum, 'f'),
                            'known_usage_maximum_usd': format(maximum, 'f'),
                            'complete_cost_coverage': unknown == 0 and not in_flight,
                            'unestimated_response_reasons': [value['reason'] for value in estimates
                                                            if value['status'] == 'unknown']})
    accounted = sum(group['physical_attempts'] for group in groups.values())
    if report.get('paid_requests') != accounted:
        raise ValueError('Global physical attempt accounting conflict')
    public_groups = {method: {**{key: value for key, value in group.items() if key not in {'minimum', 'maximum'}},
                             'known_usage_minimum_usd': format(group['minimum'], 'f'),
                             'known_usage_maximum_usd': format(group['maximum'], 'f'),
                             'complete_cost_coverage': group['unknown_attempts'] == 0 and not pending}
                     for method, group in groups.items()}
    return {'schema_version': 'list-price-estimate-v1', 'created_at': datetime.now(UTC).isoformat(),
            'source_report_status': report.get('status'), 'currency': prices['currency'],
            'price_source': {key: prices[key] for key in ('source_url', 'verified_on', 'sha256', 'tariff_policy')},
            'groups': public_groups, 'samples': output_rows,
            'limitations': ['Offline list-price interval for recorded provider usage; not a bill or account balance.',
                            'No RMB conversion, dispatch-time tariff classification or holiday inference.',
                            'Unknown attempts are not priced and not counted as zero cost.',
                            'The maximum applies only to known usage; it is not an upper bound on unknown attempts.',
                            'Historical prices and third-party endpoints require a different verified price snapshot.']}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--prices', type=Path, default=DEFAULT_PRICES)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error('Refusing to overwrite an existing cost estimate')
    raw = args.report.read_bytes()
    report = json.loads(raw)
    result = estimate_batch(report, load_prices(args.prices))
    result['source_report_sha256'] = hashlib.sha256(raw).hexdigest()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x', encoding='utf-8') as handle:
        json.dump(result, handle, ensure_ascii=False, indent=2, allow_nan=False)
        handle.write('\n')
    print(json.dumps({'output': str(args.output), 'samples': len(result['samples']), 'groups': result['groups']},
                     ensure_ascii=False))


if __name__ == '__main__':
    main()
