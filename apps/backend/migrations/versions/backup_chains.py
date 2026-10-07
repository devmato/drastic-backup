"""Persist central backup chains and resumable runs."""

import sqlalchemy as sa
from alembic import op

revision = "backup_chains"
down_revision = "debug_access"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "backup_chains",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("created", sa.DateTime(), nullable=False),
        sa.Column("updated", sa.DateTime()),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("cron_string", sa.String(255), nullable=False),
        sa.Column("start_timeout_minutes", sa.Integer(), nullable=False),
        sa.Column("steps", sa.JSON(), nullable=False),
    )
    op.create_index("ix_backup_chains_user_id", "backup_chains", ["user_id"])
    op.create_table(
        "backup_chain_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("created", sa.DateTime(), nullable=False),
        sa.Column("updated", sa.DateTime()),
        sa.Column("chain_id", sa.Integer(), sa.ForeignKey("backup_chains.id", ondelete="CASCADE"), nullable=False),
        sa.Column("active_chain_id", sa.Integer(), unique=True),
        sa.Column("planned_slot", sa.DateTime()),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("steps", sa.JSON(), nullable=False),
        sa.Column("start_timeout_minutes", sa.Integer(), nullable=False),
        sa.Column("started", sa.DateTime(), nullable=False),
        sa.Column("ended", sa.DateTime()),
        sa.Column("cancel_requested", sa.Boolean(), nullable=False),
        sa.Column("lease_until", sa.DateTime()),
        sa.Column("lease_token", sa.String(36)),
        sa.UniqueConstraint("chain_id", "planned_slot", name="uq_chain_run_slot"),
    )
    op.create_index("ix_backup_chain_runs_chain_id", "backup_chain_runs", ["chain_id"])


def downgrade():
    op.drop_table("backup_chain_runs")
    op.drop_table("backup_chains")
