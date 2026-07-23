from flask import current_app
from flask.views import MethodView
from flask_jwt_extended import get_jwt_identity, jwt_required
from flask_smorest import Blueprint, abort
from sqlalchemy.exc import IntegrityError

from drastic_server.extensions import db
from drastic_server.models.agent import (
    Agent,
    AgentOperation,
    AgentOperationState,
    AgentOperationType,
    AgentSession,
)
from drastic_server.models.repository import Repository
from drastic_server.models.user import User
from drastic_server.schemas.agent import AgentOperationStartResponseSchema
from drastic_server.schemas.common import MessageIdSchema, MessageSchema
from drastic_server.schemas.repository import (
    CheckInputSchema,
    RepositoryManageSchema,
    RepositoryResponseSchema,
    RepositoryRevealPasswordInputSchema,
    RepositoryRevealPasswordResponseSchema,
    UnlockAgentResponseSchema,
    UnlockInputSchema,
)
from drastic_server.services.agent import (
    AgentCommand,
    is_agent_conflict_response,
    is_agent_timeout_response,
)
from drastic_server.services.agent.operation_start import (
    agent_operation_start_response,
    fail_started_agent_operation,
    start_agent_operation,
    unknown_agent_operation_dispatch_response,
)
from drastic_server.services.repository import (
    RepositorySecretError,
    delete_quarantined_native_repository,
    ensure_user_agent_recovery_envelopes,
    generate_native_repository_password,
    generate_native_repository_path,
    quarantine_native_repository,
    restore_quarantined_native_repository,
    reveal_repository_recovery_key,
    store_recovery_key,
    validate_user_recovery_key,
)

blp = Blueprint(
    "repositories", __name__, url_prefix="/api/repositories", description="Repository operations"
)


def _abort_recovery_key_error(exc):
    abort(401, message=str(exc), errors={"recovery_key": ["required"]})


@blp.route("/")
class RepositoryList(MethodView):
    @jwt_required()
    @blp.response(200, RepositoryResponseSchema(many=True))
    def get(self):
        user_id = get_jwt_identity()
        return Repository.query.filter(Repository.user_id == user_id).all()

    @jwt_required()
    @blp.arguments(RepositoryManageSchema)
    @blp.response(201, MessageIdSchema)
    def post(self, data):
        user_id = get_jwt_identity()
        if not data.get("name"):
            abort(422, message="Repository name is required")

        try:
            user_recovery_key = validate_user_recovery_key(data.get("recovery_key"))
        except RepositorySecretError as exc:
            _abort_recovery_key_error(exc)

        name = data["name"]
        duplicate = Repository.query.filter(
            Repository.user_id == user_id,
            Repository.name == name,
        ).first()
        if duplicate:
            abort(409, message="Repository with this name already exists")

        kind = data.get("kind") or Repository.KIND_CUSTOM
        environment = data.get("environment") or {}
        location = data.get("location")
        restic_id = None
        repository_recovery_key = data.get("password") or generate_native_repository_password()

        if kind == Repository.KIND_NATIVE:
            if data.get("location"):
                abort(422, message="location is not allowed for native repositories")
            if data.get("repository_path"):
                abort(422, message="repository_path is managed by the server for native repositories")

            repository_path = generate_native_repository_path()
            existing = Repository.query.filter(
                Repository.user_id == user_id,
                Repository.kind == Repository.KIND_NATIVE,
            ).all()
            if any(candidate.repository_path == repository_path for candidate in existing):
                abort(409, message="Native repository path already exists")

            location = repository_path
            environment = {}
        elif not location:
            abort(422, message="location is required for custom repositories")

        repository = Repository(
            name=data["name"],
            kind=kind,
            location=location,
            environment=environment,
            restic_id=restic_id,
            user_id=user_id,
        )
        store_recovery_key(repository, repository_recovery_key, user_recovery_key)
        db.session.add(repository)
        try:
            db.session.flush()
            ensure_user_agent_recovery_envelopes(int(user_id), repository, repository_recovery_key)
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            abort(409, message="Repository with this name already exists")

        return {"msg": "Repository created", "id": repository.id}


@blp.route("/<int:repository_id>/reveal-password")
class RepositoryRevealPassword(MethodView):
    @jwt_required()
    @blp.arguments(RepositoryRevealPasswordInputSchema)
    @blp.response(200, RepositoryRevealPasswordResponseSchema)
    def post(self, data, repository_id):
        user_id = get_jwt_identity()
        user = User.query.get(int(user_id))
        if user is None:
            abort(404, message="User not found")

        repository = Repository.query.filter(
            Repository.id == repository_id, Repository.user_id == user_id
        ).first_or_404()

        try:
            password = reveal_repository_recovery_key(
                user=user,
                repository=repository,
                account_password=data["account_password"],
            )
        except RepositorySecretError as exc:
            abort(401, message=str(exc))

        return {"password": password}


@blp.route("/<int:repository_id>")
class RepositoryDetail(MethodView):
    @jwt_required()
    @blp.response(200, RepositoryResponseSchema)
    def get(self, repository_id):
        user_id = get_jwt_identity()
        return Repository.query.filter(
            Repository.id == repository_id, Repository.user_id == user_id
        ).first_or_404()

    @jwt_required()
    @blp.arguments(RepositoryManageSchema)
    @blp.response(200, MessageSchema)
    def put(self, data, repository_id):
        user_id = get_jwt_identity()
        repository = Repository.query.filter(
            Repository.id == repository_id, Repository.user_id == user_id
        ).first_or_404()

        if data.get("name"):
            repository.name = data["name"]

        if repository.kind == Repository.KIND_CUSTOM:
            if data.get("location"):
                repository.location = data["location"]
            if "environment" in data:
                repository.environment = data.get("environment") or {}

        if data.get("password"):
            abort(400, message="Repository password rotation is not supported yet")

        try:
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            abort(409, message="Repository with this name already exists")

        return {"msg": "Repository updated"}

    @jwt_required()
    @blp.response(200, MessageSchema)
    def delete(self, repository_id):
        user_id = get_jwt_identity()
        repository = Repository.query.filter(
            Repository.id == repository_id, Repository.user_id == user_id
        ).first_or_404()

        if repository.schedules:
            abort(
                400,
                message="Can't remove repository used by schedules. Remove those schedules first.",
            )

        quarantine = None
        try:
            if repository.kind == Repository.KIND_NATIVE:
                running_operation = AgentOperation.query.filter(
                    AgentOperation.repository_id == repository.id,
                    AgentOperation.state == AgentOperationState.running,
                ).first()
                if running_operation is not None:
                    abort(409, message="Repository has a running operation")
                repository_path = repository.repository_path
                if repository_path:
                    quarantine = quarantine_native_repository(repository_path)

            password_secret = repository.password_secret
            repository.agents = []
            db.session.delete(repository)
            db.session.flush()
            if password_secret:
                db.session.delete(password_secret)
            db.session.commit()
        except RuntimeError as exc:
            db.session.rollback()
            if quarantine is not None:
                restore_quarantined_native_repository(quarantine)
            abort(409, message=str(exc))
        except Exception:
            db.session.rollback()
            if quarantine is not None:
                restore_quarantined_native_repository(quarantine)
            raise

        if quarantine is not None:
            try:
                delete_quarantined_native_repository(quarantine)
            except (OSError, RuntimeError) as exc:
                current_app.logger.error(
                    "Could not purge quarantined native repository (%s)", type(exc).__name__
                )

        return {"msg": "Repository deleted"}


@blp.route("/<int:repository_id>/unlock")
class RepositoryUnlock(MethodView):
    @jwt_required()
    @blp.arguments(UnlockInputSchema)
    @blp.response(202, AgentOperationStartResponseSchema)
    def post(self, data, repository_id):
        user_id = get_jwt_identity()
        repository = Repository.query.filter(
            Repository.id == repository_id, Repository.user_id == user_id
        ).first_or_404()

        agent_id = data["agent_id"]
        agent = Agent.query.filter(Agent.id == agent_id, Agent.user_id == user_id).first_or_404()

        if not any(assigned.id == repository.id for assigned in agent.repositories):
            abort(400, message="Repository is not assigned to this agent")

        if not agent.online:
            abort(400, message="Agent is offline")

        operation = start_agent_operation(
            agent=agent,
            repository=repository,
            operation_type=AgentOperationType.repository_unlock,
            msg="Repository unlock started",
            log_message="Repository unlock queued",
        )
        response = AgentCommand(agent=agent).unlock_repository(
            repository_id=repository.id,
            operation_uuid=operation.uuid,
        )
        if is_agent_timeout_response(response):
            return unknown_agent_operation_dispatch_response(
                operation,
                "Repository unlock dispatch status unknown",
            )
        if response.get("state") != AgentOperationState.success:
            message = response.get("log", "Could not start repository unlock")
            fail_started_agent_operation(operation, message)
            abort(409 if is_agent_conflict_response(response) else 400, message=message)

        return agent_operation_start_response(operation, "Repository unlock started")


@blp.route("/<int:repository_id>/check")
class RepositoryCheck(MethodView):
    @jwt_required()
    @blp.arguments(CheckInputSchema)
    @blp.response(202, AgentOperationStartResponseSchema)
    def post(self, data, repository_id):
        user_id = get_jwt_identity()
        repository = Repository.query.filter(
            Repository.id == repository_id, Repository.user_id == user_id
        ).first_or_404()

        agent = Agent.query.filter(
            Agent.id == data["agent_id"], Agent.user_id == user_id
        ).first_or_404()

        if not any(assigned.id == repository.id for assigned in agent.repositories):
            abort(400, message="Repository is not assigned to this agent")

        if not agent.online:
            abort(400, message="Agent is offline")

        operation = start_agent_operation(
            agent=agent,
            repository=repository,
            operation_type=AgentOperationType.repository_check,
            msg="Repository check started",
            log_message="Repository check queued",
            data={"read_data": data.get("read_data_subset")},
        )
        response = AgentCommand(agent=agent).check_repository(
            repository_id=repository.id,
            read_data_subset=data.get("read_data_subset"),
            operation_uuid=operation.uuid,
        )
        if is_agent_timeout_response(response):
            return unknown_agent_operation_dispatch_response(
                operation,
                "Repository check dispatch status unknown",
            )

        if response.get("state") != AgentOperationState.success:
            message = response.get("log", "Could not start repository check")
            fail_started_agent_operation(operation, message)
            abort(409 if is_agent_conflict_response(response) else 400, message=message)

        return agent_operation_start_response(operation, "Repository check started")


@blp.route("/<int:repository_id>/unlock_agents")
class RepositoryUnlockAgents(MethodView):
    @jwt_required()
    @blp.response(200, UnlockAgentResponseSchema(many=True))
    def get(self, repository_id):
        user_id = get_jwt_identity()
        Repository.query.filter(
            Repository.id == repository_id, Repository.user_id == user_id
        ).first_or_404()

        agents = (
            Agent.query.join(AgentSession)
            .filter(Agent.repositories.any(Repository.id == repository_id))
            .all()
        )

        return [agent for agent in agents if agent.online]
