from apprise import Apprise
from flask.views import MethodView
from flask_jwt_extended import get_jwt_identity, jwt_required
from flask_smorest import Blueprint

from drastic_server.extensions import db
from drastic_server.models.agent import AgentOperationState, AgentOperationType
from drastic_server.models.notification import NotificationConfig
from drastic_server.schemas.common import MessageSchema
from drastic_server.schemas.notification import (
    NotificationConfigResponseSchema,
    NotificationCreateInputSchema,
    NotificationOptionsResponseSchema,
    NotificationTestInputSchema,
    NotificationTestResponseSchema,
    NotificationUpdateInputSchema,
)

blp = Blueprint(
    "notifications",
    __name__,
    url_prefix="/api/notifications",
    description="Notification operations",
)


@blp.route("/")
class NotificationList(MethodView):
    @jwt_required()
    @blp.response(200, NotificationConfigResponseSchema(many=True))
    def get(self):
        user_id = get_jwt_identity()
        return NotificationConfig.query.filter(NotificationConfig.user_id == user_id).all()

    @jwt_required()
    @blp.arguments(NotificationCreateInputSchema)
    @blp.response(201, MessageSchema)
    def post(self, data):
        user_id = get_jwt_identity()

        config = NotificationConfig(user_id=user_id)
        config.url = data["url"]
        config.operation_types = data["operation_types"]
        config.operation_states = data["operation_states"]

        db.session.add(config)
        db.session.commit()

        return {"msg": "Notification config created"}


@blp.route("/<int:config_id>")
class NotificationDetail(MethodView):
    @jwt_required()
    @blp.response(200, NotificationConfigResponseSchema)
    def get(self, config_id):
        user_id = get_jwt_identity()
        return NotificationConfig.query.filter(
            NotificationConfig.id == config_id,
            NotificationConfig.user_id == user_id,
        ).first_or_404()

    @jwt_required()
    @blp.arguments(NotificationUpdateInputSchema)
    @blp.response(200, MessageSchema)
    def put(self, data, config_id):
        user_id = get_jwt_identity()
        config = NotificationConfig.query.filter(
            NotificationConfig.id == config_id,
            NotificationConfig.user_id == user_id,
        ).first_or_404()

        if "url" in data:
            config.url = data["url"]
        if "operation_types" in data:
            config.operation_types = data["operation_types"]
        if "operation_states" in data:
            config.operation_states = data["operation_states"]

        db.session.commit()

        return {"msg": "Notification config updated"}

    @jwt_required()
    @blp.response(200, MessageSchema)
    def delete(self, config_id):
        user_id = get_jwt_identity()
        config = NotificationConfig.query.filter(
            NotificationConfig.id == config_id,
            NotificationConfig.user_id == user_id,
        ).first_or_404()

        db.session.delete(config)
        db.session.commit()

        return {"msg": "Notification config deleted"}


@blp.route("/test")
class NotificationTest(MethodView):
    @jwt_required()
    @blp.arguments(NotificationTestInputSchema)
    @blp.response(200, NotificationTestResponseSchema)
    def post(self, data):
        url = data["url"]

        apprise = Apprise()
        apprise.add(url)
        success = apprise.notify(
            title="Test notification",
            body="This is a test notification from the drastic server",
        )

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
