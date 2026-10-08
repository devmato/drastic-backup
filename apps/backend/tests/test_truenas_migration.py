import importlib.util
from pathlib import Path

import sqlalchemy as sa


def test_migration_preserves_selections_and_exclusions(monkeypatch):
    spec = importlib.util.spec_from_file_location("truenas_paths", Path(__file__).parents[1] / "migrations/versions/truenas_paths.py")
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = sa.create_engine("sqlite://")
    metadata = sa.MetaData()
    jobs = sa.Table("jobs", metadata, sa.Column("id", sa.Integer, primary_key=True),
                    sa.Column("type", sa.String), sa.Column("config", sa.JSON))
    metadata.create_all(engine)
    with engine.begin() as connection:
        monkeypatch.setattr(migration.op, "get_bind", lambda: connection)
        for index, children in enumerate([None, False, True], 1):
            config = {"datasets": ["tank", "tank/data"], "exclude_datasets": ["tank/cache"], "exclude_patterns": ["*.tmp"]}
            if children is not None:
                config["include_children"] = children
            connection.execute(jobs.insert().values(id=index, type="truenas", config={"data": config}))
        connection.execute(jobs.insert().values(id=4, type="file", config={"data": {"paths": []}}))
        migration.upgrade()
        rows = connection.execute(sa.select(jobs).order_by(jobs.c.id)).mappings().all()
        for row in rows[:3]:
            assert row["config"]["data"] == {
                "paths": [{"dataset": name, "path": ".", "group": "dataset"} for name in ["tank", "tank/data"]],
                "exclude_paths": [{"dataset": "tank/cache", "path": ".", "group": "dataset"}],
                "exclude_patterns": ["*.tmp"],
            }
        assert rows[3]["config"] == {"data": {"paths": []}}
