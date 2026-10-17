"""Move retention to jobs and preserve reports when their source agent is deleted."""

import logging

import sqlalchemy as sa
from alembic import op

revision = "job_retention"
down_revision = "remove_repository_stats"
branch_labels = None
depends_on = None


def agent_foreign_key(connection):
    return next(fk["name"] or "fk_agent_operations_agent_id_agents"
                for fk in sa.inspect(connection).get_foreign_keys("agent_operations")
                if fk["constrained_columns"] == ["agent_id"])


def upgrade():
    with op.batch_alter_table("users") as batch:
        batch.add_column(sa.Column("default_retention_id", sa.Integer(), nullable=True))
        batch.create_foreign_key("fk_users_default_retention", "retentions", ["default_retention_id"], ["id"], ondelete="SET NULL")
    with op.batch_alter_table("jobs") as batch:
        batch.add_column(sa.Column("retention_mode", sa.String(16), nullable=False, server_default="default"))
        batch.add_column(sa.Column("retention_id", sa.Integer(), nullable=True))
        batch.create_foreign_key("fk_jobs_retention", "retentions", ["retention_id"], ["id"], ondelete="SET NULL")
    connection = op.get_bind()
    naming = {"fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s"}
    foreign_key = next(fk for fk in sa.inspect(connection).get_foreign_keys("job_schedules")
                       if fk["constrained_columns"] == ["retention_id"])
    with op.batch_alter_table("job_schedules", naming_convention=naming) as batch:
        batch.drop_constraint(foreign_key["name"] or "fk_job_schedules_retention_id_retentions", type_="foreignkey")
        batch.drop_column("retention_id")
    chains = sa.table("backup_chains", sa.column("id", sa.Integer()), sa.column("steps", sa.JSON()))
    for chain_id, steps in connection.execute(sa.select(chains)):
        connection.execute(chains.update().where(chains.c.id == chain_id).values(
            steps=[{key: value for key, value in step.items() if key != "retention_id"} for step in steps]
        ))
    with op.batch_alter_table("agent_operations", naming_convention=naming) as batch:
        batch.add_column(sa.Column("user_id", sa.Integer(), nullable=True))
        batch.create_foreign_key("fk_agent_operations_user", "users", ["user_id"], ["id"], ondelete="CASCADE")
        batch.create_index("ix_agent_operations_user_repository", ["user_id", "repository_id"])
        batch.drop_constraint(agent_foreign_key(connection), type_="foreignkey")
        batch.alter_column("agent_id", existing_type=sa.Integer(), nullable=True)
        batch.create_foreign_key("fk_agent_operations_agent", "agents", ["agent_id"], ["id"], ondelete="SET NULL")
    connection.execute(sa.text("UPDATE agent_operations SET user_id = (SELECT user_id FROM agents WHERE agents.id = agent_operations.agent_id)"))
    with op.batch_alter_table("agent_operations") as batch:
        batch.alter_column("user_id", existing_type=sa.Integer(), nullable=False)
    with op.batch_alter_table("repositories") as batch:
        batch.add_column(sa.Column("retention_check_token", sa.String(36), nullable=True))
    logging.getLogger("alembic.runtime.migration").warning(
        "Retention now belongs to jobs. Previous schedule/chain assignments were removed. "
        "Select a default retention policy or configure individual jobs; no default is selected automatically."
    )


def downgrade():
    connection = op.get_bind()
    # Refuse before any DDL: MariaDB schema changes cannot be rolled back as a unit.
    if connection.execute(sa.text("SELECT id FROM agent_operations WHERE agent_id IS NULL LIMIT 1")).first():
        raise RuntimeError("Cannot downgrade while reports of deleted agents exist; preserve those reports before rollback")
    with op.batch_alter_table("repositories") as batch:
        batch.drop_column("retention_check_token")
    with op.batch_alter_table("agent_operations") as batch:
        batch.drop_constraint(agent_foreign_key(connection), type_="foreignkey")
        batch.alter_column("agent_id", existing_type=sa.Integer(), nullable=False)
        batch.create_foreign_key("fk_agent_operations_agent", "agents", ["agent_id"], ["id"], ondelete="CASCADE")
        batch.drop_constraint("fk_agent_operations_user", type_="foreignkey")
        batch.drop_index("ix_agent_operations_user_repository")
        batch.drop_column("user_id")
    with op.batch_alter_table("job_schedules") as batch:
        batch.add_column(sa.Column("retention_id", sa.Integer(), nullable=True))
        batch.create_foreign_key("fk_job_schedules_retention", "retentions", ["retention_id"], ["id"], ondelete="SET NULL")
    with op.batch_alter_table("jobs") as batch:
        batch.drop_constraint("fk_jobs_retention", type_="foreignkey")
        batch.drop_column("retention_id")
        batch.drop_column("retention_mode")
    with op.batch_alter_table("users") as batch:
        batch.drop_constraint("fk_users_default_retention", type_="foreignkey")
        batch.drop_column("default_retention_id")
