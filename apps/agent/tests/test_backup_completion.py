"""Completion tags survive metadata rewrites without crossing account namespaces."""

import hashlib
import json
import os
import shutil
from copy import deepcopy
from types import SimpleNamespace
from uuid import UUID

import pytest
import test_database_schema

from drastic_agent.agent.enums import AgentOperationState
from drastic_agent.agent.report import AgentReport
from drastic_agent.jobs.base import BackupJobHandler
from drastic_agent.runtime import reporting
from drastic_agent.runtime.agent import Agent
from drastic_agent.services.backup_completion import complete_backup, recover_repository, task_key
from drastic_agent.services.retention import RetentionService
from drastic_common.restic.client import ResticApi
from drastic_common.restic.exceptions import ResticError
from drastic_common.restic.repository import ResticRepository
from drastic_common.restic.snapshots import RUN_COMPLETE_TAG, snapshot_identity

local_db = test_database_schema.local_db
OWNER, FOREIGN, JOB, RUN = (str(UUID(int=i)) for i in range(1, 5))
OLD = "a" * 64


def backup_report(database, native=False):
    test_database_schema.database_module.initialize_schema()
    AgentReport.pending_reports = []
    AgentReport.running_operations = {}
    report = AgentReport.backup_operation(1, 1, operation_uuid=RUN)
    report.set_data("owner_uuid", OWNER)
    report.set_data("job_uuid", JOB)
    handler = BackupJobHandler(SimpleNamespace(owner_uuid=OWNER), {"id": 1, "uuid": JOB}, 1)
    handler.operation, handler.report = report.history_operation, report
    artifact = handler.start_artifact("vm:101" if native else "default", data={
        "backup_method": "native", "vmid": 101} if native else {})
    handler.finish_artifact(artifact, snapshot_id=OLD)
    snapshot = {"id": OLD, "tree": "b" * 64, "tags": handler.backup_tags(artifact, RUN)}
    checkpoint_key = hashlib.sha256(f"{JOB}:repository-id:101".encode()).hexdigest()
    if native:
        database["proxmox_checkpoints"].insert({"key": checkpoint_key, "data": {
            "valid": True, "snapshot_id": OLD, "info": {"keep": "source identity"}}})
    return report, snapshot, checkpoint_key


class TaggedRepository:
    def __init__(self, snapshot, failure=None):
        self.items = {snapshot["id"]: deepcopy(snapshot)}
        self.failure = failure
        self.tagged, self.forgotten = [], []

    def snapshots(self):
        return deepcopy(list(self.items.values()))

    def cat_config(self):
        return {"id": "repository-id"}

    def tag_snapshot(self, snapshot_id, tag):
        self.tagged.append(snapshot_id)
        if self.failure == "before":
            raise RuntimeError("Tagging unavailable")
        snapshot = deepcopy(self.items[snapshot_id])
        snapshot["original"] = snapshot_id
        snapshot["tags"].append(tag)
        snapshot["id"] = hashlib.sha256(json.dumps(snapshot, sort_keys=True).encode()).hexdigest()
        self.items[snapshot["id"]] = snapshot
        if self.failure == "saved":
            raise SystemExit("Crash after new metadata")
        del self.items[snapshot_id]
        if self.failure == "removed":
            raise SystemExit("Crash after old metadata removal")

    def forget_snapshots(self, snapshot_ids, prune=False):
        assert not prune
        self.forgotten.extend(snapshot_ids)
        for snapshot_id in snapshot_ids:
            del self.items[snapshot_id]


@pytest.mark.parametrize("stage", ["before", "saved", "removed", "committed"])
def test_tag_recovery_updates_outbox_and_cbt_without_replaying_stale_ids(local_db, monkeypatch, stage):
    report, snapshot, checkpoint_key = backup_report(local_db, native=True)
    repository = TaggedRepository(snapshot, stage)
    settings = local_db["agent"]
    delete = settings.delete
    if stage == "committed":
        monkeypatch.setattr(settings, "delete", lambda **kwargs: (_ for _ in ()).throw(SystemExit("Crash after commit")))
    with pytest.raises(RuntimeError if stage == "before" else SystemExit):
        complete_backup(repository, settings, report, OWNER, JOB)
    assert settings.find_one(name=task_key(1, RUN)) is not None
    monkeypatch.setattr(settings, "delete", delete)
    AgentReport.load_queue()
    AgentReport.recover_interrupted()
    recovered = next(item for item in AgentReport.finished_reports if item.uuid == RUN)
    assert recovered.state == AgentOperationState.failed
    agent = Agent.__new__(Agent)
    sent = []
    agent._Agent__send_request = lambda action, **kwargs: sent.append(kwargs["operation_json"]) or {"success": True}
    assert not agent._Agent__send_report(recovered)
    assert sent[-1]["state"] == "failed" and sent[-1]["ended"] is not None and not sent[-1]["artifacts"]
    assert sent[-1]["data"]["backup_completion_pending"] is True
    repository.failure = None
    recover_repository(repository, settings, 1, OWNER)
    assert settings.find_one(name=task_key(1, RUN)) is None
    new_snapshot, = repository.snapshots()
    assert new_snapshot["id"] != OLD and RUN_COMPLETE_TAG in new_snapshot["tags"]
    assert snapshot_identity(new_snapshot) == snapshot_identity(snapshot)
    assert recovered.artifacts[0]["snapshot_id"] == new_snapshot["id"]
    assert recovered.data["snapshot_replacements"] == {OLD: new_snapshot["id"]}
    queued = json.loads(local_db["agent_operation_queue"].find_one(uuid=RUN)["payload"])
    assert queued["artifacts"][0]["snapshot_id"] == new_snapshot["id"]
    assert queued["data"]["snapshot_replacements"] == recovered.data["snapshot_replacements"]
    checkpoint = local_db["proxmox_checkpoints"].find_one(key=checkpoint_key)["data"]
    assert checkpoint == {"valid": True, "snapshot_id": new_snapshot["id"], "info": {"keep": "source identity"}}
    assert agent._Agent__send_report(recovered)
    assert sent[-1]["artifacts"][0]["snapshot_id"] == new_snapshot["id"]
    # Repeating recovery cannot tag again or change the canonical ID.
    tagged = list(repository.tagged)
    recover_repository(repository, settings, 1, OWNER)
    assert repository.tagged == tagged


def test_solo_retry_keeps_parent_when_repository_setup_is_interrupted(local_db):
    test_database_schema.database_module.initialize_schema()

    def interrupt(_):
        raise SystemExit("Agent stopped during repository setup")

    with pytest.raises(SystemExit):
        RetentionService.run(repository_id=1, job_id=None, job_uuid=None, current_operation_uuid=None,
            current_policy=None, operation_uuid=RUN, repository_scope=True, request_context=lambda: {},
            set_repository=interrupt, resticapi=None, settings=local_db["agent"], send_report=lambda _: True)
    AgentReport.load_queue()
    AgentReport.recover_interrupted()
    task, = RetentionService.pending_tasks(local_db["agent"], 1)
    assert task["current_operation_uuid"] == RUN and task["repository_scope"] is True


def test_completion_cannot_use_a_foreign_marker(local_db):
    report, snapshot, _ = backup_report(local_db)
    repository = TaggedRepository(snapshot)
    foreign = {**deepcopy(snapshot), "id": "c" * 64, "tags": [
        tag if not tag.startswith("drastic-owner:") else f"drastic-owner:{FOREIGN}" for tag in snapshot["tags"]] + [RUN_COMPLETE_TAG]}
    repository.items[foreign["id"]] = deepcopy(foreign)
    complete_backup(repository, local_db["agent"], report, OWNER, JOB)
    assert repository.items[foreign["id"]] == foreign
    assert repository.tagged == [OLD] and not repository.forgotten


def test_reconciliation_during_report_delivery_cannot_requeue_or_acknowledge_stale_ids(local_db):
    report, snapshot, _ = backup_report(local_db)
    settings = local_db["agent"]
    repository = TaggedRepository(snapshot, "before")
    with pytest.raises(RuntimeError):
        complete_backup(repository, settings, report, OWNER, JOB)
    report.log_message("Completion pending", final_state=AgentOperationState.warning)
    report.finish()
    agent = Agent.__new__(Agent)
    sent = []

    def request(action, **kwargs):
        payload = kwargs["operation_json"]
        sent.append(payload)
        if payload["data"].get("backup_completion_pending"):
            repository.failure = None
            recover_repository(repository, settings, 1, OWNER)
        return {"success": True}

    agent._Agent__send_request = request
    reporting.flush(agent._Agent__send_report)
    assert len(AgentReport.finished_reports) == 1
    assert sent[-1]["state"] == "warning" and sent[-1]["ended"] is not None and not sent[-1]["artifacts"]
    new_id = report.artifacts[0]["snapshot_id"]
    assert new_id != OLD
    reporting.flush(agent._Agent__send_report)
    assert not AgentReport.finished_reports
    assert local_db["agent_operation_queue"].find_one(uuid=RUN) is None
    assert sent[-1]["artifacts"][0]["snapshot_id"] == new_id
    assert sent[-1]["data"]["snapshot_replacements"] == {OLD: new_id}


@pytest.mark.parametrize("legacy", [False, True])
def test_pending_warning_is_delivered_once_and_retained_until_next_repository_run(local_db, monkeypatch, legacy):
    report, snapshot, _ = backup_report(local_db)
    settings = local_db["agent"]
    repository = TaggedRepository(snapshot, "before")
    with pytest.raises(RuntimeError):
        complete_backup(repository, settings, report, OWNER, JOB)
    report.log_message("Completion pending", final_state=AgentOperationState.warning)
    if legacy:
        report.remove_data("backup_completion_pending")
    # A running progress acknowledgement must not suppress the terminal warning.
    report.sent = True
    report.finish()
    ended = report.ended
    agent = Agent.__new__(Agent)
    sent = []
    agent._Agent__send_request = lambda action, **kwargs: sent.append(kwargs["operation_json"]) or {"success": True}
    for _ in range(3):
        reporting.flush(agent._Agent__send_report)
    assert len(sent) == 1
    assert sent[0]["state"] == "warning" and sent[0]["ended"] is not None
    assert sent[0]["data"]["backup_completion_pending"] is True
    assert local_db["agent_operation_queue"].find_one(uuid=RUN) is not None
    assert repository.tagged == [OLD]  # Sending reports never retries Restic work.

    # Existing intents ignore obsolete retry timestamps and retain their backup parent.
    task = json.loads(settings.find_one(name=task_key(1, RUN))["settings"])
    task["retry_at"] = 10**15
    settings.update({"name": task_key(1, RUN), "settings": json.dumps(task)}, ["name"])
    settings.insert({"name": "backup_owner_uuid", "settings": OWNER})
    local_db["repositories"].insert({"id": 1, "kind": "custom", "location": "/repo"})
    agent._Agent__resticapi = repository
    monkeypatch.setattr(agent, "configure_repository", lambda _: None)
    cleanup = []

    def retention(**args):
        assert report.artifacts[0]["snapshot_id"] != OLD
        assert not settings.find_one(name=task_key(1, RUN))
        cleanup.append(args)

    monkeypatch.setattr(agent, "cmd_run_retention", retention)
    with pytest.raises(RuntimeError, match="Tagging unavailable"):
        agent.set_repository(1)
    assert not cleanup
    repository.failure = None
    agent.set_repository(1)
    assert len(cleanup) == 1 and cleanup[0]["current_operation_uuid"] == RUN
    assert report.data["backup_completion_failed"] is True
    queued = json.loads(local_db["agent_operation_queue"].find_one(uuid=RUN)["payload"])
    assert queued["data"]["backup_completion_failed"] is True
    assert report.ended == ended and report.state == AgentOperationState.warning
    reporting.flush(agent._Agent__send_report)
    assert sent[-1]["data"]["backup_completion_pending"] is False
    assert not AgentReport.finished_reports
    assert local_db["agent_operation_queue"].find_one(uuid=RUN) is None


@pytest.mark.parametrize("locked", [False, True])
def test_unlock_bypasses_completion_recovery_and_preserves_pending_work(local_db, monkeypatch, locked):
    report, snapshot, _ = backup_report(local_db)
    repository = TaggedRepository(snapshot, "before")
    settings = local_db["agent"]
    with pytest.raises(RuntimeError):
        complete_backup(repository, settings, report, OWNER, JOB)
    settings.insert({"name": "backup_owner_uuid", "settings": OWNER})
    local_db["repositories"].insert({"id": 1, "kind": "custom", "location": "/repo"})
    calls = []

    def unlock():
        calls.append("unlock")
        if locked:
            raise ResticError("Lock still active")
        return "removed stale lock"

    repository.unlock = unlock
    agent = Agent.__new__(Agent)
    agent._Agent__resticapi = repository
    monkeypatch.setattr(agent, "configure_repository", lambda _: calls.append("configure"))
    result = agent.cmd_unlock_repository(1)
    assert calls == ["configure", "unlock"]
    assert result.state == (AgentOperationState.failed if locked else AgentOperationState.success)
    assert settings.find_one(name=task_key(1, RUN)) is not None
    assert repository.tagged == [OLD]


@pytest.mark.parametrize("scenario", ["check", "interrupted", "existing_cleanup"])
def test_completion_recovery_preserves_cleanup_for_the_next_backup(local_db, monkeypatch, scenario):
    report, snapshot, _ = backup_report(local_db)
    repository = TaggedRepository(snapshot, "before")
    settings = local_db["agent"]
    with pytest.raises(RuntimeError):
        complete_backup(repository, settings, report, OWNER, JOB)
    report.log_message("Completion pending", final_state=AgentOperationState.warning)
    report.finish()
    settings.insert({"name": "backup_owner_uuid", "settings": OWNER})
    local_db["repositories"].insert({"id": 1, "kind": "custom", "location": "/repo"})
    retention_key = f"retention_task:1:{JOB}"
    expected_args = {"repository_id": 1, "job_id": 1, "job_uuid": JOB, "current_operation_uuid": RUN}
    if scenario == "existing_cleanup":
        expected_args = {**expected_args, "current_operation_uuid": str(UUID(int=99)), "repository_scope": False}
        settings.insert({"name": retention_key, "settings": json.dumps({"args": expected_args, "retry_at": 123})})

    agent = Agent.__new__(Agent)
    agent._Agent__resticapi = repository
    monkeypatch.setattr(agent, "configure_repository", lambda _: None)
    repository.failure = None
    repository.check = lambda **kwargs: {}
    if scenario == "interrupted":
        delete = settings.delete

        def interrupt(**kwargs):
            if kwargs.get("name") == task_key(1, RUN):
                raise SystemExit("Stopped before clearing completion intent")
            return delete(**kwargs)

        monkeypatch.setattr(settings, "delete", interrupt)
        with pytest.raises(SystemExit):
            agent.cmd_check_repository(1)
        monkeypatch.setattr(settings, "delete", delete)
    else:
        assert agent.cmd_check_repository(1).state == AgentOperationState.success
        assert settings.find_one(name=task_key(1, RUN)) is None

    assert json.loads(settings.find_one(name=retention_key)["settings"])["args"] == expected_args
    AgentReport.recover_interrupted()
    AgentReport.load_queue()
    cleanup = []
    monkeypatch.setattr(agent, "cmd_run_retention", lambda **kwargs: cleanup.append(kwargs))
    agent.set_repository(1)  # Preparation is repository-scoped, even for a different next job.
    assert cleanup == [expected_args]
    assert settings.find_one(name=task_key(1, RUN)) is None


def test_recovery_cannot_mutate_foreign_or_ambiguously_owned_snapshots(local_db):
    # Even identical job/run/artifact UUIDs do not let another owner finish our task.
    report, snapshot, _ = backup_report(local_db)
    repository = TaggedRepository(snapshot, "before")
    with pytest.raises(RuntimeError):
        complete_backup(repository, local_db["agent"], report, OWNER, JOB)
    with pytest.raises(ValueError, match="another backup owner"):
        recover_repository(repository, local_db["agent"], 1, FOREIGN)
    assert repository.tagged == [OLD]
    repository.items[OLD]["tags"].append(f"drastic-owner:{FOREIGN}")
    repository.failure = None
    with pytest.raises(ValueError, match="identity"):
        recover_repository(repository, local_db["agent"], 1, OWNER)
    assert repository.tagged == [OLD]


def test_recovery_checks_local_owner_proof_before_tagging(local_db):
    report, snapshot, _ = backup_report(local_db)
    repository = TaggedRepository(snapshot, "before")
    with pytest.raises(RuntimeError):
        complete_backup(repository, local_db["agent"], report, OWNER, JOB)
    operation = local_db["agent_operations"].find_one(uuid=RUN)
    local_db["agent_operations"].update({"id": operation["id"], "data": {
        **operation["data"], "owner_uuid": FOREIGN}}, ["id"])
    repository.failure = None
    with pytest.raises(ValueError, match="no longer belongs"):
        recover_repository(repository, local_db["agent"], 1, OWNER)
    assert repository.tagged == [OLD]


@pytest.mark.parametrize("cancelled", [False, True])
def test_partial_and_cancelled_backups_never_receive_a_completion_tag(local_db, cancelled):
    report, snapshot, _ = backup_report(local_db)
    report.set_data("backup_data_complete", cancelled)
    if cancelled:
        report.cancel_event.set()
    repository = TaggedRepository(snapshot)
    complete_backup(repository, local_db["agent"], report, OWNER, JOB)
    assert not repository.tagged and not local_db["agent"].find_one(name=task_key(1, RUN))


def test_real_restic_tag_changes_metadata_id_but_preserves_restore_contents(local_db, tmp_path):
    binary = os.environ.get("RESTIC_BINARY") or shutil.which("restic")
    if not binary:
        pytest.skip("Restic is required for the real completion-tag round trip")
    report, snapshot, _ = backup_report(local_db)
    source = tmp_path / "source"
    source.mkdir()
    (source / "data").write_bytes(b"complete backup\n")
    api = ResticApi(binary, repository=ResticRepository(location=str(tmp_path / "repo"), password="test-password"))
    api.init()
    status = api.backup(["."], cwd=str(source), tags=snapshot["tags"])
    old_id = status["snapshot_id"]
    artifact = local_db["agent_operation_artifacts"].find_one(operation_id=report.history_operation["id"])
    local_db["agent_operation_artifacts"].update({"id": artifact["id"], "snapshot_id": old_id}, ["id"])
    report.artifacts[0]["snapshot_id"] = old_id
    complete_backup(api, local_db["agent"], report, OWNER, JOB)
    marked, = api.snapshots()
    assert marked["id"] != old_id and marked["original"] == old_id
    assert marked["tags"].count(RUN_COMPLETE_TAG) == 1
    assert report.data["backup_run_complete"]
    target = tmp_path / "restored"
    api.restore(marked["id"], str(target), ["/"])
    assert (target / "data").read_bytes() == b"complete backup\n"


@pytest.mark.parametrize("partial,stats_warning,retention_crash", [
    (False, False, False), (False, True, False), (True, False, False), (False, False, True)])
def test_shared_lifecycle_tags_only_after_all_data_and_before_post_backup_warnings(local_db, partial, stats_warning, retention_crash):
    test_database_schema.database_module.initialize_schema()
    AgentReport.pending_reports, AgentReport.running_operations = [], {}
    repository = TaggedRepository({"id": OLD, "tree": "b" * 64, "tags": []})
    repository.items.clear()

    def stats():
        if stats_warning:
            raise ResticError("Statistics unavailable")
        return {}

    repository.stats = stats

    def retention(**kwargs):
        if retention_crash:
            raise SystemExit("Agent stopped during retention")
        return AgentReport.command_report(data={"cleanup_pending": False}).finish()

    agent = SimpleNamespace(owner_uuid=OWNER, resticapi=repository,
        set_repository=lambda identity: {"id": identity, "location": "/repo"},
        execute_actions=lambda **kwargs: None,
        cmd_run_retention=retention)

    class Handler(BackupJobHandler):
        def run_backup(self, report):
            report.set_data("backup_items_total", 2)
            for index, identity in enumerate((OLD, "d" * 64)):
                artifact = self.start_artifact(f"vm:{101 + index}")
                repository.items[identity] = {"id": identity, "tree": "b" * 64,
                    "tags": self.backup_tags(artifact, report.uuid)}
                self.finish_artifact(artifact, snapshot_id=identity,
                    state=AgentOperationState.failed if partial and index else AgentOperationState.success)
                assert not repository.tagged
            if partial:
                raise ResticError("Second VM incomplete")

    handler = Handler(agent, {"id": 1, "uuid": JOB}, 1, operation_uuid=RUN)
    if retention_crash:
        with pytest.raises(SystemExit):
            handler.run()
        AgentReport.load_queue()
        AgentReport.recover_interrupted()
        recovered = next(report for report in AgentReport.finished_reports if report.uuid == RUN)
        assert recovered.data["retention_logs_nested"] is True
        return
    report = handler.run()
    if partial:
        assert report.state == AgentOperationState.failed
        assert not repository.tagged and not report.data.get("backup_run_complete")
    else:
        assert repository.tagged == ["d" * 64]
        assert report.data["backup_run_complete"]
        assert report.state == (AgentOperationState.warning if stats_warning else AgentOperationState.success)
