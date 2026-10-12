"""Run a paid benchmark command under a CNY cap priced at the official off-peak list price.

Usage: python scripts/guard_paid_run.py <repo> <cap_cny> <report.json> <command...>
The command must write a report that benchmarks.costs.estimate_batch understands.
Above the cap the whole process tree is killed; the guard log is written next to
the report as <report>-guard.json. Estimates are list prices, not a bill."""
import json
import subprocess
import sys
import time
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

ROOT, CAP, REPORT = Path(sys.argv[1]), Decimal(sys.argv[2]), Path(sys.argv[3])
COMMAND = sys.argv[4:]
sys.path.insert(0, str(ROOT))
from benchmarks.costs import estimate_batch, load_prices  # noqa: E402

RATE, UNKNOWN_USD = Decimal('7.3'), Decimal('0.003')
prices = load_prices(ROOT / 'benchmarks/deepseek-prices-2026-10-08.json')
log = {'started_at': datetime.now(UTC).isoformat(), 'cap_cny': str(CAP), 'tariff_used': 'off_peak_minimum',
       'command': COMMAND, 'checks': [], 'stopped_by_guard': False}
process = subprocess.Popen(COMMAND, cwd=ROOT)


def estimate():
    try:
        groups = estimate_batch(json.loads(REPORT.read_text(encoding='utf-8')), prices)['groups'].values()
    except (OSError, ValueError, KeyError):
        return None
    usd = sum(Decimal(g['known_usage_minimum_usd']) for g in groups)
    return (usd + sum(g['unknown_attempts'] for g in groups) * UNKNOWN_USD) * RATE


while process.poll() is None:
    time.sleep(10)
    value = estimate()
    if value is not None:
        log['checks'].append({'at': datetime.now(UTC).isoformat(), 'cny': str(round(value, 3))})
        if value > CAP:
            subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'], capture_output=True)
            log['stopped_by_guard'] = True
            break
process.wait()
final = estimate()
log.update(exit_code=process.returncode, completed_at=datetime.now(UTC).isoformat(),
           final_offpeak_cny=str(round(final, 3)) if final is not None else None)
REPORT.with_name(REPORT.stem + '-guard.json').write_text(json.dumps(log, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps({k: log[k] for k in ('stopped_by_guard', 'exit_code', 'final_offpeak_cny')}))
