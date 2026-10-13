"""Optional local multi-user sign-off: users, sessions, approvals and actor columns.

Revision ID: 0007
Existing rows keep NULL actors (single-user history); nothing is backfilled or
attributed to a person after the fact. Approvals are append-only.
"""
from alembic import op
import sqlalchemy as sa

revision = '0007'
down_revision = '0006'
branch_labels = None
depends_on = None


def entity_columns():
    return [sa.Column('id', sa.String(36), primary_key=True),
            sa.Column('created_at', sa.String(40), nullable=False)]


def upgrade():
    op.create_table('users', *entity_columns(),
                    sa.Column('username', sa.String(), nullable=False),
                    sa.Column('display_name', sa.String(), nullable=False),
                    sa.Column('roles', sa.JSON(), nullable=False),
                    sa.Column('password_salt', sa.String(), nullable=False),
                    sa.Column('password_hash', sa.String(), nullable=False),
                    sa.Column('active', sa.Boolean(), nullable=False),
                    sa.UniqueConstraint('username'))
    op.create_table('user_sessions', *entity_columns(),
                    sa.Column('user_id', sa.String(), nullable=False),
                    sa.Column('token_hash', sa.String(), nullable=False),
                    sa.Column('expires_at', sa.String(), nullable=False),
                    sa.Column('revoked_at', sa.String(), nullable=True),
                    sa.UniqueConstraint('token_hash'))
    op.create_index('ix_user_sessions_user_id', 'user_sessions', ['user_id'])
    op.create_table('approvals', *entity_columns(),
                    sa.Column('case_id', sa.String(), nullable=False),
                    sa.Column('snapshot_id', sa.String(), nullable=False),
                    sa.Column('review_id', sa.String(), nullable=False),
                    sa.Column('user_id', sa.String(), nullable=False),
                    sa.Column('decision', sa.String(), nullable=False),
                    sa.Column('comment', sa.String(), nullable=False))
    op.create_index('ix_approvals_case_id', 'approvals', ['case_id'])
    op.create_index('ix_approvals_review_id', 'approvals', ['review_id'])
    op.add_column('reviews', sa.Column('user_id', sa.String(), nullable=True))
    op.add_column('agent_runs', sa.Column('created_by', sa.String(), nullable=True))


def downgrade():
    raise RuntimeError('Downgrade would destroy evidence and sign-off history; use a forward migration')
