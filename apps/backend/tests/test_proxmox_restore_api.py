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
        agent = Agent(user=user, secret="secret", protocol_version=2)
        foreign = Agent(user=other, secret="other-secret", protocol_version=2)
        AgentSession(agent=agent, request_sid="sid")
        repository = Repository(user=user, name="Repo", kind=Repository.KIND_CUSTOM, location="/repo")
        agent.repositories.append(repository)
        job = Job(agent=agent, name="VMs", type=JobType.proxmox, config={})
        db.session.add_all([user, other, agent, foreign, repository, job])
        db.session.commit()
        client = app.test_client()
        assert client.post("/api/auth/login", json={"username": user.name, "password": "test-password"}).status_code == 200
        snapshot = {"id": "snapshot", "tags": [f"job_uuid:{job.uuid}", "source:proxmox", "guest_type:qemu", "backup_method:vzdump"]}
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
    agent.protocol_version = 1
    db.session.commit()
    assert client.post("/api/restores/", json=payload).status_code == 400
    assert len(calls) == 1


def test_snapshot_backup_restore_requires_updated_agent(restore_api, monkeypatch):
    client, source, agent, _, job, calls = restore_api
    snapshot = {"id": "snapshot", "tags": [f"job_uuid:{job.uuid}", "source:proxmox", "guest_type:qemu", "backup_method:snapshot"]}
    monkeypatch.setattr(AgentService, "list_restore_snapshots", lambda *a, **kw: {
        "state": AgentOperationState.success, "data": {"snapshots": [snapshot]},
    })
    payload = {**source, "mode": "proxmox_vm", "vmid": 101, "storage": "local-lvm"}
    assert client.post("/api/restores/", json=payload).status_code == 400
    assert not calls
    agent.protocol_version = 7
    db.session.commit()
    assert client.post("/api/restores/", json=payload).status_code == 202
    assert len(calls) == 1


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
