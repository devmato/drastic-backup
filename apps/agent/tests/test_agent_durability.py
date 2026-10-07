import configparser
from concurrent.futures import Future
from datetime import datetime, timedelta, timezone

import dataset

import drastic_agent.agent.agent as agent_module
from drastic_agent.agent.agent import Agent


def build_agent():
    agent = Agent.__new__(Agent)
    agent.identifier = "agent-17"
    agent.client = None
    agent._Agent__secret = "secret-17"
    agent._Agent__server = "http://server.test"
    agent._Agent__config = configparser.ConfigParser()
    agent._Agent__config["AGENT"] = {"private_key": "private-key", "public_key": "public-key"}
    agent._Agent__repository_passwords = {}
    agent._Agent__secret_values = {}
    return agent


def connect_database(tmp_path):
    return dataset.connect(
        f"sqlite:///{tmp_path}/database.db",
        engine_kwargs={"connect_args": {"check_same_thread": False}},
    )


def test_sync_rolls_back_all_tables_on_write_failure(monkeypatch, tmp_path):
    test_db = connect_database(tmp_path)
    table_names = {
        "repositories": "repositories",
        "repository_secrets": "repository_secrets",
        "retentions": "retentions",
        "jobs": "jobs",
        "schedules": "job_schedules",
        "actions": "job_actions",
    }
    tables = {name: test_db[table_name] for name, table_name in table_names.items()}
    tables["repositories"].insert(
        {
            "id": 1,
            "location": "old-repository",
            "environment": {},
            "encrypted_restic_access_key": {"old": True},
        },
        types={
            "environment": test_db.types.json,
            "encrypted_restic_access_key": test_db.types.json,
        },
    )
    tables["repository_secrets"].insert({"repository_id": 1, "password": "legacy"})
    tables["retentions"].insert({"id": 1, "name": "old-retention"})
    tables["jobs"].insert(
        {"id": 1, "name": "old-job", "config": {}}, types={"config": test_db.types.json}
    )
    tables["schedules"].insert(
        {
            "id": 1,
            "job_id": 1,
            "repository_id": 1,
            "cron_string": "0 0 * * *",
            "config": {},
        },
        types={"config": test_db.types.json},
    )
    tables["actions"].insert({"id": 1, "job_id": 1, "hook": "old"})
    test_db.query(
        """
        CREATE TRIGGER reject_new_actions BEFORE INSERT ON job_actions
        WHEN NEW.id = 2 BEGIN SELECT RAISE(ABORT, 'injected sync failure'); END
        """
    )

    monkeypatch.setattr(agent_module, "db", test_db)
    for name, table in tables.items():
        monkeypatch.setattr(agent_module, name, table)

    agent = build_agent()
    monkeypatch.setattr(
        agent,
        "_Agent__send_request",
        lambda action: {
            "success": True,
            "result": {
                "repositories": [
                    {
                        "id": 2,
                        "location": "new-repository",
                        "environment": {},
                        "encrypted_restic_access_key": {"new": True},
                        "encrypted_recovery_key": {"must": "not persist"},
                    }
                ],
                "retentions": [{"id": 2, "name": "new-retention"}],
                "jobs": [{"id": 2, "name": "new-job", "config": {}}],
                "schedules": [
                    {
                        "id": 2,
                        "job_id": 2,
                        "repository_id": 2,
                        "cron_string": "* * * * *",
                        "config": {},
                    }
                ],
                "actions": [{"id": 2, "job_id": 2, "hook": "start"}],
            },
        },
    )

    report = Agent.cmd_sync(agent)

    assert report.final_state.name == "failed"
    assert [row["id"] for row in tables["repositories"].all()] == [1]
    assert [row["repository_id"] for row in tables["repository_secrets"].all()] == [1]
    assert [row["id"] for row in tables["retentions"].all()] == [1]
    assert [row["id"] for row in tables["jobs"].all()] == [1]
    assert [row["id"] for row in tables["schedules"].all()] == [1]
    assert [row["id"] for row in tables["actions"].all()] == [1]


def test_persisted_agent_key_unlocks_repository_after_restart(monkeypatch, tmp_path):
    test_db = connect_database(tmp_path)
    repository_table = test_db["repositories"]
    repository_table.insert(
        {
            "id": 7,
            "location": "s3:s3.example.test/bucket",
            "environment": {"AWS_DEFAULT_REGION": "eu-central-1"},
            "encrypted_restic_access_key": {"sealed": "agent-password"},
        },
        types={
            "environment": test_db.types.json,
            "encrypted_restic_access_key": test_db.types.json,
        },
    )
    configured = []

    class FakeResticApi:
        def set_repository(self, repository):
            configured.append(repository)

    restarted_agent = build_agent()
    restarted_agent._Agent__resticapi = FakeResticApi()
    monkeypatch.setattr(agent_module, "repositories", repository_table)
    monkeypatch.setattr(
        agent_module,
        "decrypt_with_private_key",
        lambda envelope, private_key: (
            "agent-password"
            if envelope == {"sealed": "agent-password"} and private_key == "private-key"
            else None
        ),
    )
    monkeypatch.setattr(
        restarted_agent,
        "_Agent__send_request",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("backend must not be used")),
    )

    Agent._Agent__set_repository(restarted_agent, 7)

    assert configured[0].password == "agent-password"
    assert restarted_agent._Agent__repository_passwords == {7: "agent-password"}


def create_schedule_runs_table(test_db):
    test_db.query(
        """
        CREATE TABLE schedule_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            schedule_id INTEGER NOT NULL,
            planned_slot TEXT NOT NULL,
            status TEXT NOT NULL,
            reason TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE (schedule_id, planned_slot)
        )
        """
    )
    return test_db["schedule_runs"]


class Schedules:
    def __init__(self, schedule):
        self.schedule = schedule

    def find(self, **kwargs):
        return [self.schedule] if kwargs == {"job_id": self.schedule["job_id"]} else []


class ImmediateExecution:
    def submit(self, target, *args, **kwargs):
        kwargs.pop("resources")
        target(*args, **kwargs)
        return object()


def test_scheduler_restart_deduplicates_persisted_utc_slot(monkeypatch, tmp_path):
    schedule_runs = create_schedule_runs_table(connect_database(tmp_path))
    schedule = {
        "id": 41,
        "job_id": 4,
        "repository_id": 9,
        "cron_string": "* * * * *",
        "config": {},
    }
    started = []
    monkeypatch.setattr(agent_module, "schedule_runs", schedule_runs)
    monkeypatch.setattr(agent_module, "jobs", [{"id": 4}])
    monkeypatch.setattr(agent_module, "schedules", Schedules(schedule))

    for _ in range(2):
        restarted_agent = build_agent()
        restarted_agent._Agent__execution = ImmediateExecution()
        restarted_agent.cmd_run_job = lambda *args, **kwargs: started.append((args, kwargs))
        Agent._Agent__run_due_schedules(
            restarted_agent, datetime(2026, 7, 23, 12, 5, 30, tzinfo=timezone.utc)
        )

    rows = list(schedule_runs.all())
    assert len(started) == 1
    assert len(rows) == 1
    assert rows[0]["planned_slot"] == "2026-07-23T12:05:00+00:00"
    assert rows[0]["status"] == "started"


def test_once_schedule_is_not_replayed_after_restart_or_history_cleanup(monkeypatch, tmp_path):
    runs = create_schedule_runs_table(connect_database(tmp_path))
    schedule = {"id": 41, "job_id": 4, "repository_id": 9, "cron_string": "",
                "config": {"timing": {"type": "once", "date": "2026-07-23", "hour": 12, "minute": 5}}}
    started = []
    monkeypatch.setattr(agent_module, "schedule_runs", runs)
    monkeypatch.setattr(agent_module, "jobs", [{"id": 4}])
    monkeypatch.setattr(agent_module, "schedules", Schedules(schedule))
    now = datetime(2026, 7, 23, 12, 5)
    for _ in range(2):
        agent = build_agent()
        agent._Agent__execution = ImmediateExecution()
        agent.cmd_run_job = lambda *args, **kwargs: started.append(1)
        agent._Agent__run_due_schedules(now)
        agent._Agent__recover_schedule_runs()
        agent._Agent__prune_schedule_runs(datetime(2030, 1, 1, tzinfo=timezone.utc))
    assert started == [1]
    assert runs.find_one(schedule_id=41)["planned_slot"].startswith('once:')
    agent._Agent__run_due_schedules(now + timedelta(days=1))
    assert started == [1]


def test_arbitrary_periodic_schedule_survives_agent_restart(monkeypatch, tmp_path):
    runs = create_schedule_runs_table(connect_database(tmp_path))
    schedule = {"id": 42, "job_id": 4, "repository_id": 9, "cron_string": "",
                "config": {"timing": {"type": "periodic", "interval": 90, "offset": 5}}}
    started = []
    monkeypatch.setattr(agent_module, "schedule_runs", runs)
    monkeypatch.setattr(agent_module, "jobs", [{"id": 4}])
    monkeypatch.setattr(agent_module, "schedules", Schedules(schedule))
    for at in (datetime(2026, 10, 7, 1, 35, tzinfo=timezone.utc), datetime(2026, 10, 7, 2, 5, tzinfo=timezone.utc),
               datetime(2026, 10, 7, 3, 5, tzinfo=timezone.utc)):
        agent = build_agent()
        agent._Agent__execution = ImmediateExecution()
        agent.cmd_run_job = lambda *args, **kwargs: started.append(1)
        agent._Agent__run_due_schedules(at)
    assert len(started) == 2


def test_scheduler_persists_rejected_slot_as_skipped(monkeypatch, tmp_path):
    schedule_runs = create_schedule_runs_table(connect_database(tmp_path))
    schedule = {
        "id": 42,
        "job_id": 4,
        "repository_id": 9,
        "cron_string": "* * * * *",
        "config": {},
    }

    class RejectingExecution:
        @staticmethod
        def submit(*args, **kwargs):
            return None

    monkeypatch.setattr(agent_module, "schedule_runs", schedule_runs)
    monkeypatch.setattr(agent_module, "jobs", [{"id": 4}])
    monkeypatch.setattr(agent_module, "schedules", Schedules(schedule))
    agent = build_agent()
    agent._Agent__execution = RejectingExecution()

    Agent._Agent__run_due_schedules(agent, datetime(2026, 7, 23, 12, 6, tzinfo=timezone.utc))

    row = schedule_runs.find_one(schedule_id=42)
    assert row["status"] == "skipped"
    assert row["reason"] == "execution capacity or requested resource is busy"


def test_scheduler_marks_completed_future_finished(monkeypatch, tmp_path):
    schedule_runs = create_schedule_runs_table(connect_database(tmp_path))
    schedule = {
        "id": 43,
        "job_id": 4,
        "repository_id": 9,
        "cron_string": "* * * * *",
        "config": {},
    }

    class CompletedExecution:
        @staticmethod
        def submit(*args, **kwargs):
            future = Future()
            future.set_result(None)
            return future

    monkeypatch.setattr(agent_module, "schedule_runs", schedule_runs)
    monkeypatch.setattr(agent_module, "jobs", [{"id": 4}])
    monkeypatch.setattr(agent_module, "schedules", Schedules(schedule))
    agent = build_agent()
    agent._Agent__execution = CompletedExecution()

    Agent._Agent__run_due_schedules(agent, datetime(2026, 7, 23, 12, 7, tzinfo=timezone.utc))

    assert schedule_runs.find_one(schedule_id=43)["status"] == "finished"


def test_scheduler_marks_future_exception_failed(monkeypatch, tmp_path, caplog):
    schedule_runs = create_schedule_runs_table(connect_database(tmp_path))
    schedule = {
        "id": 44,
        "job_id": 4,
        "repository_id": 9,
        "cron_string": "* * * * *",
        "config": {},
    }

    class FailedExecution:
        @staticmethod
        def submit(*args, **kwargs):
            future = Future()
            future.set_exception(RuntimeError("scheduled boom"))
            return future

    monkeypatch.setattr(agent_module, "schedule_runs", schedule_runs)
    monkeypatch.setattr(agent_module, "jobs", [{"id": 4}])
    monkeypatch.setattr(agent_module, "schedules", Schedules(schedule))
    agent = build_agent()
    agent._Agent__execution = FailedExecution()

    Agent._Agent__run_due_schedules(agent, datetime(2026, 7, 23, 12, 8, tzinfo=timezone.utc))

    row = schedule_runs.find_one(schedule_id=44)
    assert row["status"] == "failed"
    assert row["reason"] == "scheduled boom"
    assert "Scheduled operation failed" in caplog.text


def test_scheduler_recovery_fails_interrupted_slots(monkeypatch, tmp_path):
    schedule_runs = create_schedule_runs_table(connect_database(tmp_path))
    created_at = datetime.now(timezone.utc).isoformat()
    started_id = schedule_runs.insert(
        {
            "schedule_id": 45,
            "planned_slot": "2026-07-23T12:09:00+00:00",
            "status": "started",
            "reason": None,
            "created_at": created_at,
            "updated_at": created_at,
        }
    )
    finished_id = schedule_runs.insert(
        {
            "schedule_id": 46,
            "planned_slot": "2026-07-23T12:09:00+00:00",
            "status": "finished",
            "reason": None,
            "created_at": created_at,
            "updated_at": created_at,
        }
    )
    monkeypatch.setattr(agent_module, "schedule_runs", schedule_runs)

    Agent._Agent__recover_schedule_runs(build_agent())

    interrupted = schedule_runs.find_one(id=started_id)
    assert interrupted["status"] == "failed"
    assert interrupted["reason"] == "agent stopped before scheduled execution completed"
    assert schedule_runs.find_one(id=finished_id)["status"] == "finished"


def test_schedule_run_pruning_removes_only_terminal_rows_older_than_90_days(
    monkeypatch, tmp_path
):
    schedule_runs = create_schedule_runs_table(connect_database(tmp_path))
    now = datetime(2026, 7, 23, 12, tzinfo=timezone.utc)

    def insert_run(schedule_id, status, updated_at):
        timestamp = updated_at.isoformat()
        return schedule_runs.insert(
            {
                "schedule_id": schedule_id,
                "planned_slot": timestamp,
                "status": status,
                "reason": None,
                "created_at": timestamp,
                "updated_at": timestamp,
            }
        )

    old_terminal_ids = {
        insert_run(index, status, now - timedelta(days=91))
        for index, status in enumerate(("finished", "failed", "skipped"), start=1)
    }
    retained_ids = {
        insert_run(10, "finished", now - timedelta(days=90)),
        insert_run(11, "failed", now - timedelta(days=1)),
        insert_run(12, "claimed", now - timedelta(days=120)),
        insert_run(13, "started", now - timedelta(days=120)),
    }
    monkeypatch.setattr(agent_module, "schedule_runs", schedule_runs)

    Agent._Agent__prune_schedule_runs(build_agent(), now)

    remaining_ids = {row["id"] for row in schedule_runs.all()}
    assert remaining_ids == retained_ids
    assert remaining_ids.isdisjoint(old_terminal_ids)
