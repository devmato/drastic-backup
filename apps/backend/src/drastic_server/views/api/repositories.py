"""Repository HTTP contracts; lifecycle and filesystem writes live in services."""

from flask.views import MethodView
from flask_jwt_extended import get_jwt_identity, jwt_required
from flask_smorest import Blueprint

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
from drastic_server.services.repository import maintenance, management
from drastic_server.views.api.errors import register_service_errors

blp = Blueprint("repositories", __name__, url_prefix="/api/repositories", description="Repository operations")
register_service_errors(blp)


@blp.route("/")
class RepositoryList(MethodView):
    @jwt_required()
    @blp.response(200, RepositoryResponseSchema(many=True))
    def get(self):
        return management.list_repositories(get_jwt_identity())

    @jwt_required()
    @blp.arguments(RepositoryManageSchema)
    @blp.response(201, MessageIdSchema)
    def post(self, data):
        repository = management.create_repository(get_jwt_identity(), data)
        return {"msg": "Repository created", "id": repository.id}


@blp.route("/<int:repository_id>/reveal-password")
class RepositoryRevealPassword(MethodView):
    @jwt_required()
    @blp.arguments(RepositoryRevealPasswordInputSchema)
    @blp.response(200, RepositoryRevealPasswordResponseSchema)
    def post(self, data, repository_id):
        return {"password": management.reveal_password(get_jwt_identity(), repository_id, data["account_password"])}


@blp.route("/<int:repository_id>")
class RepositoryDetail(MethodView):
    @jwt_required()
    @blp.response(200, RepositoryResponseSchema)
    def get(self, repository_id):
        return management.get_repository(get_jwt_identity(), repository_id)

    @jwt_required()
    @blp.arguments(RepositoryManageSchema)
    @blp.response(200, MessageSchema)
    def put(self, data, repository_id):
        management.update_repository(get_jwt_identity(), repository_id, data)
        return {"msg": "Repository updated"}

    @jwt_required()
    @blp.response(200, MessageSchema)
    def delete(self, repository_id):
        management.delete_repository(get_jwt_identity(), repository_id)
        return {"msg": "Repository deleted"}


@blp.route("/<int:repository_id>/unlock")
class RepositoryUnlock(MethodView):
    @jwt_required()
    @blp.arguments(UnlockInputSchema)
    @blp.response(202, AgentOperationStartResponseSchema)
    def post(self, data, repository_id):
        return maintenance.start_unlock(get_jwt_identity(), repository_id, data)


@blp.route("/<int:repository_id>/check")
class RepositoryCheck(MethodView):
    @jwt_required()
    @blp.arguments(CheckInputSchema)
    @blp.response(202, AgentOperationStartResponseSchema)
    def post(self, data, repository_id):
        return maintenance.start_check(get_jwt_identity(), repository_id, data)


@blp.route("/<int:repository_id>/unlock_agents")
class RepositoryUnlockAgents(MethodView):
    @jwt_required()
    @blp.response(200, UnlockAgentResponseSchema(many=True))
    def get(self, repository_id):
        return management.unlock_agents(get_jwt_identity(), repository_id)
