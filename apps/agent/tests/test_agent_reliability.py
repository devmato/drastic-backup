import configparser
import json
import shutil
from concurrent.futures import Future
from datetime import datetime, timezone

import dataset
import pytest

from drastic_agent.agent.enums import AgentOperationState
from drastic_agent.agent.report import AgentReport
from drastic_agent.jobs.file_backup import FileBackupJobHandler
from drastic_agent.runtime.agent import Agent
from drastic_agent.services import secrets as repository_secrets_service
from drastic_agent.services.retention import RetentionService
from drastic_agent.storage.database import (
    actions,
    agent_operation_artifacts,
    agent_operation_queue,
    agent_operations,
    jobs,
    repositories,
    repository_secrets,
    retentions,
    schedule_runs,
    schedules,
)
from drastic_common.restic.client import ResticApi
from drastic_common.restic.repository import ResticRepository


@pytest.fixture
def restic_binary():
    binary = shutil.which("restic")
    if binary is None:
        pytest.skip("restic is not installed")
    return binary


@pytest.fixture
def clean_agent_state():
    tables = (
        agent_operation_artifacts,
        agent_operation_queue,
        agent_operations,
        schedule_runs,
        actions,
        schedules,
        jobs,
        retentions,
        repository_secrets,
        repositories,
    )
    for table in tables:
        table.delete()
    AgentReport.pending_reports = []
    AgentReport.finished_reports.clear()
    AgentReport.running_operations = {}
    yield
    for table in tables:
        table.delete()
    AgentReport.pending_reports = []
    AgentReport.finished_reports.clear()
    AgentReport.running_operations = {}


class _OfflineAgent:
    def __init__(self, resticapi, repository):
        self.resticapi = resticapi
        self.repository = repository

    def set_repository(self, repository_id):
        assert repository_id == self.repository["id"]
        return self.repository

    @staticmethod
    def execute_actions(report, actions):
        assert list(actions) == []

    def cmd_get_repository_stats(self, repository_id):
        assert repository_id == self.repository["id"]
        self.resticapi.stats()
        return type("StatsReport", (), {"state": AgentOperationState.success, "log_list": []})()


def test_real_file_backup_finishes_in_durable_outbox(
    tmp_path, restic_binary, clean_agent_state
):
    source = tmp_path / "source"
    source.mkdir()
    (source / "payload.txt").write_text("durable backup\n", encoding="utf-8")
    repository_path = tmp_path / "repository"
    repository = ResticRepository(location=str(repository_path), password="test-password")
    resticapi = ResticApi(binary_path=restic_binary, repository=repository, timeout=10)
    resticapi.init()

    handler = FileBackupJobHandler(
        agent=_OfflineAgent(
            resticapi,
            {"id": 71, "kind": "custom", "location": str(repository_path)},
        ),
        job={
            "id": 81,
            "uuid": "file-job-81",
            "type": "file",
            "config": {"paths": [{"path": str(source)}], "exclude_patterns": []},
        },
        repository_id=71,
        operation_uuid="real-file-backup-81",
    )

    report = handler.run()

    assert report.final_state == AgentOperationState.success
    assert report.ended is not None
    assert report.artifacts[0]["snapshot_id"]
    assert any(snapshot["id"] == report.artifacts[0]["snapshot_id"] for snapshot in resticapi.snapshots())
    queued = agent_operation_queue.find_one(uuid=report.uuid)
    assert queued["status"] == "finished"
    assert json.loads(queued["payload"])["final_state"] == "success"

    AgentReport.pending_reports = []
    AgentReport.finished_reports.clear()
    AgentReport.load_queue()

    restored = AgentReport.finished_reports.popleft()
    assert restored.uuid == report.uuid
    assert restored.artifacts == report.artifacts
    assert restored.final_state == AgentOperationState.success


def test_real_ordered_backups_keep_latest_snapshot_per_job_in_shared_repository(tmp_path, restic_binary, clean_agent_state):
    source = tmp_path / "source"
    source.mkdir()
    repository = ResticRepository(location=str(tmp_path / "repository"), password="test-password")
    resticapi = ResticApi(binary_path=restic_binary, repository=repository, timeout=10)
    resticapi.init()
    retentions.insert({"id": 73, "name": "Latest only", "keep_last": 1})
    settings_db = dataset.connect(f"sqlite:///{tmp_path}/settings.db")
    settings = settings_db["agent"]
    settings.create_column("name", settings_db.types.text)
    settings.create_column("settings", settings_db.types.text)

    class RetentionAgent(_OfflineAgent):
        def cmd_run_retention(self, **kwargs):
            return RetentionService.run(**kwargs, set_repository=self.set_repository,
                                        resticapi=self.resticapi, retentions_table=retentions,
                                        operations_table=agent_operations,
                                        operation_artifacts_table=agent_operation_artifacts,
                                        settings_table=settings)

    agent = RetentionAgent(resticapi, {"id": 73, "kind": "custom", "location": repository.location})
    latest = []
    try:
        for cycle in range(2):
            latest = []
            for job_id in (83, 84):
                (source / "payload.txt").write_text(f"job {job_id}, cycle {cycle}", encoding="utf-8")
                report = FileBackupJobHandler(agent=agent, job={"id": job_id, "uuid": f"job-{job_id}",
                    "type": "file", "config": {"paths": [{"path": str(source)}], "exclude_patterns": []}},
                    repository_id=73, retention_id=73, operation_uuid=f"chain-{cycle}-{job_id}").run()
                assert report.final_state == AgentOperationState.success
                latest.append(report.artifacts[0]["snapshot_id"])
        assert {snapshot["id"] for snapshot in resticapi.snapshots()} == set(latest)
        assert not list(settings.all())
        resticapi.check()
    finally:
        settings_db.engine.dispose()


class _SynchronousExecution:
    def __init__(self):
        self.futures = []

    def submit(self, target, *args, **kwargs):
        kwargs.pop("resources")
        future = Future()
        try:
            future.set_result(target(*args, **kwargs))
        except Exception as exc:
            future.set_exception(exc)
        self.futures.append(future)
        return future


def test_scheduled_synced_native_repository_endpoint_failure_is_durable(
    tmp_path, restic_binary, clean_agent_state, monkeypatch
):
    source = tmp_path / "source"
    source.mkdir()
    (source / "payload.txt").write_text("must not be backed up\n", encoding="utf-8")
    agent = Agent.__new__(Agent)
    agent.identifier = "offline-agent"
    agent.client = None
    agent._Agent__secret = "offline-secret"
    agent._Agent__server = "http://127.0.0.1:0"
    agent._Agent__config = configparser.ConfigParser()
    agent._Agent__config["AGENT"] = {
        "private_key": "private-key",
        "public_key": "public-key",
    }
    agent._Agent__repository_passwords = {}
    agent._Agent__secret_values = {}
    agent._Agent__schedule_run_slots = {}
    agent._Agent__resticapi = ResticApi(binary_path=restic_binary, timeout=1)
    execution = _SynchronousExecution()
    agent._Agent__execution = execution

    monkeypatch.setattr(repository_secrets_service, "decrypt_with_private_key", lambda envelope, key: "repo-key")
    monkeypatch.setattr(
        agent,
        "_Agent__send_request",
        lambda action: {
            "success": True,
            "result": {
                "repositories": [
                    {
                        "id": 72,
                        "kind": "native",
                        "location": "native/unreachable",
                        "environment": {},
                        "encrypted_restic_access_key": {"sealed": "repo-key"},
                    }
                ],
                "retentions": [],
                "jobs": [
                    {
                        "id": 82,
                        "uuid": "file-job-82",
                        "type": "file",
                        "config": {
                            "paths": [{"path": str(source)}],
                            "exclude_patterns": [],
                        },
                    }
                ],
                "schedules": [
                    {
                        "id": 92,
                        "job_id": 82,
                        "repository_id": 72,
                        "cron_string": "* * * * *",
                        "config": {},
                    }
                ],
                "actions": [],
            },
        }
        if action == "sync"
        else {"success": False, "result": {}},
    )

    sync_report = Agent.cmd_sync(agent)
    Agent._Agent__run_due_schedules(
        agent, datetime(2026, 7, 23, 12, 10, tzinfo=timezone.utc)
    )

    assert sync_report.final_state == AgentOperationState.success
    assert len(execution.futures) == 1
    assert execution.futures[0].exception() is None
    report = execution.futures[0].result()
    assert report.final_state == AgentOperationState.failed
    assert report.ended is not None
    assert "Obtaining repository config failed" in report.log
    assert schedule_runs.find_one(schedule_id=92)["status"] == "failed"
    queued = agent_operation_queue.find_one(uuid=report.uuid)
    assert queued["status"] == "finished"
    assert json.loads(queued["payload"])["final_state"] == "failed"
