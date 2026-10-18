"""Account time zones are validated, owner-scoped and synchronized after commit."""

from datetime import datetime, timezone
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import sqlalchemy as sa
import test_proxmox_settings_api
from alembic.migration import MigrationContext
from alembic.operations import Operations

from drastic_common.agent.commands import AGENT_PROTOCOL_VERSION
from drastic_server.extensions import db
from drastic_server.models.agent import Agent, AgentSession
from drastic_server.services.agent import AgentService
from drastic_server.services.agent.request import AgentRequestService
from drastic_server.services.jobs import execution

api_client = test_proxmox_settings_api.api_client


def test_user_timezone_is_validated_committed_and_synced_only_to_owned_agents(api_client, monkeypatch):
    client, agent, foreign, _ = api_client
    agent.protocol_version = foreign.protocol_version = AGENT_PROTOCOL_VERSION
    foreign.session = AgentSession(request_sid="foreign-sid")
    offline = Agent(user=agent.user, secret="offline", protocol_version=AGENT_PROTOCOL_VERSION)
    db.session.add(offline)
    db.session.commit()
    assert client.get("/api/user/").json["schedule_timezone"] == "UTC"
    anonymous = client.application.test_client()
    assert anonymous.get("/api/user/timezones").status_code == 401
    assert anonymous.put("/api/user/", json={"schedule_timezone": "Europe/Berlin"}).status_code == 401
    zones = client.get("/api/user/timezones").json["timezones"]
    assert "UTC" in zones and "Europe/Berlin" in zones and "localtime" not in zones
    calls = []

    def sync(target):
        db.session.rollback()  # A missing commit would lose the setting here.
        calls.append((target.id, AgentRequestService(target).sync()["schedule_timezone"]))

    monkeypatch.setattr(AgentService, "sync", sync)
    for invalid in ("", "Mars/Olympus", "../etc/passwd", "localtime", "posix/Europe/Berlin"):
        assert client.put("/api/user/", json={"schedule_timezone": invalid}).status_code == 422
    assert calls == []
    response = client.put("/api/user/", json={"schedule_timezone": "Europe/Berlin"})
    assert response.status_code == 200, response.json
    assert response.json["schedule_timezone"] == "Europe/Berlin"
    assert calls == [(agent.id, "Europe/Berlin")]
    assert AgentRequestService(offline).sync()["schedule_timezone"] == "Europe/Berlin"
    assert foreign.user.schedule_timezone == "UTC"

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 10, 7, 23, tzinfo=timezone.utc).astimezone(tz)

    monkeypatch.setattr(execution, "datetime", Clock)
    preview = client.post("/api/jobs/schedules/preview", json={
        "timing": {"type": "daily", "hour": 2, "minute": 30},
    })
    assert preview.status_code == 200, preview.json
    assert preview.json["timezone"] == "Europe/Berlin"
    assert preview.json["next_run"] == "2026-10-08T02:30:00+02:00"


def test_user_timezone_migration_preserves_users_and_defaults_existing_accounts_to_utc():
    path = Path(__file__).parents[1] / "migrations/versions/user_timezone.py"
    spec = spec_from_file_location("user_timezone_migration", path)
    migration = module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = sa.create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        connection.exec_driver_sql("CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT NOT NULL)")
        connection.exec_driver_sql("INSERT INTO users VALUES (1, 'existing')")
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()
            assert connection.exec_driver_sql("SELECT name, schedule_timezone FROM users").one() == ("existing", "UTC")
            connection.exec_driver_sql("INSERT INTO users (id, name) VALUES (2, 'new')")
            assert connection.exec_driver_sql("SELECT schedule_timezone FROM users WHERE id = 2").scalar() == "UTC"
            migration.downgrade()
        assert connection.exec_driver_sql("SELECT * FROM users ORDER BY id").all() == [(1, "existing"), (2, "new")]
    engine.dispose()
