import os

import dataset

from drastic_agent.config import DefaultConfig, env_value


def _agent_data_dir() -> str:
    return env_value("DRASTIC_AGENT_DATA_DIR", DefaultConfig.AGENT_DATA_DIR)


os.makedirs(_agent_data_dir(), exist_ok=True)
db = dataset.connect(
    f"sqlite:///{_agent_data_dir()}/database.db",
    engine_kwargs={"connect_args": {"check_same_thread": False, "timeout": 30}},
    sqlite_wal_mode=True,
    on_connect_statements=[
        "PRAGMA busy_timeout=30000",
        "PRAGMA synchronous=NORMAL",
    ],
)

agent = db["agent"]
repositories = db["repositories"]
repository_secrets = db["repository_secrets"]
retentions = db["retentions"]
jobs = db["jobs"]
actions = db["job_actions"]
schedules = db["job_schedules"]
agent_operations = db["agent_operations"]
agent_operation_artifacts = db["agent_operation_artifacts"]
agent_operation_queue = db["agent_operation_queue"]
agent_schema = db["agent_schema"]
truenas_snapshots = db["truenas_snapshots"]
proxmox_snapshots = db["proxmox_snapshots"]


def initialize_schema():
    """Apply the small, additive agent-local schema migration set."""
    version = agent_schema.find_one(name="agent")
    current = int(version["version"]) if version else 0
    if current < 1:
        # dataset creates the queue table on first insert; this row records the
        # serialization contract independently from application releases.
        agent_schema.upsert({"name": "agent", "version": 1}, ["name"])

    if current < 2:
        db.query(
            """
            CREATE TABLE IF NOT EXISTS schedule_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                schedule_id INTEGER NOT NULL,
                planned_slot TEXT NOT NULL,
                status TEXT NOT NULL,
                reason TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        db.query(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS uq_schedule_runs_schedule_slot
            ON schedule_runs (schedule_id, planned_slot)
            """
        )
        agent_schema.upsert({"name": "agent", "version": 2}, ["name"])

    if current < 3:
        # Prepare all known columns before networking/workers start. Runtime
        # transactions must not rely on dataset's lazy schema creation.
        schemas = {
            "agent": {
                "name": db.types.text,
                "settings": db.types.text,
            },
            "repositories": {
                "kind": db.types.text,
                "location": db.types.text,
                "environment": db.types.json,
                "password_secret_id": db.types.bigint,
                "encrypted_restic_access_key": db.types.json,
            },
            "retentions": {
                "name": db.types.text,
                "keep_last": db.types.bigint,
                "keep_hourly": db.types.bigint,
                "keep_weekly": db.types.bigint,
                "keep_monthly": db.types.bigint,
                "keep_yearly": db.types.bigint,
            },
            "jobs": {
                "uuid": db.types.text,
                "type": db.types.text,
                "config": db.types.json,
            },
            "job_actions": {
                "job_id": db.types.bigint,
                "module": db.types.text,
                "hook": db.types.text,
                "data": db.types.json,
            },
            "job_schedules": {
                "job_id": db.types.bigint,
                "enabled": db.types.boolean,
                "repository_id": db.types.bigint,
                "retention_id": db.types.bigint,
                "cron_string": db.types.text,
                "config": db.types.json,
            },
            "agent_operations": {
                "uuid": db.types.text,
                "type": db.types.text,
                "source": db.types.text,
                "job_id": db.types.bigint,
                "repository_id": db.types.bigint,
                "schedule_id": db.types.bigint,
                "retention_id": db.types.bigint,
                "parent_operation_uuid": db.types.text,
                "state": db.types.text,
                "started": db.types.text,
                "ended": db.types.text,
                "data": db.types.json,
            },
            "agent_operation_artifacts": {
                "uuid": db.types.text,
                "operation_id": db.types.bigint,
                "artifact_key": db.types.text,
                "snapshot_id": db.types.text,
                "state": db.types.text,
                "data": db.types.json,
                "forgotten_at": db.types.text,
            },
            "agent_operation_queue": {
                "uuid": db.types.text,
                "status": db.types.text,
                "payload": db.types.text,
            },
            "truenas_snapshots": {
                "snapshot_id": db.types.text,
                "api_url": db.types.text,
                "host_id": db.types.text,
                "operation_uuid": db.types.text,
                "created_at": db.types.float,
                "confirmed": db.types.boolean,
            },
        }
        for table_name, columns in schemas.items():
            for column_name, column_type in columns.items():
                db[table_name].create_column(column_name, column_type)
        # dataset.upsert() creates these indexes lazily as well.
        db["agent"].create_index(["name"])
        db["agent_operation_queue"].create_index(["uuid"])
        agent_schema.upsert({"name": "agent", "version": 3}, ["name"])

    if current < 4:
        for name, kind in {"vmid": db.types.bigint, "snapshot_name": db.types.text,
                           "owner": db.types.text, "host_id": db.types.text,
                           "confirmed": db.types.boolean, "created_at": db.types.float}.items():
            db["proxmox_snapshots"].create_column(name, kind)
        agent_schema.upsert({"name": "agent", "version": 4}, ["name"])


initialize_schema()

schedule_runs = db["schedule_runs"]
