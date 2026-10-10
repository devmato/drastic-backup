"""Agent registration, assignments and maintenance commands."""

import bcrypt

from drastic_common.agent.commands import AgentCommandName
from drastic_common.secret_envelope import SecretEnvelopeError, encrypt_for_public_key
from drastic_common.ssh_keys import ssh_public_key_algorithm, ssh_public_key_fingerprint
from drastic_common.truenas import AgentConnectionsSchema
from drastic_server.extensions import db
from drastic_server.integrations.agent import (
    AgentService,
    is_agent_conflict_response,
    is_agent_timeout_response,
)
from drastic_server.models.agent import Agent, AgentOperationState
from drastic_server.models.job import Job
from drastic_server.models.repository import Repository
from drastic_server.models.user import User
from drastic_server.services.agent.installer import installation_target
from drastic_server.services.chains import require_unused_chain_reference
from drastic_server.services.exceptions import (
    AgentCommandFailed,
    AuthenticationFailed,
    ResourceConflict,
    ResourceNotFound,
)
from drastic_server.services.queries import require_result
from drastic_server.services.repository import ensure_agent_envelopes_from_user_recovery_key
from drastic_server.utils.realtime import emit_agents_update, emit_jobs_update


def get_agent(user_id, agent_id):
    return require_result(Agent.query.filter_by(id=agent_id, user_id=user_id))


def list_agents(user_id):
    return Agent.query.filter_by(user_id=user_id).all()


def set_ssh_public_key(agent, public_key):
    public_key = str(public_key or "").strip() or None
    agent.ssh_public_key = public_key
    agent.ssh_key_fingerprint = ssh_public_key_fingerprint(public_key) if public_key else None
    agent.ssh_key_algorithm = ssh_public_key_algorithm(public_key) if public_key else None


def register_agent(data):
    user = User.query.filter_by(name=data["username"].strip().lower()).first()
    if user is None or not bcrypt.checkpw(data["password"].encode("UTF-8"), user.password.encode("UTF-8")):
        raise AuthenticationFailed("Wrong username or password")
    secret = Agent.generate_secret()
    agent = Agent(
        user_id=user.id, secret=bcrypt.hashpw(secret.encode("UTF-8"), bcrypt.gensalt()).decode("UTF-8"),
        public_key=data.get("public_key"), hostname=data.get("hostname"), os=data.get("os"),
        version=data.get("version"), install_type=Agent.normalize_install_type(data.get("install_type")),
    )
    set_ssh_public_key(agent, data.get("ssh_public_key"))
    db.session.add(agent)
    db.session.commit()
    emit_agents_update(user.id)
    return {"identifier": agent.id, "uuid": agent.uuid, "secret": secret}


def update_agent(user_id, agent_id, data):
    agent = get_agent(user_id, agent_id)
    agent.alias = (data["alias"] or "").strip() or None
    db.session.commit()
    emit_agents_update(user_id)
    emit_jobs_update(user_id)
    return agent


def delete_agent(user_id, agent_id):
    agent = get_agent(user_id, agent_id)
    try:
        for job in agent.jobs:
            require_unused_chain_reference(user_id, "job_id", job.id)
    except ValueError as exc:
        raise ResourceConflict(str(exc)) from exc
    for operation in agent.operations:
        db.session.delete(operation)
    for job in Job.query.filter_by(agent_id=agent.id):
        for action in job.actions:
            db.session.delete(action)
        for schedule in job.schedules:
            db.session.delete(schedule)
        db.session.delete(job)
    agent.repositories = []
    if agent.session is not None:
        db.session.delete(agent.session)
    db.session.delete(agent)
    db.session.commit()
    emit_agents_update(user_id)
    emit_jobs_update(user_id)


def assign_repositories(user_id, agent_id, data):
    agent = get_agent(user_id, agent_id)
    ids = list(dict.fromkeys(data["repository_ids"]))
    repositories = Repository.query.filter(Repository.user_id == user_id, Repository.id.in_(ids)).all() if ids else []
    if len(repositories) != len(ids):
        raise ValueError("One or more repositories are invalid")
    ensure_agent_envelopes_from_user_recovery_key(
        agent=agent, repositories=repositories, user_recovery_key=data.get("recovery_key"),
    )
    removed = {repository.id for repository in agent.repositories} - set(ids)
    if any(schedule.repository_id in removed for job in agent.jobs for schedule in job.schedules):
        raise ValueError("Cannot unassign repositories that are used by job schedules")
    agent.repositories = repositories
    db.session.commit()
    emit_agents_update(user_id)
    emit_jobs_update(user_id)
    if agent.online:
        AgentService.sync(agent)


def sync_agent(user_id, agent_id, data):
    agent = get_agent(user_id, agent_id)
    if data.get("recovery_key"):
        ensure_agent_envelopes_from_user_recovery_key(
            agent=agent, repositories=list(agent.repositories), user_recovery_key=data["recovery_key"],
        )
        db.session.commit()
    if not agent.online:
        raise ValueError("Agent is offline")
    response = AgentService.sync(agent, await_response=True)
    if is_agent_timeout_response(response):
        raise AgentCommandFailed(response)
    if response.get("state") != AgentOperationState.success:
        raise ValueError(response.get("log", "Agent sync failed"))


def run_action(user_id, agent_id, action):
    actions = {
        "update": (AgentCommandName.update, "Agent update started", "Agent update could not be started"),
        "reset-known-hosts": (AgentCommandName.reset_known_hosts, "Known hosts reset", "Reset known hosts failed"),
        "rotate-ssh-key": (AgentCommandName.rotate_ssh_key, "SSH key rotated", "Rotate SSH key failed"),
    }
    if action not in actions:
        raise ResourceNotFound("Action not found")
    command, message, failure = actions[action]
    agent = get_agent(user_id, agent_id)
    if not agent.online:
        raise ValueError("Agent is offline")
    if command == AgentCommandName.update and (str(agent.os or "").lower() != "linux" or not (
        agent.install_type == "git" or (agent.install_type == "docker" and (agent.protocol_version or 0) >= 4)
    )):
        raise ValueError("Updates require a managed Linux installation; Docker agents require protocol 4")
    if command == AgentCommandName.update:
        # Reject unreproducible backend builds before pausing a remote agent.
        installation_target()
    response = AgentService.send_command(agent, command)
    if is_agent_timeout_response(response):
        if command == AgentCommandName.update:
            response = {**response, "log": "Update acknowledgement timed out; check the agent logs before retrying"}
        raise AgentCommandFailed(response)
    if command == AgentCommandName.update and is_agent_conflict_response(response):
        raise ResourceConflict(response.get("log"))
    if response.get("state") != AgentOperationState.success:
        raise ValueError(response.get("log") or failure)
    public_key = (response.get("data") or {}).get("ssh_public_key") if command == AgentCommandName.rotate_ssh_key else None
    if public_key:
        set_ssh_public_key(agent, public_key)
        db.session.commit()
        emit_agents_update(agent.user_id)
    return {"msg": message}


def connection_command(user_id, agent_id, command, data=None, secret_field="token_secret", **args):
    agent = get_agent(user_id, agent_id)
    if not agent.online:
        raise ValueError("Agent is offline")
    if data is not None:
        settings = dict(data)
        secret = settings.pop(secret_field, None)
        args["settings"] = settings
        if secret is not None:
            try:
                args["encrypted_value"] = encrypt_for_public_key(secret, agent.public_key)
            except SecretEnvelopeError as exc:
                raise ValueError("Agent encryption key is unavailable; reconnect or update the agent") from exc
    response = getattr(AgentService, command)(agent, **args)
    if is_agent_timeout_response(response):
        raise AgentCommandFailed({**response, "log": "Agent request timed out; reload the settings before retrying"})
    if response.get("state") != AgentOperationState.success:
        raise ValueError(response.get("log") or "Connection request failed")
    result = response.get("data") or {}
    if result.get("connections") is not None:
        agent.connections = AgentConnectionsSchema().load(result["connections"])
        db.session.commit()
        emit_agents_update(agent.user_id)
    return result
