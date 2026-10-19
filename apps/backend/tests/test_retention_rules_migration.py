"""Fixed counters migrate without losing policies or their assignments."""

import json
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations


def test_retention_rules_migration_preserves_counters_and_assignments():
    path = Path(__file__).resolve().parents[1] / "migrations/versions/retention_rules.py"
    spec = spec_from_file_location("retention_rules_migration", path)
    migration = module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = sa.create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        connection.exec_driver_sql("CREATE TABLE retentions (id INTEGER PRIMARY KEY, name TEXT NOT NULL, keep_last INTEGER, keep_hourly INTEGER, keep_weekly INTEGER, keep_monthly INTEGER, keep_yearly INTEGER)")
        connection.exec_driver_sql("CREATE TABLE jobs (id INTEGER PRIMARY KEY, retention_id INTEGER REFERENCES retentions(id))")
        connection.exec_driver_sql("INSERT INTO retentions VALUES (1, 'Mixed', 2, 3, 4, 5, 6), (2, 'Count', 1, NULL, NULL, NULL, NULL)")
        connection.exec_driver_sql("INSERT INTO jobs VALUES (10, 1)")
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()
            rows = connection.execute(sa.text("SELECT * FROM retentions ORDER BY id")).mappings().all()
            assert set(rows[0]) == {"id", "name", "rules"}
            assert json.loads(rows[0]["rules"]) == [
                {"type": "last", "count": 2}, {"type": "last", "count": 3, "period": "hour"},
                {"type": "last", "count": 4, "period": "week"}, {"type": "last", "count": 5, "period": "month"},
                {"type": "last", "count": 6, "period": "year"}]
            assert json.loads(rows[1]["rules"]) == [{"type": "last", "count": 1}]
            assert connection.exec_driver_sql("SELECT retention_id FROM jobs").scalar() == 1
            assert not connection.exec_driver_sql("PRAGMA foreign_key_check").all()
            with pytest.raises(RuntimeError, match="while policies exist"):
                migration.downgrade()
            connection.exec_driver_sql("DELETE FROM jobs")
            connection.exec_driver_sql("DELETE FROM retentions")
            migration.downgrade()
            migration.upgrade()
    engine.dispose()
