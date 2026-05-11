import bcrypt
from flask import current_app
from flask.views import MethodView
from flask_jwt_extended import get_jwt_identity, jwt_required
from flask_smorest import Blueprint, abort

from drastic_server.extensions import db
from drastic_server.models.agent import Agent, AgentOperation, AgentOperationState
from drastic_server.models.job import Job
from drastic_server.models.repository import Repository
from drastic_server.models.user import User
from drastic_server.schemas.agent import (
    AgentInstallOptionsResponseSchema,
    AgentOperationQuerySchema,
    AgentOperationResponseSchema,
    AgentRegisterInputSchema,
    AgentRegisterResponseSchema,
    AgentRepositoryAssignInputSchema,
    AgentResponseSchema,
    AgentSyncInputSchema,
)
from drastic_server.schemas.common import MessageSchema
from drastic_server.services.agent import (
    AgentCommand,
    build_agent_install_targets,
    is_agent_timeout_response,
)
from drastic_server.services.repository import (
    RepositorySecretError,
    decrypt_recovery_key,
    ensure_agent_envelopes_from_user_recovery_key,
    ensure_agent_recovery_envelope,
    validate_user_recovery_key,
)
from drastic_server.utils.realtime import emit_agents_update, emit_jobs_update
from drastic_server.utils.urls import explicit_public_url, public_server_url

blp = Blueprint("agents", __name__, url_prefix="/api/agents", description="Agent operations")


def _abort_recovery_key_error(exc):
    abort(401, message=str(exc), errors={"recovery_key": ["required"]})


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
        )
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
            AgentCommand(agent=agent).sync()

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
                user_recovery_key = validate_user_recovery_key(recovery_key)
                for repository in agent.repositories:
                    if repository.encrypted_recovery_key:
                        repository_recovery_key = decrypt_recovery_key(repository, user_recovery_key)
                        ensure_agent_recovery_envelope(agent, repository, repository_recovery_key)
            except RepositorySecretError as exc:
                _abort_recovery_key_error(exc)
            db.session.commit()

        if not agent.online:
            abort(400, message="Agent is offline")

        response = AgentCommand(agent=agent).sync(await_response=True)
        if is_agent_timeout_response(response):
            abort(504, message=response.get("log", "Agent request timed out"))
        if response.get("state") != AgentOperationState.success:
            abort(400, message=response.get("log", "Agent sync failed"))

        return {"msg": "Agent synchronized"}


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
    @blp.response(200, AgentOperationResponseSchema)
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
