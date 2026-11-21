"""Keep Removed history across draft reopen and application upgrades."""
from alembic import op
import sqlalchemy as sa

revision = '0002'
down_revision = '0001'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('stream_drafts', sa.Column('removed', sa.JSON(), nullable=False, server_default='[]'))


def downgrade():
    op.drop_column('stream_drafts', 'removed')
