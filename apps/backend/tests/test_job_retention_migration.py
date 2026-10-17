"""The retention reset preserves policies, jobs, schedules and backup history."""

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations


def test_retention_migration_preserves_data_and_refuses_lossy_downgrade():
    path = Path(__file__).resolve().parents[1] / "migrations/versions/job_retention.py"
    spec = spec_from_file_location("job_retention_migration", path)
    migration = module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = sa.create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        connection.exec_driver_sql("CREATE TABLE users (id INTEGER PRIMARY KEY)")
        connection.exec_driver_sql("CREATE TABLE agents (id INTEGER PRIMARY KEY, user_id INTEGER REFERENCES users(id))")
        connection.exec_driver_sql("CREATE TABLE repositories (id INTEGER PRIMARY KEY)")
        connection.exec_driver_sql("CREATE TABLE retentions (id INTEGER PRIMARY KEY, user_id INTEGER REFERENCES users(id), keep_last INTEGER)")
        connection.exec_driver_sql("CREATE TABLE jobs (id INTEGER PRIMARY KEY, uuid TEXT)")
        connection.exec_driver_sql("CREATE TABLE job_schedules (id INTEGER PRIMARY KEY, job_id INTEGER REFERENCES jobs(id), retention_id INTEGER REFERENCES retentions(id))")
        connection.exec_driver_sql("CREATE TABLE backup_chains (id INTEGER PRIMARY KEY, steps JSON)")
        connection.exec_driver_sql("CREATE TABLE agent_operations (id INTEGER PRIMARY KEY, agent_id INTEGER NOT NULL REFERENCES agents(id) ON DELETE CASCADE, job_id INTEGER REFERENCES jobs(id), repository_id INTEGER, data JSON)")
        connection.exec_driver_sql("CREATE TABLE agent_operation_artifacts (id INTEGER PRIMARY KEY, operation_id INTEGER REFERENCES agent_operations(id))")
        connection.exec_driver_sql("CREATE TABLE agent_operation_logs (operation_id INTEGER, message TEXT)")
        connection.exec_driver_sql("INSERT INTO users VALUES (1)")
        connection.exec_driver_sql("INSERT INTO agents VALUES (8, 1)")
        connection.exec_driver_sql("INSERT INTO retentions VALUES (2, 1, 5)")
        connection.exec_driver_sql("INSERT INTO jobs VALUES (3, 'job-uuid')")
        connection.exec_driver_sql("INSERT INTO job_schedules VALUES (4, 3, 2)")
        connection.execute(sa.text("INSERT INTO backup_chains VALUES (5, :steps)"), {"steps": '[{"job_id": 3, "repository_id": 6, "retention_id": 2}]'})
        connection.exec_driver_sql("INSERT INTO repositories VALUES (6)")
        connection.exec_driver_sql("INSERT INTO agent_operations VALUES (7, 8, 3, 6, '{}')")
        connection.exec_driver_sql("INSERT INTO agent_operation_artifacts VALUES (9, 7)")
        connection.exec_driver_sql("INSERT INTO agent_operation_logs VALUES (7, 'Repository check failed after backup: damaged pack')")
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()
        assert connection.exec_driver_sql("SELECT default_retention_id FROM users").scalar() is None
        assert connection.exec_driver_sql("SELECT uuid, retention_mode, retention_id FROM jobs").one() == ("job-uuid", "default", None)
        assert connection.exec_driver_sql("SELECT keep_last FROM retentions").scalar() == 5
        assert connection.exec_driver_sql("SELECT id, job_id FROM job_schedules").one() == (4, 3)
        assert "retention_id" not in {column["name"] for column in sa.inspect(connection).get_columns("job_schedules")}
        assert "retention_id" not in connection.exec_driver_sql("SELECT steps FROM backup_chains").scalar()
        assert connection.exec_driver_sql("SELECT id, job_id, data FROM agent_operations").one() == (7, 3, "{}")
        assert connection.exec_driver_sql("SELECT user_id FROM agent_operations").scalar() == 1
        assert connection.exec_driver_sql("SELECT retention_check_token FROM repositories").scalar() is None
        assert connection.exec_driver_sql("SELECT message FROM agent_operation_logs").scalar() == "Repository check failed after backup: damaged pack"
        assert not connection.exec_driver_sql("PRAGMA foreign_key_check").all()
        with Operations.context(MigrationContext.configure(connection)):
            connection.exec_driver_sql("UPDATE agent_operations SET agent_id = NULL")
            with pytest.raises(RuntimeError, match="reports of deleted agents"):
                migration.downgrade()
            assert connection.exec_driver_sql("SELECT user_id FROM agent_operations").scalar() == 1
            assert connection.exec_driver_sql("SELECT retention_check_token FROM repositories").scalar() is None
            connection.exec_driver_sql("UPDATE agent_operations SET agent_id = 8")
            migration.downgrade()
        assert connection.exec_driver_sql("SELECT retention_id FROM job_schedules").scalar() is None
        assert connection.exec_driver_sql("SELECT * FROM agent_operations").one() == (7, 8, 3, 6, "{}")
        assert connection.exec_driver_sql("SELECT * FROM agent_operation_artifacts").one() == (9, 7)
    engine.dispose()
