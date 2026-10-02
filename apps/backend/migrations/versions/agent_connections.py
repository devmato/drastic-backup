"""Cache the agent's public connection status for offline display."""

import sqlalchemy as sa
from alembic import op

revision = "agent_connections"
down_revision = "agent_protocol"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("agents", sa.Column("connections", sa.JSON(), nullable=True))


def downgrade():
    op.drop_column("agents", "connections")
