"""Add an optional agent display alias."""

import sqlalchemy as sa
from alembic import op

revision = "agent_alias"
down_revision = "agent_connections"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("agents", sa.Column("alias", sa.String(length=255), nullable=True))


def downgrade():
    op.drop_column("agents", "alias")
