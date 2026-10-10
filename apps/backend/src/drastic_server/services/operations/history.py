"""Owner-scoped operation history and deletion constraints."""

from sqlalchemy import func
from sqlalchemy.orm import contains_eager

from drastic_common.agent.enums import AgentOperationState, AgentOperationType
from drastic_server.extensions import db
from drastic_server.models.agent import Agent, AgentOperation
from drastic_server.models.chain import BackupChainRun
from drastic_server.services.exceptions import InvalidInput, ResourceConflict
from drastic_server.services.queries import require_result


def get_operation(user_id, operation_id):
    return require_result(AgentOperation.query.join(Agent).filter(
        Agent.user_id == user_id, AgentOperation.id == operation_id,
    ))


def _filtered_operations(user_id, agent_id, filters):
    """Use the same owner and time constraints for history and dashboard counts."""
    if agent_id is not None:
        require_result(Agent.query.filter_by(id=agent_id, user_id=user_id))
    query = AgentOperation.query.join(Agent).filter(Agent.user_id == user_id)
    if agent_id is not None:
        query = query.filter(AgentOperation.agent_id == agent_id)
    for key in ("type", "job_id"):
        if filters.get(key) is not None:
            query = query.filter(getattr(AgentOperation, key) == filters[key])
    if filters.get("group") == "other":
        query = query.filter(AgentOperation.type.notin_([
            AgentOperationType.backup, AgentOperationType.restore, AgentOperationType.agent_update,
        ]))
    state = filters.get("state")
    if state == "attention":
        query = query.filter(AgentOperation.state.in_([
            AgentOperationState.warning, AgentOperationState.failed,
        ]))
    elif state is not None:
        query = query.filter(AgentOperation.state == AgentOperationState[state])
    since, until = filters.get("since"), filters.get("until")
    if since is not None and until is not None and since >= until:
        raise InvalidInput("The start of the period must be before its end")
    # Admission can reserve an operation before its start timestamp is reported.
    timestamp = func.coalesce(AgentOperation.started, AgentOperation.created)
    if since is not None:
        query = query.filter(timestamp >= since)
    if until is not None:
        query = query.filter(timestamp < until)
    return query


def list_operations(user_id, agent_id, filters):
    """Return filtered history; agent_id=None includes all agents owned by the user."""
    return _filtered_operations(user_id, agent_id, filters).options(contains_eager(AgentOperation.agent)).order_by(
        func.coalesce(AgentOperation.started, AgentOperation.created).desc(), AgentOperation.id.desc(),
    ).all()


def summarize_operations(user_id, filters):
    """Count reports without loading their logs, artifacts or payloads."""
    query = _filtered_operations(user_id, filters.get("agent_id"), filters)
    rows = query.with_entities(AgentOperation.type, AgentOperation.state, func.count(AgentOperation.id)).group_by(
        AgentOperation.type, AgentOperation.state,
    ).all()
    return [{"type": operation_type.name, "state": state.name, "count": count}
            for operation_type, state, count in rows]


def delete_operation(user_id, operation_id):
    operation = get_operation(user_id, operation_id)
    for run in BackupChainRun.query.filter_by(state="running"):
        if any(step.get("operation_uuid") == operation.uuid for step in run.steps):
            raise ResourceConflict("Operation is still used by an active backup chain")
    db.session.delete(operation)
    db.session.commit()
