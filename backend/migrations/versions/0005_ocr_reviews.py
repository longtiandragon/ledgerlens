"""Local OCR jobs and immutable human field review versions."""
from alembic import op
import sqlalchemy as sa

revision = '0005'
down_revision = '0004'
branch_labels = None
depends_on = None


def entity_columns():
    return [sa.Column('id', sa.String(36), primary_key=True),
            sa.Column('created_at', sa.String(40), nullable=False)]


def upgrade():
    op.create_table('ocr_runs', *entity_columns(),
                    sa.Column('book_id', sa.String(), nullable=False),
                    sa.Column('document_id', sa.String(), nullable=False),
                    sa.Column('status', sa.String(), nullable=False),
                    sa.Column('worker_token', sa.String(), nullable=True),
                    sa.Column('book_version', sa.Integer(), nullable=False),
                    sa.Column('period', sa.String(), nullable=False),
                    sa.Column('result', sa.JSON(), nullable=False),
                    sa.Column('result_digest', sa.String(), nullable=True),
                    sa.Column('error', sa.String(), nullable=True))
    op.create_index('ix_ocr_runs_book_id', 'ocr_runs', ['book_id'])
    op.create_table('ocr_reviews', *entity_columns(),
                    sa.Column('book_id', sa.String(), nullable=False),
                    sa.Column('run_id', sa.String(), nullable=False),
                    sa.Column('version', sa.Integer(), nullable=False),
                    sa.Column('parent_id', sa.String(), nullable=True),
                    sa.Column('book_revision', sa.Integer(), nullable=False),
                    sa.Column('book_version', sa.Integer(), nullable=False),
                    sa.Column('payload', sa.JSON(), nullable=False),
                    sa.Column('digest', sa.String(), nullable=False),
                    sa.Column('import_id', sa.String(), nullable=False),
                    sa.UniqueConstraint('run_id', 'version'), sa.UniqueConstraint('parent_id'),
                    sa.UniqueConstraint('import_id'))
    op.create_index('ix_ocr_reviews_book_id', 'ocr_reviews', ['book_id'])
    op.create_index('ix_ocr_reviews_run_id', 'ocr_reviews', ['run_id'])


def downgrade():
    raise RuntimeError('Downgrade would destroy evidence and OCR review history; use a forward migration')
