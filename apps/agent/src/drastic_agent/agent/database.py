import os

import dataset


def _agent_data_dir() -> str:
    return os.environ.get("DRASTIC_AGENT_DATA_DIR", "/app/data")


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


initialize_schema()

schedule_runs = db["schedule_runs"]
