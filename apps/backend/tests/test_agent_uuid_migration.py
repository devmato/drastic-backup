from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from uuid import UUID

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations


def test_agent_uuid_backfill_preserves_references_and_enforces_unique_identity():
    path = Path(__file__).resolve().parents[1] / "migrations/versions/agent_uuid.py"
    spec = spec_from_file_location("agent_uuid_migration", path)
    migration = module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = sa.create_engine("sqlite:///:memory:")
    metadata = sa.MetaData()
    agents = sa.Table("agents", metadata, sa.Column("id", sa.Integer, primary_key=True))
    jobs = sa.Table("jobs", metadata, sa.Column("id", sa.Integer, primary_key=True),
                    sa.Column("agent_id", sa.Integer, sa.ForeignKey("agents.id")))
    metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(agents.insert(), [{"id": 2}, {"id": 7}])
        connection.execute(jobs.insert().values(id=12, agent_id=2))
        context = MigrationContext.configure(connection)
        with Operations.context(context):
            migration.upgrade()
        current = sa.Table("agents", sa.MetaData(), autoload_with=connection)
        rows = connection.execute(sa.select(current).order_by(current.c.id)).mappings().all()
        assert [row["id"] for row in rows] == [2, 7]
        assert len({row["uuid"] for row in rows}) == 2
        assert all(str(UUID(row["uuid"])) == row["uuid"] and UUID(row["uuid"]).version == 4 for row in rows)
        assert connection.execute(sa.select(jobs.c.agent_id)).scalar_one() == 2
        for invalid in (None, rows[0]["uuid"]):
            with pytest.raises(sa.exc.IntegrityError):
                with connection.begin_nested():
                    connection.execute(current.insert().values(id=9, uuid=invalid))
        with Operations.context(context):
            migration.downgrade()
        assert [column["name"] for column in sa.inspect(connection).get_columns("agents")] == ["id"]
        assert connection.execute(sa.select(jobs.c.agent_id)).scalar_one() == 2
    engine.dispose()
