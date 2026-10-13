"""Optional local multi-user sign-off: accounts, sessions and segregation of duties.

Single-user mode (the default) keeps the original local workflow unchanged.
Multi-user mode requires a bearer session on every API call except health,
settings and login, and enforces separation of incompatible duties
(《企业内部控制基本规范》第二十九条): whoever started an investigation of a
case cannot review it, and a review cannot be approved by its reviewer or by an
investigator of the same case. Roles are assigned by an administrator; the
program never infers them. This is a local control, not an identity provider.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
import hashlib
import hmac
import re
import secrets

from fastapi import HTTPException, Request
from sqlalchemy import select

from .config import settings
from .db import Run, User, UserSession, now

ROLES = ('preparer', 'reviewer', 'approver', 'admin')
ROLE_LABELS = {'preparer': '编制／调查', 'reviewer': '复核', 'approver': '审批签发', 'admin': '用户管理'}
OPEN_PATHS = {'/api/v1/health', '/api/v1/settings', '/api/v1/auth/login', '/api/v1/auth/me'}
USERNAME = re.compile(r'[a-z][a-z0-9_.-]{2,31}')
# scrypt parameters (RFC 7914); 16 MiB memory per hash, checked by tests.
SCRYPT = {'n': 2 ** 14, 'r': 8, 'p': 1, 'dklen': 32}


def multi_user() -> bool:
    return settings.auth_mode == 'multi_user'


def hash_password(password: str, salt: bytes | None = None) -> tuple[str, str]:
    if not isinstance(password, str) or len(password) < 10 or len(password) > 256:
        raise ValueError('Password must contain 10 to 256 characters')
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode('utf-8'), salt=salt, maxmem=64 * 1024 * 1024, **SCRYPT)
    return salt.hex(), digest.hex()


def verify_password(password: str, salt_hex: str, expected_hex: str) -> bool:
    try:
        _, actual = hash_password(password, bytes.fromhex(salt_hex))
    except ValueError:
        return False
    return hmac.compare_digest(actual, expected_hex)


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode('utf-8')).hexdigest()


def create_user(s, username: str, display_name: str, roles: list[str], password: str) -> User:
    if not USERNAME.fullmatch(username or ''):
        raise ValueError('Username must be 3-32 lowercase letters, digits, dot, dash or underscore')
    if not roles or len(set(roles)) != len(roles) or not set(roles) <= set(ROLES):
        raise ValueError('Roles must be a non-empty subset of ' + ', '.join(ROLES))
    if not display_name or len(display_name) > 64:
        raise ValueError('Display name must contain 1 to 64 characters')
    if s.scalar(select(User).where(User.username == username)):
        raise ValueError('Username already exists')
    salt, digest = hash_password(password)
    user = User(username=username, display_name=display_name, roles=sorted(roles),
                password_salt=salt, password_hash=digest, active=True)
    s.add(user)
    s.flush()
    return user


def login(s, username: str, password: str) -> tuple[str, User, str]:
    user = s.scalar(select(User).where(User.username == username))
    # Hash even for unknown users so response time does not reveal accounts.
    salt, expected = (user.password_salt, user.password_hash) if user else ('00' * 16, '00' * 32)
    if not verify_password(password, salt, expected) or not user or not user.active:
        raise HTTPException(401, '用户名或密码错误')
    token = secrets.token_urlsafe(32)
    expires = (datetime.now(UTC) + timedelta(hours=settings.session_hours)).isoformat()
    s.add(UserSession(user_id=user.id, token_hash=_token_hash(token), expires_at=expires))
    return token, user, expires


def resolve(s, token: str | None) -> User | None:
    if not token:
        return None
    row = s.scalar(select(UserSession).where(UserSession.token_hash == _token_hash(token)))
    if not row or row.revoked_at or row.expires_at <= datetime.now(UTC).isoformat():
        return None
    user = s.get(User, row.user_id)
    return user if user and user.active else None


def revoke(s, token: str | None) -> None:
    row = s.scalar(select(UserSession).where(UserSession.token_hash == _token_hash(token or '')))
    if row and not row.revoked_at:
        row.revoked_at = now()


def bearer(request: Request) -> str | None:
    header = request.headers.get('authorization', '')
    return header[7:].strip() if header.lower().startswith('bearer ') else None


def public_user(user: User | None) -> dict | None:
    if user is None:
        return None
    return {'id': user.id, 'username': user.username, 'display_name': user.display_name,
            'roles': list(user.roles), 'active': user.active}


def acting_user(request: Request, s, role: str | None = None) -> User | None:
    """The authenticated user in multi-user mode (None in single-user mode)."""
    if not multi_user():
        return None
    user = resolve(s, bearer(request))
    if user is None:
        raise HTTPException(401, '请先登录')
    if role and role not in user.roles:
        raise HTTPException(403, f'当前账户没有“{ROLE_LABELS[role]}”职责')
    return user


def investigators(s, case_id: str) -> set[str]:
    return {identity for identity in s.scalars(select(Run.created_by).where(Run.case_id == case_id)) if identity}


def require_separate(user: User | None, others: set[str], message: str) -> None:
    if user is not None and user.id in others:
        raise HTTPException(403, message)
