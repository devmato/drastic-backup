"""init

Revision ID: init
Revises:
Create Date: 2026-05-03 00:00:00.000000

"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "init"
down_revision = None
branch_labels = None
depends_on = None


agent_operation_state = sa.Enum(
    "running",
    "warning",
    "failed",
    "success",
    "cancelled",
    name="agentoperationstate",
    native_enum=False,
    length=255,
)
agent_operation_type = sa.Enum(
    "backup",
    "restore",
    "retention",
    "repository_check",
    "repository_unlock",
    "repository_stats",
    "sync",
    "command",
    name="agentoperationtype",
    native_enum=False,
    length=255,
)
agent_operation_source = sa.Enum(
    "manual",
    "schedule",
    "triggered",
    "system",
    name="agentoperationsource",
    native_enum=False,
    length=255,
)
agent_operation_log_level = sa.Enum(
    "info",
    "warning",
    "error",
    name="agentoperationloglevel",
    native_enum=False,
    length=255,
)
job_type = sa.Enum("file", "proxmox", name="jobtype", native_enum=False, length=255)
job_action_module = sa.Enum(
    "command",
    "docker",
    name="jobactionmoduleenum",
    native_enum=False,
    length=255,
)


def upgrade():
    op.create_table(
        "users",
        sa.Column("name", sa.String(length=120), nullable=True),
        sa.Column("email", sa.String(length=120), nullable=True),
        sa.Column("password", sa.String(length=120), nullable=True),
        sa.Column("encrypted_recovery_key", sa.JSON(), nullable=True),
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("created", sa.DateTime(), nullable=False),
        sa.Column("updated", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_users")),
        sa.UniqueConstraint("email", name=op.f("uq_users_email")),
        sa.UniqueConstraint("name", name=op.f("uq_users_name")),
    )
    op.create_table(
        "agents",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("secret", sa.String(length=255), nullable=False),
        sa.Column("public_key", sa.Text(), nullable=True),
        sa.Column("os", sa.String(length=255), nullable=True),
        sa.Column("version", sa.String(length=255), nullable=True),
        sa.Column("hostname", sa.String(length=255), nullable=True),
        sa.Column("last_connection", sa.DateTime(), nullable=True),
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("created", sa.DateTime(), nullable=False),
        sa.Column("updated", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=op.f("fk_agents_user_id_users")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_agents")),
    )
    op.create_table(
        "notification_configs",
        sa.Column("_url", sa.JSON(), nullable=True),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("operation_types", sa.JSON(), nullable=False),
        sa.Column("operation_states", sa.JSON(), nullable=False),
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("created", sa.DateTime(), nullable=False),
        sa.Column("updated", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_notification_configs_user_id_users")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_notification_configs")),
    )
    op.create_table(
        "repositories",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("location", sa.String(length=255), nullable=False),
        sa.Column("encrypted_recovery_key", sa.JSON(), nullable=True),
        sa.Column("restic_id", sa.String(length=255), nullable=True),
        sa.Column("_environment", sa.JSON(), nullable=True),
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("created", sa.DateTime(), nullable=False),
        sa.Column("updated", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_repositories_user_id_users")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_repositories")),
        sa.UniqueConstraint("user_id", "name", name="uq_repositories_user_id_name"),
    )
    op.create_table(
        "retentions",
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("keep_last", sa.Integer(), nullable=True),
        sa.Column("keep_hourly", sa.Integer(), nullable=True),
        sa.Column("keep_weekly", sa.Integer(), nullable=True),
        sa.Column("keep_monthly", sa.Integer(), nullable=True),
        sa.Column("keep_yearly", sa.Integer(), nullable=True),
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("created", sa.DateTime(), nullable=False),
        sa.Column("updated", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_retentions_user_id_users")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_retentions")),
    )
    op.create_table(
        "agent_repositories",
        sa.Column("agent_id", sa.Integer(), nullable=False),
        sa.Column("repository_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["agent_id"], ["agents.id"], name=op.f("fk_agent_repositories_agent_id_agents")
        ),
        sa.ForeignKeyConstraint(
            ["repository_id"],
            ["repositories.id"],
            name=op.f("fk_agent_repositories_repository_id_repositories"),
        ),
        sa.PrimaryKeyConstraint("agent_id", "repository_id", name=op.f("pk_agent_repositories")),
    )
    op.create_table(
        "agent_repository_secrets",
        sa.Column("agent_id", sa.Integer(), nullable=False),
        sa.Column("repository_id", sa.Integer(), nullable=False),
        sa.Column("encrypted_recovery_key", sa.JSON(), nullable=False),
        sa.Column("encrypted_agent_key", sa.JSON(), nullable=True),
        sa.Column("restic_key_id", sa.String(length=255), nullable=True),
        sa.Column("provisioned", sa.Boolean(), nullable=False),
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("created", sa.DateTime(), nullable=False),
        sa.Column("updated", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(
            ["agent_id"], ["agents.id"], name=op.f("fk_agent_repository_secrets_agent_id_agents")
        ),
        sa.ForeignKeyConstraint(
            ["repository_id"],
            ["repositories.id"],
            name=op.f("fk_agent_repository_secrets_repository_id_repositories"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_agent_repository_secrets")),
        sa.UniqueConstraint(
            "agent_id",
            "repository_id",
            name="uq_agent_repository_secrets_agent_id_repository_id",
        ),
    )
    op.create_table(
        "agent_sessions",
        sa.Column("agent_id", sa.Integer(), nullable=False),
        sa.Column("request_sid", sa.String(length=255), nullable=False),
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("created", sa.DateTime(), nullable=False),
        sa.Column("updated", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(
            ["agent_id"], ["agents.id"], name=op.f("fk_agent_sessions_agent_id_agents")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_agent_sessions")),
    )
    op.create_table(
        "jobs",
        sa.Column("uuid", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("agent_id", sa.Integer(), nullable=False),
        sa.Column("type", job_type, nullable=False),
        sa.Column("config", sa.JSON(), nullable=False),
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("created", sa.DateTime(), nullable=False),
        sa.Column("updated", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["agent_id"], ["agents.id"], name=op.f("fk_jobs_agent_id_agents")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_jobs")),
        sa.UniqueConstraint("uuid", name=op.f("uq_jobs_uuid")),
    )
    op.create_table(
        "user_sessions",
        sa.Column("public_id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("refresh_token_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(), nullable=True),
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
        sa.Column("user_agent", sa.String(length=255), nullable=True),
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("created", sa.DateTime(), nullable=False),
        sa.Column("updated", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_user_sessions_user_id_users")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_user_sessions")),
        sa.UniqueConstraint("public_id", name=op.f("uq_user_sessions_public_id")),
    )
    op.create_index(op.f("ix_user_sessions_user_id"), "user_sessions", ["user_id"], unique=False)
    op.create_table(
        "job_actions",
        sa.Column("job_id", sa.Integer(), nullable=False),
        sa.Column("module", job_action_module, nullable=False),
        sa.Column("hook", sa.String(length=255), nullable=False),
        sa.Column("data", sa.JSON(), nullable=False),
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("created", sa.DateTime(), nullable=False),
        sa.Column("updated", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(
            ["job_id"], ["jobs.id"], name=op.f("fk_job_actions_job_id_jobs")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_job_actions")),
    )
    op.create_table(
        "job_schedules",
        sa.Column("job_id", sa.Integer(), nullable=False),
        sa.Column("repository_id", sa.Integer(), nullable=False),
        sa.Column("retention_id", sa.Integer(), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=True),
        sa.Column("advanced", sa.Boolean(), nullable=True),
        sa.Column("config", sa.JSON(), nullable=False),
        sa.Column("cron_string", sa.String(length=255), nullable=False),
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("created", sa.DateTime(), nullable=False),
        sa.Column("updated", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(
            ["job_id"], ["jobs.id"], name=op.f("fk_job_schedules_job_id_jobs")
        ),
        sa.ForeignKeyConstraint(
            ["repository_id"],
            ["repositories.id"],
            name=op.f("fk_job_schedules_repository_id_repositories"),
        ),
        sa.ForeignKeyConstraint(
            ["retention_id"], ["retentions.id"], name=op.f("fk_job_schedules_retention_id_retentions")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_job_schedules")),
    )
    op.create_table(
        "agent_operations",
        sa.Column("uuid", sa.String(length=36), nullable=False),
        sa.Column("agent_id", sa.Integer(), nullable=False),
        sa.Column("repository_id", sa.Integer(), nullable=True),
        sa.Column("job_id", sa.Integer(), nullable=True),
        sa.Column("schedule_id", sa.Integer(), nullable=True),
        sa.Column("retention_id", sa.Integer(), nullable=True),
        sa.Column("parent_operation_id", sa.Integer(), nullable=True),
        sa.Column("data", sa.JSON(), nullable=True),
        sa.Column("state", agent_operation_state, nullable=False),
        sa.Column("type", agent_operation_type, nullable=False),
        sa.Column("source", agent_operation_source, nullable=False),
        sa.Column("started", sa.DateTime(), nullable=True),
        sa.Column("ended", sa.DateTime(), nullable=True),
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("created", sa.DateTime(), nullable=False),
        sa.Column("updated", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(
            ["agent_id"], ["agents.id"], name=op.f("fk_agent_operations_agent_id_agents")
        ),
        sa.ForeignKeyConstraint(
            ["job_id"], ["jobs.id"], name=op.f("fk_agent_operations_job_id_jobs")
        ),
        sa.ForeignKeyConstraint(
            ["parent_operation_id"],
            ["agent_operations.id"],
            name=op.f("fk_agent_operations_parent_operation_id_agent_operations"),
        ),
        sa.ForeignKeyConstraint(
            ["repository_id"],
            ["repositories.id"],
            name=op.f("fk_agent_operations_repository_id_repositories"),
        ),
        sa.ForeignKeyConstraint(
            ["retention_id"],
            ["retentions.id"],
            name=op.f("fk_agent_operations_retention_id_retentions"),
        ),
        sa.ForeignKeyConstraint(
            ["schedule_id"],
            ["job_schedules.id"],
            name=op.f("fk_agent_operations_schedule_id_job_schedules"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_agent_operations")),
        sa.UniqueConstraint("uuid", name=op.f("uq_agent_operations_uuid")),
    )
    op.create_table(
        "agent_operation_logs",
        sa.Column("operation_id", sa.Integer(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("level", agent_operation_log_level, nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("data", sa.JSON(), nullable=True),
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("created", sa.DateTime(), nullable=False),
        sa.Column("updated", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(
            ["operation_id"],
            ["agent_operations.id"],
            name=op.f("fk_agent_operation_logs_operation_id_agent_operations"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_agent_operation_logs")),
        sa.UniqueConstraint(
            "operation_id",
            "sequence",
            name="uq_agent_operation_logs_operation_sequence",
        ),
    )
    op.create_table(
        "agent_operation_artifacts",
        sa.Column("uuid", sa.String(length=36), nullable=False),
        sa.Column("operation_id", sa.Integer(), nullable=False),
        sa.Column("artifact_key", sa.String(length=255), nullable=False),
        sa.Column("snapshot_id", sa.String(length=255), nullable=True),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("data", sa.JSON(), nullable=True),
        sa.Column("forgotten_at", sa.DateTime(), nullable=True),
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("created", sa.DateTime(), nullable=False),
        sa.Column("updated", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(
            ["operation_id"],
            ["agent_operations.id"],
            name=op.f("fk_agent_operation_artifacts_operation_id_agent_operations"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_agent_operation_artifacts")),
        sa.UniqueConstraint(
            "operation_id", "artifact_key", name="uq_agent_operation_artifacts_operation_key"
        ),
        sa.UniqueConstraint("uuid", name=op.f("uq_agent_operation_artifacts_uuid")),
    )


def downgrade():
    op.drop_table("agent_operation_artifacts")
    op.drop_table("agent_operation_logs")
    op.drop_table("agent_operations")
    op.drop_table("job_schedules")
    op.drop_table("job_actions")
    op.drop_index(op.f("ix_user_sessions_user_id"), table_name="user_sessions")
    op.drop_table("user_sessions")
    op.drop_table("jobs")
    op.drop_table("agent_sessions")
    op.drop_table("agent_repositories")
    op.drop_table("agent_repository_secrets")
    op.drop_table("retentions")
    op.drop_table("repositories")
    op.drop_table("notification_configs")
    op.drop_table("agents")
    op.drop_table("users")
