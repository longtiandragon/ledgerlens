"""Establish test isolation BEFORE collection can import application settings.

Shared API fixtures may reset only this session's exact disposable database.
They must never inherit the developer's working ledger or provider key from .env.
"""
import ipaddress
import os
from pathlib import Path
import sys
import tempfile


TEST_ROOT = Path(tempfile.mkdtemp(prefix='ledgerlens-pytest-session-'))
TEST_DATABASE = (TEST_ROOT / 'app.sqlite').resolve()
os.environ.update({
    'DATA_DIR': str(TEST_ROOT),
    'DATABASE_URL': 'sqlite:///' + TEST_DATABASE.as_posix(),
    'DEEPSEEK_API_KEY': '',
    'TASK_MODE': 'local',
    'AUTH_MODE': 'single_user',
    'TELEMETRY_ENABLED': 'false',
})
for name in ('HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'http_proxy', 'https_proxy', 'all_proxy'):
    os.environ.pop(name, None)


def reject_external_network(event, args):
    if event != 'socket.connect' or not isinstance(args[1], tuple):
        return
    host = args[1][0]
    if host == 'localhost':
        return
    try:
        if ipaddress.ip_address(host).is_loopback:
            return
    except ValueError:
        pass
    raise RuntimeError('External network is forbidden in the pytest process; use an explicit isolated validator')


sys.addaudithook(reject_external_network)


def reset_test_database(metadata, engine):
    """Fail before connecting if a fixture selects any other database."""
    url = engine.url
    if (url.get_backend_name() != 'sqlite' or url.query or not url.database
            or Path(url.database).resolve() != TEST_DATABASE):
        raise RuntimeError('Refusing to reset a database not owned by this pytest session')
    metadata.drop_all(engine)
