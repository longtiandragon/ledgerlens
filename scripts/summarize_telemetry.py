"""Read local spans without contacting any service; retain corrupt-file failures."""
import argparse
import hashlib
import json
from pathlib import Path
import re


def summarize(directory):
    runs, failures = {}, []
    for path in sorted(Path(directory).glob('*.json')):
        try:
            raw = path.read_bytes()
            if len(raw) > 64_000:
                raise ValueError('oversized_span')
            span = json.loads(raw)
            attrs = span['attributes']
            identity = attrs['ledgerlens.run_sha256']
            if span['schema_version'] != 'local-run-span-v1' or not re.fullmatch('[a-f0-9]{64}', identity):
                raise ValueError('invalid_span_identity')
            start, end, duration = (span[key] for key in ('start_time_unix_ns', 'end_time_unix_ns', 'duration_ns'))
            if any(type(value) is not int or value < 0 for value in (start, end, duration)) or end - start != duration:
                raise ValueError('invalid_duration')
            rows = runs.setdefault(identity, [])
            rows.append((start, span['span_id'], duration, attrs, hashlib.sha256(raw).hexdigest()))
        except (ValueError, KeyError, TypeError, OSError):
            # Do not echo malformed content or arbitrary paths from file contents.
            failures.append({'file': path.name, 'status': 'invalid_or_unreadable'})
    summaries = []
    for identity, leases in sorted(runs.items()):
        latest = max(leases, key=lambda value: (value[0], value[1]))
        # Usage is cumulative; summing cost from all leases double-counts resumes.
        attrs = {key.removeprefix('ledgerlens.'): value for key, value in latest[3].items()}
        summaries.append({'run_sha256': identity, 'leases': len(leases),
                          'worker_duration_ns_sum': sum(item[2] for item in leases),
                          'latest_lease_duration_ns': latest[2],
                          'latest_cumulative': {key: attrs[key] for key in (
                              'outcome', 'usage_status', 'model_calls', 'tokens', 'reserved_tokens', 'tools',
                              'cost_status', 'priced_responses', 'unknown_cost_attempts',
                              'known_usage_peak_estimate_cny', 'cost_currency', 'cost_basis', 'price_sha256')
                              if key in attrs}, 'source_sha256': [item[4] for item in leases]})
    return {'schema_version': 'local-telemetry-summary-v1', 'runs': summaries, 'failures': failures,
            'status': 'invalid_files' if failures else 'available' if summaries else 'no_data',
            'scope': 'local_worker_leases_cumulative_usage_not_provider_bills', 'model_requests': 0}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', type=Path, default=Path('runtime/telemetry'))
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = summarize(args.directory)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x', encoding='utf-8') as handle:
        json.dump(result, handle, ensure_ascii=True, indent=2, allow_nan=False)
        handle.write('\n')
    print(json.dumps({'status': result['status'], 'runs': len(result['runs']), 'failures': len(result['failures'])}))
    return int(result['status'] != 'available')


if __name__ == '__main__':
    raise SystemExit(main())
