"""Append-only policy annotation versions and independently versioned results.

Revision ID: 0002
No legacy tables or historical core identities are rewritten.
"""
from alembic import op
import sqlalchemy as sa

revision = '0002'
down_revision = '0001'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('policy_observation_versions',
                    sa.Column('book_id', sa.String(), nullable=False),
                    sa.Column('snapshot_id', sa.String(), nullable=False),
                    sa.Column('logical_key', sa.String(), nullable=False),
                    sa.Column('version', sa.Integer(), nullable=False),
                    sa.Column('parent_version_id', sa.String(), nullable=True),
                    sa.Column('payload', sa.JSON(), nullable=False),
                    sa.Column('digest', sa.String(), nullable=False),
                    sa.Column('comment', sa.String(), nullable=False),
                    sa.Column('id', sa.String(length=36), nullable=False),
                    sa.Column('created_at', sa.String(length=40), nullable=False),
                    sa.PrimaryKeyConstraint('id'),
                    sa.UniqueConstraint('book_id', 'snapshot_id', 'logical_key', 'version'),
                    sa.UniqueConstraint('parent_version_id'))
    op.create_index('ix_policy_observation_versions_book_id', 'policy_observation_versions', ['book_id'])
    op.create_index('ix_policy_observation_versions_snapshot_id', 'policy_observation_versions', ['snapshot_id'])
    op.create_table('policy_check_runs',
                    sa.Column('book_id', sa.String(), nullable=False),
                    sa.Column('snapshot_id', sa.String(), nullable=False),
                    sa.Column('observation_id', sa.String(), nullable=False),
                    sa.Column('pack_key', sa.String(), nullable=False),
                    sa.Column('observation_digest', sa.String(), nullable=False),
                    sa.Column('artifact_digest', sa.String(), nullable=False),
                    sa.Column('binding', sa.JSON(), nullable=False),
                    sa.Column('result', sa.JSON(), nullable=False),
                    sa.Column('id', sa.String(length=36), nullable=False),
                    sa.Column('created_at', sa.String(length=40), nullable=False),
                    sa.PrimaryKeyConstraint('id'),
                    sa.UniqueConstraint('observation_id', 'pack_key'))
    op.create_index('ix_policy_check_runs_book_id', 'policy_check_runs', ['book_id'])
    op.create_index('ix_policy_check_runs_observation_id', 'policy_check_runs', ['observation_id'])


def downgrade():
    raise RuntimeError('Downgrade would destroy evidence and policy history; use a new forward migration')
