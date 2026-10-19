"""Manage job policies and the account default; agents select retained backup runs."""

from drastic_server.extensions import db
from drastic_server.integrations.agent import AgentService
from drastic_server.models.agent import Agent
from drastic_server.models.job import Job
from drastic_server.models.retention import Retention
from drastic_server.models.user import User
from drastic_server.schemas.retention import RetentionCreateInputSchema
from drastic_server.services.queries import require_result


def get_retention(user_id, retention_id):
    return require_result(Retention.query.filter_by(id=retention_id, user_id=user_id))


def list_retentions(user_id):
    return Retention.query.filter_by(user_id=user_id).all()


def _sync_agents(user_id):
    for agent in Agent.query.filter_by(user_id=user_id):
        if agent.online:
            AgentService.sync(agent)


def _apply(retention, data):
    retention.name = data["name"]
    retention.rules = data["rules"]


def create_retention(user_id, data):
    retention = Retention(user_id=user_id)
    _apply(retention, RetentionCreateInputSchema().load(data))
    db.session.add(retention)
    db.session.commit()
    return retention


def update_retention(user_id, retention_id, data):
    user = require_result(User.query.filter_by(id=user_id))
    retention = get_retention(user_id, retention_id)
    db.session.refresh(retention, with_for_update=True)
    payload = {"name": data.get("name", retention.name), "rules": data.get("rules", retention.rules)}
    _apply(retention, RetentionCreateInputSchema().load(payload))
    if data.get("is_default"):
        user.default_retention_id = retention.id
    elif data.get("is_default") is False and user.default_retention_id == retention.id:
        user.default_retention_id = None
    db.session.commit()
    _sync_agents(user_id)


def delete_retention(user_id, retention_id):
    user = require_result(User.query.filter_by(id=user_id))
    retention = get_retention(user_id, retention_id)
    if user.default_retention_id == retention_id:
        user.default_retention_id = None
    Job.query.filter_by(retention_id=retention_id).update({"retention_id": None, "retention_mode": "disabled"})
    db.session.delete(retention)
    db.session.commit()
    _sync_agents(user_id)
