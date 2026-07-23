import json
import logging
from datetime import datetime
from threading import RLock

from drastic_agent.agent.database import agent_operation_queue
from drastic_agent.agent.enums import AgentOperationSource, AgentOperationState, AgentOperationType


def _enum_value(value):
    return value.name if hasattr(value, "name") else value


def _datetime_value(value):
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    if hasattr(value, "name"):
        return value.name
    return str(value)


class OperationStore:
    """Durable outbox for completed operation reports."""

    def __init__(self):
        self._lock = RLock()

    def save(self, operation):
        with operation._lock:
            if not operation.ended:
                raise ValueError("Only completed operations can be added to the outbox")
            payload = {
                "uuid": operation.uuid,
                "type": _enum_value(operation.type),
                "source": _enum_value(operation.source),
                "started": _datetime_value(operation.started),
                "ended": _datetime_value(operation.ended),
                "job_id": operation.job_id,
                "repository_id": operation.repository_id,
                "schedule_id": operation.schedule_id,
                "retention_id": operation.retention_id,
                "parent_operation_uuid": operation.parent_operation_uuid,
                "data": operation.data,
                "artifacts": operation.artifacts,
                "logs": operation._logs,
                "log_cursor": operation._log_cursor,
                "final_state": _enum_value(operation.final_state),
                "full_log": operation._full_log,
            }
            serialized = json.dumps(payload, default=_datetime_value)
            with self._lock:
                agent_operation_queue.upsert(
                    {
                        "uuid": operation.uuid,
                        "status": "finished",
                        "payload": serialized,
                    },
                    ["uuid"],
                )

    def delete(self, operation_uuid):
        with self._lock:
            agent_operation_queue.delete(uuid=operation_uuid)

    def load(self, operation_class):
        operations = []
        with self._lock:
            for row in list(agent_operation_queue.all()):
                try:
                    payload = json.loads(row["payload"])
                    operation = operation_class(
                        type=AgentOperationType[payload["type"]],
                        operation_uuid=payload["uuid"],
                        job_id=payload.get("job_id"),
                        repository_id=payload.get("repository_id"),
                        retention_id=payload.get("retention_id"),
                        schedule_id=payload.get("schedule_id"),
                        parent_operation_uuid=payload.get("parent_operation_uuid"),
                        source=AgentOperationSource[payload["source"]],
                        data=payload.get("data") or {},
                        persist=False,
                    )
                    operation.started = datetime.fromisoformat(payload["started"])
                    operation.ended = (
                        datetime.fromisoformat(payload["ended"])
                        if payload.get("ended")
                        else None
                    )
                    operation.set_artifacts(payload.get("artifacts") or [])
                    operation._logs = payload.get("logs") or []
                    for log in operation._logs:
                        if isinstance(log.get("created"), str):
                            log["created"] = datetime.fromisoformat(log["created"])
                    operation._log_cursor = payload.get("log_cursor", 0)
                    operation._final_state = AgentOperationState[payload["final_state"]]
                    operation._full_log = bool(payload.get("full_log"))
                    if row.get("status") != "finished" or operation.ended is None:
                        raise ValueError("Outbox entry is not a completed operation")
                    operations.append(operation)
                except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
                    logging.error(
                        "Discarding corrupt persisted operation %s: %s", row.get("uuid"), exc
                    )
                    agent_operation_queue.delete(uuid=row.get("uuid"))
        return operations


operation_store = OperationStore()
