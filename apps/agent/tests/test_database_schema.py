import configparser
import json
from collections import deque
from concurrent.futures import ThreadPoolExecutor

import dataset
import pytest

import drastic_agent.agent.agent as agent_module
import drastic_agent.agent.database as database_module
import drastic_agent.agent.operation as operation_module
import drastic_agent.agent.operation_store as store_module
import drastic_agent.jobs.base as job_module
from drastic_agent.agent.agent import Agent, _encode_config_secret
from drastic_agent.agent.enums import AgentOperationType
from drastic_agent.agent.report import AgentReport
from drastic_agent.jobs.base import BackupJobHandler
from drastic_common.agent.schemas import AgentSyncSchema
from drastic_common.secret_envelope import encrypt_for_public_key, generate_agent_keypair


@pytest.fixture
def local_db(monkeypatch, tmp_path):
    db = dataset.connect(
        f"sqlite:///{tmp_path}/database.db",
        engine_kwargs={"connect_args": {"check_same_thread": False}},
    )
    monkeypatch.setattr(database_module, "db", db)
    monkeypatch.setattr(database_module, "agent_schema", db["agent_schema"])
    for module in (agent_module, operation_module, store_module, job_module):
        if hasattr(module, "db"):
            monkeypatch.setattr(module, "db", db)
        for name, value in vars(module).copy().items():
            if isinstance(value, dataset.Table):
                monkeypatch.setattr(module, name, db[value.name])
    monkeypatch.setattr(AgentReport, "finished_reports", deque())
    yield db
    db.close()


@pytest.mark.parametrize("existing", [False, True], ids=["fresh", "upgrade-v2"])
@pytest.mark.filterwarnings("error:Changing the database schema:RuntimeWarning")
def test_startup_prepares_schema_for_threaded_writes(local_db, monkeypatch, existing):
    db = local_db
    if existing:
        db["agent_schema"].insert({"name": "agent", "version": 2})
        db.query("""
            CREATE TABLE schedule_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                schedule_id INTEGER NOT NULL,
                planned_slot TEXT NOT NULL,
                status TEXT NOT NULL,
                reason TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)
        db.query("""
            CREATE UNIQUE INDEX uq_schedule_runs_schedule_slot
            ON schedule_runs (schedule_id, planned_slot)
        """)
        # A v2 database may have only the columns its earlier payloads needed.
        db["jobs"].insert({"id": 7, "config": {"paths": ["/old"]}, "extra": "keep me"})
        db["agent"].insert({"name": "existing-setting", "settings": "keep me"})
        db["retentions"].insert({"id": 7, "keep_last": None})
        old_columns = list(db.query("PRAGMA table_info(retentions)"))

    database_module.initialize_schema()
    assert db["agent_schema"].find_one(name="agent")["version"] == 3
    if existing:
        old_job = db["jobs"].find_one(id=7)
        assert old_job["config"] == {"paths": ["/old"]}
        assert old_job["extra"] == "keep me"
        assert db["agent"].find_one(name="existing-setting")["settings"] == "keep me"
        assert list(db.query("PRAGMA table_info(retentions)"))[: len(old_columns)] == old_columns

    schema = list(db.query("SELECT name, sql FROM sqlite_master ORDER BY name"))
    database_module.initialize_schema()
    assert list(db.query("SELECT name, sql FROM sqlite_master ORDER BY name")) == schema

    private_key, public_key = generate_agent_keypair()
    agent = Agent.__new__(Agent)
    agent._Agent__config = configparser.ConfigParser()
    agent._Agent__config["AGENT"] = {
        "private_key": _encode_config_secret(private_key),
        "public_key": _encode_config_secret(public_key),
    }
    payload = AgentSyncSchema().dump(
        {
            "repositories": [{
                "id": 1,
                "kind": "local",
                "location": "/backup",
                "environment": {},
                "password_secret_id": 1,
                "encrypted_restic_access_key": encrypt_for_public_key("password", public_key),
            }],
            "jobs": [{"id": 1, "uuid": "job-1", "type": "file", "config": {"paths": ["/data"]}}],
            "retentions": [{
                "id": 1,
                "name": "daily",
                "keep_last": 2,
                "keep_hourly": None,
                "keep_weekly": None,
                "keep_monthly": None,
                "keep_yearly": None,
            }],
            "actions": [{"id": 1, "job_id": 1, "module": "shell", "hook": "start", "data": {}}],
            "schedules": [{
                "id": 1,
                "job_id": 1,
                "enabled": True,
                "repository_id": 1,
                "retention_id": None,
                "cron_string": "0 0 * * *",
                "config": {},
            }],
        }
    )
    monkeypatch.setattr(
        agent, "_Agent__send_request", lambda action: {"success": True, "result": payload}
    )

    def write_from_worker():
        assert agent.cmd_sync().final_state.name == "success"
        assert db["jobs"].find_one(id=1)["config"] == {"paths": ["/data"]}
        assert db["job_actions"].find_one(id=1)["data"] == {}
        assert agent._Agent__repository_passwords == {1: "password"}

        settings_report = agent.cmd_update_proxmox_settings(
            {
                "api_url": "https://pve.test:8006/api2/json",
                "token_id": "root@pam!backup",
                "node": "pve",
                "verify_tls": True,
            },
            encrypt_for_public_key("token-secret", public_key),
        )
        assert settings_report.final_state.name == "success"
        assert agent.get_proxmox_client().token_secret == "token-secret"

        report = AgentReport(type=AgentOperationType.backup, job_id=1, repository_id=1)
        handler = BackupJobHandler(agent=agent, job={"id": 1}, repository_id=1)
        handler.operation = report.history_operation
        artifact = handler.start_artifact("default", data={"paths": ["/data"]})
        handler.finish_artifact(artifact, snapshot_id="snapshot-1")
        report.set_data("bytes_processed", 42)
        report.finish()
        assert db["agent_operations"].find_one(uuid=report.uuid)["data"]["bytes_processed"] == 42
        queued = db["agent_operation_queue"].find_one(uuid=report.uuid)
        assert json.loads(queued["payload"])["final_state"] == "success"

        with db:
            db["truenas_snapshots"].insert(
                {
                    "snapshot_id": "pool/data@backup",
                    "api_url": "wss://nas.test/api/current",
                    "host_id": "nas-1",
                    "operation_uuid": report.uuid,
                    "created_at": 123.5,
                    "confirmed": False,
                }
            )
        assert db["truenas_snapshots"].find_one(host_id="nas-1")["confirmed"] is False

    with ThreadPoolExecutor(max_workers=1) as executor:
        executor.submit(write_from_worker).result()
    assert list(db.query("SELECT name, sql FROM sqlite_master ORDER BY name")) == schema


@pytest.mark.filterwarnings("error:Changing the database schema:RuntimeWarning")
def test_sync_failure_keeps_previous_data_after_migration(local_db, monkeypatch):
    db = local_db
    database_module.initialize_schema()
    tables = ("repositories", "retentions", "jobs", "job_schedules", "job_actions")
    for name in tables:
        db[name].insert({"id": 1})
    db["repository_secrets"].insert({"repository_id": 1, "password": "legacy"})
    db.query("""
        CREATE TRIGGER reject_new_actions BEFORE INSERT ON job_actions
        WHEN NEW.id = 2 BEGIN SELECT RAISE(ABORT, 'injected sync failure'); END
    """)
    agent = Agent.__new__(Agent)
    payload = {
        name: [{"id": 2}]
        for name in ("repositories", "retentions", "jobs", "schedules", "actions")
    }
    monkeypatch.setattr(
        agent, "_Agent__send_request", lambda action: {"success": True, "result": payload}
    )

    with ThreadPoolExecutor(max_workers=1) as executor:
        report = executor.submit(agent.cmd_sync).result()

    assert report.final_state.name == "failed"
    assert "injected sync failure" in report.log
    for name in tables:
        assert [row["id"] for row in db[name].all()] == [1]
    assert db["repository_secrets"].find_one(repository_id=1)["password"] == "legacy"
