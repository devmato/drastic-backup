"""Add the agent protocol version, defaulting existing agents to legacy protocol 0."""

import sqlalchemy as sa
from alembic import op

revision = "agent_protocol"
down_revision = "init"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("agents", sa.Column("protocol_version", sa.Integer(), nullable=False, server_default="0"))


def downgrade():
    op.drop_column("agents", "protocol_version")
