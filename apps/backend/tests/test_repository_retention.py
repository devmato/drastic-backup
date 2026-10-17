"""Retained reports are the single authority for run membership and cleanup recovery."""

import pytest
import test_proxmox_restore_api

from drastic_server.extensions import db
from drastic_server.models.agent import (
    Agent,
    AgentOperation,
    AgentOperationArtifact,
    AgentOperationLog,
    AgentOperationState,
    AgentOperationType,
)
from drastic_server.models.job import Job, JobType
from drastic_server.models.repository import Repository
from drastic_server.services.agent import AgentService
from drastic_server.services.agent.management import delete_agent
from drastic_server.services.agent.request import AgentException, AgentRequestService
from drastic_server.services.exceptions import ResourceNotFound
from drastic_server.services.jobs.management import delete_job
from drastic_server.services.operations.history import (
    delete_operation,
    get_operation,
    list_operations,
)
from drastic_server.services.operations.lifecycle import (
    fail_started_agent_operation,
    start_agent_operation,
    unknown_agent_operation_dispatch_response,
)
from drastic_server.services.operations.reports import AgentOperationService
from drastic_server.services.retention import create_retention, update_retention

restore_api = test_proxmox_restore_api.restore_api


@pytest.mark.parametrize("state", [AgentOperationState.running, AgentOperationState.success])
def test_late_dispatch_callbacks_do_not_replace_confirmed_report_data(restore_api, state):
    _, _, agent, _, _, _ = restore_api
    operation = start_agent_operation(agent=agent, repository=agent.repositories[0],
        operation_type=AgentOperationType.retention, msg="Queued deletion", data={"manual_delete": True, "scope": "repository"})
    assert operation.data["dispatch_status"] == "pending"
    confirmed = {"manual_delete": True, "scope": "repository", "deleted_snapshot_ids": ["snapshot"]}
    # Update the database without refreshing the ORM object, as another request would.
    db.session.execute(AgentOperation.__table__.update().where(AgentOperation.id == operation.id).values(state=state, data=confirmed))
    response = unknown_agent_operation_dispatch_response(operation, "Timed out")
    assert response["dispatch_status"] == "accepted"
    fail_started_agent_operation(operation, "Late dispatch error")
    db.session.refresh(operation)
    assert operation.state == state and operation.data == confirmed


@pytest.mark.parametrize("kind", [AgentOperationType.backup, AgentOperationType.restore])
def test_agent_deletion_waits_for_running_operations(restore_api, kind):
    client, _, agent, _, job, _ = restore_api
    operation = AgentOperation(agent=agent, job=job, type=kind, state=AgentOperationState.running)
    db.session.add(operation)
    db.session.commit()
    assert client.delete(f"/api/agents/{agent.id}").status_code == 409
    db.session.refresh(operation)
    assert operation.agent_id == agent.id and agent.session is not None
    operation.state = AgentOperationState.success
    db.session.commit()
    assert client.delete(f"/api/agents/{agent.id}").status_code == 200
    assert operation.agent_id is None and operation.user_id == agent.user_id


@pytest.mark.parametrize("order", ["before_backup", "after_backup", "other_repository", "other_owner"])
def test_manual_deletion_receipt_reconciles_late_backup_artifacts_only_in_its_scope(restore_api, order):
    _, _, agent, foreign, job, _ = restore_api
    repository = agent.repositories[0]
    observer = Agent(user=agent.user, secret="observer")
    observer.repositories.append(repository)
    db.session.add(observer)
    db.session.commit()
    backup = {"uuid": "backup", "type": "backup", "state": "success", "job_id": job.id, "repository_id": repository.id,
        "data": {"job_uuid": job.uuid}, "artifacts": [
            {"uuid": "artifact", "artifact_key": "default", "snapshot_id": "snapshot", "state": "success"}]}
    request = AgentRequestService(agent)
    if order == "after_backup":
        request.operation(backup)
    executor = foreign if order == "other_owner" else observer
    target = repository
    if order in {"other_repository", "other_owner"}:
        target = Repository(user=executor.user, name="Other repository", kind="custom", location="/other")
        db.session.add(target)
        executor.repositories.append(target)
        db.session.commit()
    deletion = {"uuid": "deletion", "type": "retention", "state": "failed", "repository_id": target.id,
        "data": {"scope": "repository", "manual_delete": True, "deleted_snapshot_ids": ["snapshot"]}}
    service = AgentOperationService(executor)
    service.ingest(deletion)
    service.ingest({**deletion, "state": "running", "data": {}})  # Delayed progress cannot erase the receipt.
    for _ in range(2):
        request.operation(backup)
    artifact = AgentOperationArtifact.query.filter_by(uuid="artifact").one()
    assert bool(artifact.forgotten_at) is (order in {"before_backup", "after_backup"})
    assert bool(request.retention_context(repository.id, job_uuid=job.uuid)["runs"]) is (artifact.forgotten_at is None)


def test_reports_remain_owned_and_usable_after_job_and_agent_deletion(restore_api, monkeypatch):
    client, _, agent, foreign, job, _ = restore_api
    monkeypatch.setattr(AgentService, "sync", lambda *a, **kw: None)
    user = agent.user
    repository = agent.repositories[0]
    observer = Agent(user=user, secret="observer")
    observer.repositories.append(repository)
    db.session.add(observer)
    db.session.commit()
    request = AgentRequestService(observer)
    source = AgentOperation(agent=agent, job=job, repository=repository, type=AgentOperationType.backup,
                            state=AgentOperationState.success, data={"job_uuid": job.uuid})
    artifact = AgentOperationArtifact(operation=source, artifact_key="default", snapshot_id="snapshot", state="success")
    log = AgentOperationLog(operation=source, sequence=1, level="info", message="Backup complete")
    db.session.add_all([source, artifact, log])
    db.session.commit()
    policy = create_retention(user.id, {"name": "Keep two", "rtype": "count", "keep_last": 2})
    update_retention(user.id, policy.id, {"is_default": True})
    job_uuid = job.uuid
    assert request.retention_context(repository.id)["job_uuids"] == [job_uuid]
    db.session.execute(AgentOperation.__table__.update().where(AgentOperation.id == source.id).values(
        data={**source.data, "cleanup_pending": False}))
    delete_job(user.id, job.id)
    assert source.data["cleanup_pending"] is False
    assert request.retention_context(repository.id)["job_uuids"] == []
    assert request.retention_context(repository.id)["default"]["keep_last"] == 2
    db.session.execute(AgentOperation.__table__.update().where(AgentOperation.id == source.id).values(
        data={**source.data, "retention_state": "success"}))
    delete_agent(user.id, agent.id)
    assert source.data["retention_state"] == "success"
    assert source.agent_id is None and source.user_id == user.id
    assert get_operation(user.id, source.id).logs[0].message == "Backup complete"
    assert source in list_operations(user.id, None, {})
    detail = client.get(f"/api/agents/operations/{source.id}")
    assert detail.status_code == 200 and detail.json["agent_id"] is None
    assert "deleted" in detail.json["agent_display_name"].lower()
    with pytest.raises(ResourceNotFound):
        get_operation(foreign.user_id, source.id)
    context = request.retention_context(repository.id)
    assert context["runs"][0]["job_uuid"] == job_uuid
    assert context["runs"][0]["artifacts"][0]["snapshot_id"] == artifact.snapshot_id
    update_retention(user.id, policy.id, {"keep_last": 3})
    assert request.retention_context(repository.id)["default"]["keep_last"] == 3
    delete_operation(user.id, source.id)
    assert request.retention_context(repository.id)["runs"] == []


def test_live_context_includes_all_owned_jobs_only(restore_api):
    _, _, agent, foreign, job, _ = restore_api
    agent.protocol_version = 21
    offline = Agent(user=agent.user, secret="offline")
    disabled = Job(agent=offline, name="Disabled", type=JobType.file, retention_mode="disabled")
    foreign_job = Job(agent=foreign, name="Foreign", type=JobType.file)
    db.session.add_all([offline, disabled, foreign_job])
    db.session.commit()
    request = AgentRequestService(agent)
    context = request.retention_context(agent.repositories[0].id)
    assert set(context.pop("job_uuids")) == {job.uuid, disabled.uuid}
    assert context == {"default": None, "runs": [], "check_token": agent.repositories[0].retention_check_token}
    with pytest.raises(AgentException, match="not assigned"):
        AgentRequestService(foreign).retention_context(agent.repositories[0].id)
    with pytest.raises(AgentException, match="not assigned"):
        AgentRequestService(offline).retention_context(agent.repositories[0].id)


@pytest.mark.parametrize("wrong", [None, "owner", "snapshot", "operation", "repository"])
def test_repository_results_update_only_matching_owned_artifacts(restore_api, wrong):
    _, _, agent, foreign, job, _ = restore_api
    repository = agent.repositories[0]
    source = AgentOperation(agent=agent, job=job, repository=repository,
                            type=AgentOperationType.backup, state=AgentOperationState.success)
    artifact = AgentOperationArtifact(operation=source, artifact_key="default", snapshot_id="snapshot", state="success")
    db.session.add(artifact)
    db.session.commit()
    payload = {"uuid": "cleanup", "type": "retention", "state": "success", "repository_id": repository.id,
        "data": {"scope": "repository", "forgotten_artifacts": [{
            "uuid": artifact.uuid, "operation_uuid": source.uuid, "snapshot_id": artifact.snapshot_id,
            "forgotten_at": "2026-01-01T00:00:00Z"}]}}
    if wrong == "owner":
        source.agent = foreign
    elif wrong == "repository":
        source.repository = None
    elif wrong:
        payload["data"]["forgotten_artifacts"][0][wrong + "_id" if wrong == "snapshot" else "operation_uuid"] = "wrong"
    db.session.commit()
    service = AgentOperationService(agent)
    if wrong:
        with pytest.raises(PermissionError):
            service.ingest(payload)
        db.session.rollback()
        assert artifact.forgotten_at is None
    else:
        service.ingest(payload)
        service.ingest(payload)
        assert artifact.forgotten_at is not None


def test_context_keeps_snapshot_ids_until_cleanup_is_reported_and_replay_cannot_undo_it(restore_api):
    _, _, agent, _, job, _ = restore_api
    repository = agent.repositories[0]
    source = AgentOperation(agent=agent, job=job, repository=repository, type=AgentOperationType.backup,
                            state=AgentOperationState.success, data={"job_uuid": job.uuid})
    artifact = AgentOperationArtifact(operation=source, artifact_key="default", snapshot_id="snapshot", state="success")
    db.session.add(artifact)
    db.session.commit()
    item = {"uuid": artifact.uuid, "operation_uuid": source.uuid, "snapshot_id": artifact.snapshot_id}
    report = {"uuid": "cleanup", "type": "retention", "state": "running", "repository_id": repository.id,
               "data": {"scope": "repository"}}
    request = AgentRequestService(agent)
    assert request.retention_context(repository.id, job_uuid=job.uuid)["runs"][0]["artifacts"][0]["snapshot_id"] == "snapshot"
    assert artifact.forgotten_at is None
    finished = {**report, "state": "success", "data": {**report["data"], "forgotten_artifacts": [
        {**item, "forgotten_at": "2026-01-01T00:00:00Z"}]}}
    assert request.retention_context(repository.id, finished)["runs"] == []
    request.retention_context(repository.id, report)
    assert artifact.forgotten_at
    delete_operation(agent.user_id, source.id)


@pytest.mark.parametrize("operation_type", ["retention", "repository_check"])
def test_full_check_only_clears_the_observed_failure_and_failure_replays_are_idempotent(restore_api, operation_type):
    _, _, agent, _, _, _ = restore_api
    repository = agent.repositories[0]
    service = AgentOperationService(agent)
    service.ingest({"uuid": "locked-check", "type": "repository_check", "state": "failed", "repository_id": repository.id,
                    "data": {"repository_locked": True}})
    assert repository.retention_check_token is None
    failure = {"uuid": "first-failure", "type": operation_type, "state": "failed", "repository_id": repository.id,
               "data": {"scope": "repository", **({"retention_check_failed": True} if operation_type == "retention" else {})}}
    service.ingest(failure)
    observed = repository.retention_check_token
    service.ingest({**failure, "uuid": "newer-failure"})
    passed = {"uuid": "full-check", "type": "retention", "state": "success", "repository_id": repository.id,
              "data": {"scope": "repository", "retention_check_full": True, "retention_check_verified": observed}}
    service.ingest(passed)
    assert repository.retention_check_token == "newer-failure"
    service.ingest({**passed, "uuid": "later-full-check", "data": {**passed["data"], "retention_check_verified": "newer-failure"}})
    assert repository.retention_check_token is None
    service.ingest(failure)
    assert repository.retention_check_token is None


def test_late_progress_cannot_retract_an_acknowledged_complete_run(restore_api):
    _, _, agent, _, job, _ = restore_api
    repository = agent.repositories[0]
    payload = {"uuid": "backup", "type": "backup", "state": "running", "job_id": job.id, "repository_id": repository.id,
               "data": {"job_uuid": job.uuid, "backup_data_complete": True, "backup_items_total": 1},
               "artifacts": [{"uuid": "artifact", "artifact_key": "default", "snapshot_id": "snapshot", "state": "success"}]}
    request = AgentRequestService(agent)
    assert request.retention_context(repository.id, payload, job.uuid)["runs"][0]["artifacts"][0]["snapshot_id"] == "snapshot"
    stale = {**payload, "data": {"job_uuid": job.uuid, "backup_data_complete": False}, "artifacts": [
        {**payload["artifacts"][0], "snapshot_id": None, "state": "running"}]}
    result = request.retention_context(repository.id, stale, job.uuid)["runs"][0]
    assert result["artifacts"][0]["snapshot_id"] == "snapshot"


def test_manual_check_requires_full_data_to_clear_its_captured_token(restore_api):
    _, _, agent, _, _, _ = restore_api
    repository = agent.repositories[0]
    repository.retention_check_token = "known-failure"
    token = repository.retention_check_token
    for read_data in ("5%", "100%"):
        check = AgentOperation(agent=agent, repository=repository, type=AgentOperationType.repository_check,
                               state=AgentOperationState.running, data={"retention_check_token": token})
        db.session.add(check)
        db.session.commit()
        AgentOperationService(agent).ingest({"uuid": check.uuid, "type": "repository_check", "state": "success",
            "repository_id": repository.id, "data": {"read_data": read_data, "check": {}}})
        assert repository.retention_check_token == (None if read_data == "100%" else token)
