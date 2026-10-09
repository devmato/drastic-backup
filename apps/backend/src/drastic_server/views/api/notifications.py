from flask.views import MethodView
from flask_jwt_extended import get_jwt_identity, jwt_required
from flask_smorest import Blueprint

from drastic_server.models.agent import AgentOperationState, AgentOperationType
from drastic_server.schemas.common import MessageSchema
from drastic_server.schemas.notification import (
    NotificationConfigResponseSchema,
    NotificationCreateInputSchema,
    NotificationOptionsResponseSchema,
    NotificationTestInputSchema,
    NotificationTestResponseSchema,
    NotificationUpdateInputSchema,
)
from drastic_server.services import notifications
from drastic_server.views.api.errors import register_service_errors

blp = Blueprint(
    "notifications",
    __name__,
    url_prefix="/api/notifications",
    description="Notification operations",
)
register_service_errors(blp)


@blp.route("/")
class NotificationList(MethodView):
    @jwt_required()
    @blp.response(200, NotificationConfigResponseSchema(many=True))
    def get(self):
        return notifications.list_configs(get_jwt_identity())

    @jwt_required()
    @blp.arguments(NotificationCreateInputSchema)
    @blp.response(201, MessageSchema)
    def post(self, data):
        notifications.create_config(get_jwt_identity(), data)

        return {"msg": "Notification config created"}


@blp.route("/<int:config_id>")
class NotificationDetail(MethodView):
    @jwt_required()
    @blp.response(200, NotificationConfigResponseSchema)
    def get(self, config_id):
        return notifications.get_config(get_jwt_identity(), config_id)

    @jwt_required()
    @blp.arguments(NotificationUpdateInputSchema)
    @blp.response(200, MessageSchema)
    def put(self, data, config_id):
        notifications.update_config(get_jwt_identity(), config_id, data)

        return {"msg": "Notification config updated"}

    @jwt_required()
    @blp.response(200, MessageSchema)
    def delete(self, config_id):
        notifications.delete_config(get_jwt_identity(), config_id)

        return {"msg": "Notification config deleted"}


@blp.route("/test")
class NotificationTest(MethodView):
    @jwt_required()
    @blp.arguments(NotificationTestInputSchema)
    @blp.response(200, NotificationTestResponseSchema)
    def post(self, data):
        success = notifications.test_delivery(data["url"])

        if success:
            return {"msg": "Notification test was successful", "success": True}

        return {"msg": "Notification test failed. Please check your apprise URL", "success": False}


@blp.route("/options")
class NotificationOptions(MethodView):
    @jwt_required()
    @blp.response(200, NotificationOptionsResponseSchema)
    def get(self):
        operation_types = [{"value": t.name, "label": t.value["text"]} for t in AgentOperationType]
        operation_states = [{"value": s.name, "label": s.name.capitalize()} for s in AgentOperationState]
        return {"operation_types": operation_types, "operation_states": operation_states}
