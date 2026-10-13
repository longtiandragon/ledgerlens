"""Login, logout and user administration for the optional multi-user mode."""
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import Field
from sqlalchemy import select

from . import auth
from .db import Audit, User, session
from .schemas import StrictModel

router = APIRouter(prefix='/api/v1/auth', tags=['auth'])
Role = Literal['preparer', 'reviewer', 'approver', 'admin']


class LoginInput(StrictModel):
    username: str = Field(min_length=3, max_length=32)
    password: str = Field(min_length=1, max_length=256)


class UserInput(StrictModel):
    username: str = Field(min_length=3, max_length=32)
    display_name: str = Field(min_length=1, max_length=64)
    roles: list[Role] = Field(min_length=1, max_length=4)
    password: str = Field(min_length=10, max_length=256)


def _require_multi_user():
    if not auth.multi_user():
        raise HTTPException(409, '当前为单用户本地模式；多用户审签需设置 AUTH_MODE=multi_user')


@router.post('/login')
def login(body: LoginInput, s=Depends(session)):
    _require_multi_user()
    token, user, expires = auth.login(s, body.username, body.password)
    s.add(Audit(book_id='-', action='auth.login', details={'user_id': user.id}))
    s.commit()
    return {'token': token, 'expires_at': expires, 'user': auth.public_user(user)}


@router.post('/logout')
def logout(request: Request, s=Depends(session)):
    _require_multi_user()
    auth.acting_user(request, s)
    auth.revoke(s, auth.bearer(request))
    s.commit()
    return {'status': 'logged_out'}


@router.get('/me')
def me(request: Request, s=Depends(session)):
    if not auth.multi_user():
        return {'mode': 'single_user', 'user': None, 'roles': list(auth.ROLES), 'role_labels': auth.ROLE_LABELS}
    return {'mode': 'multi_user', 'user': auth.public_user(auth.resolve(s, auth.bearer(request))),
            'roles': list(auth.ROLES), 'role_labels': auth.ROLE_LABELS}


@router.get('/users')
def users(request: Request, s=Depends(session)):
    _require_multi_user()
    auth.acting_user(request, s, 'admin')
    return [auth.public_user(user) for user in s.scalars(select(User).order_by(User.created_at))]


@router.post('/users', status_code=201)
def create_user(body: UserInput, request: Request, s=Depends(session)):
    _require_multi_user()
    admin = auth.acting_user(request, s, 'admin')
    try:
        user = auth.create_user(s, body.username, body.display_name, list(body.roles), body.password)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    s.add(Audit(book_id='-', action='auth.user_created',
                details={'user_id': user.id, 'roles': user.roles, 'by': admin.id}))
    s.commit()
    return auth.public_user(user)
