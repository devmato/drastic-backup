import json
from types import SimpleNamespace

import dataset
import pytest

import drastic_agent.jobs.base as base_module
import drastic_agent.runtime.agent as agent_module
from drastic_agent.agent.enums import AgentReportState, AgentReportType
from drastic_agent.agent.exceptions import AgentExeption
from drastic_agent.agent.report import AgentReport
from drastic_agent.runtime.agent import Agent
from drastic_agent.services.retention import RetentionService
from drastic_common.restic.exceptions import ResticError


@pytest.fixture(autouse=True)
def retention_settings(monkeypatch, tmp_path):
    db = dataset.connect(f"sqlite:///{tmp_path}/retention.db")
    table = db["agent"]
    table.create_column("name", db.types.text)
    table.create_column("settings", db.types.text)
    monkeypatch.setattr(agent_module, "agent_settings", table)
    yield table
    db.engine.dispose()


class FakeTable:
    def __init__(self, row):
        self.row = row

    def find_one(self, **kwargs):
        return self.row


class FakeResticApi:
    def __init__(self, result=None):
        self.result = result or []
        self.calls = []
        self.prune_error = None

    def forget(self, **kwargs):
        self.calls.append(kwargs)
        return self.result

    def forget_snapshots(self, snapshot_ids, prune=True):
        self.calls.append({"snapshot_ids": snapshot_ids, "prune": prune})
        return self.result

    def snapshots(self):
        return [
            {
                "id": "snap-new",
                "tags": ["operation_uuid:operation-new", "artifact_uuid:artifact-new"],
            },
            {
                "id": "snap-old",
                "tags": ["operation_uuid:operation-old", "artifact_uuid:artifact-old"],
            },
        ]

    def prune(self):
        self.calls.append({"prune": True})
        if self.prune_error:
            error = self.prune_error
            self.prune_error = None
            raise error
        return {"removed": 1}


class FakeRunsTable:
    def __init__(self):
        self.rows = [
            {"id": 1, "uuid": "operation-new", "type": "backup", "job_id": 7, "repository_id": 1, "state": "success", "ended": "2026-05-10T03:00:00"},
            {"id": 2, "uuid": "operation-old", "type": "backup", "job_id": 7, "repository_id": 1, "state": "success", "ended": "2026-05-10T02:00:00"},
        ]

    def find(self, **kwargs):
        return [
            row
            for row in self.rows
            if all(row.get(key) == value for key, value in kwargs.items())
        ]


class FakeArtifactsTable:
    def __init__(self):
        self.rows = [
            {"id": 1, "uuid": "artifact-new", "operation_id": 1, "snapshot_id": "snap-new", "forgotten_at": None},
            {"id": 2, "uuid": "artifact-old", "operation_id": 2, "snapshot_id": "snap-old", "forgotten_at": None},
        ]

    def find(self, **kwargs):
        return [
            row
            for row in self.rows
            if all(row.get(key) == value for key, value in kwargs.items())
        ]

    def update(self, row, keys):
        for index, existing in enumerate(self.rows):
            if all(existing[key] == row[key] for key in keys):
                self.rows[index] = dict(row)
                return


def test_cmd_run_retention_prunes_old_operation_artifacts(monkeypatch):
    agent = Agent.__new__(Agent)
    resticapi = FakeResticApi(result=[{"remove": [{"id": "snap-1"}]}])
    artifact_table = FakeArtifactsTable()

    monkeypatch.setattr(
        agent_module,
        "retentions",
        FakeTable(
            {
                "id": 2,
                "name": "Daily",
                "keep_last": 1,
                "keep_hourly": None,
                "keep_weekly": None,
                "keep_monthly": None,
                "keep_yearly": None,
            }
        ),
    )
    monkeypatch.setattr(agent_module, "agent_operations", FakeRunsTable())
    monkeypatch.setattr(agent_module, "agent_operation_artifacts", artifact_table)
    agent._Agent__resticapi = resticapi
    agent._Agent__set_repository = lambda repository_id: {"id": repository_id, "location": "/repo"}

    report = Agent.cmd_run_retention(agent, repository_id=1, retention_id=2, job_id=7)

    assert resticapi.calls == [
        {"snapshot_ids": ["snap-old"], "prune": False},
        {"prune": True},
    ]
    assert report.data["kept_operation_ids"] == [1]
    assert report.data["pruned_operation_ids"] == [2]
    assert report.data["snapshot_ids"] == ["snap-old"]
    assert artifact_table.rows[1]["forgotten_at"] is not None
    assert report.final_state == AgentReportState.success
    assert any("Removed 1 snapshots: snap-old" in line for line in report.log_list)

    resticapi.calls.clear()
    report = Agent.cmd_run_retention(agent, repository_id=1, retention_id=2, job_id=7)
    assert report.final_state == AgentReportState.success
    assert resticapi.calls == []


def test_pending_retention_recovers_after_forget_before_local_commit(tmp_path, monkeypatch):
    from drastic_agent.services import retention as retention_module

    database = dataset.connect(f"sqlite:///{tmp_path}/cleanup.db")
    settings = database["agent"]
    settings.create_column("name", database.types.text)
    settings.create_column("settings", database.types.text)
    resticapi = FakeResticApi()
    artifact_table = FakeArtifactsTable()
    snapshots = resticapi.snapshots()
    calls = []

    def forget(snapshot_ids, prune=False):
        calls.append(list(snapshot_ids))
        snapshots[:] = [item for item in snapshots if item["id"] not in snapshot_ids]

    resticapi.forget_snapshots = forget
    resticapi.snapshots = lambda: snapshots
    resticapi.check = lambda: calls.append("check")
    artifact_table.update = lambda *args: (_ for _ in ()).throw(RuntimeError("agent stopped after forget"))
    args = dict(repository_id=1, retention_id=2, job_id=7, current_operation_uuid="operation-new",
                set_repository=lambda repository_id: {"location": "/repo"}, resticapi=resticapi,
                retentions_table=FakeTable({"id": 2, "name": "Keep one", "keep_last": 1}),
                operations_table=FakeRunsTable(), operation_artifacts_table=artifact_table, settings_table=settings)
    monkeypatch.setattr(retention_module, "time", lambda: 1000)
    first = RetentionService.run(**args)
    assert first.final_state == AgentReportState.failed
    assert settings.find_one(name="retention_task:1:7")
    database.engine.dispose()
    reopened = dataset.connect(f"sqlite:///{tmp_path}/cleanup.db")
    args["settings_table"] = reopened["agent"]
    artifact_table = FakeArtifactsTable()  # Reload the pre-crash local artifact history.
    args["operation_artifacts_table"] = artifact_table
    monkeypatch.setattr(retention_module, "time", lambda: 2000)
    assert RetentionService.pending_tasks(reopened["agent"])[0]["current_operation_uuid"] == "operation-new"
    second = RetentionService.run(**args, retry=True)
    assert second.final_state == AgentReportState.success
    assert calls == [["snap-old"], "check"]
    assert artifact_table.rows[1]["forgotten_at"] is not None
    assert resticapi.calls == [{"prune": True}]
    assert not list(reopened["agent"].all())
    reopened.engine.dispose()


@pytest.mark.parametrize("failed_check", ["100%", "5%", "1/10"])
def test_failed_data_check_blocks_cleanup_until_full_recheck_passes(retention_settings, failed_check):
    retention_settings.upsert({"name": "retention_check_failed:1", "settings": failed_check}, ["name"])
    resticapi = FakeResticApi()
    checked = []
    failing = True

    def check(**kwargs):
        checked.append(kwargs)
        if failing:
            raise ResticError("repository data damaged")

    resticapi.check = check
    args = dict(repository_id=1, retention_id=2, job_id=7,
                set_repository=lambda repository_id: {"location": "/repo"}, resticapi=resticapi,
                retentions_table=FakeTable({"id": 2, "name": "Keep one", "keep_last": 1}),
                operations_table=FakeRunsTable(), operation_artifacts_table=FakeArtifactsTable(),
                settings_table=retention_settings)
    assert RetentionService.run(**args).final_state == AgentReportState.failed
    assert resticapi.calls == []
    assert retention_settings.find_one(name="retention_task:1:7")
    failing = False
    assert RetentionService.run(**args, retry=True).final_state == AgentReportState.success
    assert checked == [{"read_data": True, "read_data_subset": None}] * 2
    assert not retention_settings.find_one(name="retention_check_failed:1")
    assert not retention_settings.find_one(name="retention_task:1:7")


def test_successful_sample_cannot_clear_failed_data_check_block(monkeypatch, retention_settings):
    monkeypatch.setattr(base_module, "agent_settings", retention_settings)
    failing = True

    def check(**kwargs):
        if failing:
            raise ResticError("damaged pack found in sample")

    handler = base_module.BackupJobHandler(
        agent=SimpleNamespace(resticapi=SimpleNamespace(check=check)), job={"id": 7}, repository_id=1,
        run_options={"repository_check": {"enabled": True, "read_data": "5%"}},
    )
    assert handler._run_post_backup_check(AgentReport.command_report()) is False
    assert retention_settings.find_one(name="retention_check_failed:1")["settings"] == "100%"
    failing = False
    for stored in ("100%", "5%", "1/10"):
        retention_settings.upsert({"name": "retention_check_failed:1", "settings": stored}, ["name"])
        assert handler._run_post_backup_check(AgentReport.command_report()) is True
        assert retention_settings.find_one(name="retention_check_failed:1"), "new sample must not unblock retention"
    handler.run_options["repository_check"]["read_data"] = "100%"
    assert handler._run_post_backup_check(AgentReport.command_report()) is True
    assert retention_settings.find_one(name="retention_check_failed:1") is None


@pytest.mark.parametrize("prune_pending", [False, True])
def test_deleted_policy_retires_retry_but_preserves_prune_across_restart(monkeypatch, retention_settings, prune_pending):
    from drastic_agent.services import retention as retention_module

    monkeypatch.setattr(retention_module, "time", lambda: 1000)
    policy = FakeTable({"id": 2, "name": "Keep one", "keep_last": 1})
    resticapi = FakeResticApi()
    if prune_pending:
        resticapi.prune_error = ResticError("prune interrupted")
    else:
        resticapi.snapshots = lambda: (_ for _ in ()).throw(ResticError("repository offline"))
    args = dict(repository_id=1, retention_id=2, job_id=7, current_operation_uuid="operation-new",
                set_repository=lambda repository_id: {"location": "/repo"}, resticapi=resticapi,
                retentions_table=policy, operations_table=FakeRunsTable(),
                operation_artifacts_table=FakeArtifactsTable(), settings_table=retention_settings)
    assert RetentionService.run(**args).final_state == AgentReportState.failed
    policy.row = None  # Successful synchronization removed the deleted policy.
    resticapi.calls.clear()
    retired = RetentionService.run(**args, retry=True)
    assert retired.final_state == AgentReportState.warning
    assert retired.retention_id is None
    assert retired.data == {"retired_policy_id": 2, "cleanup_pending": prune_pending}
    assert resticapi.calls == [], "policy retirement must not delete anything"

    reopened = dataset.connect(str(retention_settings.db.engine.url))
    try:
        monkeypatch.setattr(retention_module, "time", lambda: 2000)
        tasks = RetentionService.pending_tasks(reopened["agent"])
        if not prune_pending:
            assert tasks == []
            return
        assert tasks == [{"repository_id": 1, "retention_id": None, "job_id": 7, "current_operation_uuid": "operation-new"}]
        assert reopened["agent"].find_one(name="retention_prune:1")
        resticapi.check = lambda: resticapi.calls.append({"check": True})
        resticapi.snapshots = lambda: pytest.fail("prune-only retry must not select snapshots")
        policy.find_one = lambda **kwargs: pytest.fail("deleted policy must not be retried")
        prune_args = {**args, **tasks[0], "settings_table": reopened["agent"], "retry": True}
        resticapi.prune_error = ResticError("repository locked")
        assert RetentionService.run(**prune_args).final_state == AgentReportState.failed
        assert json.loads(reopened["agent"].find_one(name="retention_task:1:7")["settings"])["args"]["retention_id"] is None
        assert reopened["agent"].find_one(name="retention_prune:1")
        assert RetentionService.run(**prune_args).final_state == AgentReportState.success
        assert resticapi.calls == [{"check": True}, {"prune": True}] * 2
        assert not list(reopened["agent"].all())
    finally:
        reopened.engine.dispose()


def test_retention_database_error_keeps_policy_retry(retention_settings):
    def unavailable(**kwargs):
        raise RuntimeError("database temporarily unavailable")

    report = RetentionService.run(
        repository_id=1, retention_id=2, job_id=7, retry=True,
        set_repository=lambda repository_id: pytest.fail("policy lookup failed"),
        resticapi=FakeResticApi(), retentions_table=SimpleNamespace(find_one=unavailable),
        operations_table=FakeRunsTable(), operation_artifacts_table=FakeArtifactsTable(),
        settings_table=retention_settings,
    )
    assert report.final_state == AgentReportState.failed
    assert report.retention_id == 2
    pending = json.loads(retention_settings.find_one(name="retention_task:1:7")["settings"])
    assert pending["args"]["retention_id"] == 2


@pytest.mark.parametrize("month, utc_hour", [(7, 2), (1, 3)])
def test_retention_sorts_mixed_legacy_and_utc_runs(monkeypatch, berlin_timezone, month, utc_hour):
    agent = Agent.__new__(Agent)
    resticapi = FakeResticApi()
    runs = FakeRunsTable()
    runs.rows[0]["ended"] = f"2026-{month:02d}-10T{utc_hour:02d}:00:00+00:00"
    runs.rows[1]["ended"] = f"2026-{month:02d}-10T03:00:00"
    monkeypatch.setattr(agent_module, "retentions", FakeTable({"id": 2, "name": "Daily", "keep_last": 1}))
    monkeypatch.setattr(agent_module, "agent_operations", runs)
    monkeypatch.setattr(agent_module, "agent_operation_artifacts", FakeArtifactsTable())
    agent._Agent__resticapi = resticapi
    agent._Agent__set_repository = lambda repository_id: {"id": repository_id, "location": "/repo"}

    report = Agent.cmd_run_retention(agent, repository_id=1, retention_id=2, job_id=7)

    assert report.final_state == AgentReportState.success
    assert report.data["kept_operation_ids"] == [1]
    assert report.data["pruned_operation_ids"] == [2]
    assert report.data["forgotten_artifacts"][0]["forgotten_at"].endswith("+00:00")
    same_hour = {"id": 3, "ended": f"2026-{month:02d}-10T04:30:00"}
    assert RetentionService.keep_operation_ids(
        [runs.rows[0], same_hour, runs.rows[1]], {"keep_hourly": 2},
    ) == {1, 2}


def test_retention_never_forgets_snapshot_with_mismatched_tags(monkeypatch):
    agent = Agent.__new__(Agent)
    resticapi = FakeResticApi()
    resticapi.snapshots = lambda: [{"id": "snap-old", "tags": ["operation_uuid:other"]}]
    artifact_table = FakeArtifactsTable()
    monkeypatch.setattr(
        agent_module,
        "retentions",
        FakeTable({"id": 2, "name": "Daily", "keep_last": 1}),
    )
    monkeypatch.setattr(agent_module, "agent_operations", FakeRunsTable())
    monkeypatch.setattr(agent_module, "agent_operation_artifacts", artifact_table)
    agent._Agent__resticapi = resticapi
    agent._Agent__set_repository = lambda repository_id: {"id": repository_id, "location": "/repo"}

    report = Agent.cmd_run_retention(agent, repository_id=1, retention_id=2, job_id=7)

    assert not any("snapshot_ids" in call for call in resticapi.calls)
    assert resticapi.calls == []
    assert artifact_table.rows[1]["forgotten_at"] is None
    assert report.data["skipped_snapshot_ids"] == ["snap-old"]
    assert report.final_state == AgentReportState.warning


def test_retention_retries_prune_without_forgetting_twice(monkeypatch, retention_settings):
    agent = Agent.__new__(Agent)
    resticapi = FakeResticApi()
    resticapi.prune_error = ResticError("prune interrupted")
    artifact_table = FakeArtifactsTable()
    monkeypatch.setattr(
        agent_module,
        "retentions",
        FakeTable({"id": 2, "name": "Daily", "keep_last": 1}),
    )
    monkeypatch.setattr(agent_module, "agent_operations", FakeRunsTable())
    monkeypatch.setattr(agent_module, "agent_operation_artifacts", artifact_table)
    agent._Agent__resticapi = resticapi
    agent._Agent__set_repository = lambda repository_id: {"id": repository_id, "location": "/repo"}

    first_report = Agent.cmd_run_retention(agent, repository_id=1, retention_id=2, job_id=7)
    assert retention_settings.find_one(name="retention_prune:1")
    # Reopen persisted state and retry from another job on the same repository.
    restarted_db = dataset.connect(str(retention_settings.db.engine.url))
    monkeypatch.setattr(agent_module, "agent_settings", restarted_db["agent"])
    resticapi.calls.clear()
    try:
        second_report = Agent.cmd_run_retention(agent, repository_id=1, retention_id=2, job_id=8)
    finally:
        monkeypatch.setattr(agent_module, "agent_settings", retention_settings)
        restarted_db.engine.dispose()

    assert first_report.final_state == AgentReportState.failed
    assert artifact_table.rows[1]["forgotten_at"] is not None
    assert resticapi.calls == [{"prune": True}]
    assert second_report.final_state == AgentReportState.success
    assert retention_settings.find_one(name="retention_prune:1") is None

    resticapi.calls.clear()
    Agent.cmd_run_retention(agent, repository_id=1, retention_id=2, job_id=7)
    assert resticapi.calls == []


def test_retention_retry_reconciles_forget_committed_before_local_update(monkeypatch, retention_settings):
    agent = Agent.__new__(Agent)

    class ForgetCommittedThenFailedResticApi(FakeResticApi):
        def __init__(self):
            super().__init__()
            self.repository_snapshots = super().snapshots()

        def snapshots(self):
            return self.repository_snapshots

        def forget_snapshots(self, snapshot_ids, prune=True):
            assert retention_settings.find_one(name="retention_prune:1")
            self.calls.append({"snapshot_ids": snapshot_ids, "prune": prune})
            self.repository_snapshots = [
                snapshot
                for snapshot in self.repository_snapshots
                if snapshot["id"] not in snapshot_ids
            ]
            raise ResticError("connection lost after forget committed")

    resticapi = ForgetCommittedThenFailedResticApi()
    artifact_table = FakeArtifactsTable()
    monkeypatch.setattr(
        agent_module,
        "retentions",
        FakeTable({"id": 2, "name": "Daily", "keep_last": 1}),
    )
    monkeypatch.setattr(agent_module, "agent_operations", FakeRunsTable())
    monkeypatch.setattr(agent_module, "agent_operation_artifacts", artifact_table)
    agent._Agent__resticapi = resticapi
    agent._Agent__set_repository = lambda repository_id: {"id": repository_id, "location": "/repo"}

    first_report = Agent.cmd_run_retention(agent, repository_id=1, retention_id=2, job_id=7)

    assert first_report.final_state == AgentReportState.failed
    assert artifact_table.rows[1]["forgotten_at"] is None
    assert resticapi.calls == [{"snapshot_ids": ["snap-old"], "prune": False}]

    resticapi.calls.clear()
    second_report = Agent.cmd_run_retention(agent, repository_id=1, retention_id=2, job_id=7)

    assert resticapi.calls == [{"prune": True}]
    assert artifact_table.rows[1]["forgotten_at"] is not None
    assert second_report.data["missing_snapshot_ids"] == ["snap-old"]
    assert second_report.final_state == AgentReportState.success


def test_pending_prune_is_scoped_to_repository(monkeypatch, retention_settings):
    agent = Agent.__new__(Agent)
    resticapi = FakeResticApi()
    monkeypatch.setattr(agent_module, "retentions", FakeTable({"name": "Keep all", "keep_last": 2}))
    monkeypatch.setattr(agent_module, "agent_operations", FakeRunsTable())
    monkeypatch.setattr(agent_module, "agent_operation_artifacts", FakeArtifactsTable())
    agent._Agent__resticapi = resticapi
    agent._Agent__set_repository = lambda repository_id: {"id": repository_id, "location": "/repo"}
    retention_settings.insert({"name": "retention_prune:2", "settings": "pending"})

    report = Agent.cmd_run_retention(agent, repository_id=1, retention_id=2, job_id=7)

    assert report.final_state == AgentReportState.success
    assert resticapi.calls == []
    assert retention_settings.find_one(name="retention_prune:2")


def test_retention_forgets_tagged_successful_artifact_from_partial_failed_backup(monkeypatch):
    agent = Agent.__new__(Agent)
    resticapi = FakeResticApi()
    failed_operation = {
        "id": 3,
        "uuid": "operation-partial",
        "type": "backup",
        "job_id": 7,
        "repository_id": 1,
        "state": "failed",
        "ended": "2026-05-10T01:00:00",
        "data": {"partial_failure": True},
    }
    runs_table = FakeRunsTable()
    runs_table.rows.append(failed_operation)
    artifact_table = FakeArtifactsTable()
    artifact_table.rows.append(
        {
            "id": 3,
            "uuid": "artifact-partial",
            "operation_id": 3,
            "snapshot_id": "snap-partial",
            "state": "success",
            "forgotten_at": None,
        }
    )
    resticapi.snapshots = lambda: [
        {
            "id": "snap-partial",
            "tags": [
                "operation_uuid:operation-partial",
                "artifact_uuid:artifact-partial",
            ],
        }
    ]
    monkeypatch.setattr(
        agent_module,
        "retentions",
        FakeTable({"id": 2, "name": "Daily", "keep_last": 2}),
    )
    monkeypatch.setattr(agent_module, "agent_operations", runs_table)
    monkeypatch.setattr(agent_module, "agent_operation_artifacts", artifact_table)
    agent._Agent__resticapi = resticapi
    agent._Agent__set_repository = lambda repository_id: {
        "id": repository_id,
        "location": "/repo",
    }

    report = Agent.cmd_run_retention(agent, repository_id=1, retention_id=2, job_id=7)

    assert resticapi.calls == [
        {"snapshot_ids": ["snap-partial"], "prune": False},
        {"prune": True},
    ]
    assert artifact_table.rows[2]["forgotten_at"] is not None
    assert failed_operation["state"] == "failed"
    assert report.final_state == AgentReportState.success


def test_cmd_get_repository_stats_returns_after_repository_error(monkeypatch):
    agent = Agent.__new__(Agent)

    class UnexpectedResticApi:
        def stats(self):
            raise AssertionError("stats should not be called")

        def cat_config(self):
            raise AssertionError("cat_config should not be called")

    agent._Agent__resticapi = UnexpectedResticApi()

    def raise_missing(repository_id):
        raise AgentExeption(f"Repository with id {repository_id} not found")

    agent._Agent__set_repository = raise_missing

    report = Agent.cmd_get_repository_stats(agent, repository_id=99)

    assert report.final_state == AgentReportState.failed
    assert any("Repository with id 99 not found" in line for line in report.log_list)


def test_operation_queue_is_persisted_in_database():
    from drastic_agent.storage.database import agent_operation_queue

    agent_operation_queue.delete()
    AgentReport.pending_reports = []
    AgentReport.finished_reports.clear()

    report = AgentReport.job_report(job_id=1, repository_id=2)
    AgentReport.save_queue()

    row = agent_operation_queue.find_one(uuid=report.uuid)
    assert row["status"] == "finished"


def test_cmd_check_repository_stores_check_report():
    agent = Agent.__new__(Agent)
    AgentReport.pending_reports = []
    AgentReport.finished_reports.clear()

    class FakeResticApi:
        def __init__(self):
            self.calls = []

        def check(self, **kwargs):
            self.calls.append(kwargs)
            return {"message_type": "summary", "errors": 0}

    resticapi = FakeResticApi()
    agent._Agent__resticapi = resticapi
    agent._Agent__set_repository = lambda repository_id: {"id": repository_id, "location": "/repo"}

    report = Agent.cmd_check_repository(agent, repository_id=5, read_data_subset="1/10")

    assert report.type == AgentReportType.repository_check
    assert report.repository_id == 5
    assert report.final_state == AgentReportState.success
    assert report.data["read_data"] == "1/10"
    assert report.data["check"] == {"message_type": "summary", "errors": 0}
    assert resticapi.calls == [{"read_data": False, "read_data_subset": "1/10"}]
