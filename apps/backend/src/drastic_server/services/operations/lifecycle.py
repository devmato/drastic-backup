"""Persist operation transitions before publishing them or dispatching work."""

from datetime import datetime, timedelta, timezone
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

DISPATCH_STATUS_KEY = "dispatch_status"
DISPATCH_STATUS_UPDATED_AT_KEY = "dispatch_status_updated_at"
DISPATCH_UNKNOWN_RECONCILE_AFTER = timedelta(minutes=15)


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
    """Commit the operation before dispatch so late reports can resolve timeouts."""
    operation = AgentOperation(
        uuid=str(uuid4()),
        agent=agent,
        job=job,
        repository=repository,
        type=operation_type,
        state=AgentOperationState.running,
        source=source,
        started=datetime.now(timezone.utc).replace(tzinfo=None),
        data=data or {},
    )
    db.session.add(operation)
    db.session.flush()
    db.session.add(
        AgentOperationLog(
            operation=operation,
            sequence=0,
            level=AgentOperationLogLevel.info,
            created=datetime.now(timezone.utc).replace(tzinfo=None),
            message=log_message or msg,
        )
    )
    db.session.commit()

    emit_operation_update(operation)
    if job is not None:
        emit_job_state(job)

    return operation


def agent_operation_start_response(operation, msg, *, success=True, dispatch_status="accepted"):
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
        "dispatch_status": dispatch_status,
    }


def unknown_agent_operation_dispatch_response(operation, msg):
    operation.data = {
        **(operation.data or {}),
        DISPATCH_STATUS_KEY: "unknown",
        DISPATCH_STATUS_UPDATED_AT_KEY: datetime.now().isoformat(),
    }
    db.session.commit()
    return agent_operation_start_response(operation, msg, dispatch_status="unknown")


def reconcile_unknown_agent_operation_dispatches(*, now=None):
    cutoff = (now or datetime.now()) - DISPATCH_UNKNOWN_RECONCILE_AFTER
    operations = AgentOperation.query.filter(
        AgentOperation.state == AgentOperationState.running,
        AgentOperation.data[DISPATCH_STATUS_KEY].as_string() == "unknown",
    ).all()
    reconciled = 0
    for operation in operations:
        timestamp = (operation.data or {}).get(DISPATCH_STATUS_UPDATED_AT_KEY)
        try:
            dispatch_updated_at = datetime.fromisoformat(timestamp)
            if dispatch_updated_at > cutoff:
                continue
        except (TypeError, ValueError):
            continue

        fail_started_agent_operation(
            operation,
            "Agent did not confirm operation dispatch within 15 minutes",
        )
        reconciled += 1

    return reconciled


def fail_started_agent_operation(operation, message):
    operation.state = AgentOperationState.failed
    operation.ended = datetime.now(timezone.utc).replace(tzinfo=None)
    next_sequence = max((log.sequence or 0 for log in operation.logs), default=0) + 1
    db.session.add(
        AgentOperationLog(
            operation=operation,
            sequence=next_sequence,
            level=AgentOperationLogLevel.error,
            created=datetime.now(timezone.utc).replace(tzinfo=None),
            message=message,
        )
    )
    db.session.commit()

    emit_operation_update(operation)
    if operation.job is not None:
        emit_job_state(operation.job)
