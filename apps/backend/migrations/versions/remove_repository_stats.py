"""Remove standalone repository statistics and their notification subscriptions."""

import sqlalchemy as sa
from alembic import op

revision = "remove_repository_stats"
down_revision = "backup_retries"
branch_labels = None
depends_on = None


def upgrade():
    connection = op.get_bind()
    retired_ids = "SELECT id FROM agent_operations WHERE type = 'repository_stats'"
    # SQLite installations may not enforce foreign keys, so clean dependents explicitly.
    for name in ("agent_operation_logs", "agent_operation_artifacts", "notification_deliveries"):
        op.execute(f"DELETE FROM {name} WHERE operation_id IN ({retired_ids})")
    op.execute(f"UPDATE agent_operations SET parent_operation_id = NULL WHERE parent_operation_id IN ({retired_ids})")
    op.execute("DELETE FROM agent_operations WHERE type = 'repository_stats'")
    configs = sa.table(
        "notification_configs", sa.column("id", sa.Integer()), sa.column("operation_types", sa.JSON())
    )
    for config_id, types in connection.execute(sa.select(configs)):
        if "repository_stats" in (types or []):
            connection.execute(configs.update().where(configs.c.id == config_id).values(
                operation_types=[kind for kind in types if kind != "repository_stats"]
            ))


def downgrade():
    # Deleted statistics reports and subscriptions cannot be reconstructed.
    pass
