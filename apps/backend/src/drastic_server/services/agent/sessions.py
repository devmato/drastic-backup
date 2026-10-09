"""Authenticate agent connections and maintain their single active session."""

from datetime import datetime

import bcrypt
from marshmallow import ValidationError
from sqlalchemy import delete

from drastic_common.agent.commands import AGENT_PROTOCOL_VERSION
from drastic_common.ssh_keys import ssh_public_key_algorithm, ssh_public_key_fingerprint
from drastic_common.truenas import AgentConnectionsSchema
from drastic_server.extensions import db
from drastic_server.models.agent import Agent, AgentSession
from drastic_server.utils.realtime import emit_agent_state


def connect_agent(auth, session_id):
    """Reject invalid credentials before changing identity, keys or session state."""
    try:
        agent_id = auth["agent_id"]
        secret = auth["agent_secret"]
        agent_os = auth["agent_os"]
        hostname = auth["agent_hostname"]
        version = auth["agent_version"]
        protocol = auth.get("protocol_version", 0)
        connections = AgentConnectionsSchema().load(auth.get("connections") or {})
    except (KeyError, TypeError, AttributeError, ValidationError):
        return False
    if type(protocol) is not int or not 0 <= protocol <= AGENT_PROTOCOL_VERSION:
        return False
    agent = Agent.query.filter_by(id=agent_id).first()
    if not agent or not isinstance(secret, str):
        return False
    if not bcrypt.checkpw(secret.encode(), agent.secret.encode()):
        return False

    agent.os = agent_os
    agent.hostname = hostname
    agent.version = version
    agent.protocol_version = protocol
    agent.connections = connections if protocol >= 3 else None
    agent.install_type = Agent.normalize_install_type(auth.get("agent_install_type"))
    public_key = auth.get("agent_public_key")
    ssh_public_key = auth.get("agent_ssh_public_key")
    if public_key and agent.public_key != public_key:
        agent.public_key = public_key
    if ssh_public_key and agent.ssh_public_key != ssh_public_key:
        agent.ssh_public_key = ssh_public_key
        agent.ssh_key_fingerprint = ssh_public_key_fingerprint(ssh_public_key)
        agent.ssh_key_algorithm = ssh_public_key_algorithm(ssh_public_key)
    agent.last_connection = datetime.now()
    db.session.execute(delete(AgentSession).where(AgentSession.agent_id == agent.id))
    db.session.add(AgentSession(agent_id=agent.id, request_sid=session_id))
    db.session.commit()
    emit_agent_state(agent, online=True)
    return True


def disconnect_agent(session_id):
    # A late disconnect from an old socket must not remove its replacement.
    session = AgentSession.query.filter_by(request_sid=session_id).first()
    if session:
        agent = session.agent
        agent.last_connection = datetime.now()
        db.session.delete(session)
        db.session.commit()
        emit_agent_state(agent, online=False)
