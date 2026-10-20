"""Collection order must never select the working ledger or a paid provider."""
from pathlib import Path
import tempfile
import sqlite3
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url

from app.config import settings
from conftest import TEST_DATABASE, reject_external_network, reset_test_database


def test_application_settings_are_disposable_and_keyless():
    assert settings.data_dir.resolve().is_relative_to(Path(tempfile.gettempdir()).resolve())
    assert settings.data_dir.resolve() != (Path.cwd() / 'runtime').resolve()
    assert settings.deepseek_api_key == ''
    assert settings.task_mode == 'local'


@pytest.mark.parametrize('host', ['api.deepseek.com', '8.8.8.8', '2001:4860:4860::8888'])
def test_external_network_guard_rejects_without_opening_a_socket(host):
    with pytest.raises(RuntimeError, match='External network is forbidden'):
        reject_external_network('socket.connect', (None, (host, 443)))


@pytest.mark.parametrize('host', ['127.0.0.1', '::1', 'localhost'])
def test_loopback_test_services_remain_allowed(host):
    reject_external_network('socket.connect', (None, (host, 5432)))


def test_reset_rejects_foreign_sqlite_and_preserves_its_rows(tmp_path):
    foreign = tmp_path / 'working-ledger.sqlite'
    with sqlite3.connect(foreign) as connection:
        connection.execute('CREATE TABLE books (id TEXT)')
        connection.execute("INSERT INTO books VALUES ('must-survive')")
    before = foreign.read_bytes()
    engine = create_engine('sqlite:///' + foreign.as_posix())
    metadata = Mock()
    try:
        with pytest.raises(RuntimeError, match='not owned'):
            reset_test_database(metadata, engine)
        metadata.drop_all.assert_not_called()
        assert foreign.read_bytes() == before
        with sqlite3.connect(foreign) as connection:
            assert connection.execute('SELECT id FROM books').fetchall() == [('must-survive',)]
    finally:
        engine.dispose()


@pytest.mark.parametrize('url', [
    'postgresql://localhost/working_ledger', 'sqlite://', 'sqlite:///:memory:',
    'sqlite:///' + TEST_DATABASE.as_posix() + '?uri=true',
    'sqlite:///' + TEST_DATABASE.with_name('another.sqlite').as_posix(),
])
def test_reset_rejects_unowned_targets_before_database_dispatch(url):
    metadata = Mock()
    with pytest.raises(RuntimeError, match='not owned'):
        reset_test_database(metadata, SimpleNamespace(url=make_url(url)))
    metadata.drop_all.assert_not_called()


def test_reset_accepts_only_the_session_database():
    metadata = Mock()
    engine = SimpleNamespace(url=make_url('sqlite:///' + TEST_DATABASE.as_posix()))
    reset_test_database(metadata, engine)
    metadata.drop_all.assert_called_once_with(engine)
