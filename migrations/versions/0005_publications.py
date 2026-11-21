"""Durable per-stream publication intent and request completion evidence."""
from alembic import op
import sqlalchemy as sa

revision = '0005'
down_revision = '0004'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('stream_drafts', sa.Column('publication_id', sa.String(), nullable=True))
    op.create_table('publications',
        sa.Column('id', sa.String(), primary_key=True),
        sa.Column('stream_id', sa.Integer(), nullable=False),
        sa.Column('campaign_id', sa.Integer(), nullable=False),
        sa.Column('base', sa.JSON(), nullable=False),
        sa.Column('sent', sa.JSON(), nullable=False),
        sa.Column('status', sa.String(), nullable=False),
        sa.Column('completed', sa.Boolean(), nullable=False),
        sa.Column('unfinished', sa.Boolean(), nullable=False))


def downgrade():
    op.drop_table('publications')
    op.drop_column('stream_drafts', 'publication_id')
