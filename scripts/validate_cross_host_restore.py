"""Transfer a synthetic SQLite backup between separately identified execution hosts.

prepare --output NEW_DIR publishes NEW_DIR/bundle after a real isolated API run.
verify --bundle BUNDLE --output NEW_DIR restores and checks that bundle elsewhere.
Host identifiers describe execution hosts, not independently attested physical machines.
No working ledger, .env, provider, external network, or online-backup claim is involved.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path, PurePosixPath
import platform
import re
import sys
import time
import traceback

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.backup import backup  # noqa: E402
from scripts.backup_manifest import no_links, read_metadata, verify as verify_backup  # noqa: E402
from scripts.restore_local import restore  # noqa: E402
from scripts.validate_recovery_load import IsolatedServer, api, require, seed, views  # noqa: E402
from scripts.validate_restored_run import rows  # noqa: E402

VERSION = 'cross-host-synthetic-restore-v1'
ORACLE = {
    'record_count': 2, 'difference': '10.00', 'period': '2026-09', 'currency': 'CNY',
    'records': [
        {'source_id': 'load-debit', 'kind': 'journal', 'business_date': '2026-09-01',
         'period': '2026-09', 'currency': 'CNY', 'voucher_id': 'LOAD-1',
         'account': '1405', 'debit': '100.00', 'credit': '0.00'},
        {'source_id': 'load-credit', 'kind': 'journal', 'business_date': '2026-09-01',
         'period': '2026-09', 'currency': 'CNY', 'voucher_id': 'LOAD-1',
         'account': '2202', 'debit': '0.00', 'credit': '90.00'},
    ],
}
HELPERS = ('scripts/validate_cross_host_restore.py', 'scripts/validate_recovery_load.py',
           'scripts/validate_restored_run.py', 'scripts/validate_clean_checkout.py',
           'scripts/backup.py', 'scripts/backup_manifest.py', 'scripts/restore_local.py',
           'pyproject.toml', 'uv.lock')


def canonical(value):
    def encode(item):
        if isinstance(item, bytes):
            return {'base64_bytes': base64.b64encode(item).decode('ascii')}
        raise TypeError(f'Unsupported snapshot value: {type(item).__name__}')
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'), default=encode).encode()


def digest(value):
    return hashlib.sha256(value).hexdigest()


def write_json(path, value):
    with path.open('x', encoding='utf-8', newline='\n') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write('\n')


def read_json(path):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            require(key not in result, 'Duplicate JSON key')
            result[key] = value
        return result
    return json.loads(path.read_text(encoding='utf-8'), object_pairs_hook=unique)


def source_hashes():
    paths = [ROOT / name for name in HELPERS] + sorted((ROOT / 'backend/app').rglob('*.py'))
    return {path.relative_to(ROOT).as_posix(): digest(path.read_bytes()) for path in paths}


def host_metadata():
    node = platform.node().strip().casefold()
    require(bool(node), 'Execution host must have an identifiable hostname')
    marker, method = node, 'hostname-fallback'
    if sys.platform == 'win32':
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r'SOFTWARE\Microsoft\Cryptography') as key:
            marker = str(winreg.QueryValueEx(key, 'MachineGuid')[0])
        method = 'windows-machine-guid'
    else:
        machine_id = Path('/etc/machine-id')
        if machine_id.is_file():
            marker = machine_id.read_text(encoding='utf-8').strip()
            require(bool(marker), 'Empty execution host machine identifier')
            method = 'machine-id'
    return {'node_sha256': digest(node.encode()), 'host_id_sha256': digest(marker.encode()),
            'identity_method': method, 'system': platform.system(), 'release': platform.release(),
            'machine': platform.machine(), 'python': platform.python_version()}


def require_other_host(source, target):
    for host in (source, target):
        require(isinstance(host, dict), 'Missing execution host metadata')
        require(all(isinstance(host.get(key), str) and re.fullmatch(r'[0-9a-f]{64}', host[key])
                    for key in ('node_sha256', 'host_id_sha256')), 'Invalid execution host identity')
        require(all(isinstance(host.get(key), str) and host[key]
                    for key in ('identity_method', 'system', 'release', 'machine', 'python')),
                'Incomplete execution host metadata')
    require(source['node_sha256'] != target['node_sha256']
            and source['host_id_sha256'] != target['host_id_sha256'],
            'Same execution host cannot establish cross-host restoration')


def safe_relative(name):
    require(isinstance(name, str) and name and '\\' not in name and ':' not in name,
            'Unsafe bundle path')
    path = PurePosixPath(name)
    require(not path.is_absolute() and all(part not in ('', '.', '..') for part in name.split('/')),
            'Unsafe bundle path')
    return path


def inventory(bundle):
    bundle = no_links(bundle)
    require(bundle.is_dir(), 'Bundle directory is missing')
    result = {}
    for path in sorted(bundle.rglob('*')):
        no_links(path)
        if path.is_dir():
            continue
        require(path.is_file(), 'Unsupported bundle entry')
        name = path.relative_to(bundle).as_posix()
        safe_relative(name)
        if name != 'bundle.json':
            result[name] = {'sha256': digest(path.read_bytes()), 'bytes': path.stat().st_size}
    return result


def check_oracle(observed, run, seeded):
    require(isinstance(observed, dict) and set(observed) == {'records', 'documents', 'snapshot', 'overview'},
            'Incomplete expected API views')
    records = observed['records']
    require(isinstance(records, list) and len(records) == ORACLE['record_count'], 'Independent record count failed')
    keys = set(ORACLE['records'][0])
    actual = sorted(({key: row.get(key) for key in keys} for row in records), key=lambda row: row['source_id'])
    require(actual == sorted(ORACLE['records'], key=lambda row: row['source_id']), 'Independent ledger oracle failed')
    require(observed['snapshot'].get('id') == seeded['snapshot_id']
            and observed['snapshot'].get('book_id') == seeded['book_id'], 'Snapshot scope differs')
    overview = observed['overview']
    require(overview.get('book', {}).get('id') == seeded['book_id']
            and overview.get('latest_snapshot_id') == seeded['snapshot_id']
            and overview.get('counts', {}).get('records') == 2, 'Book overview differs from independent oracle')
    documents = observed['documents']
    require(isinstance(documents, list) and len(documents) == 1, 'Expected one synthetic source document')
    require(documents[0].get('book_id') == seeded['book_id']
            and documents[0].get('id') and re.fullmatch(r'[0-9a-f]{64}', documents[0].get('sha256', '')),
            'Missing snapshot-bound source document')
    require(isinstance(run, dict) and run.get('id') and run.get('case_id') == seeded['case_id']
            and run.get('mode') == 'rules' and run.get('status') == 'complete', 'Expected completed rule investigation')
    result = run.get('result') or {}
    require(result.get('status') == 'supported' and bool(result.get('claims')), 'Missing supported rule result')
    require(result['claims'][0].get('calculation', {}).get('difference') == ORACLE['difference'],
            'Independent 100.00 minus 90.00 oracle failed')


def validate_expected(expected):
    require(isinstance(expected, dict) and expected.get('schema_version') == VERSION
            and expected.get('oracle') == ORACLE, 'Independent expected oracle was changed or omitted')
    seeded = expected.get('seeded')
    require(isinstance(seeded, dict) and set(seeded) == {'book_id', 'snapshot_id', 'case_id', 'check_id'}
            and all(isinstance(value, str) and re.fullmatch(r'[0-9a-f-]{36}', value)
                    for value in seeded.values()), 'Invalid synthetic scope identifiers')
    require(all(isinstance(expected.get(key), str) and re.fullmatch(r'[0-9a-f]{64}', expected[key])
                for key in ('business_rows_sha256', 'checkpoint_rows_sha256')), 'Missing database row evidence')
    check_oracle(expected.get('views'), expected.get('run'), seeded)


def validate_bundle(bundle, target_host):
    bundle = no_links(bundle)
    metadata = read_json(no_links(bundle / 'bundle.json'))
    require(isinstance(metadata, dict) and metadata.get('schema_version') == VERSION
            and metadata.get('synthetic') is True, 'Unsupported or nonsynthetic bundle')
    require_other_host(metadata.get('source_host'), target_host)
    require(metadata.get('source_sha256') == source_hashes(), 'Source and target code hashes differ')
    expected_inventory = metadata.get('files')
    require(isinstance(expected_inventory, dict) and bool(expected_inventory), 'Missing bundle inventory')
    for name in expected_inventory:
        safe_relative(name)
    require(expected_inventory == inventory(bundle), 'Bundle files, sizes, or hashes differ')
    require({'expected.json', 'backup/backup.json', 'backup/ledgerlens.sqlite', 'backup/checkpoints.sqlite'}
            <= set(expected_inventory), 'Missing backup or checkpoint evidence')
    require(all(name == 'expected.json' or name.startswith('backup/') for name in expected_inventory),
            'Unexpected file in synthetic bundle')
    expected = read_json(bundle / 'expected.json')
    validate_expected(expected)
    backup_metadata = read_metadata(bundle / 'backup')
    require(backup_metadata.get('schema_version') == 'sqlite-local-backup-v2'
            and backup_metadata.get('checkpoints_included') is True, 'Checkpoint backup is required')
    verify_backup(bundle / 'backup', backup_metadata)
    return metadata, expected


def database_digests(data):
    business, checkpoints = rows(data), rows(data, 'checkpoints.sqlite')
    require(bool(checkpoints) and any(checkpoints.values()), 'Missing nonempty investigation checkpoints')
    return {'business_rows_sha256': digest(canonical(business)),
            'checkpoint_rows_sha256': digest(canonical(checkpoints))}


async def wait_investigation(client, seeded):
    run = await api(client, 'POST', f"/api/v1/cases/{seeded['case_id']}/investigate", json={'mode': 'rules'})
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        run = await api(client, 'GET', '/api/v1/runs/' + run['id'])
        if run['status'] not in ('queued', 'running'):
            return run
        await asyncio.sleep(.1)
    raise TimeoutError('Rule investigation did not finish within 30 seconds')


async def check_downloads(client, observed):
    documents = observed['documents']
    require(len(documents) == 1, 'Missing independently expected source document')
    for document in documents:
        response = await client.get('/api/v1/documents/' + document['id'] + '/file')
        require(response.is_success and digest(response.content) == document['sha256'], 'Source download hash mismatch')
        require(json.loads(response.content) == ORACLE['records'], 'Downloaded source is not the independent synthetic input')


def client_for(server):
    return httpx.AsyncClient(base_url=server.base_url, timeout=25, trust_env=False,
                             limits=httpx.Limits(keepalive_expiry=2))


async def prepare(output, report):
    data = output / 'source'
    server = IsolatedServer(output, data)
    try:
        await server.start()
        async with client_for(server) as client:
            seeded = await seed(client)
            run = await wait_investigation(client, seeded)
            observed = await views(client, seeded)
            check_oracle(observed, run, seeded)
            await check_downloads(client, observed)
    finally:
        await server.stop()
    fingerprints = database_digests(data)
    bundle = output / 'bundle'
    bundle.mkdir()
    backup(data, bundle / 'backup')
    require(database_digests(bundle / 'backup') == fingerprints, 'Backup logical rows changed')
    expected = {'schema_version': VERSION, 'oracle': ORACLE, 'seeded': seeded,
                'views': observed, 'run': run, **fingerprints}
    validate_expected(expected)
    write_json(bundle / 'expected.json', expected)
    metadata = {'schema_version': VERSION, 'synthetic': True, 'created_at': report['created_at'],
                'source_host': report['execution_host'], 'source_sha256': report['source_sha256'],
                'files': inventory(bundle),
                'limits': ['Hashes check consistency, not authorship or resistance to a rewritten manifest.',
                           'Host fingerprints identify execution environments, not physical-machine independence.',
                           'Two synthetic rows, SQLite, stopped writer, deterministic rules; no enterprise or online-backup claim.']}
    write_json(bundle / 'bundle.json', metadata)
    require(database_digests(data) == fingerprints, 'Synthetic source changed while publishing backup')
    report.update(bundle=str(bundle), bundle_manifest_sha256=digest((bundle / 'bundle.json').read_bytes()),
                  checks={'independent_ledger_oracle': True, 'real_rule_investigation': True,
                          'downloaded_source_oracle': True, 'nonempty_checkpoints': True,
                          'stopped_writer_backup_equal': True, 'external_network_and_models_blocked': True},
                  status='prepared', independent_host_verified=False)


async def verify(bundle, output, report):
    bundle = no_links(bundle)
    metadata, expected = validate_bundle(bundle, report['execution_host'])
    before = inventory(bundle)
    manifest_before = digest((bundle / 'bundle.json').read_bytes())
    report.update(source_host=metadata['source_host'], bundle_manifest_sha256=manifest_before)
    backup_data = bundle / 'backup'
    fingerprints = {key: expected[key] for key in ('business_rows_sha256', 'checkpoint_rows_sha256')}
    require(database_digests(backup_data) == fingerprints, 'Backup rows differ from published evidence')
    restored = restore(backup_data, output / 'restored')
    require(database_digests(restored) == fingerprints, 'Restored logical rows differ')
    server = IsolatedServer(output, restored)
    try:
        await server.start()
        async with client_for(server) as client:
            seeded = expected['seeded']
            observed = await views(client, seeded)
            run = await api(client, 'GET', '/api/v1/runs/' + expected['run']['id'])
            check_oracle(observed, run, seeded)
            require(observed == expected['views'] and run == expected['run'], 'Restored HTTP views or investigation changed')
            checked = await api(client, 'POST', '/api/v1/check-runs',
                                json={'book_id': seeded['book_id'], 'snapshot_id': seeded['snapshot_id']})
            require(checked['id'] == seeded['check_id'], 'Existing check was not preserved')
            await check_downloads(client, observed)
    finally:
        await server.stop()
    require(database_digests(restored) == fingerprints, 'Restored startup or reads changed completed business/checkpoint rows')
    require(inventory(bundle) == before and digest((bundle / 'bundle.json').read_bytes()) == manifest_before,
            'Transferred bundle changed during verification')
    report.update(status='passed', independent_host_verified=True,
                  checks={'distinct_execution_host': True, 'same_source_code': True,
                          'independent_ledger_oracle': True, 'business_rows_equal': True,
                          'checkpoint_rows_equal': True, 'http_views_equal': True,
                          'completed_investigation_equal': True, 'check_identity_equal': True,
                          'downloaded_source_oracle': True, 'bundle_unchanged': True,
                          'external_network_and_models_blocked': True})


async def execute(phase, output, bundle=None):
    output = no_links(output)
    if bundle is not None:
        bundle = no_links(bundle)
        require(output != bundle and bundle not in output.parents and output not in bundle.parents,
                'Output and transferred bundle must be disjoint')
    output.mkdir(parents=True, exist_ok=False)
    report = {'schema_version': VERSION, 'phase': phase, 'status': 'running',
              'created_at': datetime.now(timezone.utc).isoformat(), 'model_requests': 0,
              'physical_machine_independence_claimed': False, 'independent_host_verified': False}
    started = time.monotonic()
    try:
        report.update(execution_host=host_metadata(), source_sha256=source_hashes())
        if phase == 'prepare':
            await prepare(output, report)
        else:
            require(bundle is not None, 'Verification requires a transferred bundle')
            await verify(bundle, output, report)
    except Exception as exc:
        report.update(status='failed', error={'type': type(exc).__name__, 'message': str(exc)},
                      traceback=traceback.format_exc())
    finally:
        report['elapsed_seconds'] = round(time.monotonic() - started, 2)
        write_json(output / 'report.json', report)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest='phase', required=True)
    for phase in ('prepare', 'verify'):
        child = subparsers.add_parser(phase)
        child.add_argument('--output', type=Path, required=True)
        if phase == 'verify':
            child.add_argument('--bundle', type=Path, required=True)
    args = parser.parse_args(argv)
    report = asyncio.run(execute(args.phase, args.output, getattr(args, 'bundle', None)))
    print(json.dumps({key: report.get(key) for key in
                      ('phase', 'status', 'independent_host_verified', 'elapsed_seconds', 'error')}))
    return 0 if report['status'] in ('prepared', 'passed') else 1


if __name__ == '__main__':
    raise SystemExit(main())
