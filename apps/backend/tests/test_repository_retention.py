"""Current retention context is account-scoped and independent of backup history."""

from datetime import datetime, timezone
from uuid import uuid4

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


def test_completion_id_replacement_survives_old_reports_and_rejects_other_owners(restore_api):
    _, _, agent, foreign, job, _ = restore_api
    repository = agent.repositories[0]
    old_id, new_id = "a" * 64, "b" * 64
    payload = {"uuid": str(uuid4()), "type": "backup", "state": "running", "job_id": job.id,
               "repository_id": repository.id, "data": {"owner_uuid": agent.user.backup_owner_uuid, "job_uuid": job.uuid},
               "artifacts": [{"uuid": str(uuid4()), "artifact_key": "default", "snapshot_id": old_id, "state": "success"}]}
    service = AgentOperationService(agent)
    operation_id = service.ingest(payload)
    client = restore_api[0]
    deletion = client.delete(f"/api/repositories/{repository.id}")
    assert deletion.status_code == 409 and "running operation" in deletion.json["message"]
    assert db.session.get(Repository, repository.id) is not None
    replacement = {**payload, "state": "success", "data": {**payload["data"],
        "snapshot_replacements": {old_id: new_id}, "backup_run_complete": True},
        "artifacts": [{**payload["artifacts"][0], "snapshot_id": new_id}]}
    for invalid in ({**replacement["data"], "owner_uuid": foreign.user.backup_owner_uuid},
                    {**replacement["data"], "snapshot_replacements": {old_id: old_id}}):
        with pytest.raises((PermissionError, ValueError)):
            service.ingest({**replacement, "data": invalid})
        db.session.rollback()
    service.ingest(replacement)
    operation = db.session.get(AgentOperation, operation_id)
    artifact = AgentOperationArtifact.query.filter_by(operation_id=operation_id).one()
    artifact.forgotten_at = datetime.now(timezone.utc).replace(tzinfo=None)
    db.session.commit()
    forgotten = artifact.forgotten_at
    service.ingest(payload)
    db.session.refresh(operation)
    db.session.refresh(artifact)
    assert artifact.snapshot_id == new_id and artifact.forgotten_at == forgotten
    assert operation.data["snapshot_replacements"] == {old_id: new_id}
    assert operation.data["backup_run_complete"] is True
    assert operation.state == AgentOperationState.success
    assert client.delete(f"/api/repositories/{repository.id}").status_code == 200
    db.session.refresh(operation)
    assert operation.repository_id is None
    assert service.ingest(replacement) == operation_id  # Lost final acknowledgements remain replayable.
    db.session.refresh(artifact)
    assert artifact.snapshot_id == new_id and artifact.forgotten_at == forgotten


@pytest.mark.parametrize("manual", [False, True])
def test_replayed_cleanup_reports_cannot_erase_confirmed_snapshot_removals(restore_api, manual):
    _, _, agent, _, _, _ = restore_api
    operation = start_agent_operation(agent=agent, repository=agent.repositories[0],
        operation_type=AgentOperationType.retention, msg="Cleanup", data={"scope": "repository"})
    payload = {"uuid": operation.uuid, "type": "retention", "state": "running", "repository_id": operation.repository_id,
               "data": {"scope": "repository", "manual_delete": manual, "deleted_snapshot_ids": ["removed"]}}
    service = AgentOperationService(agent)
    service.ingest(payload)
    service.ingest({**payload, "state": "warning", "data": {"scope": "repository", "deleted_snapshot_ids": []}})
    db.session.refresh(operation)
    assert operation.data["deleted_snapshot_ids"] == ["removed"]
    assert bool(operation.data.get("manual_delete")) is manual


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


def test_running_report_cannot_be_deleted_after_its_job_is_deleted(restore_api, monkeypatch):
    client, _, agent, _, job, _ = restore_api
    monkeypatch.setattr(AgentService, "sync", lambda *a, **kw: None)
    repository = agent.repositories[0]
    operation = AgentOperation(agent=agent, job=job, repository=repository,
        type=AgentOperationType.backup, state=AgentOperationState.running, data={"job_uuid": job.uuid})
    db.session.add(operation)
    db.session.commit()
    delete_job(agent.user_id, job.id)
    url = f"/api/agents/operations/{operation.id}"
    assert client.delete(url).status_code == 409
    assert AgentRequestService(agent).retention_context(repository.id)["running_operation_uuids"] == [operation.uuid]
    assert client.delete(f"/api/agents/{agent.id}").status_code == 409
    operation.state = AgentOperationState.success
    db.session.commit()
    assert client.delete(url).status_code == 200
    assert AgentRequestService(agent).retention_context(repository.id)["running_operation_uuids"] == []


def test_reports_remain_owned_and_usable_after_job_and_agent_deletion(restore_api, monkeypatch):
    client, _, agent, foreign, job, _ = restore_api
    monkeypatch.setattr(AgentService, "sync", lambda *a, **kw: None)
    user = agent.user
    owner = user.backup_owner_uuid
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
    assert context["owner_uuid"] == owner
    update_retention(user.id, policy.id, {"keep_last": 3})
    assert request.retention_context(repository.id)["default"]["keep_last"] == 3
    context = request.retention_context(repository.id)
    delete_operation(user.id, source.id)
    assert request.retention_context(repository.id) == context


def test_live_context_includes_all_owned_jobs_only(restore_api):
    _, _, agent, foreign, job, _ = restore_api
    agent.protocol_version = 23
    offline = Agent(user=agent.user, secret="offline")
    disabled = Job(agent=offline, name="Disabled", type=JobType.file, retention_mode="disabled")
    foreign_job = Job(agent=foreign, name="Foreign", type=JobType.file)
    db.session.add_all([offline, disabled, foreign_job])
    running = AgentOperation(agent=offline, repository=agent.repositories[0], type=AgentOperationType.backup,
                             state=AgentOperationState.running)
    db.session.add(running)
    foreign_running = AgentOperation(agent=foreign, repository=agent.repositories[0], type=AgentOperationType.backup,
                                     state=AgentOperationState.running)
    db.session.add(foreign_running)
    db.session.commit()
    request = AgentRequestService(agent)
    context = request.retention_context(agent.repositories[0].id)
    assert set(context.pop("job_uuids")) == {job.uuid, disabled.uuid}
    assert context == {"owner_uuid": agent.user.backup_owner_uuid, "default": None,
                       "running_operation_uuids": [running.uuid], "check_token": agent.repositories[0].retention_check_token}
    assert request.sync()["backup_owner_uuid"] == context["owner_uuid"] != foreign.user.backup_owner_uuid
    with pytest.raises(AgentException, match="not assigned"):
        AgentRequestService(foreign).retention_context(agent.repositories[0].id)
    with pytest.raises(AgentException, match="not assigned"):
        AgentRequestService(offline).retention_context(agent.repositories[0].id)


@pytest.mark.parametrize("wrong", [None, "owner", "snapshot", "repository", "other_agent"])
def test_repository_results_update_only_matching_owned_artifacts(restore_api, wrong):
    _, _, agent, foreign, job, _ = restore_api
    repository = agent.repositories[0]
    source = AgentOperation(agent=agent, job=job, repository=repository,
                            type=AgentOperationType.backup, state=AgentOperationState.success)
    artifact = AgentOperationArtifact(operation=source, artifact_key="default", snapshot_id="snapshot", state="success")
    db.session.add(artifact)
    db.session.commit()
    payload = {"uuid": "cleanup", "type": "retention", "state": "success", "repository_id": repository.id,
        "data": {"scope": "repository", "deleted_snapshot_ids": [artifact.snapshot_id]}}
    if wrong == "owner":
        source.agent = foreign
    elif wrong == "repository":
        source.repository = None
    elif wrong == "snapshot":
        payload["data"]["deleted_snapshot_ids"] = ["wrong"]
    elif wrong == "other_agent":
        source.agent = Agent(user=agent.user, secret="other-agent")
    db.session.commit()
    service = AgentOperationService(agent)
    service.ingest(payload)
    service.ingest(payload)
    assert bool(artifact.forgotten_at) is (wrong in {None, "other_agent"})


def test_cleanup_results_do_not_change_retention_context_and_replay_cannot_undo_them(restore_api):
    _, _, agent, _, job, _ = restore_api
    repository = agent.repositories[0]
    source = AgentOperation(agent=agent, job=job, repository=repository, type=AgentOperationType.backup,
                            state=AgentOperationState.success, data={"job_uuid": job.uuid})
    artifact = AgentOperationArtifact(operation=source, artifact_key="default", snapshot_id="snapshot", state="success")
    db.session.add(artifact)
    db.session.commit()
    report = {"uuid": "cleanup", "type": "retention", "state": "running", "repository_id": repository.id,
               "data": {"scope": "repository"}}
    request = AgentRequestService(agent)
    context = request.retention_context(repository.id)
    assert artifact.forgotten_at is None
    finished = {**report, "state": "success", "data": {**report["data"], "deleted_snapshot_ids": [artifact.snapshot_id]}}
    request.operation(finished)
    request.operation(report)
    assert request.retention_context(repository.id) == context
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
    request.operation(payload)
    stale = {**payload, "data": {"job_uuid": job.uuid, "backup_data_complete": False}, "artifacts": [
        {**payload["artifacts"][0], "snapshot_id": None, "state": "running"}]}
    request.operation(stale)
    assert AgentOperationArtifact.query.filter_by(uuid="artifact").one().snapshot_id == "snapshot"


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
