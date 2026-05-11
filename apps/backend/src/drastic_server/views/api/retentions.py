from flask.views import MethodView
from flask_jwt_extended import get_jwt_identity, jwt_required
from flask_smorest import Blueprint, abort
from marshmallow import ValidationError

from drastic_server.extensions import db
from drastic_server.models.agent import Agent
from drastic_server.models.job import Job, JobSchedule
from drastic_server.models.retention import Retention
from drastic_server.schemas.common import MessageIdSchema, MessageSchema
from drastic_server.schemas.retention import (
    RetentionCreateInputSchema,
    RetentionResponseSchema,
    RetentionUpdateInputSchema,
)
from drastic_server.services.agent import AgentCommand

blp = Blueprint(
    "retentions", __name__, url_prefix="/api/retentions", description="Retention policy operations"
)


def _apply_retention_payload(retention, data):
    retention.name = data["name"]

    if data["rtype"] == "count":
        retention.keep_last = data["keep_last"]
        retention.keep_hourly = None
        retention.keep_weekly = None
        retention.keep_monthly = None
        retention.keep_yearly = None
        return

    retention.keep_last = None
    retention.keep_hourly = data["keep_hourly"]
    retention.keep_weekly = data["keep_weekly"]
    retention.keep_monthly = data["keep_monthly"]
    retention.keep_yearly = data["keep_yearly"]


@blp.route("/")
class RetentionList(MethodView):
    @jwt_required()
    @blp.response(200, RetentionResponseSchema(many=True))
    def get(self):
        user_id = get_jwt_identity()
        return Retention.query.filter(Retention.user_id == user_id).all()

    @jwt_required()
    @blp.arguments(RetentionCreateInputSchema)
    @blp.response(201, MessageIdSchema)
    def post(self, data):
        user_id = get_jwt_identity()
        retention = Retention(user_id=user_id)
        _apply_retention_payload(retention, data)

        db.session.add(retention)
        db.session.commit()

        return {"msg": "Retention created", "id": retention.id}


@blp.route("/<int:retention_id>")
class RetentionDetail(MethodView):
    @jwt_required()
    @blp.response(200, RetentionResponseSchema)
    def get(self, retention_id):
        user_id = get_jwt_identity()
        return Retention.query.filter(
            Retention.id == retention_id, Retention.user_id == user_id
        ).first_or_404()

    @jwt_required()
    @blp.arguments(RetentionUpdateInputSchema)
    @blp.response(200, MessageSchema)
    def put(self, data, retention_id):
        user_id = get_jwt_identity()
        retention = Retention.query.filter(
            Retention.id == retention_id, Retention.user_id == user_id
        ).first_or_404()

        try:
            payload = RetentionCreateInputSchema().load(
                {
                    "name": data.get("name", retention.name),
                    "rtype": data.get("rtype", retention.rtype),
                    "keep_last": data.get("keep_last", retention.keep_last or 0),
                    "keep_hourly": data.get("keep_hourly", retention.keep_hourly or 0),
                    "keep_weekly": data.get("keep_weekly", retention.keep_weekly or 0),
                    "keep_monthly": data.get("keep_monthly", retention.keep_monthly or 0),
                    "keep_yearly": data.get("keep_yearly", retention.keep_yearly or 0),
                }
            )
        except ValidationError as exc:
            abort(422, errors=exc.messages)
        _apply_retention_payload(retention, payload)

        db.session.commit()

        agents = (
            Agent.query.join(Job)
            .join(JobSchedule)
            .filter(JobSchedule.retention_id == retention.id)
            .distinct()
            .all()
        )
        for agent in agents:
            if agent.online:
                AgentCommand(agent=agent).sync()

        return {"msg": "Retention updated"}

    @jwt_required()
    @blp.response(200, MessageSchema)
    def delete(self, retention_id):
        user_id = get_jwt_identity()
        retention = Retention.query.filter(
            Retention.id == retention_id, Retention.user_id == user_id
        ).first_or_404()

        db.session.delete(retention)
        db.session.commit()

        return {"msg": "Retention deleted"}
