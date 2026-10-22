"""Session revocation and agent recipient-key changes invalidate live access."""

import bcrypt
import pytest
from test_repository_api import build_app

from drastic_common.agent.commands import AGENT_PROTOCOL_VERSION
from drastic_common.secret_envelope import decrypt_with_private_key, generate_agent_keypair
from drastic_server.extensions import db, socketio
from drastic_server.models.agent import Agent
from drastic_server.models.repository import Repository
from drastic_server.models.secret import AgentSecretEnvelope
from drastic_server.models.user import User
from drastic_server.services.agent.sessions import connect_agent
from drastic_server.services.repository.secrets import (
    agent_repository_assignment,
    ensure_agent_envelopes_from_user_recovery_key,
    ensure_agent_secret_envelope,
    store_agent_restic_access_key,
    store_recovery_key,
)
from drastic_server.utils.realtime import emit_ui_event


@pytest.mark.parametrize("revoke", ["logout", "password"])
def test_revocation_disconnects_every_tab_but_not_other_sessions(monkeypatch, revoke):
    app = build_app(monkeypatch)
    with app.app_context():
        db.create_all()
        user = User(name="owner")
        user.set_initial_password("account-password")
        db.session.add(user)
        db.session.commit()
        user_id = user.id
    first, second = app.test_client(), app.test_client()
    for client in (first, second):
        assert client.post("/api/auth/login", json={"username": "owner", "password": "account-password"}).status_code == 200
    tabs = [socketio.test_client(app, flask_test_client=first) for _ in range(2)]
    other = socketio.test_client(app, flask_test_client=second)
    try:
        assert all(tab.is_connected() for tab in [*tabs, other])
        if revoke == "logout":
            assert first.post("/api/auth/logout").status_code == 200
        else:
            response = first.put("/api/user/password", json={"current_password": "account-password", "new_password": "changed-password"})
            assert response.status_code == 200, response.json
        assert all(not tab.is_connected() for tab in tabs)
        assert other.is_connected() is (revoke == "logout")
        if other.is_connected():
            with app.app_context():
                emit_ui_event(user_id, "still-authorized")
            assert other.get_received()[0]["args"][0]["name"] == "still-authorized"
    finally:
        for tab in [*tabs, other]:
            if tab.is_connected():
                tab.disconnect()
        with app.app_context():
            db.session.remove()
            db.drop_all()


def test_new_agent_recipient_key_requires_and_recovers_new_envelopes(monkeypatch):
    app = build_app(monkeypatch)
    with app.app_context():
        db.create_all()
        try:
            _, old_public = generate_agent_keypair()
            new_private, new_public = generate_agent_keypair()
            user = User(name="owner", password="unused", encrypted_recovery_key={})
            repository = Repository(user=user, name="Repo", location="/repo")
            agent = Agent(user=user, public_key=old_public,
                          secret=bcrypt.hashpw(b"agent-secret", bcrypt.gensalt(rounds=4)).decode(), repositories=[repository])
            db.session.add(agent)
            db.session.flush()
            store_recovery_key(repository, "repository-password", "recovery-key")
            db.session.flush()
            ensure_agent_secret_envelope(agent, repository.password_secret, "repository-password")
            store_agent_restic_access_key(agent, repository.id, {"old": "envelope"}, "old-key")
            db.session.commit()
            auth = {"agent_id": agent.id, "agent_secret": "agent-secret", "agent_os": "Linux",
                    "agent_hostname": "host", "agent_version": "test", "protocol_version": AGENT_PROTOCOL_VERSION,
                    "agent_public_key": new_public}
            assert not connect_agent({**auth, "agent_secret": "wrong"}, "rejected")
            assert AgentSecretEnvelope.query.one().active
            assert connect_agent(auth, "new-session")
            assert not AgentSecretEnvelope.query.one().active
            assignment = agent_repository_assignment(agent, repository.id)
            assert assignment["encrypted_restic_access_key"] is None and not assignment["provisioned"]
            ensure_agent_envelopes_from_user_recovery_key(agent, [repository], "recovery-key")
            db.session.commit()
            envelope = AgentSecretEnvelope.query.one()
            assert envelope.active and decrypt_with_private_key(envelope.encrypted_value, new_private) == "repository-password"
            assert connect_agent(auth, "same-key-session")
            assert AgentSecretEnvelope.query.one().active
        finally:
            db.session.remove()
            db.drop_all()
