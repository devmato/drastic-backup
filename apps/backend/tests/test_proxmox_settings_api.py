import pytest

from drastic_common.secret_envelope import decrypt_with_private_key, generate_agent_keypair
from drastic_server import models as _models  # noqa: F401
from drastic_server.app import create_app
from drastic_server.extensions import db
from drastic_server.models.agent import Agent, AgentOperationState, AgentSession
from drastic_server.models.user import User
from drastic_server.services.agent import command as agent_command

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
            agent = Agent(user=user, secret="unused", public_key=public_key)
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
