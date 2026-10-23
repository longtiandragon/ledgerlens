"""Case flow: case continuation links, simulation basis and review open items.

Revision ID: 0008
Existing simulations and reviews keep NULL for the new columns: their origin
and the open items at review time were not recorded, and nothing is inferred
after the fact. Case links are append-only.
"""
from alembic import op
import sqlalchemy as sa

revision = '0008'
down_revision = '0007'
branch_labels = None
depends_on = None


def entity_columns():
    return [sa.Column('id', sa.String(36), primary_key=True),
            sa.Column('created_at', sa.String(40), nullable=False)]


def upgrade():
    op.create_table('case_links', *entity_columns(),
                    sa.Column('book_id', sa.String(), nullable=False),
                    sa.Column('case_id', sa.String(), nullable=False),
                    sa.Column('prior_case_id', sa.String(), nullable=False),
                    sa.Column('basis', sa.JSON(), nullable=False),
                    sa.UniqueConstraint('case_id', 'prior_case_id'))
    op.create_index('ix_case_links_book_id', 'case_links', ['book_id'])
    op.create_index('ix_case_links_case_id', 'case_links', ['case_id'])
    op.create_index('ix_case_links_prior_case_id', 'case_links', ['prior_case_id'])
    op.add_column('simulations', sa.Column('basis', sa.JSON(), nullable=True))
    op.add_column('reviews', sa.Column('simulation_id', sa.String(), nullable=True))
    op.add_column('reviews', sa.Column('open_items', sa.JSON(), nullable=True))


def downgrade():
    raise RuntimeError('Downgrade would destroy evidence, case history and review records; use a forward migration')
