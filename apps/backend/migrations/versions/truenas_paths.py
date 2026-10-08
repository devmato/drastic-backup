"""Unify TrueNAS selection; parent datasets always include their children."""

import sqlalchemy as sa
from alembic import op

revision = "truenas_paths"
down_revision = "chain_schedules"
branch_labels = None
depends_on = None


def upgrade():
    jobs = sa.table("jobs", sa.column("id", sa.Integer), sa.column("type", sa.String), sa.column("config", sa.JSON))
    connection = op.get_bind()
    for job in connection.execute(sa.select(jobs).where(jobs.c.type == "truenas")).mappings():
        config = (job["config"] or {}).get("data") or {}
        converted = {
            "paths": [{"dataset": name, "path": ".", "group": "dataset"} for name in config.get("datasets", [])],
            "exclude_paths": [{"dataset": name, "path": ".", "group": "dataset"} for name in config.get("exclude_datasets", [])],
            "exclude_patterns": config.get("exclude_patterns", []),
        }
        connection.execute(jobs.update().where(jobs.c.id == job["id"]).values(config={"data": converted}))


def downgrade():
    # A subdirectory selection cannot be represented by the old dataset-only format.
    connection = op.get_bind()
    jobs = sa.table("jobs", sa.column("type", sa.String))
    if connection.execute(sa.select(jobs).where(jobs.c.type == "truenas")).first():
        raise RuntimeError("Remove TrueNAS jobs before downgrading path selection")
