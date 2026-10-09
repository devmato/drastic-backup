from uuid import UUID

import bcrypt
import pytest
from flask import has_request_context

from drastic_common.agent.commands import AGENT_PROTOCOL_VERSION
from drastic_common.secret_envelope import decrypt_with_private_key, generate_agent_keypair
from drastic_common.ssh_keys import ssh_public_key_algorithm, ssh_public_key_fingerprint
from drastic_common.version import get_version
from drastic_server import models as _models  # noqa: F401
from drastic_server.app import create_app
from drastic_server.extensions import db, socketio
from drastic_server.integrations import agent as agent_command
from drastic_server.models.agent import Agent, AgentOperationState, AgentSession
from drastic_server.models.job import Job
from drastic_server.models.user import User
from drastic_server.services.agent.request import AgentRequestService
from drastic_server.services.exceptions import ResourceNotFound
from drastic_server.services.jobs import management as job_management

SETTINGS = {
    "api_url": "https://127.0.0.1:8006/api2/json",
    "token_id": "root@pam!drastic-agent",
    "node": "",
    "verify_tls": False,
}


@pytest.fixture
def api_client(monkeypatch):
    monkeypatch.setenv("DRASTIC_ENV", "test")
    monkeypatch.setenv("DRASTIC_APP_MASTER_SECRET", "test-master-secret")
    monkeypatch.setenv("DRASTIC_SQLALCHEMY_DATABASE_URI", "sqlite:///:memory:")
    monkeypatch.setenv("DRASTIC_SQLALCHEMY_TRACK_MODIFICATIONS", "false")
    monkeypatch.setenv("DRASTIC_JWT_COOKIE_CSRF_PROTECT", "false")
    app = create_app()
    app.config["TESTING"] = True
    with app.app_context():
        db.create_all()
        try:
            private_key, public_key = generate_agent_keypair()
            user = User(name="admin")
            user.set_initial_password("account-password", recovery_key="user-recovery-key")
            other_user = User(name="other")
            other_user.set_initial_password("other-password", recovery_key="other-recovery-key")
            agent = Agent(user=user, secret="unused", public_key=public_key, protocol_version=1)
            foreign_agent = Agent(user=other_user, secret="unused")
            db.session.add_all([user, other_user, agent, foreign_agent])
            db.session.flush()
            agent.session = AgentSession(request_sid="agent-sid")
            db.session.commit()
            client = app.test_client()
            assert client.get(f"/api/agents/{agent.id}/proxmox-settings").status_code == 401
            assert client.post(
                "/api/auth/login", json={"username": "admin", "password": "account-password"}
            ).status_code == 200
            yield client, agent, foreign_agent, private_key
        finally:
            db.session.remove()
            db.drop_all()


def test_job_services_enforce_ownership_and_commit_without_an_http_request(api_client, monkeypatch):
    _, agent, foreign_agent, _ = api_client
    assert not has_request_context()
    monkeypatch.setattr(agent_command.AgentService, "sync", lambda *args, **kwargs: None)
    data = {"agent_id": agent.id, "name": "Files", "type": "file", "config": {"paths": []}}
    with pytest.raises(ResourceNotFound):
        job_management.create_job(foreign_agent.user_id, data)
    job = job_management.create_job(agent.user_id, data)
    job_id = job.id
    with pytest.raises(ResourceNotFound):
        job_management.update_job(foreign_agent.user_id, job_id, {"name": "Denied"})
    assert db.session.get(Job, job_id).name == "Files"
    job_management.update_job(agent.user_id, job_id, {"name": "Updated"})
    db.session.expire_all()
    assert db.session.get(Job, job_id).name == "Updated"
    with pytest.raises(ResourceNotFound):
        job_management.delete_job(foreign_agent.user_id, job_id)
    job_management.delete_job(agent.user_id, job_id)
    assert db.session.get(Job, job_id) is None


def test_agent_alias_can_be_set_offline_and_cleared_with_validated_owner_access(api_client):
    client, agent, foreign_agent, _ = api_client
    url = f"/api/agents/{agent.id}"
    assert client.application.test_client().put(url, json={"alias": "Denied"}).status_code == 401
    assert client.put(f"/api/agents/{foreign_agent.id}", json={"alias": "Denied"}).status_code == 404
    for payload in ({}, {"alias": "x" * 256}, {"alias": 123}, {"alias": "Name", "hostname": "override"}):
        assert client.put(url, json=payload).status_code == 422
    db.session.delete(agent.session)
    agent.hostname = "backup-host"
    db.session.commit()
    response = client.put(url, json={"alias": "  Home server  "})
    assert response.status_code == 200
    assert response.json["backend_version"] == get_version()
    assert response.json["online"] is False
    assert response.json["alias"] == response.json["display_name"] == "Home server"
    assert response.json["hostname"] == "backup-host"
    assert client.get(url).json["display_name"] == "Home server"
    assert client.put(url, json={"alias": "x" * 255}).json["alias"] == "x" * 255
    for value in ("", "   ", None):
        response = client.put(url, json={"alias": value})
        assert response.status_code == 200
        assert response.json["alias"] is None
        assert response.json["display_name"] == "backup-host"
    agent.hostname = None
    db.session.commit()
    assert client.get(url).json["display_name"] == f"Agent #{agent.id}"


def test_registration_sync_and_agent_api_share_server_owned_uuid(api_client):
    client, existing, _, _ = api_client
    response = client.post("/api/agents/register", json={
        "username": "admin", "password": "account-password", "hostname": "container",
        "os": "Linux", "version": "test", "uuid": existing.uuid,
    })
    assert response.status_code == 201
    data = response.json
    registered = db.session.get(Agent, data["identifier"])
    assert data["uuid"] == registered.uuid != existing.uuid
    assert UUID(registered.uuid).version == 4
    assert AgentRequestService(registered).sync()["agent_uuid"] == registered.uuid
    registered.hostname = "replacement-container"
    db.session.commit()
    assert client.get(f"/api/agents/{registered.id}").json["uuid"] == data["uuid"]


def test_settings_commands_encrypt_tokens_and_return_only_public_settings(api_client, monkeypatch):
    client, agent, _, private_key = api_client
    calls = []

    def call(event, payload, **kwargs):
        calls.append((event, payload, kwargs))
        return {
            "type": "command", "state": "success", "logs": [],
            "data": {**SETTINGS, "token_secret_configured": True, "configured": True,
                     "source": "agent", "token_secret": "must-not-be-returned"},
        }

    monkeypatch.setattr(agent_command, "call", call)
    url = f"/api/agents/{agent.id}/proxmox-settings"
    response = client.put(url, json={**SETTINGS, "token_secret": "new-secret"})
    assert response.status_code == 200
    assert response.json["token_secret_configured"] is True
    assert "token_secret" not in response.json
    event, payload, kwargs = calls[-1]
    assert event == "execute"
    assert kwargs["to"] == "agent-sid"
    assert payload["command"] == "update_proxmox_settings"
    assert payload["args"]["settings"] == SETTINGS
    assert "new-secret" not in str(payload)
    assert decrypt_with_private_key(payload["args"]["encrypted_value"], private_key) == "new-secret"

    assert client.put(url, json=SETTINGS).status_code == 200
    assert "encrypted_value" not in calls[-1][1]["args"]
    assert client.get(url).status_code == 200
    assert calls[-1][1] == {"command": "get_proxmox_settings", "args": {}}
    monkeypatch.setattr(
        agent_command, "call", lambda *_args, **_kwargs: {
            "type": "command", "state": "success", "logs": [],
            "data": {"node": "pve01", "guest_count": 3},
        }
    )
    assert client.post(f"{url}/test", json=SETTINGS).json == {"node": "pve01", "guest_count": 3}


def test_settings_api_rejects_wrong_owner_offline_invalid_input_and_missing_key(api_client, monkeypatch):
    client, agent, foreign_agent, _ = api_client
    calls = []
    monkeypatch.setattr(agent_command, "call", lambda *_args, **_kwargs: calls.append(True))
    assert client.get(f"/api/agents/{foreign_agent.id}/proxmox-settings").status_code == 404
    assert client.put(
        f"/api/agents/{foreign_agent.id}/proxmox-settings", json=SETTINGS
    ).status_code == 404
    url = f"/api/agents/{agent.id}/proxmox-settings"
    for invalid in (
        {"api_url": "http://pve:8006/api2/json"},
        {"api_url": "https://user:password@pve:8006/api2/json"},
        {"api_url": "https://pve:8006/api2/json?secret=foo"},
        {"api_url": "https://pve:8006/api2/json\x00"},
        {"token_id": "bad\r\nheader"},
        {"token_id": "root@pam!token\x00"},
        {"token_id": "root@pam!token\n"},
        {"token_secret": "bad\r\nheader"},
        {"token_secret": ""},
        {"node": "../another-node"},
        {"node": "pve\n"},
    ):
        assert client.put(url, json={**SETTINGS, **invalid}).status_code == 422
    agent.public_key = None
    db.session.commit()
    assert client.put(url, json={**SETTINGS, "token_secret": "new-secret"}).status_code == 400
    db.session.delete(agent.session)
    db.session.commit()
    assert client.get(url).status_code == 400
    assert not calls


def test_agent_failures_and_timeouts_are_reported(api_client, monkeypatch):
    client, agent, _, _ = api_client
    monkeypatch.setattr(
        agent_command.AgentService, "send_command",
        lambda *_args, **_kwargs: {"state": AgentOperationState.failed, "log": "Unsupported command"},
    )
    url = f"/api/agents/{agent.id}/proxmox-settings"
    assert client.get(url).json["message"] == "Unsupported command"
    monkeypatch.setattr(
        agent_command.AgentService, "send_command",
        lambda *_args, **_kwargs: {"data": {"timeout": True}},
    )
    assert client.put(url, json=SETTINGS).status_code == 504


def test_failed_socket_request_rolls_back_unfinished_service_work(api_client, monkeypatch):
    client, agent, _, _ = api_client
    db.session.delete(agent.session)
    agent.secret = bcrypt.hashpw(b"agent-secret", bcrypt.gensalt()).decode()
    agent.alias = "Saved alias"
    db.session.commit()

    def failed_sync(service):
        service.agent.alias = "Uncommitted alias"
        db.session.flush()
        raise ValueError("injected service failure")

    monkeypatch.setattr(AgentRequestService, "sync", failed_sync)
    connection = socketio.test_client(client.application, namespace="/agent", auth={
        "agent_id": agent.id, "agent_secret": "agent-secret", "agent_os": "Linux",
        "agent_hostname": "test-host", "agent_version": "test",
    })
    try:
        response = connection.emit("request", {"action": "sync", "args": {}}, namespace="/agent", callback=True)
        assert response == {"success": False, "result": {"error": "ValueError"}}
        db.session.refresh(agent)
        assert agent.alias == "Saved alias"
    finally:
        connection.disconnect(namespace="/agent")


def test_agent_handshake_persists_protocol_and_rejects_unknown_versions(api_client):
    client, agent, _, _ = api_client
    db.session.delete(agent.session)
    agent.secret = bcrypt.hashpw(b"agent-secret", bcrypt.gensalt()).decode()
    agent.alias = "Backup alias"
    db.session.commit()
    auth = {
        "agent_id": agent.id, "agent_secret": "agent-secret", "agent_os": "Linux",
        "agent_hostname": "test-host", "agent_version": "test",
    }
    for payload in (auth, {**auth, "protocol_version": AGENT_PROTOCOL_VERSION}):
        connection = socketio.test_client(client.application, namespace="/agent", auth=payload)
        assert connection.is_connected("/agent")
        assert client.get(f"/api/agents/{agent.id}").json["protocol_version"] == payload.get("protocol_version", 0)
        assert client.get(f"/api/agents/{agent.id}").json["display_name"] == "Backup alias"
        connection.disconnect(namespace="/agent")

    for version in (None, True, "1", -1, AGENT_PROTOCOL_VERSION + 1):
        connection = socketio.test_client(
            client.application, namespace="/agent", auth={**auth, "protocol_version": version}
        )
        assert not connection.is_connected("/agent")
    db.session.refresh(agent)
    assert agent.protocol_version == AGENT_PROTOCOL_VERSION
    assert agent.session is None


@pytest.mark.parametrize("action,command,message", [
    ("update", "update", "Agent update started"),
    ("reset-known-hosts", "reset_known_hosts", "Known hosts reset"),
    ("rotate-ssh-key", "rotate_ssh_key", "SSH key rotated"),
])
def test_actions_keep_dispatch_identity_permissions_and_errors(api_client, monkeypatch, action, command, message):
    client, agent, foreign_agent, _ = api_client
    agent.protocol_version = 0
    db.session.commit()
    calls = []
    public_key = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIDqk8TfE0CFLBDtyIWXJPmOPNhENwTYKGYUlQU20GGz2 smoke"
    monkeypatch.setattr(agent_command, "call", lambda _event, payload, **_kwargs:
                        calls.append(payload) or {"type": "command", "state": "success", "logs": [],
                                                  "data": {"ssh_public_key": public_key}})
    url = f"/api/agents/{agent.id}/actions/{action}"
    assert client.application.test_client().post(url).status_code == 401
    assert client.post(f"/api/agents/{foreign_agent.id}/actions/{action}").status_code == 404
    assert client.post(f"/api/agents/{agent.id}/actions/sync").status_code == 404
    if action == "update":
        for install_type in ("manual", "docker", "release", "git"):
            agent.install_type = install_type
            db.session.commit()
            assert client.post(url).status_code == 400
        agent.os = "Linux"
        db.session.commit()
        assert client.post(url).status_code == 400  # Legacy protocol, even on native Linux.
        agent.protocol_version = AGENT_PROTOCOL_VERSION
        db.session.commit()
    assert not calls
    assert client.post(url).json == {"msg": message}
    assert calls == [{"command": command, "args": {}}]
    assert agent.ssh_public_key == (public_key if action == "rotate-ssh-key" else None)
    if action == "rotate-ssh-key":
        assert agent.ssh_key_algorithm == ssh_public_key_algorithm(public_key)
        assert agent.ssh_key_fingerprint == ssh_public_key_fingerprint(public_key)
    if action == "update":
        agent.install_type = "docker"
        agent.protocol_version = 3
        db.session.commit()
        assert client.post(url).status_code == 400
        assert len(calls) == 1
        agent.protocol_version = 4
        db.session.commit()
        assert client.post(url).json == {"msg": message}
        assert calls[-1] == {"command": "update", "args": {}}
    monkeypatch.setattr(agent_command, "call", lambda *_args, **_kwargs:
                        {"type": "command", "state": "failed",
                         "logs": [{"sequence": 1, "message": "execution capacity is busy"}]})
    response = client.post(url)
    assert response.status_code == (409 if action == "update" else 400)
    assert response.json["message"] == "execution capacity is busy"
    def timeout(*_args, **_kwargs):
        raise agent_command.TimeoutError()
    monkeypatch.setattr(agent_command, "call", timeout)
    assert client.post(url).status_code == 504
    db.session.delete(agent.session)
    db.session.commit()
    assert client.post(url).status_code == 400
