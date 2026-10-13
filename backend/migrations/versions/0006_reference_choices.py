"""Optional persisted reference search and append-only snapshot selections."""
from alembic import op
import sqlalchemy as sa

revision = '0006'
down_revision = '0005'
branch_labels = None
depends_on = None


def entity_columns():
    return [sa.Column('id', sa.String(36), primary_key=True),
            sa.Column('created_at', sa.String(40), nullable=False)]


def upgrade():
    op.create_table('reference_searches', *entity_columns(),
                    sa.Column('book_id', sa.String(), nullable=False),
                    sa.Column('book_version', sa.Integer(), nullable=False),
                    sa.Column('book_revision', sa.Integer(), nullable=False),
                    sa.Column('framework', sa.String(), nullable=False),
                    sa.Column('period', sa.String(), nullable=False),
                    sa.Column('query', sa.String(), nullable=False),
                    sa.Column('registry_key', sa.String(), nullable=False),
                    sa.Column('manifest', sa.JSON(), nullable=False),
                    sa.Column('status', sa.String(), nullable=False),
                    sa.Column('worker_token', sa.String(), nullable=True),
                    sa.Column('result', sa.JSON(), nullable=False),
                    sa.Column('result_digest', sa.String(), nullable=True),
                    sa.Column('error', sa.String(), nullable=True))
    op.create_index('ix_reference_searches_book_id', 'reference_searches', ['book_id'])
    op.create_table('reference_choices', *entity_columns(),
                    sa.Column('book_id', sa.String(), nullable=False),
                    sa.Column('book_version', sa.Integer(), nullable=False),
                    sa.Column('version', sa.Integer(), nullable=False),
                    sa.Column('parent_id', sa.String(), nullable=True),
                    sa.Column('payload', sa.JSON(), nullable=False),
                    sa.Column('digest', sa.String(), nullable=False),
                    sa.Column('snapshot_id', sa.String(), nullable=True),
                    sa.UniqueConstraint('book_id', 'version'), sa.UniqueConstraint('parent_id'))
    op.create_index('ix_reference_choices_book_id', 'reference_choices', ['book_id'])


def downgrade():
    raise RuntimeError('Downgrade would destroy evidence and reference history; use a forward migration')
