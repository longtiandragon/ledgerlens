from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from conftest import reset_test_database

from app import auth
from app.config import settings
from app.db import Base, Session, User, UserSession, engine
from app.main import app

PASSWORD = 'correct horse battery'


@pytest.fixture
def client():
    reset_test_database(Base.metadata, engine)
    with TestClient(app) as client:
        yield client


@pytest.fixture
def multi(monkeypatch):
    monkeypatch.setattr(settings, 'auth_mode', 'multi_user')


def make_users(**people):
    """people: username -> roles. Users are created directly, as the local CLI would."""
    with Session() as s:
        ids = {name: auth.create_user(s, name, name.title(), roles, PASSWORD).id for name, roles in people.items()}
        s.commit()
    return ids


def login(client, username, password=PASSWORD):
    response = client.post('/api/v1/auth/login', json={'username': username, 'password': password})
    assert response.status_code == 200, response.text
    return {'Authorization': 'Bearer ' + response.json()['token']}


def demo_case(client, headers=None):
    book = client.post('/api/v1/books', json={'name': '审签验收', 'framework': 'CAS', 'period': '2026-09'},
                       headers=headers).json()['id']
    snap = client.post(f'/api/v1/books/{book}/demo', headers=headers).json()['snapshot_id']
    cases = client.post('/api/v1/check-runs', json={'book_id': book, 'snapshot_id': snap}, headers=headers).json()['cases']
    return book, snap, cases[0]['id']


def test_single_user_mode_keeps_the_original_workflow(client):
    assert client.get('/api/v1/auth/me').json()['mode'] == 'single_user'
    book, snap, case = demo_case(client)
    assert client.post(f'/api/v1/cases/{case}/investigate', json={'mode': 'rules'}).status_code == 200
    review = client.post('/api/v1/reviews', json={'case_id': case, 'expected_snapshot_id': snap,
                                                   'decision': 'accepted', 'comment': '本地复核'})
    assert review.status_code == 200 and review.json()['user_id'] is None
    approval = client.post(f'/api/v1/cases/{case}/approvals', json={
        'review_id': review.json()['id'], 'expected_snapshot_id': snap, 'decision': 'approved'})
    assert approval.status_code == 409
    assert client.post('/api/v1/auth/login', json={'username': 'nobody', 'password': PASSWORD}).status_code == 409


def test_multi_user_mode_requires_a_session_outside_open_entry_points(client, multi):
    assert client.get('/api/v1/books').status_code == 401
    assert client.post('/api/v1/books', json={'name': 'x', 'framework': 'CAS', 'period': '2026-09'}).status_code == 401
    assert client.get('/api/v1/books', headers={'Authorization': 'Bearer forged'}).status_code == 401
    assert client.get('/api/v1/health').status_code == 200
    assert client.get('/api/v1/settings').json()['auth_mode'] == 'multi_user'
    assert client.get('/api/v1/auth/me').json() == {
        'mode': 'multi_user', 'user': None, 'roles': list(auth.ROLES), 'role_labels': auth.ROLE_LABELS}


def test_login_logout_expiry_and_deactivation(client, multi):
    make_users(alice=['preparer'])
    wrong = client.post('/api/v1/auth/login', json={'username': 'alice', 'password': 'wrong password!'})
    unknown = client.post('/api/v1/auth/login', json={'username': 'mallory', 'password': PASSWORD})
    assert wrong.status_code == unknown.status_code == 401 and wrong.json() == unknown.json()
    headers = login(client, 'alice')
    assert client.get('/api/v1/auth/me', headers=headers).json()['user']['username'] == 'alice'
    assert client.get('/api/v1/books', headers=headers).status_code == 200
    assert client.post('/api/v1/auth/logout', headers=headers).status_code == 200
    assert client.get('/api/v1/books', headers=headers).status_code == 401
    headers = login(client, 'alice')
    with Session() as s:
        for row in s.scalars(select(UserSession)):
            row.expires_at = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
        s.commit()
    assert client.get('/api/v1/books', headers=headers).status_code == 401
    headers = login(client, 'alice')
    with Session() as s:
        s.scalar(select(User).where(User.username == 'alice')).active = False
        s.commit()
    assert client.get('/api/v1/books', headers=headers).status_code == 401
    assert client.post('/api/v1/auth/login', json={'username': 'alice', 'password': PASSWORD}).status_code == 401


def test_passwords_are_salted_scrypt_hashes_and_tokens_are_not_stored(client, multi):
    make_users(alice=['preparer'], bob=['reviewer'])
    headers = login(client, 'alice')
    with Session() as s:
        users = {u.username: u for u in s.scalars(select(User))}
        stored = [session.token_hash for session in s.scalars(select(UserSession))]
    assert users['alice'].password_salt != users['bob'].password_salt
    assert users['alice'].password_hash != users['bob'].password_hash
    assert PASSWORD not in users['alice'].password_hash and len(users['alice'].password_hash) == 64
    assert headers['Authorization'].split()[1] not in stored
    assert auth.verify_password(PASSWORD, users['alice'].password_salt, users['alice'].password_hash)
    assert not auth.verify_password(PASSWORD + '!', users['alice'].password_salt, users['alice'].password_hash)
    with pytest.raises(ValueError):
        auth.hash_password('short')


def test_roles_are_enforced_and_only_admins_create_users(client, multi):
    make_users(paul=['preparer'], rita=['reviewer'], ada=['admin'])
    paul, rita, ada = login(client, 'paul'), login(client, 'rita'), login(client, 'ada')
    book, snap, case = demo_case(client, paul)
    assert client.post(f'/api/v1/cases/{case}/investigate', json={'mode': 'rules'}, headers=rita).status_code == 403
    review = {'case_id': case, 'expected_snapshot_id': snap, 'decision': 'accepted', 'comment': ''}
    assert client.post('/api/v1/reviews', json=review, headers=paul).status_code == 403
    new_user = {'username': 'vera', 'display_name': 'Vera', 'roles': ['approver'], 'password': PASSWORD}
    assert client.post('/api/v1/auth/users', json=new_user, headers=paul).status_code == 403
    created = client.post('/api/v1/auth/users', json=new_user, headers=ada)
    assert created.status_code == 201 and created.json()['roles'] == ['approver']
    assert 'password' not in str(created.json()) and 'hash' not in str(created.json())
    assert client.post('/api/v1/auth/users', json=new_user, headers=ada).status_code == 422   # duplicate name
    weak = {**new_user, 'username': 'walt', 'password': 'short'}
    assert client.post('/api/v1/auth/users', json=weak, headers=ada).status_code == 422


def test_incompatible_duties_are_separated_through_the_signoff_chain(client, multi):
    ids = make_users(paul=['preparer', 'reviewer', 'approver'], rita=['reviewer', 'approver'], amy=['approver'])
    paul, rita, amy = login(client, 'paul'), login(client, 'rita'), login(client, 'amy')
    book, snap, case = demo_case(client, paul)
    run = client.post(f'/api/v1/cases/{case}/investigate', json={'mode': 'rules'}, headers=paul)
    assert run.status_code == 200 and run.json()['created_by'] == ids['paul']
    review = {'case_id': case, 'expected_snapshot_id': snap, 'decision': 'accepted', 'comment': '证据核对一致'}
    assert client.post('/api/v1/reviews', json=review, headers=paul).status_code == 403   # investigator
    reviewed = client.post('/api/v1/reviews', json=review, headers=rita)
    assert reviewed.status_code == 200 and reviewed.json()['user_id'] == ids['rita']
    approval = {'review_id': reviewed.json()['id'], 'expected_snapshot_id': snap, 'decision': 'approved',
                'comment': '同意签发'}
    assert client.post(f'/api/v1/cases/{case}/approvals', json=approval, headers=rita).status_code == 403  # reviewer
    assert client.post(f'/api/v1/cases/{case}/approvals', json=approval, headers=paul).status_code == 403  # investigator
    approved = client.post(f'/api/v1/cases/{case}/approvals', json=approval, headers=amy)
    assert approved.status_code == 200 and approved.json()['user_id'] == ids['amy']
    detail = client.get(f'/api/v1/cases/{case}', headers=amy).json()
    assert [a['decision'] for a in detail['approvals']] == ['approved']
    assert {detail['actors'][identity]['username'] for identity in ids.values()} == {'paul', 'rita', 'amy'}
    exported = client.get(f'/api/v1/exports/cases/{case}?format=json', headers=amy).json()
    assert exported['approvals'][0]['review_id'] == reviewed.json()['id']
    markdown = client.get(f'/api/v1/exports/cases/{case}?format=markdown', headers=amy).text
    assert '## 复核与审批' in markdown and '（Rita）' in markdown and '（Amy）' in markdown


def test_approval_needs_the_latest_unreturned_review_on_the_current_snapshot(client, multi):
    make_users(paul=['preparer'], rita=['reviewer'], amy=['approver'])
    paul, rita, amy = login(client, 'paul'), login(client, 'rita'), login(client, 'amy')
    book, snap, case = demo_case(client, paul)
    base = {'case_id': case, 'expected_snapshot_id': snap, 'comment': ''}
    returned = client.post('/api/v1/reviews', json={**base, 'decision': 'returned'}, headers=rita).json()
    ask = {'expected_snapshot_id': snap, 'decision': 'approved'}
    assert client.post(f'/api/v1/cases/{case}/approvals', json={**ask, 'review_id': returned['id']},
                       headers=amy).status_code == 409
    accepted = client.post('/api/v1/reviews', json={**base, 'decision': 'accepted'}, headers=rita).json()
    assert client.post(f'/api/v1/cases/{case}/approvals', json={**ask, 'review_id': returned['id']},
                       headers=amy).status_code == 409                                   # superseded review
    assert client.post(f'/api/v1/cases/{case}/approvals', json={**ask, 'review_id': 'missing'},
                       headers=amy).status_code == 409
    client.post(f'/api/v1/books/{book}/supplement-demo', headers=paul)                   # new data, new snapshot
    assert client.post(f'/api/v1/cases/{case}/approvals', json={**ask, 'review_id': accepted['id']},
                       headers=amy).status_code == 409


def test_single_user_reviews_are_never_attributed_or_approved_later(client, monkeypatch):
    book, snap, case = demo_case(client)
    review = client.post('/api/v1/reviews', json={'case_id': case, 'expected_snapshot_id': snap,
                                                  'decision': 'accepted', 'comment': ''}).json()
    monkeypatch.setattr(settings, 'auth_mode', 'multi_user')
    make_users(amy=['approver'])
    response = client.post(f'/api/v1/cases/{case}/approvals', json={
        'review_id': review['id'], 'expected_snapshot_id': snap, 'decision': 'approved'}, headers=login(client, 'amy'))
    assert response.status_code == 409
    with Session() as s:
        assert s.scalar(select(User).where(User.username == 'amy')) is not None


def test_local_cli_creates_lists_deactivates_and_never_takes_passwords_as_arguments(client, monkeypatch, capsys):
    from app import manage_users
    monkeypatch.setenv('LEDGERLENS_NEW_PASSWORD', PASSWORD)
    assert manage_users.main(['create', '--username', 'carol', '--display-name', 'Carol', '--roles', 'reviewer']) == 0
    created = capsys.readouterr().out
    assert '"carol"' in created and PASSWORD not in created
    with pytest.raises(SystemExit):
        manage_users.main(['create', '--username', 'carol', '--display-name', 'Carol', '--roles', 'reviewer'])
    with pytest.raises(SystemExit):   # unknown option: no --password argument exists
        manage_users.main(['create', '--username', 'dave', '--display-name', 'Dave', '--roles', 'reviewer',
                           '--password', PASSWORD])
    monkeypatch.setattr(settings, 'auth_mode', 'multi_user')
    headers = login(client, 'carol')
    assert manage_users.main(['deactivate', '--username', 'carol']) == 0
    assert '"sessions_revoked": 1' in capsys.readouterr().out
    assert client.get('/api/v1/books', headers=headers).status_code == 401
    assert manage_users.main(['list']) == 0
    assert '"active": false' in capsys.readouterr().out
