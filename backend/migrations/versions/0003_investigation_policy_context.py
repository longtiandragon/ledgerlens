"""Freeze saved policy check context independently for each investigation run.

Revision ID: 0003
No historical ledger, rule identity or saved policy artifact is rewritten.
"""
from alembic import op
import sqlalchemy as sa

revision = '0003'
down_revision = '0002'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('investigation_policy_contexts',
                    sa.Column('run_id', sa.String(), nullable=False),
                    sa.Column('book_id', sa.String(), nullable=False),
                    sa.Column('snapshot_id', sa.String(), nullable=False),
                    sa.Column('payload', sa.JSON(), nullable=False),
                    sa.Column('digest', sa.String(), nullable=False),
                    sa.Column('id', sa.String(length=36), nullable=False),
                    sa.Column('created_at', sa.String(length=40), nullable=False),
                    sa.PrimaryKeyConstraint('id'),
                    sa.UniqueConstraint('run_id'))
    op.create_index('ix_investigation_policy_contexts_book_id', 'investigation_policy_contexts', ['book_id'])
    op.create_index('ix_investigation_policy_contexts_snapshot_id', 'investigation_policy_contexts', ['snapshot_id'])


def downgrade():
    raise RuntimeError('Downgrade would destroy evidence and investigation context history; use a forward migration')
