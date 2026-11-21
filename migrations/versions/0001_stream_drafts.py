"""Persist the initial draft and its Keitaro base without normalizing shares."""
from alembic import op
import sqlalchemy as sa

revision = '0001'
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'stream_drafts',
        sa.Column('stream_id', sa.Integer(), primary_key=True),
        sa.Column('campaign_id', sa.Integer(), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('base', sa.JSON(), nullable=False),
        sa.Column('bindings', sa.JSON(), nullable=False),
    )


def downgrade():
    op.drop_table('stream_drafts')
