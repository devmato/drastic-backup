"""Replace fixed retention counters with composable account-local rules."""

import sqlalchemy as sa
from alembic import op

revision = "retention_rules"
down_revision = "user_timezone"
branch_labels = None
depends_on = None

COUNTERS = {"keep_last": None, "keep_hourly": "hour", "keep_weekly": "week",
            "keep_monthly": "month", "keep_yearly": "year"}


def upgrade():
    connection = op.get_bind()
    rows = connection.execute(sa.text("SELECT id, keep_last, keep_hourly, keep_weekly, keep_monthly, keep_yearly FROM retentions")).mappings()
    policies = []
    for row in rows:
        rules = [{"type": "last", "count": row[key], **({"period": period} if period else {})}
                 for key, period in COUNTERS.items() if (row[key] or 0) > 0]
        if not rules:
            raise RuntimeError(f"Retention policy {row['id']} keeps no backups; fix it before upgrading")
        policies.append((row["id"], rules))
    with op.batch_alter_table("retentions") as batch:
        batch.add_column(sa.Column("rules", sa.JSON(), nullable=True))
    table = sa.table("retentions", sa.column("id", sa.Integer()), sa.column("rules", sa.JSON()))
    for policy_id, rules in policies:
        connection.execute(table.update().where(table.c.id == policy_id).values(rules=rules))
    with op.batch_alter_table("retentions") as batch:
        batch.alter_column("rules", existing_type=sa.JSON(), nullable=False)
        for key in COUNTERS:
            batch.drop_column(key)


def downgrade():
    if op.get_bind().execute(sa.text("SELECT id FROM retentions LIMIT 1")).first():
        raise RuntimeError("Retention rules cannot be downgraded while policies exist without losing rule and time-zone semantics")
    with op.batch_alter_table("retentions") as batch:
        for key in COUNTERS:
            batch.add_column(sa.Column(key, sa.Integer(), nullable=True))
        batch.drop_column("rules")
