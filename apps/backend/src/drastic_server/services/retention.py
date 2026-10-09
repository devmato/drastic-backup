"""Retention policy management and synchronization to all owned agents."""

from drastic_server.extensions import db
from drastic_server.integrations.agent import AgentService
from drastic_server.models.agent import Agent
from drastic_server.models.retention import Retention
from drastic_server.schemas.retention import RetentionCreateInputSchema
from drastic_server.services.chains import require_unused_chain_reference
from drastic_server.services.exceptions import ResourceConflict
from drastic_server.services.queries import require_result


def get_retention(user_id, retention_id):
    return require_result(Retention.query.filter_by(id=retention_id, user_id=user_id))


def list_retentions(user_id):
    return Retention.query.filter_by(user_id=user_id).all()


def _apply(retention, data):
    retention.name = data["name"]
    retention.keep_last = data["keep_last"] if data["rtype"] == "count" else None
    for key in ("keep_hourly", "keep_weekly", "keep_monthly", "keep_yearly"):
        setattr(retention, key, None if data["rtype"] == "count" else data[key])


def _sync_agents(user_id):
    # Chain-only jobs and pending cleanup may still need policies without schedules.
    for agent in Agent.query.filter_by(user_id=user_id):
        if agent.online:
            AgentService.sync(agent)


def create_retention(user_id, data):
    retention = Retention(user_id=user_id)
    _apply(retention, data)
    db.session.add(retention)
    db.session.commit()
    return retention


def update_retention(user_id, retention_id, data):
    retention = get_retention(user_id, retention_id)
    payload = {"name": data.get("name", retention.name), "rtype": data.get("rtype", retention.rtype)}
    for key in ("keep_last", "keep_hourly", "keep_weekly", "keep_monthly", "keep_yearly"):
        payload[key] = data.get(key, getattr(retention, key) or 0)
    _apply(retention, RetentionCreateInputSchema().load(payload))
    db.session.commit()
    _sync_agents(user_id)


def delete_retention(user_id, retention_id):
    retention = get_retention(user_id, retention_id)
    try:
        require_unused_chain_reference(user_id, "retention_id", retention.id)
    except ValueError as exc:
        raise ResourceConflict(str(exc)) from exc
    db.session.delete(retention)
    db.session.commit()
    _sync_agents(user_id)
