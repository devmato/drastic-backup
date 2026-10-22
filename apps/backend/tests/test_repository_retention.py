"""Current retention context is account-scoped and independent of backup history."""

from datetime import datetime, timezone
from uuid import uuid4

import pytest
import test_proxmox_restore_api
from sqlalchemy import event

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
from drastic_server.services.exceptions import ResourceNotFound, RestoreServiceException
from drastic_server.services.jobs.management import delete_job
from drastic_server.services.operations.history import (
    delete_operation,
    get_operation,
    list_operations,
    retention_logs,
)
from drastic_server.services.operations.lifecycle import (
    fail_started_agent_operation,
    start_agent_operation,
    unknown_agent_operation_dispatch_response,
)
from drastic_server.services.operations.reports import AgentOperationService
from drastic_server.services.restore import RestoreService
from drastic_server.services.retention import create_retention, update_retention

restore_api = test_proxmox_restore_api.restore_api


def test_finished_pending_backup_keeps_deletion_fences_until_verified_replacement(restore_api, monkeypatch):
    client, _, agent, _, job, _ = restore_api
    agent.protocol_version = 27
    db.session.commit()
    repository = agent.repositories[0]
    old_id, new_id = "a" * 64, "b" * 64
    payload = {"uuid": str(uuid4()), "type": "backup", "state": "warning", "job_id": job.id,
        "repository_id": repository.id, "started": "2026-10-20T00:00:00Z", "ended": "2026-10-20T00:10:00Z",
        "data": {"owner_uuid": agent.user.backup_owner_uuid, "job_uuid": job.uuid,
                 "backup_data_complete": True, "backup_completion_pending": True},
        "artifacts": [{"uuid": str(uuid4()), "artifact_key": "default", "snapshot_id": old_id, "state": "success"}]}
    service = AgentOperationService(agent)
    for key in ("backup_completion_pending", "backup_completion_failed"):
        with pytest.raises(ValueError, match=f"Invalid {key}"):
            service.ingest({**payload, "data": {**payload["data"], key: "false"}})
        db.session.rollback()
    operation_id = service.ingest(payload)
    operation = db.session.get(AgentOperation, operation_id)
    assert operation.state == AgentOperationState.warning and operation.ended is not None
    assert operation.blocks_cleanup
    assert list_operations(agent.user_id, None, {"state": "running"}) == []
    assert AgentRequestService(agent).retention_context(repository.id)["running_operation_uuids"] == [operation.uuid]
    assert client.delete(f"/api/agents/operations/{operation_id}").status_code == 409
    assert client.delete(f"/api/agents/{agent.id}").status_code == 409
    assert client.delete(f"/api/repositories/{repository.id}").status_code == 409

    snapshot = {"id": old_id, "tags": [f"drastic-owner:{agent.user.backup_owner_uuid}",
        f"job_uuid:{job.uuid}", f"operation_uuid:{operation.uuid}"]}
    monkeypatch.setattr(RestoreService, "list_snapshots", lambda *args: {"snapshots": [snapshot]})
    with pytest.raises(RestoreServiceException, match="finalization is pending"):
        RestoreService.delete_point(agent.user_id, {"agent_id": agent.id, "repository_id": repository.id,
            "snapshot_id": old_id, "snapshot_ids": [old_id]})
    db.session.rollback()

    # Neither omission nor an explicit false value can release unresolved references.
    for data in ({}, {"backup_completion_pending": False}):
        service.ingest({**payload, "data": data})
        db.session.refresh(operation)
        assert operation.blocks_cleanup
    service.ingest({**payload, "data": {**payload["data"], "backup_run_complete": True,
        "backup_completion_pending": False, "snapshot_replacements": {old_id: new_id}},
        "artifacts": [{**payload["artifacts"][0], "snapshot_id": new_id}]})
    service.ingest(payload)  # An old pending report cannot reactivate the fence.
    db.session.refresh(operation)
    assert operation.state == AgentOperationState.warning
    assert operation.data["backup_completion_pending"] is False
    assert operation.data["backup_completion_failed"] is True
    assert not operation.blocks_cleanup
    assert operation.artifacts[0].snapshot_id == new_id
    assert AgentRequestService(agent).retention_context(repository.id)["running_operation_uuids"] == []
    assert client.delete(f"/api/agents/operations/{operation_id}").status_code == 200


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
    agent.protocol_version = 26
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
    observer.protocol_version = 26
    policy = create_retention(user.id, {"name": "Keep two", "rules": [{"type": "last", "count": 2}]})
    update_retention(user.id, policy.id, {"is_default": True})
    job_uuid = job.uuid
    assert request.retention_context(repository.id)["job_uuids"] == [job_uuid]
    db.session.execute(AgentOperation.__table__.update().where(AgentOperation.id == source.id).values(
        data={**source.data, "cleanup_pending": False}))
    delete_job(user.id, job.id)
    assert source.data["cleanup_pending"] is False
    assert request.retention_context(repository.id)["job_uuids"] == []
    assert request.retention_context(repository.id)["default"]["rules"][0]["count"] == 2
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
    update_retention(user.id, policy.id, {"rules": [{"type": "last", "count": 3}]})
    assert request.retention_context(repository.id)["default"]["rules"][0]["count"] == 3
    context = request.retention_context(repository.id)
    delete_operation(user.id, source.id)
    assert request.retention_context(repository.id) == context


def test_live_context_includes_all_owned_jobs_only(restore_api):
    _, _, agent, foreign, job, _ = restore_api
    agent.protocol_version = 26
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
    assert context == {"owner_uuid": agent.user.backup_owner_uuid, "default": None, "timezone": "UTC",
                       "running_operation_uuids": [running.uuid], "check_token": agent.repositories[0].retention_check_token}
    assert request.sync()["backup_owner_uuid"] == context["owner_uuid"] != foreign.user.backup_owner_uuid
    named_context = request.retention_context(agent.repositories[0].id, include_job_names=True)
    assert named_context["job_names"] == {job.uuid: job.name, disabled.uuid: "Disabled"}
    assert "job_names" not in request.retention_context(agent.repositories[0].id)
    default = create_retention(agent.user_id, {"name": "Default", "rules": [{"type": "last", "count": 2}]})
    update_retention(agent.user_id, default.id, {"is_default": True})
    custom = create_retention(agent.user_id, {"name": "Custom", "rules": [{"type": "last", "count": 3}]})
    job.retention_mode, job.retention = "custom", custom
    db.session.commit()
    context = request.retention_context(agent.repositories[0].id, include_policies=True, include_job_names=True)
    assert context["job_names"] == named_context["job_names"]
    assert set(context["job_policies"]) == {job.uuid, disabled.uuid}
    assert context["job_policies"][job.uuid]["rules"][0]["count"] == 3
    assert context["job_policies"][disabled.uuid] is None and context["default"]["rules"][0]["count"] == 2
    with pytest.raises(AgentException, match="not assigned"):
        AgentRequestService(foreign).retention_context(agent.repositories[0].id)
    with pytest.raises(AgentException, match="not assigned"):
        AgentRequestService(offline).retention_context(agent.repositories[0].id)


def test_retention_crud_validates_rules_and_context_uses_effective_account_zone(restore_api, monkeypatch):
    client, _, agent, foreign, _, _ = restore_api
    agent.protocol_version = 26
    monkeypatch.setattr(AgentService, "sync", lambda *a, **kw: None)
    rules = [{"type": "last", "count": 2}, {"type": "calendar", "period": "week", "count": 4, "weekday": 6},
             {"type": "recent", "months": 1}, {"type": "since", "date": "2026-01-01"}]
    response = client.post("/api/retentions/", json={"name": "Flexible", "rules": rules})
    assert response.status_code == 201
    policy_id = response.json["id"]
    assert client.get(f"/api/retentions/{policy_id}").json["rules"] == rules
    assert client.put(f"/api/retentions/{policy_id}", json={"rules": []}).status_code == 422
    assert client.put(f"/api/retentions/{policy_id}", json={"keep_last": 1}).status_code == 422
    assert client.put(f"/api/retentions/{policy_id}", json={"rules": [{"type": "calendar", "period": "week", "count": 1}]}).status_code == 422
    assert client.get(f"/api/retentions/{policy_id}").json["rules"] == rules
    assert client.put(f"/api/retentions/{policy_id}", json={"is_default": True}).status_code == 200
    request = AgentRequestService(agent)
    assert request.retention_context(agent.repositories[0].id)["timezone"] == "UTC"
    agent.user.schedule_timezone = "Europe/Berlin"
    db.session.commit()
    context = request.retention_context(agent.repositories[0].id)
    assert context["timezone"] == "Europe/Berlin" and context["default"] == {"rules": rules}
    assert request.sync()["retentions"][0]["rules"] == rules
    from drastic_server.services.retention import get_retention
    with pytest.raises(ResourceNotFound):
        get_retention(foreign.user_id, policy_id)
    agent.protocol_version = 25
    with pytest.raises(AgentException, match="protocol 26"):
        request.retention_context(agent.repositories[0].id)
    with pytest.raises(AgentException, match="protocol 26"):
        request.sync()
    assert client.delete(f"/api/retentions/{policy_id}").status_code == 200


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
    agent.protocol_version = 26
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


@pytest.mark.parametrize("parent_type,legacy", [("backup", False), ("backup", True), ("retention", False)])
def test_retention_retries_share_original_history_and_logs(restore_api, monkeypatch, parent_type, legacy):
    from drastic_server.services.operations import reports

    client, _, agent, _, job, _ = restore_api
    events = []
    monkeypatch.setattr(reports, "emit_operation_update", lambda operation: events.append(operation.uuid))
    service = AgentOperationService(agent)
    parent_uuid = str(uuid4())
    child = {"uuid": str(uuid4()), "type": "retention", "source": "triggered", "state": "warning",
        "job_id": job.id if parent_type == "backup" else None,
        "repository_id": agent.repositories[0].id, "parent_operation_uuid": parent_uuid,
        "started": "2026-10-19T02:01:00Z", "ended": "2026-10-19T02:02:00Z",
        "data": {"scope": "repository", "job_uuid": job.uuid if parent_type == "backup" else None, "cleanup_pending": True},
        "logs": [{"sequence": 1, "level": "warning", "message": "Retention deferred"}]}
    child_id = service.ingest(child)  # Offline replay may precede its parent.
    parent = {"uuid": parent_uuid, "type": parent_type, "state": "warning", "source": "manual",
        "job_id": child["job_id"], "repository_id": agent.repositories[0].id,
        "started": "2026-10-19T02:00:00Z", "ended": "2026-10-19T02:03:00Z",
        "data": {"job_uuid": child["data"]["job_uuid"], "retention_logs_nested": not legacy, "cleanup_pending": True},
        "logs": [{"sequence": 1, "level": "info", "message": "Run finished"}]}
    parent_id = service.ingest(parent)
    retry = {**child, "uuid": str(uuid4()), "state": "success", "started": "2026-10-19T02:04:00Z",
        "ended": "2026-10-19T02:05:00Z", "data": {**child["data"], "cleanup_pending": False},
        "logs": [{"sequence": 1, "level": "info", "message": "Retention finished"}]}
    service.ingest(retry)
    service.ingest(retry)
    assert events[-1] == parent_uuid
    # A stale parent report must not erase the log format marker or the retry result.
    service.ingest({**parent, "data": {"job_uuid": child["data"]["job_uuid"], "cleanup_pending": True}})
    db.session.expire_all()
    operation = get_operation(agent.user_id, parent_id)
    queries = []

    def count_query(*_):
        queries.append(True)

    event.listen(db.engine, "before_cursor_execute", count_query)
    try:
        retention_logs(operation)
    finally:
        event.remove(db.engine, "before_cursor_execute", count_query)
    assert len(queries) == 1  # Child count must not determine the number of log queries.
    detail = client.get(f"/api/agents/operations/{parent_id}").json
    assert [log["message"] for log in detail["logs"]] == ["Run finished"]
    assert [log["message"] for log in detail["retention_logs"]] == (
        ["Retention finished"] if legacy else ["Retention deferred", "Retention finished"])
    assert detail["data"]["cleanup_pending"] is False and detail["data"]["retention_state"] == "success"
    assert db.session.get(AgentOperation, child_id).parent_operation_id == parent_id
    for url in ("/api/agents/operations", f"/api/agents/{agent.id}/operations"):
        assert [row["id"] for row in client.get(url).json] == [parent_id]
    assert sum(row["count"] for row in client.get("/api/agents/operations/summary").json) == 1
    assert client.delete(f"/api/agents/operations/{parent_id}").status_code == 200
    assert client.get("/api/agents/operations").json == []  # Detached child reports stay internal.


@pytest.mark.parametrize("outcome", ["accepted", "unknown", "busy"])
def test_manual_retention_uses_committed_operation_and_preserves_unknown_dispatch(restore_api, monkeypatch, outcome):
    client, _, agent, _, _, _ = restore_api
    agent.protocol_version = 26
    db.session.commit()

    def send(selected_agent, **kwargs):
        operation = AgentOperation.query.filter_by(uuid=kwargs["operation_uuid"]).one()
        assert operation.state == AgentOperationState.running and selected_agent.id == agent.id
        if outcome == "unknown":
            return AgentService.failed_command_report("Agent request timed out", timeout=True)
        if outcome == "busy":
            return AgentService.failed_command_report("Requested resource is busy")
        return {"state": AgentOperationState.success}

    monkeypatch.setattr(AgentService, "run_retention", send)
    response = client.post(f"/api/repositories/{agent.repositories[0].id}/retention", json={"agent_id": agent.id})
    assert response.status_code == (409 if outcome == "busy" else 202)
    operation = AgentOperation.query.filter_by(type=AgentOperationType.retention).one()
    assert operation.source.name == "manual"
    assert operation.state == (AgentOperationState.failed if outcome == "busy" else AgentOperationState.running)
    if outcome != "busy":
        assert response.json["dispatch_status"] == outcome


@pytest.mark.parametrize("invalid", ["foreign_agent", "unassigned", "old_protocol"])
def test_manual_retention_validates_before_reserving_history(restore_api, invalid):
    client, _, agent, foreign, _, _ = restore_api
    agent.protocol_version = 26
    repository = agent.repositories[0]
    agent_id = agent.id
    expected = 422
    if invalid == "foreign_agent":
        agent_id, expected = foreign.id, 404
    elif invalid == "unassigned":
        agent.repositories.clear()
    else:
        agent.protocol_version = 25
    db.session.commit()
    response = client.post(f"/api/repositories/{repository.id}/retention", json={"agent_id": agent_id})
    assert response.status_code == expected
    assert AgentOperation.query.count() == 0
