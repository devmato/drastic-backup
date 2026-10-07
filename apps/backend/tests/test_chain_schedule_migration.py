from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations


def test_existing_chain_schedule_activation_and_configuration_survive_migration():
    path = Path(__file__).resolve().parents[1] / "migrations/versions/chain_schedules.py"
    spec = spec_from_file_location("chain_schedules_migration", path)
    migration = module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = sa.create_engine("sqlite:///:memory:")
    metadata = sa.MetaData()
    old = sa.Table("backup_chains", metadata,
                   sa.Column("id", sa.Integer(), primary_key=True),
                   sa.Column("name", sa.String(255), nullable=False),
                   sa.Column("enabled", sa.Boolean(), nullable=False),
                   sa.Column("cron_string", sa.String(255), nullable=False),
                   sa.Column("steps", sa.JSON(), nullable=False))
    runs = sa.Table("backup_chain_runs", metadata,
                    sa.Column("id", sa.Integer(), primary_key=True),
                    sa.Column("chain_id", sa.Integer(), sa.ForeignKey("backup_chains.id")),
                    sa.Column("state", sa.String(32)), sa.Column("steps", sa.JSON()))
    metadata.create_all(engine)
    steps = [{"job_id": 17, "repository_id": 27, "retention_id": 7}]
    with engine.begin() as connection:
        connection.execute(old.insert(), [
            {"id": 1, "name": "Active", "enabled": True, "cron_string": "0 2 * * 1,5", "steps": steps},
            {"id": 2, "name": "Disabled", "enabled": False, "cron_string": "30 3 * * *", "steps": steps},
        ])
        connection.execute(runs.insert().values(id=42, chain_id=1, state="running", steps=steps))
        context = MigrationContext.configure(connection)
        with Operations.context(context):
            migration.upgrade()
        current = sa.Table("backup_chains", sa.MetaData(), autoload_with=connection)
        rows = connection.execute(sa.select(current).order_by(current.c.id)).mappings().all()
        assert rows[0]["schedules"] == [{"enabled": True, "cron_string": "0 2 * * 1,5"}]
        assert rows[1]["schedules"] == [{"enabled": False, "cron_string": "30 3 * * *"}]
        assert rows[0]["steps"] == rows[1]["steps"] == steps
        assert "enabled" not in current.c and "cron_string" not in current.c
        active = connection.execute(sa.select(runs)).mappings().one()
        assert active["chain_id"] == 1 and active["state"] == "running" and active["steps"] == steps
        connection.execute(current.update().where(current.c.id == 1).values(schedules=[
            {"enabled": False, "cron_string": "0 2 * * 1,5"},
            {"enabled": True, "cron_string": "15 4 * * *"},
        ]))
        connection.execute(current.insert().values(id=3, name="Manual", schedules=[], steps=steps))
        with Operations.context(context):
            migration.downgrade()
        legacy = sa.Table("backup_chains", sa.MetaData(), autoload_with=connection)
        rows = connection.execute(sa.select(legacy).order_by(legacy.c.id)).mappings().all()
        assert (rows[0]["enabled"], rows[0]["cron_string"]) == (True, "15 4 * * *")
        assert (rows[1]["enabled"], rows[1]["cron_string"]) == (False, "30 3 * * *")
        assert rows[2]["enabled"] is False
        assert all(row["steps"] == steps for row in rows)
    engine.dispose()
