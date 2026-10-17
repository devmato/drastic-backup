from uuid import uuid4

import pytest

from drastic_server.app import create_app
from drastic_server.extensions import db
from drastic_server.models.agent import (
    Agent,
    AgentOperation,
    AgentOperationArtifact,
    AgentOperationSource,
    AgentOperationState,
    AgentOperationType,
    AgentSession,
)
from drastic_server.models.job import Job, JobType
from drastic_server.models.repository import Repository
from drastic_server.models.user import User
from drastic_server.services.agent import AgentService


@pytest.fixture
def restore_api(monkeypatch):
    monkeypatch.setenv("DRASTIC_ENV", "test")
    monkeypatch.setenv("DRASTIC_APP_MASTER_SECRET", "test-master-secret")
    monkeypatch.setenv("DRASTIC_SQLALCHEMY_DATABASE_URI", "sqlite:///:memory:")
    monkeypatch.setenv("DRASTIC_JWT_COOKIE_CSRF_PROTECT", "false")
    app = create_app()
    app.config["TESTING"] = True
    with app.app_context():
        db.create_all()
        user = User(name="restore-user")
        user.set_initial_password("test-password", recovery_key="recovery")
        other = User(name="other")
        other.set_initial_password("other-password", recovery_key="other-recovery")
        agent = Agent(user=user, secret="secret", protocol_version=7)
        foreign = Agent(user=other, secret="other-secret", protocol_version=7)
        AgentSession(agent=agent, request_sid="sid")
        repository = Repository(user=user, name="Repo", kind=Repository.KIND_CUSTOM, location="/repo")
        agent.repositories.append(repository)
        job = Job(agent=agent, name="VMs", type=JobType.proxmox, config={})
        db.session.add_all([user, other, agent, foreign, repository, job])
        db.session.commit()
        client = app.test_client()
        assert client.post("/api/auth/login", json={"username": user.name, "password": "test-password"}).status_code == 200
        snapshot = {"id": "snapshot", "tags": [f"job_uuid:{job.uuid}", "source:proxmox", "guest_type:qemu", "backup_method:snapshot"]}
        monkeypatch.setattr(AgentService, "list_restore_snapshots", lambda *a, **kw: {
            "state": AgentOperationState.success, "data": {"snapshots": [snapshot]},
        })
        calls = []

        def send(agent, **kwargs):
            calls.append(kwargs)
            return {"state": AgentOperationState.success, "data": {}}

        monkeypatch.setattr(AgentService, "run_restore", send)
        source = {"job_id": job.id, "agent_id": agent.id, "repository_id": repository.id, "snapshot_id": snapshot["id"]}
        yield client, source, agent, foreign, job, calls
        db.session.remove()
        db.drop_all()


def test_vm_restore_dispatch_and_protocol_gate(restore_api):
    client, source, agent, _, _, calls = restore_api
    payload = {**source, "mode": "proxmox_vm", "vmid": 101, "storage": "local-lvm"}
    response = client.post("/api/restores/", json=payload)
    assert response.status_code == 202
    assert calls[0]["vmid"] == 101 and calls[0]["unique"] is True
    assert calls[0]["mode"] == "proxmox_vm"
    assert calls[0]["expected_job_tag"].startswith("job_uuid:")
    agent.protocol_version = 6
    db.session.commit()
    assert client.post("/api/restores/", json=payload).status_code == 400
    assert len(calls) == 1


@pytest.mark.parametrize("mode", ["plain_file", "proxmox_vm", "proxmox_prepare", "proxmox_files"])
def test_restore_after_hard_job_deletion(restore_api, monkeypatch, mode):
    client, source, agent, foreign, job, calls = restore_api
    job_uuid = job.uuid
    monkeypatch.setattr(AgentService, "sync", lambda *a, **kw: None)
    assert client.delete(f"/api/jobs/{job.id}").status_code == 200
    assert db.session.get(Job, source["job_id"]) is None
    source.pop("job_id")
    query = {key: value for key, value in source.items() if key != "snapshot_id"}
    response = client.get("/api/restores/snapshots", query_string=query)
    assert response.status_code == 200
    assert response.json["snapshots"][0]["proxmox_archive"] is True
    payload = {**source, "mode": mode, "restore_location": "/restore", "include_paths": ["/etc"],
               "vmid": 101, "storage": "local-lvm", "session_id": str(uuid4()), "volume": "/dev/sda1"}
    assert client.post("/api/restores/", json={**payload, "agent_id": foreign.id}).status_code == 404
    response = client.post("/api/restores/", json=payload)
    assert response.status_code == 202
    assert calls[-1]["job_id"] is None
    assert calls[-1]["job_uuid"] == job_uuid
    operation = db.session.get(AgentOperation, response.json["operation_id"])
    assert operation.job_id is None and operation.data["job_uuid"] == job_uuid


def test_job_retention_defaults_ownership_and_sync(restore_api, monkeypatch):
    from drastic_server.models.retention import Retention
    from drastic_server.services.agent.request import AgentRequestService
    from drastic_server.services.exceptions import ResourceNotFound
    from drastic_server.services.jobs.configuration import configure_job_retention

    client, _, agent, foreign, job, _ = restore_api
    agent.protocol_version = 21
    policy = Retention(user_id=agent.user_id, name="Default", keep_last=3)
    second = Retention(user_id=agent.user_id, name="Other", keep_last=2)
    other = Retention(user_id=foreign.user_id, name="Foreign", keep_last=1)
    db.session.add_all([policy, second, other])
    db.session.commit()
    monkeypatch.setattr(AgentService, "sync", lambda *a, **kw: None)
    assert job.effective_retention_id is None
    assert client.put(f"/api/retentions/{other.id}", json={"is_default": True}).status_code == 404
    for target, selected, expected in [(policy.id, True, policy.id), (second.id, False, policy.id),
                                       (second.id, True, second.id), (policy.id, False, second.id),
                                       (second.id, False, None), (policy.id, True, policy.id)]:
        assert client.put(f"/api/retentions/{target}", json={"is_default": selected}).status_code == 200
        assert job.effective_retention_id == expected
    assert client.put(f"/api/retentions/{policy.id}", json={"keep_last": 4}).status_code == 200
    assert client.get("/api/retentions/").json[0]["is_default"] is True
    configure_job_retention(agent.user_id, job, {"retention_mode": "disabled"})
    assert job.effective_retention_id is None
    with pytest.raises(ResourceNotFound):
        configure_job_retention(agent.user_id, job, {"retention_mode": "custom", "retention_id": other.id})
    configure_job_retention(agent.user_id, job, {"retention_mode": "custom", "retention_id": policy.id})
    db.session.commit()
    request = AgentRequestService(agent)
    monkeypatch.setattr(request, "_repositories_payload", lambda: [])
    monkeypatch.setattr(request, "_secret_envelopes_payload", lambda: [])
    synced = request.sync()
    assert synced["jobs"][0]["retention_id"] == policy.id
    assert client.delete(f"/api/retentions/{policy.id}").status_code == 200
    db.session.refresh(job)
    assert job.retention_mode == "disabled" and job.retention_id is None
    assert agent.user.default_retention_id is None


def test_deleted_job_restore_session_can_close_without_source_snapshot(restore_api, monkeypatch):
    client, source, agent, _, job, _ = restore_api
    identity = {"job_uuid": job.uuid, "repository_id": source["repository_id"], "snapshot_id": source["snapshot_id"]}
    db.session.delete(job)
    db.session.commit()
    commands = []
    monkeypatch.setattr(AgentService, "list_restore_snapshots", lambda *a, **kw: pytest.fail("Closing must not read a removed snapshot"))
    monkeypatch.setattr(AgentService, "send_command", lambda *a, **kw: commands.append(kw) or {"state": AgentOperationState.success})
    response = client.post("/api/restores/proxmox", json={**identity, "agent_id": agent.id,
                           "session_id": str(uuid4()), "action": "close"})
    assert response.status_code == 200
    assert commands[0]["identity"] == identity


def test_all_repository_snapshots_are_listed_and_foreign_backups_restore_as_files(restore_api, monkeypatch):
    client, source, agent, _, _, calls = restore_api
    agent.protocol_version = 22
    monkeypatch.setattr(AgentService, "list_restore_snapshots", lambda *a, **kw: {
        "state": AgentOperationState.success, "data": {"snapshots": [{"id": "foreign", "tags": [], "paths": ["/data"]}]}})
    query = {"agent_id": agent.id, "repository_id": source["repository_id"]}
    snapshots = client.get("/api/restores/snapshots", query_string=query).json["snapshots"]
    assert snapshots[0]["id"] == "foreign" and not snapshots[0]["proxmox_archive"]
    payload = {**query, "snapshot_id": "foreign", "mode": "plain_file", "restore_location": "/restore", "include_paths": ["/"]}
    assert client.post("/api/restores/", json=payload).status_code == 202
    assert calls[-1]["job_uuid"] is None and calls[-1]["expected_job_tag"] is None
    assert client.post("/api/restores/", json={**payload, "mode": "proxmox_vm", "vmid": 101, "storage": "local"}).status_code == 400


@pytest.mark.parametrize("case", ["success", "foreign_agent", "stale_members", "running", "old_protocol"])
def test_restore_point_deletion_groups_by_run_and_checks_access(restore_api, monkeypatch, case):
    client, source, agent, foreign, job, _ = restore_api
    agent.protocol_version = 22 if case != "old_protocol" else 21
    run = AgentOperation(agent=agent, job=job, repository_id=source["repository_id"], type=AgentOperationType.backup,
                         state=AgentOperationState.running if case == "running" else AgentOperationState.success)
    db.session.add(run)
    db.session.commit()
    tags = [f"job_uuid:{job.uuid}", f"operation_uuid:{run.uuid}"]
    snapshots = [{"id": "a", "tags": tags}, {"id": "b", "tags": tags}, {"id": "other", "tags": []}]
    artifact = AgentOperationArtifact(operation=run, artifact_key="default", snapshot_id="a", state="success")
    db.session.add(artifact)
    db.session.commit()
    monkeypatch.setattr(AgentService, "list_restore_snapshots", lambda *a, **kw: {
        "state": AgentOperationState.success, "data": {"snapshots": snapshots}})
    dispatched = []

    def dispatch(agent, command, **kwargs):
        dispatched.append(kwargs)
        return {"state": AgentOperationState.success, "data": {}}

    monkeypatch.setattr(AgentService, "send_command", dispatch)
    payload = {"agent_id": foreign.id if case == "foreign_agent" else agent.id, "repository_id": source["repository_id"],
               "snapshot_id": "a", "snapshot_ids": ["a"] if case == "stale_members" else ["a", "b"]}
    response = client.post("/api/restores/snapshots/delete", json=payload)
    assert response.status_code == (202 if case == "success" else 404 if case == "foreign_agent" else 400)
    if case == "success":
        assert set(dispatched[0]["snapshot_ids"]) == {"a", "b"}
        from drastic_server.services.operations.reports import AgentOperationService

        AgentOperationService(agent).ingest({"uuid": dispatched[0]["operation_uuid"], "type": "retention", "state": "failed",
            "repository_id": source["repository_id"], "data": {"scope": "repository", "manual_delete": True,
            "deleted_snapshot_ids": ["a", "b"], "error": "prune failed"}})
        assert artifact.forgotten_at is not None
    else:
        assert not dispatched


def test_deleted_job_cleanup_reports_do_not_affect_other_jobs(restore_api):
    from datetime import datetime

    from drastic_server.services.operations.reports import AgentOperationService

    _, source, agent, _, replacement_job, _ = restore_api
    old_uuid, other_uuid = str(uuid4()), str(uuid4())
    backups = [AgentOperation(agent=agent, repository_id=source["repository_id"],
               type=AgentOperationType.backup, state=AgentOperationState.warning,
               started=datetime(2026, 1, 1), ended=datetime(2026, 1, 2),
               data={"job_uuid": job_uuid, "cleanup_pending": True}) for job_uuid in (old_uuid, other_uuid)]
    db.session.add_all(backups)
    db.session.commit()
    operation_id = AgentOperationService(agent).ingest({
        "uuid": str(uuid4()), "type": "retention", "state": "success",
        "job_id": replacement_job.id, "repository_id": source["repository_id"],
        "started": "2026-01-03T00:00:00Z", "ended": "2026-01-03T00:01:00Z",
        "data": {"job_uuid": old_uuid, "cleanup_pending": False},
    })
    assert db.session.get(AgentOperation, operation_id).job_id is None
    assert backups[0].data["cleanup_pending"] is False
    assert backups[1].data["cleanup_pending"] is True


@pytest.mark.parametrize("options", [
    {"ownership_by_name": True}, {"skip_xattrs": True}, {"ownership_by_name": True, "skip_xattrs": True},
])
def test_restore_metadata_options_gate_old_agents_and_record_options(restore_api, options):
    client, source, agent, _, _, calls = restore_api
    payload = {**source, "mode": "plain_file", "restore_location": "/restore", "include_paths": ["/etc"],
               "ownership_by_name": False, "skip_xattrs": False}
    agent.protocol_version = 16
    db.session.commit()
    response = client.post("/api/restores/", json=payload)
    assert response.status_code == 202
    assert "ownership_by_name" not in calls[-1]
    assert "skip_xattrs" not in calls[-1]
    calls.clear()
    operations = AgentOperation.query.count()
    payload.update(options)
    response = client.post("/api/restores/", json=payload)
    assert response.status_code == 400
    assert "protocol 17" in response.json["message"]
    assert not calls
    assert AgentOperation.query.count() == operations
    agent.protocol_version = 17
    db.session.commit()
    response = client.post("/api/restores/", json=payload)
    assert response.status_code == 202
    operation = db.session.get(AgentOperation, response.json["operation_id"])
    for key in options:
        assert calls[0][key] is True
        assert operation.data[key] is True


@pytest.mark.parametrize("mode", ["proxmox_vm", "proxmox_prepare", "proxmox_files"])
def test_legacy_vma_is_hidden_and_rejected_but_plain_restore_remains_available(restore_api, monkeypatch, mode):
    client, source, _, _, job, calls = restore_api
    snapshot = {"id": "snapshot", "tags": [f"job_uuid:{job.uuid}", "source:proxmox", "guest_type:qemu", "backup_method:vzdump"]}
    monkeypatch.setattr(AgentService, "list_restore_snapshots", lambda *a, **kw: {
        "state": AgentOperationState.success, "data": {"snapshots": [snapshot]},
    })
    query = {key: value for key, value in source.items() if key != "snapshot_id"}
    response = client.get("/api/restores/snapshots", query_string=query)
    assert response.status_code == 200
    assert response.json["snapshots"][0]["proxmox_archive"] is False
    payload = {**source, "mode": mode, "vmid": 101, "storage": "local-lvm",
               "restore_location": "/restore", "include_paths": ["/etc"],
               "session_id": str(uuid4()), "volume": "/dev/sda1"}
    response = client.post("/api/restores/", json=payload)
    assert response.status_code == 400
    assert "legacy VMA" in response.json["message"]
    assert not calls
    response = client.post("/api/restores/", json={**source, "mode": "plain_file", "restore_location": "/restore",
                                                  "include_paths": ["/archive.vma"]})
    assert response.status_code == 202
    assert calls[0]["mode"] == "plain_file"


def test_native_backup_restore_protocol_gate(restore_api, monkeypatch):
    client, source, agent, _, job, calls = restore_api
    snapshot = {"id": "snapshot", "tags": [f"job_uuid:{job.uuid}", "source:proxmox", "guest_type:qemu", "backup_method:native"]}
    monkeypatch.setattr(AgentService, "list_restore_snapshots", lambda *a, **kw: {
        "state": AgentOperationState.success, "data": {"snapshots": [snapshot]},
    })
    payload = {**source, "mode": "proxmox_vm", "vmid": 101, "storage": "local-lvm"}
    agent.protocol_version = 7
    db.session.commit()
    assert client.post("/api/restores/", json=payload).status_code == 400 and not calls
    agent.protocol_version = 8
    db.session.commit()
    assert client.post("/api/restores/", json=payload).status_code == 202


def test_known_failed_archives_are_rejected_but_plain_archive_restore_remains_available(restore_api):
    client, source, agent, _, job, calls = restore_api
    operation = AgentOperation(agent=agent, job=job, repository_id=source["repository_id"],
                               type=AgentOperationType.backup, source=AgentOperationSource.manual,
                               state=AgentOperationState.failed)
    artifact = AgentOperationArtifact(operation=operation, uuid=str(uuid4()), artifact_key="vm:100",
                                      snapshot_id="snapshot", state="failed", data={})
    db.session.add(artifact)
    db.session.commit()
    response = client.get("/api/restores/snapshots", query_string={k: v for k, v in source.items() if k != "snapshot_id"})
    assert response.status_code == 200
    assert response.json["snapshots"][0]["restore_error"]
    assert client.post("/api/restores/", json={**source, "mode": "proxmox_prepare"}).status_code == 400
    assert not calls
    assert client.post("/api/restores/", json={**source, "mode": "plain_file", "restore_location": "/restore", "include_paths": ["/archive.vma"]}).status_code == 202
    assert "unique" not in calls[0]  # Older agents still accept plain-file dispatch.


def test_guest_browser_authorizes_agent_repository_and_job(restore_api, monkeypatch):
    client, source, agent, foreign, job, _ = restore_api
    commands = []

    def send(target, command, **kwargs):
        commands.append((target.id, kwargs))
        return {"state": AgentOperationState.success, "data": {"entries": []}}

    monkeypatch.setattr(AgentService, "send_command", send)
    payload = {**source, "action": "entries", "session_id": str(uuid4()), "volume": "/dev/vg/root", "path": "/etc"}
    assert client.post("/api/restores/proxmox", json={**payload, "agent_id": foreign.id}).status_code == 404
    assert not commands
    assert client.post("/api/restores/proxmox", json=payload).status_code == 200
    assert commands[0][0] == agent.id
    assert commands[0][1]["identity"] == {"job_uuid": job.uuid, "repository_id": source["repository_id"], "snapshot_id": "snapshot"}
    agent.repositories.clear()
    db.session.commit()
    assert client.post("/api/restores/proxmox", json=payload).status_code == 400
    assert len(commands) == 1


def test_on_demand_browser_receives_repository_context_only_on_updated_agents(restore_api, monkeypatch):
    client, source, agent, _, _, _ = restore_api
    commands = []

    def send(target, command, **kwargs):
        commands.append(kwargs)
        return {"state": AgentOperationState.success, "data": {}}

    monkeypatch.setattr(AgentService, "send_command", send)
    agent.protocol_version = 9
    db.session.commit()
    payload = {**source, "action": "entries", "session_id": str(uuid4()), "volume": "/dev/sda1"}
    assert client.post("/api/restores/proxmox", json=payload).status_code == 200
    assert "repository" not in commands[-1]
    agent.protocol_version = 10
    db.session.commit()
    assert client.post("/api/restores/proxmox", json=payload).status_code == 200
    assert commands[-1]["repository"]["location"] == "/repo"
    assert client.post("/api/restores/proxmox", json={
        "action": "options", "mode": "proxmox_files", "agent_id": agent.id}).status_code == 200
    assert commands[-1]["mode"] == "proxmox_files"


@pytest.mark.parametrize("extra", [
    {"mode": "proxmox_vm"},
    {"mode": "proxmox_vm", "vmid": 99, "storage": "local"},
    {"mode": "proxmox_vm", "vmid": 101, "storage": "--force"},
    {"mode": "proxmox_files", "restore_location": "/restore", "include_paths": ["/etc"]},
])
def test_invalid_mode_parameters_do_not_dispatch(restore_api, extra):
    client, source, _, _, _, calls = restore_api
    assert client.post("/api/restores/", json={**source, **extra}).status_code == 422
    assert not calls
