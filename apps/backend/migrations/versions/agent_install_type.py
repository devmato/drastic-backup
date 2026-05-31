"""add agent install type

Revision ID: agent_install_type
Revises: secret_refactor
Create Date: 2026-05-17 00:00:00.000000

"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "agent_install_type"
down_revision = "secret_refactor"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "agents",
        sa.Column("install_type", sa.String(length=32), nullable=False, server_default="manual"),
    )
    op.alter_column("agents", "install_type", server_default=None)


def downgrade():
    op.drop_column("agents", "install_type")
