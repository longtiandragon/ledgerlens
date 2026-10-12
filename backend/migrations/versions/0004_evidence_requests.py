"""Track evidence requests and their fulfilment as append-only records.

Revision ID: 0004
Requests are never edited; fulfilment or withdrawal is an appended event.
"""
from alembic import op
import sqlalchemy as sa

revision = '0004'
down_revision = '0003'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('evidence_requests',
                    sa.Column('book_id', sa.String(), nullable=False),
                    sa.Column('case_id', sa.String(), nullable=False),
                    sa.Column('snapshot_id', sa.String(), nullable=False),
                    sa.Column('kind', sa.String(), nullable=False),
                    sa.Column('reference', sa.String(), nullable=True),
                    sa.Column('description', sa.String(), nullable=False),
                    sa.Column('origin', sa.String(), nullable=False),
                    sa.Column('id', sa.String(length=36), nullable=False),
                    sa.Column('created_at', sa.String(length=40), nullable=False),
                    sa.PrimaryKeyConstraint('id'))
    op.create_index('ix_evidence_requests_book_id', 'evidence_requests', ['book_id'])
    op.create_index('ix_evidence_requests_case_id', 'evidence_requests', ['case_id'])
    op.create_table('evidence_request_events',
                    sa.Column('request_id', sa.String(), nullable=False),
                    sa.Column('action', sa.String(), nullable=False),
                    sa.Column('snapshot_id', sa.String(), nullable=True),
                    sa.Column('record_ids', sa.JSON(), nullable=False),
                    sa.Column('comment', sa.String(), nullable=False),
                    sa.Column('id', sa.String(length=36), nullable=False),
                    sa.Column('created_at', sa.String(length=40), nullable=False),
                    sa.PrimaryKeyConstraint('id'))
    op.create_index('ix_evidence_request_events_request_id', 'evidence_request_events', ['request_id'])


def downgrade():
    raise RuntimeError('Downgrade would destroy evidence request history; use a forward migration')
