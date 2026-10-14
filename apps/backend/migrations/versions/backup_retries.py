"""Add optional chain retry settings without changing existing chains."""

import sqlalchemy as sa
from alembic import op

revision = "backup_retries"
down_revision = "agent_uuid"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("backup_chains", sa.Column("retry", sa.JSON(), nullable=True))


def downgrade():
    op.drop_column("backup_chains", "retry")
