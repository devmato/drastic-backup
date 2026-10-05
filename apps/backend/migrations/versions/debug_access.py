"""Add opt-in diagnostic access and bounded event history."""

import sqlalchemy as sa
from alembic import op

revision = "debug_access"
down_revision = "agent_alias"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("users") as batch:
        batch.add_column(sa.Column("debug_token_hash", sa.String(64), nullable=True))
        batch.add_column(sa.Column("debug_enabled_at", sa.DateTime(), nullable=True))
        batch.create_unique_constraint("uq_users_debug_token_hash", ["debug_token_hash"])
    op.create_table(
        "diagnostic_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("agent_id", sa.Integer(), sa.ForeignKey("agents.id", ondelete="CASCADE")),
        sa.Column("event_uuid", sa.String(36), nullable=False),
        sa.Column("operation_uuid", sa.String(36)),
        sa.Column("component", sa.String(32), nullable=False),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("occurred_at", sa.DateTime(), nullable=False),
        sa.Column("received_at", sa.DateTime(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.UniqueConstraint("user_id", "event_uuid", name="uq_diagnostic_event_uuid"),
    )
    op.create_index("ix_diagnostics_owner_id", "diagnostic_events", ["user_id", "id"])
    op.create_index("ix_diagnostics_operation", "diagnostic_events", ["user_id", "operation_uuid", "id"])
    op.create_index("ix_diagnostics_received", "diagnostic_events", ["received_at"])


def downgrade():
    op.drop_table("diagnostic_events")
    with op.batch_alter_table("users") as batch:
        batch.drop_constraint("uq_users_debug_token_hash", type_="unique")
        batch.drop_column("debug_enabled_at")
        batch.drop_column("debug_token_hash")
