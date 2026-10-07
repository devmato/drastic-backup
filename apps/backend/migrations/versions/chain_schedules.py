"""Give backup chains multiple independent schedules."""

import sqlalchemy as sa
from alembic import op

revision = "chain_schedules"
down_revision = "backup_chains"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("backup_chains", sa.Column("schedules", sa.JSON(), nullable=True))
    chains = sa.table("backup_chains", sa.column("id", sa.Integer()),
                      sa.column("enabled", sa.Boolean()), sa.column("cron_string", sa.String()),
                      sa.column("schedules", sa.JSON()))
    connection = op.get_bind()
    for row in connection.execute(sa.select(chains.c.id, chains.c.enabled, chains.c.cron_string)).mappings().all():
        connection.execute(chains.update().where(chains.c.id == row["id"]).values(
            schedules=[{"enabled": row["enabled"], "cron_string": row["cron_string"]}]))
    with op.batch_alter_table("backup_chains") as batch:
        batch.alter_column("schedules", existing_type=sa.JSON(), nullable=False)
        batch.drop_column("enabled")
        batch.drop_column("cron_string")


def downgrade():
    with op.batch_alter_table("backup_chains") as batch:
        batch.add_column(sa.Column("enabled", sa.Boolean(), nullable=True))
        batch.add_column(sa.Column("cron_string", sa.String(255), nullable=True))
    chains = sa.table("backup_chains", sa.column("id", sa.Integer()),
                      sa.column("enabled", sa.Boolean()), sa.column("cron_string", sa.String()),
                      sa.column("schedules", sa.JSON()))
    connection = op.get_bind()
    for row in connection.execute(sa.select(chains.c.id, chains.c.schedules)).mappings().all():
        schedules = row["schedules"] or []
        # The old schema can represent only one schedule; prefer an active one.
        schedule = next((item for item in schedules if item["enabled"]),
                        schedules[0] if schedules else {"enabled": False, "cron_string": "0 0 * * *"})
        connection.execute(chains.update().where(chains.c.id == row["id"]).values(
            enabled=schedule["enabled"], cron_string=schedule["cron_string"]))
    with op.batch_alter_table("backup_chains") as batch:
        batch.alter_column("enabled", existing_type=sa.Boolean(), nullable=False)
        batch.alter_column("cron_string", existing_type=sa.String(255), nullable=False)
        batch.drop_column("schedules")
