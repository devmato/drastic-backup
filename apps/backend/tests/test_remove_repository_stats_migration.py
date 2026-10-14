from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations


@pytest.mark.parametrize("foreign_keys", [False, True])
def test_statistics_cleanup_preserves_backups_and_other_subscriptions(foreign_keys):
    path = Path(__file__).resolve().parents[1] / "migrations/versions/remove_repository_stats.py"
    spec = spec_from_file_location("remove_repository_stats_migration", path)
    migration = module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = sa.create_engine("sqlite:///:memory:")
    metadata = sa.MetaData()
    operations = sa.Table(
        "agent_operations", metadata, sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("type", sa.String), sa.Column("data", sa.JSON),
        sa.Column("parent_operation_id", sa.Integer, sa.ForeignKey("agent_operations.id", ondelete="SET NULL")),
    )
    dependents = [sa.Table(
        name, metadata, sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("operation_id", sa.Integer, sa.ForeignKey("agent_operations.id", ondelete="CASCADE")),
    ) for name in ("agent_operation_logs", "agent_operation_artifacts", "notification_deliveries")]
    configs = sa.Table("notification_configs", metadata, sa.Column("id", sa.Integer, primary_key=True),
                       sa.Column("operation_types", sa.JSON))
    with engine.begin() as connection:
        connection.exec_driver_sql(f"PRAGMA foreign_keys = {int(foreign_keys)}")
        metadata.create_all(connection)
        connection.execute(operations.insert(), [
            {"id": 1, "type": "repository_stats", "data": {"stats": {}}, "parent_operation_id": None},
            {"id": 2, "type": "backup", "data": {"keep": True}, "parent_operation_id": 1},
            {"id": 3, "type": "retention", "data": {}, "parent_operation_id": 2},
        ])
        for table in dependents:
            connection.execute(table.insert(), [{"id": 1, "operation_id": 1}, {"id": 2, "operation_id": 2}])
        connection.execute(configs.insert(), [
            {"id": 1, "operation_types": ["repository_stats", "backup", "retention"]},
            {"id": 2, "operation_types": ["repository_stats"]},
            {"id": 3, "operation_types": ["restore"]},
        ])
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()
        rows = connection.execute(sa.select(operations).order_by(operations.c.id)).mappings().all()
        assert [(row["id"], row["parent_operation_id"], row["data"]) for row in rows] == [
            (2, None, {"keep": True}), (3, 2, {}),
        ]
        for table in dependents:
            assert connection.execute(sa.select(table.c.operation_id)).scalars().all() == [2]
        assert connection.execute(sa.select(configs.c.operation_types).order_by(configs.c.id)).scalars().all() == [
            ["backup", "retention"], [], ["restore"],
        ]
    engine.dispose()
