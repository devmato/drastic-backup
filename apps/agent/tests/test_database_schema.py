import configparser
import json
from collections import deque
from concurrent.futures import ThreadPoolExecutor

import dataset
import pytest

import drastic_agent.agent.operation as operation_module
import drastic_agent.jobs.base as job_module
import drastic_agent.runtime.agent as agent_module
import drastic_agent.storage.database as database_module
import drastic_agent.storage.operation_store as store_module
import drastic_agent.storage.snapshot_references as references_module
from drastic_agent.agent.enums import AgentOperationType
from drastic_agent.agent.report import AgentReport
from drastic_agent.jobs.base import BackupJobHandler
from drastic_agent.runtime.agent import Agent, _encode_config_secret
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
    for module in (database_module, agent_module, operation_module, store_module, job_module, references_module):
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
    assert db["agent_schema"].find_one(name="agent")["version"] == 8
    assert {"vmid", "snapshot_name", "owner", "host_id", "confirmed", "created_at"}.issubset(db["proxmox_snapshots"].columns)
    if existing:
        old_job = db["jobs"].find_one(id=7)
        assert old_job["config"] == {"paths": ["/old"]}
        assert old_job["extra"] == "keep me"
        assert db["agent"].find_one(name="existing-setting")["settings"] == "keep me"
        assert list(db.query("PRAGMA table_info(retentions)"))[: len(old_columns)] == old_columns

    schema = list(db.query("SELECT name, sql FROM sqlite_master ORDER BY name"))
    database_module.initialize_schema()
    assert list(db.query("SELECT name, sql FROM sqlite_master ORDER BY name")) == schema
    db["agent"].upsert({"name": "retention_check_failed:1", "settings": "100%"}, ["name"])

    private_key, public_key = generate_agent_keypair()
    agent = Agent.__new__(Agent)
    agent._Agent__config = configparser.ConfigParser()
    agent._Agent__config["AGENT"] = {
        "uuid": "550e8400-e29b-41d4-a716-446655440000",
        "private_key": _encode_config_secret(private_key),
        "public_key": _encode_config_secret(public_key),
    }
    payload = AgentSyncSchema().dump(
        {
            "agent_uuid": "550e8400-e29b-41d4-a716-446655440000",
            "backup_owner_uuid": "00000000-0000-0000-0000-000000000003",
            "schedule_timezone": "Europe/Berlin",
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
                "rules": [{"type": "last", "count": 2}],
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
    requests = []
    monkeypatch.setattr(agent, "_Agent__send_request", lambda action, **kwargs:
                        requests.append(kwargs) or {"success": True, "result": payload})

    def write_from_worker():
        assert agent.cmd_sync().final_state.name == "success"
        assert requests[-1] == {}
        assert agent.cmd_sync().final_state.name == "success"
        assert requests[-1] == {}
        assert db["jobs"].find_one(id=1)["config"] == {"paths": ["/data"]}
        assert db["agent"].find_one(name="backup_owner_uuid")["settings"] == payload["backup_owner_uuid"]
        assert db["agent"].find_one(name="schedule_timezone")["settings"] == "Europe/Berlin"
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
    db["agent"].upsert({"name": "backup_owner_uuid", "settings": "previous-owner"}, ["name"])
    db["agent"].upsert({"name": "schedule_timezone", "settings": "Europe/Berlin"}, ["name"])
    db.query("""
        CREATE TRIGGER reject_new_actions BEFORE INSERT ON job_actions
        WHEN NEW.id = 2 BEGIN SELECT RAISE(ABORT, 'injected sync failure'); END
    """)
    agent = Agent.__new__(Agent)
    agent._Agent__config = configparser.ConfigParser()
    agent._Agent__config["AGENT"] = {"uuid": "550e8400-e29b-41d4-a716-446655440000"}
    payload = {
        name: [{"id": 2}]
        for name in ("repositories", "retentions", "jobs", "schedules", "actions")
    }
    payload["agent_uuid"] = "550e8400-e29b-41d4-a716-446655440000"
    payload["backup_owner_uuid"] = "00000000-0000-0000-0000-000000000003"
    payload["schedule_timezone"] = "America/New_York"
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
    assert db["agent"].find_one(name="backup_owner_uuid")["settings"] == "previous-owner"
    assert db["agent"].find_one(name="schedule_timezone")["settings"] == "Europe/Berlin"


def test_newer_schema_is_rejected_before_modifying_agent_state(local_db):
    database_module.initialize_schema()
    future_version = database_module.CURRENT_SCHEMA_VERSION + 1
    local_db["agent_schema"].update({"name": "agent", "version": future_version}, ["name"])
    local_db["agent"].insert({"name": "keep-setting", "settings": "keep-value"})
    schema = list(local_db.query("SELECT name, sql FROM sqlite_master ORDER BY name"))
    with pytest.raises(RuntimeError, match="newer than supported.*compatible agent"):
        database_module.initialize_schema()
    assert local_db["agent_schema"].find_one(name="agent")["version"] == future_version
    assert local_db["agent"].find_one(name="keep-setting")["settings"] == "keep-value"
    assert list(local_db.query("SELECT name, sql FROM sqlite_master ORDER BY name")) == schema


def test_retention_migration_keeps_local_count_policies_without_a_runtime_fallback(local_db):
    database_module.initialize_schema()
    local_db["agent_schema"].update({"name": "agent", "version": 7}, ["name"])
    local_db["retentions"].insert({"id": 1, "name": "Existing", "keep_last": 2, "keep_weekly": 4})
    database_module.initialize_schema()
    assert local_db["retentions"].find_one(id=1)["rules"] == [
        {"type": "last", "count": 2}, {"type": "last", "count": 4, "period": "week"}]
    local_db["retentions"].update({"id": 1, "rules": [{"type": "recent", "days": 7}]}, ["id"])
    database_module.initialize_schema()
    assert local_db["retentions"].find_one(id=1)["rules"] == [{"type": "recent", "days": 7}]


def test_statistics_migration_preserves_backup_history_and_replay(local_db):
    database_module.initialize_schema()
    local_db["agent_schema"].update({"name": "agent", "version": 5}, ["name"])
    operations = local_db["agent_operations"]
    queue = local_db["agent_operation_queue"]
    artifacts = local_db["agent_operation_artifacts"]
    backup = AgentReport.job_report(job_id=1, repository_id=2, operation_uuid="keep-backup")
    backup.log_message("keep log")
    backup.finish()
    queued_backup = queue.find_one(uuid=backup.uuid)
    artifacts.insert({"operation_id": backup.history_operation["id"], "snapshot_id": "keep-snapshot"})
    for state in ("running", "success"):
        uuid = f"retired-{state}"
        operation_id = operations.insert({"uuid": uuid, "type": "repository_stats", "state": state})
        artifacts.insert({"operation_id": operation_id})
        queue.insert({"uuid": uuid, "status": "finished", "payload": json.dumps({"type": "repository_stats"})})
    queue.insert({"uuid": "orphan-stats", "status": "finished", "payload": json.dumps({"type": "repository_stats"})})
    operations.update({"uuid": backup.uuid, "parent_operation_uuid": "retired-success"}, ["uuid"])

    database_module.initialize_schema()
    database_module.initialize_schema()

    assert [row["uuid"] for row in operations.all()] == [backup.uuid]
    assert operations.find_one(uuid=backup.uuid)["parent_operation_uuid"] is None
    assert list(queue.all()) == [queued_backup]
    assert [row["snapshot_id"] for row in artifacts.all()] == ["keep-snapshot"]
    AgentReport.recover_interrupted()
    restored = store_module.operation_store.load(AgentReport)
    assert [report.uuid for report in restored] == [backup.uuid]
    assert restored[0].log == "keep log"
