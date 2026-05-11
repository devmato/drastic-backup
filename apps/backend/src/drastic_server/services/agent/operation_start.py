from datetime import datetime
from uuid import uuid4

from drastic_server.extensions import db
from drastic_server.models.agent import (
    AgentOperation,
    AgentOperationLog,
    AgentOperationLogLevel,
    AgentOperationSource,
    AgentOperationState,
)
from drastic_server.utils.realtime import emit_job_state, emit_operation_update


def start_agent_operation(
    *,
    agent,
    operation_type,
    msg,
    job=None,
    repository=None,
    source=AgentOperationSource.manual,
    data=None,
    log_message=None,
):
    operation = AgentOperation(
        uuid=str(uuid4()),
        agent=agent,
        job=job,
        repository=repository,
        type=operation_type,
        state=AgentOperationState.running,
        source=source,
        started=datetime.now(),
        data=data or {},
    )
    db.session.add(operation)
    db.session.flush()
    db.session.add(
        AgentOperationLog(
            operation=operation,
            sequence=0,
            level=AgentOperationLogLevel.info,
            message=log_message or msg,
        )
    )
    db.session.commit()

    emit_operation_update(operation)
    if job is not None:
        emit_job_state(job)

    return operation


def agent_operation_start_response(operation, msg, *, success=True):
    return {
        "msg": msg,
        "id": operation.id,
        "operation_id": operation.id,
        "operation_uuid": operation.uuid,
        "agent_id": operation.agent_id,
        "repository_id": operation.repository_id,
        "job_id": operation.job_id,
        "type": operation.type.name if operation.type else None,
        "state": operation.state.name if operation.state else None,
        "success": success,
    }


def fail_started_agent_operation(operation, message):
    operation.state = AgentOperationState.failed
    operation.ended = datetime.now()
    next_sequence = max((log.sequence or 0 for log in operation.logs), default=0) + 1
    db.session.add(
        AgentOperationLog(
            operation=operation,
            sequence=next_sequence,
            level=AgentOperationLogLevel.error,
            message=message,
        )
    )
    db.session.commit()

    emit_operation_update(operation)
    if operation.job is not None:
        emit_job_state(operation.job)
