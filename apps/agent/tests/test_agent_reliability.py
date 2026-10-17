import configparser
import json
import os
import shutil
import subprocess
import sys
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import nullcontext
from datetime import datetime, timezone
from threading import Event
from uuid import uuid4

import dataset
import pytest
from test_agent_retention import OWNER, Backend, Repo

import drastic_agent.jobs.base as backup_base
import drastic_agent.runtime.agent as runtime_module
from drastic_agent.agent.enums import AgentOperationState
from drastic_agent.agent.report import AgentReport
from drastic_agent.agent.schemas import AgentReportSchema
from drastic_agent.jobs.file_backup import FileBackupJobHandler
from drastic_agent.runtime import reporting
from drastic_agent.runtime.agent import Agent
from drastic_agent.services import secrets as repository_secrets_service
from drastic_agent.services.restore import RestoreContext, RestoreService
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
from drastic_common.restic.snapshots import restore_point_key


@pytest.fixture
def restic_binary():
    binary = os.environ.get("RESTIC_BINARY") or os.environ.get("DRASTIC_RESTIC_BINARY") or shutil.which("restic")
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
    owner_uuid = OWNER
    def __init__(self, resticapi, repository):
        self.resticapi = resticapi
        self.repository = repository

    def set_repository(self, repository_id):
        assert repository_id == self.repository["id"]
        return self.repository

    @staticmethod
    def execute_actions(report, actions):
        assert list(actions) == []

    @staticmethod
    def cmd_run_retention(**kwargs):
        return AgentReport.command_report(data={"cleanup_pending": False}).finish()


@pytest.mark.parametrize("stage", ["next_batch", "prune"])
def test_manual_deletion_replays_confirmed_removals_after_agent_restart(clean_agent_state, stage):
    repo, backend = Repo(), Backend()
    repo.operation_cancellation = lambda _: nullcontext()
    artifacts = repo.add_run(backend, count=1100)
    ids = [item["snapshot_id"] for item in artifacts]
    key = restore_point_key(repo.items[ids[0]])
    operation_uuid = str(uuid4())
    sent = []

    def interrupt():
        report = AgentReport.get_report(uuid=operation_uuid)
        assert report in AgentReport.pending_reports
        stored = agent_operations.find_one(uuid=operation_uuid)
        assert stored["state"] == "running"
        assert set(stored["data"]["deleted_snapshot_ids"]) == {item["id"] for item in repo.removed}
        reporting.flush(lambda running: sent.append(AgentReportSchema().dump(running)) or True)
        assert sent[-1]["state"] == "running" and sent[-1]["data"]["manual_delete"]
        raise SystemExit("Simulated process restart")

    forget = repo.forget_snapshots

    def remove(batch, prune=False):
        if stage == "next_batch" and repo.removed:
            interrupt()
        forget(batch, prune=prune)

    repo.forget_snapshots = remove
    repo.prune = interrupt
    with pytest.raises(SystemExit):
        RestoreService.delete_point(RestoreContext(repo, lambda _: None), repository_id=1,
            snapshot_ids=ids, point_key=key, operation_uuid=operation_uuid)
    AgentReport.load_queue()
    AgentReport.recover_interrupted()
    recovered = next(report for report in AgentReport.finished_reports if report.uuid == operation_uuid)
    assert recovered.state == AgentOperationState.failed
    assert set(recovered.data["deleted_snapshot_ids"]) == {item["id"] for item in repo.removed}
    assert len(recovered.data["deleted_snapshot_ids"]) == (1000 if stage == "next_batch" else 1100)
    assert json.loads(agent_operation_queue.find_one(uuid=operation_uuid)["payload"])["data"] == recovered.data


class _RetentionAgent(_OfflineAgent):
    def __init__(self, resticapi, repository, settings, monkeypatch, backend):
        super().__init__(resticapi, repository)
        runtime = Agent.__new__(Agent)
        runtime._Agent__resticapi = resticapi
        runtime._Agent__set_repository = self.set_repository
        runtime._Agent__send_report = backend.send_report
        self.owner_uuid = backend.owner_uuid
        def request(action, **kwargs):
            assert action == "retention_context" and kwargs["repository_id"] == repository["id"]
            return {"success": True, "result": backend()}

        runtime._Agent__send_request = request
        monkeypatch.setattr(runtime_module, "agent_settings", settings)
        self.cmd_run_retention = runtime.cmd_run_retention


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
    assert len(list(agent_operation_queue.all())) == 1
    assert report.data["stats"] == resticapi.stats()
    assert report.data["config"] == resticapi.cat_config()

    AgentReport.pending_reports = []
    AgentReport.finished_reports.clear()
    AgentReport.load_queue()

    restored = AgentReport.finished_reports.popleft()
    assert restored.uuid == report.uuid
    assert restored.artifacts == report.artifacts
    assert restored.final_state == AgentOperationState.success
    assert restored.data["stats"] == report.data["stats"]


def test_real_ordered_backups_keep_latest_snapshot_per_job_in_shared_repository(tmp_path, restic_binary, clean_agent_state, monkeypatch):
    source = tmp_path / "source"
    source.mkdir()
    repository = ResticRepository(location=str(tmp_path / "repository"), password="test-password")
    selected_folder = source / "selected"
    selected_folder.mkdir()
    (selected_folder / "keep.txt").write_text("unchanged content", encoding="utf-8")
    resticapi = ResticApi(binary_path=restic_binary, repository=repository, timeout=10)
    resticapi.init()
    settings_db = dataset.connect(f"sqlite:///{tmp_path}/settings.db")
    settings = settings_db["agent"]
    settings.create_column("name", settings_db.types.text)
    settings.create_column("settings", settings_db.types.text)

    job_uuids = {job_id: str(uuid4()) for job_id in (83, 84)}
    backend = Backend(job_uuids=list(job_uuids.values()), default={"keep_last": 3})
    agent = _RetentionAgent(resticapi, {"id": 73, "kind": "custom", "location": repository.location}, settings, monkeypatch, backend)
    retentions.insert({"id": 73, "keep_last": 1})
    for job_id, job_uuid in job_uuids.items():
        jobs.insert({"id": job_id, "uuid": job_uuid, "retention_id": 73})
    latest = []
    parents = {}
    try:
        for cycle in range(2):
            repository.backup_host = f"installation-{cycle}"
            repository.env["RESTIC_HOST"] = repository.backup_host
            latest = []
            for job_id in (83, 84):
                (source / "payload.txt").write_text(f"job {job_id}, cycle {cycle}", encoding="utf-8")
                selection = selected_folder if cycle == 1 and job_id == 83 else source
                report = FileBackupJobHandler(agent=agent, job={"id": job_id, "uuid": job_uuids[job_id],
                    "type": "file", "retention_id": 73, "config": {"paths": [{"path": str(selection)}], "exclude_patterns": []}},
                    repository_id=73, operation_uuid=str(uuid4())).run()
                assert report.final_state == AgentOperationState.success
                snapshot_id = report.artifacts[0]["snapshot_id"]
                snapshot = next(item for item in resticapi.snapshots() if item["id"] == snapshot_id)
                assert snapshot.get("parent") == parents.get(job_id)
                if cycle == 1 and job_id == 83:
                    assert report.data["files_unmodified"] == 1
                parents[job_id] = snapshot_id
                latest.append(snapshot_id)
        assert {snapshot["id"] for snapshot in resticapi.snapshots()} == set(latest)
        target = tmp_path / "restore"
        resticapi.restore(parents[83], str(target), ["/"])
        restored_source = target.joinpath(*source.parts[1:])
        assert (restored_source / "selected/keep.txt").read_text() == "unchanged content"
        assert not (restored_source / "payload.txt").exists()
        assert RetentionService.pending_tasks(settings) == []
        snapshot = next(item for item in resticapi.snapshots() if item["id"] == parents[83])
        deletion = RestoreService.delete_point(RestoreContext(resticapi, agent.set_repository),
            repository_id=73, snapshot_ids=[snapshot["id"]], point_key=restore_point_key(snapshot), operation_uuid=str(uuid4()))
        assert deletion.state == AgentOperationState.success
        assert {item["id"] for item in resticapi.snapshots()} == {parents[84]}
        resticapi.check()
    finally:
        settings_db.engine.dispose()


def test_real_backup_on_another_agent_cleans_deleted_job_without_its_local_history(
    tmp_path, restic_binary, clean_agent_state, monkeypatch,
):
    source = tmp_path / "source"
    source.mkdir()
    payload = source / "payload.txt"
    repository = ResticRepository(str(tmp_path / "repository"), "test-password")
    restic = ResticApi(restic_binary, repository, timeout=10)
    restic.init()
    settings_db = dataset.connect(f"sqlite:///{tmp_path}/settings.db")
    settings = settings_db["agent"]
    settings.create_column("name", settings_db.types.text)
    settings.create_column("settings", settings_db.types.text)
    monkeypatch.setattr(backup_base, "agent_settings", settings)
    original_uuid = str(uuid4())
    other_uuid = str(uuid4())
    backend = Backend(job_uuids=[original_uuid, other_uuid])
    original = _RetentionAgent(restic, {"id": 1, "location": repository.location}, settings, monkeypatch, backend)
    history = []
    try:
        for version in (1, 2):
            payload.write_text(f"version {version}")
            report = FileBackupJobHandler(original,
                {"id": 7, "uuid": original_uuid, "config": {"paths": [{"path": str(source)}]}}, 1).run()
            assert report.state == AgentOperationState.success
            history.append({"job_uuid": original_uuid, "operation_uuid": report.uuid, "artifact": report.artifacts[0]})
        foreign_data = restic.backup_stdin("foreign backup", "payload.txt", tags=[f"job_uuid:{uuid4()}"])["snapshot_id"]
        foreign_snapshots = {item["id"]: item for item in restic.snapshots()
                             if item["id"] == foreign_data}
        # Agent B has no source-agent history; owned snapshot tags supply run membership.
        agent_operation_artifacts.delete()
        agent_operations.delete()
        agent_operation_queue.delete()
        AgentReport.finished_reports.clear()
        backend.job_uuids = [other_uuid]
        other_api = ResticApi(restic_binary, ResticRepository(repository.location, "test-password", backup_host="other-agent"), timeout=10)
        other = _RetentionAgent(other_api, {"id": 1, "location": repository.location}, settings, monkeypatch, backend)
        payload.write_text("other agent")
        current = FileBackupJobHandler(other,
            {"id": 8, "uuid": other_uuid, "retention_id": None, "config": {"paths": [{"path": str(source)}]}}, 1).run()
        assert current.state == AgentOperationState.success
        assert {snapshot["id"] for snapshot in restic.snapshots()} == {
            history[1]["artifact"]["snapshot_id"], current.artifacts[0]["snapshot_id"], foreign_data}
        assert {item["id"]: item for item in restic.snapshots() if item["id"] in foreign_snapshots} == foreign_snapshots
        target = tmp_path / "restore"
        restic.restore(history[1]["artifact"]["snapshot_id"], str(target), ["/"])
        assert target.joinpath(*payload.parts[1:]).read_text() == "version 2"
        assert RetentionService.pending_tasks(settings) == []
    finally:
        settings_db.engine.dispose()


def test_cancel_during_parent_lookup_terminates_process_and_never_starts_backup(
    tmp_path, restic_binary, clean_agent_state, monkeypatch,
):
    source = tmp_path / "source"
    source.mkdir()
    (source / "payload.txt").write_text("must not be backed up")
    repository = ResticRepository(str(tmp_path / "repo"), "password")
    api = ResticApi(restic_binary, repository, timeout=10, terminate_grace=0.1)
    api.init()
    started = Event()
    processes, commands = [], []
    original_popen = api._ResticApi__popen

    def popen(command, **kwargs):
        commands.append(command)
        if "snapshots" not in command:
            return original_popen(command, **kwargs)
        # Stall enumeration while exercising ResticApi's real process supervision.
        process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"], **kwargs)
        processes.append(process)
        started.set()
        return process

    monkeypatch.setattr(api, "_ResticApi__popen", popen)
    handler = FileBackupJobHandler(
        _OfflineAgent(api, {"id": 71, "location": repository.location}),
        {"id": 81, "uuid": "file-job-81", "config": {"paths": [{"path": str(source)}]}},
        71, operation_uuid="cancel-parent-81",
    )
    runtime = Agent.__new__(Agent)
    with ThreadPoolExecutor(max_workers=1, initializer=api.set_repository, initargs=(repository,)) as executor:
        worker = executor.submit(handler.run)
        try:
            assert started.wait(5)
            running = AgentReport.get_report(uuid="cancel-parent-81")
            assert "pid" not in running.data
            response = runtime.cmd_cancel_job(81, operation_uuid=running.uuid)
            assert response.final_state == AgentOperationState.success
            finished = worker.result(timeout=5)
            assert finished.final_state == AgentOperationState.cancelled
            assert finished.ended is not None
            assert agent_operations.find_one(uuid=finished.uuid)["state"] == "cancelled"
            assert all("backup" not in command for command in commands)
            assert processes[0].poll() is not None
            assert api.diagnostic_processes() == []
        finally:
            if handler.report is not None:
                handler.report.cancel_event.set()
            if hasattr(runtime, "_Agent__execution"):
                runtime._Agent__execution.shutdown()


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


@pytest.mark.parametrize("retry_enabled", [False, True])
def test_scheduled_synced_native_repository_endpoint_failure_is_durable(
    tmp_path, restic_binary, clean_agent_state, monkeypatch, retry_enabled
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
                "agent_uuid": "550e8400-e29b-41d4-a716-446655440000",
                "backup_owner_uuid": OWNER,
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
                        "config": {"retry": {"enabled": retry_enabled}},
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
    assert bool(agent._Agent__schedule_attempts) is retry_enabled
    if retry_enabled:
        assert "Backup attempt 1 of 3" in report.log
    queued = agent_operation_queue.find_one(uuid=report.uuid)
    assert queued["status"] == "finished"
    assert json.loads(queued["payload"])["final_state"] == "failed"
