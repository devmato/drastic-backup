"""Owner-scoped operation history and deletion constraints."""

from drastic_server.extensions import db
from drastic_server.models.agent import Agent, AgentOperation
from drastic_server.models.chain import BackupChainRun
from drastic_server.services.exceptions import ResourceConflict
from drastic_server.services.queries import require_result


def get_operation(user_id, operation_id):
    return require_result(AgentOperation.query.join(Agent).filter(
        Agent.user_id == user_id, AgentOperation.id == operation_id,
    ))


def list_operations(user_id, agent_id, filters):
    require_result(Agent.query.filter_by(id=agent_id, user_id=user_id))
    query = AgentOperation.query.filter_by(agent_id=agent_id)
    for key in ("type", "state", "job_id"):
        if filters[key] is not None:
            query = query.filter(getattr(AgentOperation, key) == filters[key])
    return query.order_by(AgentOperation.started.desc()).all()


def delete_operation(user_id, operation_id):
    operation = get_operation(user_id, operation_id)
    for run in BackupChainRun.query.filter_by(state="running"):
        if any(step.get("operation_uuid") == operation.uuid for step in run.steps):
            raise ResourceConflict("Operation is still used by an active backup chain")
    db.session.delete(operation)
    db.session.commit()
