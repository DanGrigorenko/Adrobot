"""Persist Creator identity and intent independently of network requests."""
from alembic import op
import sqlalchemy as sa

revision = '0003'
down_revision = '0002'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'creator_operations',
        sa.Column('id', sa.String(), primary_key=True),
        sa.Column('alias', sa.String(), nullable=False, unique=True),
        sa.Column('inputs', sa.JSON(), nullable=False),
        sa.Column('status', sa.String(), nullable=False),
        sa.Column('step', sa.String(), nullable=False),
        sa.Column('campaign_id', sa.Integer(), nullable=True),
        sa.Column('state', sa.JSON(), nullable=False),
    )


def downgrade():
    op.drop_table('creator_operations')
