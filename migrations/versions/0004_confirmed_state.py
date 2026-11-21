"""Separate confirmed Editor history from unpublished changes.

No Push existed before this migration: existing Pins and Removed are unpublished.
"""
from alembic import op
import sqlalchemy as sa

revision = '0004'
down_revision = '0003'
branch_labels = None
depends_on = None


def upgrade():
    for name in ('confirmed_pins', 'confirmed_removed', 'notices'):
        op.add_column('stream_drafts', sa.Column(name, sa.JSON(), nullable=False, server_default='[]'))


def downgrade():
    for name in ('notices', 'confirmed_removed', 'confirmed_pins'):
        op.drop_column('stream_drafts', name)
