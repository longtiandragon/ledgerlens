"""Local user administration for the optional multi-user sign-off mode.

Usage (repository root, PYTHONPATH=backend;. locally; inside the API container as is):
  python -m app.manage_users create --username admin --display-name 管理员 --roles admin
  python -m app.manage_users list
  python -m app.manage_users deactivate --username alice
  python -m app.manage_users revoke-sessions --username alice
Passwords are read interactively, or from LEDGERLENS_NEW_PASSWORD for scripted
setup; they are never accepted as command-line arguments. The database must
already be migrated (start the app once, or run its migrations) to revision 0007.
"""
from __future__ import annotations

import argparse
import getpass
import json
import os
import sys

from sqlalchemy import inspect, select

from . import auth
from .db import Audit, Session, User, UserSession, engine, now


def _password() -> str:
    supplied = os.environ.get('LEDGERLENS_NEW_PASSWORD')
    if supplied:
        return supplied
    first = getpass.getpass('新密码（至少10个字符）：')
    if first != getpass.getpass('再次输入：'):
        raise SystemExit('两次输入的密码不一致')
    return first


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest='command', required=True)
    create = commands.add_parser('create')
    create.add_argument('--username', required=True)
    create.add_argument('--display-name', required=True)
    create.add_argument('--roles', nargs='+', required=True, choices=auth.ROLES)
    commands.add_parser('list')
    for name in ('deactivate', 'revoke-sessions'):
        commands.add_parser(name).add_argument('--username', required=True)
    options = parser.parse_args(argv)
    if 'users' not in inspect(engine).get_table_names():
        raise SystemExit('数据库尚未迁移到包含用户表的版本（0007）；请先启动一次应用或执行迁移')
    with Session() as s:
        if options.command == 'list':
            print(json.dumps([auth.public_user(u) for u in s.scalars(select(User).order_by(User.created_at))],
                             ensure_ascii=False, indent=1))
            return 0
        if options.command == 'create':
            try:
                user = auth.create_user(s, options.username, options.display_name, options.roles, _password())
            except ValueError as exc:
                raise SystemExit(str(exc)) from None
            s.add(Audit(book_id='-', action='auth.user_created',
                        details={'user_id': user.id, 'roles': user.roles, 'by': 'local-cli'}))
            s.commit()
            print(json.dumps(auth.public_user(user), ensure_ascii=False))
            return 0
        user = s.scalar(select(User).where(User.username == options.username))
        if user is None:
            raise SystemExit('用户不存在')
        if options.command == 'deactivate':
            user.active = False
        revoked = 0
        for row in s.scalars(select(UserSession).where(UserSession.user_id == user.id,
                                                       UserSession.revoked_at.is_(None))):
            row.revoked_at = now()
            revoked += 1
        s.add(Audit(book_id='-', action='auth.' + options.command.replace('-', '_'),
                    details={'user_id': user.id, 'sessions_revoked': revoked, 'by': 'local-cli'}))
        s.commit()
        print(json.dumps({'username': user.username, 'active': user.active, 'sessions_revoked': revoked},
                         ensure_ascii=False))
    return 0


if __name__ == '__main__':
    sys.exit(main())
