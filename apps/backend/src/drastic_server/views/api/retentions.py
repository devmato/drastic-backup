from flask.views import MethodView
from flask_jwt_extended import get_jwt_identity, jwt_required
from flask_smorest import Blueprint

from drastic_server.schemas.common import MessageIdSchema, MessageSchema
from drastic_server.schemas.retention import (
    RetentionCreateInputSchema,
    RetentionResponseSchema,
    RetentionUpdateInputSchema,
)
from drastic_server.services import retention
from drastic_server.views.api.errors import register_service_errors

blp = Blueprint("retentions", __name__, url_prefix="/api/retentions", description="Retention policy operations")
register_service_errors(blp)


@blp.route("/")
class RetentionList(MethodView):
    @jwt_required()
    @blp.response(200, RetentionResponseSchema(many=True))
    def get(self):
        return retention.list_retentions(get_jwt_identity())

    @jwt_required()
    @blp.arguments(RetentionCreateInputSchema)
    @blp.response(201, MessageIdSchema)
    def post(self, data):
        policy = retention.create_retention(get_jwt_identity(), data)
        return {"msg": "Retention created", "id": policy.id}


@blp.route("/<int:retention_id>")
class RetentionDetail(MethodView):
    @jwt_required()
    @blp.response(200, RetentionResponseSchema)
    def get(self, retention_id):
        return retention.get_retention(get_jwt_identity(), retention_id)

    @jwt_required()
    @blp.arguments(RetentionUpdateInputSchema)
    @blp.response(200, MessageSchema)
    def put(self, data, retention_id):
        retention.update_retention(get_jwt_identity(), retention_id, data)
        return {"msg": "Retention updated"}

    @jwt_required()
    @blp.response(200, MessageSchema)
    def delete(self, retention_id):
        retention.delete_retention(get_jwt_identity(), retention_id)
        return {"msg": "Retention deleted"}
