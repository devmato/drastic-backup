import bcrypt
from flask import current_app
from flask.views import MethodView
from flask_jwt_extended import get_jwt_identity, jwt_required
from flask_smorest import Blueprint, abort

from drastic_common.agent.commands import AgentCommandName
from drastic_common.secret_envelope import SecretEnvelopeError, encrypt_for_public_key
from drastic_common.ssh_keys import ssh_public_key_algorithm, ssh_public_key_fingerprint
from drastic_common.truenas import AgentConnectionsSchema
from drastic_server.extensions import db
from drastic_server.models.agent import Agent, AgentOperation, AgentOperationState
from drastic_server.models.job import Job
from drastic_server.models.repository import Repository
from drastic_server.models.user import User
from drastic_server.schemas.agent import (
    AgentInstallOptionsResponseSchema,
    AgentOperationDetailResponseSchema,
    AgentOperationQuerySchema,
    AgentOperationResponseSchema,
    AgentProxmoxSettingsInputSchema,
    AgentProxmoxSettingsResponseSchema,
    AgentProxmoxTestResponseSchema,
    AgentRegisterInputSchema,
    AgentRegisterResponseSchema,
    AgentRepositoryAssignInputSchema,
    AgentResponseSchema,
    AgentSyncInputSchema,
    AgentTrueNASSettingsInputSchema,
    AgentTrueNASSettingsResponseSchema,
    AgentTrueNASTestResponseSchema,
    TrueNASDatasetsResponseSchema,
)
from drastic_server.schemas.common import MessageSchema
from drastic_server.services.agent import (
    AgentService,
    build_agent_install_targets,
    is_agent_conflict_response,
    is_agent_timeout_response,
)
from drastic_server.services.repository import (
    RepositorySecretError,
    ensure_agent_envelopes_from_user_recovery_key,
)
from drastic_server.utils.realtime import emit_agents_update, emit_jobs_update
from drastic_server.utils.urls import explicit_public_url, public_server_url

blp = Blueprint("agents", __name__, url_prefix="/api/agents", description="Agent operations")


def _abort_recovery_key_error(exc):
    abort(401, message=str(exc), errors={"recovery_key": ["required"]})


def _set_agent_ssh_public_key(agent, public_key):
    public_key = str(public_key or "").strip() or None
    agent.ssh_public_key = public_key
    agent.ssh_key_fingerprint = ssh_public_key_fingerprint(public_key) if public_key else None
    agent.ssh_key_algorithm = ssh_public_key_algorithm(public_key) if public_key else None


@blp.route("/register")
class AgentRegister(MethodView):
    @blp.arguments(AgentRegisterInputSchema)
    @blp.response(201, AgentRegisterResponseSchema)
    def post(self, data):
        username = data["username"].strip().lower()
        password = data["password"]

        user = User.query.filter_by(name=username).first()
        if user is None or not bcrypt.checkpw(
            password.encode("UTF-8"), user.password.encode("UTF-8")
        ):
            abort(401, message="Wrong username or password")

        secret = Agent.generate_secret()
        secret_hash = bcrypt.hashpw(secret.encode("UTF-8"), bcrypt.gensalt())

        agent = Agent(
            user_id=user.id,
            secret=secret_hash.decode("UTF-8"),
            public_key=data.get("public_key"),
            hostname=data.get("hostname"),
            os=data.get("os"),
            version=data.get("version"),
            install_type=Agent.normalize_install_type(data.get("install_type")),
        )
        _set_agent_ssh_public_key(agent, data.get("ssh_public_key"))
        db.session.add(agent)
        db.session.commit()
        emit_agents_update(user.id)

        return {
            "identifier": agent.id,
            "secret": secret,
            "server_url": public_server_url(),
        }


@blp.route("/install-options")
class AgentInstallOptions(MethodView):
    @jwt_required()
    @blp.response(200, AgentInstallOptionsResponseSchema)
    def get(self):
        return {
            "server_url": explicit_public_url(current_app.config),
            "targets": build_agent_install_targets(current_app.config),
        }

@blp.route("/")
class AgentList(MethodView):
    @jwt_required()
    @blp.response(200, AgentResponseSchema(many=True))
    def get(self):
        user_id = get_jwt_identity()
        return Agent.query.filter(Agent.user_id == user_id).all()


@blp.route("/<int:agent_id>")
class AgentDetail(MethodView):
    @jwt_required()
    @blp.response(200, AgentResponseSchema)
    def get(self, agent_id):
        user_id = get_jwt_identity()
        return Agent.query.filter(Agent.id == agent_id, Agent.user_id == user_id).first_or_404()

    @jwt_required()
    @blp.response(200, MessageSchema)
    def delete(self, agent_id):
        user_id = get_jwt_identity()
        agent = Agent.query.filter(Agent.id == agent_id, Agent.user_id == user_id).first_or_404()

        for operation in agent.operations:
            db.session.delete(operation)

        for job in Job.query.filter(Job.agent_id == agent.id):
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

        return {"msg": "Agent deleted"}


@blp.route("/<int:agent_id>/repositories")
class AgentRepositories(MethodView):
    @jwt_required()
    @blp.arguments(AgentRepositoryAssignInputSchema)
    @blp.response(200, MessageSchema)
    def put(self, data, agent_id):
        user_id = get_jwt_identity()
        agent = Agent.query.filter(Agent.id == agent_id, Agent.user_id == user_id).first_or_404()
        repository_ids = list(dict.fromkeys(data["repository_ids"]))

        repositories = []
        if repository_ids:
            repositories = Repository.query.filter(
                Repository.user_id == user_id,
                Repository.id.in_(repository_ids),
            ).all()

        if len(repositories) != len(repository_ids):
            abort(400, message="One or more repositories are invalid")

        try:
            ensure_agent_envelopes_from_user_recovery_key(
                agent=agent,
                repositories=repositories,
                user_recovery_key=data.get("recovery_key"),
            )
        except RepositorySecretError as exc:
            _abort_recovery_key_error(exc)

        removed_repository_ids = {repository.id for repository in agent.repositories} - set(
            repository_ids
        )
        for job in agent.jobs:
            for schedule in job.schedules:
                if schedule.repository_id in removed_repository_ids:
                    abort(
                        400,
                        message="Cannot unassign repositories that are used by job schedules",
                    )

        agent.repositories = repositories
        db.session.commit()
        emit_agents_update(user_id)
        emit_jobs_update(user_id)

        if agent.online:
            AgentService.sync(agent)

        return {"msg": "Agent repositories updated"}


@blp.route("/<int:agent_id>/sync")
class AgentSync(MethodView):
    @jwt_required()
    @blp.arguments(AgentSyncInputSchema)
    @blp.response(200, MessageSchema)
    def post(self, data, agent_id):
        user_id = get_jwt_identity()
        agent = Agent.query.filter(Agent.id == agent_id, Agent.user_id == user_id).first_or_404()

        recovery_key = data.get("recovery_key")
        if recovery_key:
            try:
                ensure_agent_envelopes_from_user_recovery_key(
                    agent=agent,
                    repositories=list(agent.repositories),
                    user_recovery_key=recovery_key,
                )
            except RepositorySecretError as exc:
                _abort_recovery_key_error(exc)
            db.session.commit()

        if not agent.online:
            abort(400, message="Agent is offline")

        response = AgentService.sync(agent, await_response=True)
        if is_agent_timeout_response(response):
            abort(504, message=response.get("log", "Agent request timed out"))
        if response.get("state") != AgentOperationState.success:
            abort(400, message=response.get("log", "Agent sync failed"))

        return {"msg": "Agent synchronized"}


@blp.route("/<int:agent_id>/actions/<string:action>")
class AgentAction(MethodView):
    @jwt_required()
    @blp.response(200, MessageSchema)
    def post(self, agent_id, action):
        actions = {
            "update": (AgentCommandName.update, "Agent update started", "Agent update could not be started"),
            "reset-known-hosts": (AgentCommandName.reset_known_hosts, "Known hosts reset", "Reset known hosts failed"),
            "rotate-ssh-key": (AgentCommandName.rotate_ssh_key, "SSH key rotated", "Rotate SSH key failed"),
        }
        if action not in actions:
            abort(404)
        command, message, failure = actions[action]
        agent = Agent.query.filter(
            Agent.id == agent_id, Agent.user_id == get_jwt_identity()
        ).first_or_404()
        if not agent.online:
            abort(400, message="Agent is offline")
        if command == AgentCommandName.update and (
            agent.install_type != "git" or str(agent.os or "").lower() != "linux"
        ):
            abort(400, message="Updates require a managed native Linux installation")

        response = AgentService.send_command(agent, command)
        if is_agent_timeout_response(response):
            abort(504, message="Update acknowledgement timed out; check the agent journal before retrying"
                  if command == AgentCommandName.update else response.get("log", "Agent request timed out"))
        if command == AgentCommandName.update and is_agent_conflict_response(response):
            abort(409, message=response.get("log"))
        if response.get("state") != AgentOperationState.success:
            abort(400, message=response.get("log") or failure)

        public_key = (response.get("data") or {}).get("ssh_public_key") if command == AgentCommandName.rotate_ssh_key else None
        if public_key:
            _set_agent_ssh_public_key(agent, public_key)
            db.session.commit()
            emit_agents_update(agent.user_id)

        return {"msg": message}


def _connection_command(agent_id, command, data=None, secret_field="token_secret", **args):
    agent = Agent.query.filter(
        Agent.id == agent_id, Agent.user_id == get_jwt_identity()
    ).first_or_404()
    if not agent.online:
        abort(400, message="Agent is offline")

    if data is not None:
        settings = dict(data)
        token_secret = settings.pop(secret_field, None)
        args["settings"] = settings
        if token_secret is not None:
            try:
                args["encrypted_value"] = encrypt_for_public_key(token_secret, agent.public_key)
            except SecretEnvelopeError:
                abort(400, message="Agent encryption key is unavailable; reconnect or update the agent")

    response = getattr(AgentService, command)(agent, **args)
    if is_agent_timeout_response(response):
        abort(504, message="Agent request timed out; reload the settings before retrying")
    if response.get("state") != AgentOperationState.success:
        abort(400, message=response.get("log") or "Connection request failed")
    result = response.get("data") or {}
    connections = result.get("connections")
    if connections is not None:
        agent.connections = AgentConnectionsSchema().load(connections)
        db.session.commit()
        emit_agents_update(agent.user_id)
    return result


@blp.route("/<int:agent_id>/proxmox-settings")
class AgentProxmoxSettings(MethodView):
    @jwt_required()
    @blp.response(200, AgentProxmoxSettingsResponseSchema)
    def get(self, agent_id):
        return _connection_command(agent_id, "get_proxmox_settings")

    @jwt_required()
    @blp.arguments(AgentProxmoxSettingsInputSchema)
    @blp.response(200, AgentProxmoxSettingsResponseSchema)
    def put(self, data, agent_id):
        return _connection_command(agent_id, "update_proxmox_settings", data)


@blp.route("/<int:agent_id>/proxmox-settings/test")
class AgentProxmoxSettingsTest(MethodView):
    @jwt_required()
    @blp.arguments(AgentProxmoxSettingsInputSchema)
    @blp.response(200, AgentProxmoxTestResponseSchema)
    def post(self, data, agent_id):
        return _connection_command(agent_id, "test_proxmox_settings", data)


@blp.route("/<int:agent_id>/connections/<string:kind>")
class AgentConnectionDetail(MethodView):
    @jwt_required()
    @blp.response(200, MessageSchema)
    def delete(self, agent_id, kind):
        if kind not in {"proxmox", "truenas"}:
            abort(404)
        _connection_command(agent_id, "delete_connection", kind=kind)
        return {"msg": "Connection removed"}


@blp.route("/<int:agent_id>/truenas-settings")
class AgentTrueNASSettings(MethodView):
    @jwt_required()
    @blp.response(200, AgentTrueNASSettingsResponseSchema)
    def get(self, agent_id):
        return _connection_command(agent_id, "truenas_settings")

    @jwt_required()
    @blp.arguments(AgentTrueNASSettingsInputSchema)
    @blp.response(200, AgentTrueNASSettingsResponseSchema)
    def put(self, data, agent_id):
        return _connection_command(agent_id, "truenas_settings", data, secret_field="api_key", action="save")


@blp.route("/<int:agent_id>/truenas-settings/test")
class AgentTrueNASSettingsTest(MethodView):
    @jwt_required()
    @blp.arguments(AgentTrueNASSettingsInputSchema)
    @blp.response(200, AgentTrueNASTestResponseSchema)
    def post(self, data, agent_id):
        return _connection_command(agent_id, "truenas_settings", data, secret_field="api_key", action="test")


@blp.route("/<int:agent_id>/truenas-datasets")
class AgentTrueNASDatasets(MethodView):
    @jwt_required()
    @blp.response(200, TrueNASDatasetsResponseSchema)
    def get(self, agent_id):
        return _connection_command(agent_id, "truenas_settings", action="datasets")


@blp.route("/<int:agent_id>/truenas-settings/cleanup")
class AgentTrueNASCleanup(MethodView):
    @jwt_required()
    @blp.response(200, AgentTrueNASSettingsResponseSchema)
    def post(self, agent_id):
        return _connection_command(agent_id, "truenas_settings", action="cleanup")


@blp.route("/<int:agent_id>/operations")
class AgentOperationList(MethodView):
    @jwt_required()
    @blp.arguments(AgentOperationQuerySchema, location="query")
    @blp.response(200, AgentOperationResponseSchema(many=True))
    def get(self, args, agent_id):
        user_id = get_jwt_identity()
        Agent.query.filter(Agent.id == agent_id, Agent.user_id == user_id).first_or_404()

        query = AgentOperation.query.filter(AgentOperation.agent_id == agent_id)

        operation_type = args["type"]
        if operation_type is not None:
            query = query.filter(AgentOperation.type == operation_type)

        operation_state = args["state"]
        if operation_state is not None:
            query = query.filter(AgentOperation.state == operation_state)

        job_id = args["job_id"]
        if job_id is not None:
            query = query.filter(AgentOperation.job_id == job_id)

        return query.order_by(AgentOperation.started.desc()).all()


@blp.route("/operations/<int:operation_id>")
class AgentOperationDetail(MethodView):
    @jwt_required()
    @blp.response(200, AgentOperationDetailResponseSchema)
    def get(self, operation_id):
        user_id = get_jwt_identity()
        operation = (
            AgentOperation.query.join(Agent)
            .filter(Agent.user_id == user_id, AgentOperation.id == operation_id)
            .first_or_404()
        )

        return operation

    @jwt_required()
    @blp.response(200, MessageSchema)
    def delete(self, operation_id):
        user_id = get_jwt_identity()
        operation = (
            AgentOperation.query.join(Agent)
            .filter(Agent.user_id == user_id, AgentOperation.id == operation_id)
            .first_or_404()
        )

        db.session.delete(operation)
        db.session.commit()

        return {"msg": "Operation deleted"}
