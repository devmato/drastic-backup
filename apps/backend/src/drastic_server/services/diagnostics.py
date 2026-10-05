"""Read-only diagnostics; writes are isolated from backup transactions."""

import logging
from datetime import timedelta
from threading import Lock
from uuid import UUID, uuid4, uuid5

from sqlalchemy.orm import Session

from drastic_common.diagnostics import bounded
from drastic_server.extensions import db
from drastic_server.models.diagnostic import DiagnosticEvent
from drastic_server.models.user import User
from drastic_server.services.auth import utcnow

RETENTION_DAYS = 14
MAX_USER_EVENTS = 5000
# ponytail: process-local aggregation; replicas may produce overlapping summaries.
_lock = Lock()
_proxy = {}


def record(user_id, event_type, payload, *, agent_id=None, operation_uuid=None,
           component="backend", event_uuid=None, occurred_at=None):
    try:
        with Session(db.engine) as session:
            enabled = session.query(User.debug_token_hash).filter(User.id == user_id).scalar()
            if not enabled:
                return True
            event_uuid = event_uuid or str(uuid4())
            if session.query(DiagnosticEvent.id).filter_by(user_id=user_id, event_uuid=event_uuid).first():
                return True
            session.add(DiagnosticEvent(
                user_id=user_id, agent_id=agent_id, operation_uuid=operation_uuid,
                component=component, event_type=event_type, event_uuid=event_uuid,
                occurred_at=occurred_at or utcnow(), received_at=utcnow(), payload=bounded(payload),
            ))
            session.commit()
            return True
    except Exception:
        # Diagnostics must not roll back an operation or disclose an unfiltered SQL payload.
        logging.getLogger(__name__).warning("Could not persist diagnostic event %s", event_type)
        return False


def ingest_report(agent, report):
    enabled = bool(agent.user.debug_token_hash)
    if enabled:
        namespace = UUID(report["uuid"])
        if len(report["logs"]) > 4:
            raise ValueError("Diagnostic report exceeds log limit")
        for log in report["logs"]:
            data = log.get("data") or {}
            operation_uuid = str(UUID(data["operation_uuid"])) if data.get("operation_uuid") else None
            if not 1 <= len(log["message"]) <= 64 or not isinstance(data.get("payload"), dict):
                raise ValueError("Invalid diagnostic entry")
            if not record(agent.user_id, log["message"], data["payload"], agent_id=agent.id,
                          operation_uuid=operation_uuid, component="agent", occurred_at=log["created"],
                          event_uuid=str(uuid5(namespace, f"{agent.id}:{log['sequence']}"))):
                raise RuntimeError("Diagnostic storage unavailable")
    return {"enabled": enabled}


def capture_operation(operation, sampled_at):
    record(operation.agent.user_id, "operation.received", {
        "state": operation.state.name,
        "data": operation.data, "job_id": operation.job_id,
        "repository_id": operation.repository_id,
    }, agent_id=operation.agent_id, operation_uuid=operation.uuid, occurred_at=sampled_at,
        event_uuid=str(uuid5(UUID(operation.uuid), sampled_at.isoformat())))


def record_proxy(user_id, agent_id, repository_id, status, elapsed, request_bytes, response_bytes):
    key = (user_id, agent_id, repository_id)
    with _lock:
        if key not in _proxy and len(_proxy) >= 256:
            return
        item = _proxy.setdefault(key, {"requests": 0, "errors": 0, "duration_seconds": 0,
                                      "declared_request_bytes": 0, "response_bytes": 0,
                                      "max_duration_seconds": 0})
        item["requests"] += 1
        item["errors"] += int(status >= 400)
        item["duration_seconds"] += elapsed
        item["max_duration_seconds"] = max(item["max_duration_seconds"], elapsed)
        item["declared_request_bytes"] += request_bytes or 0
        item["response_bytes"] += response_bytes


def flush_proxy():
    with _lock:
        items = list(_proxy.items())
        _proxy.clear()
    for (user_id, agent_id, repository_id), payload in items:
        record(user_id, "proxy.summary", {**payload, "repository_id": repository_id}, agent_id=agent_id)


def cleanup():
    with Session(db.engine) as session:
        session.query(DiagnosticEvent).filter(
            DiagnosticEvent.received_at < utcnow() - timedelta(days=RETENTION_DAYS)
        ).delete(synchronize_session=False)
        for (user_id,) in session.query(DiagnosticEvent.user_id).distinct():
            boundary = session.query(DiagnosticEvent.id).filter_by(user_id=user_id).order_by(
                DiagnosticEvent.id.desc()).offset(MAX_USER_EVENTS - 1).limit(1).scalar()
            if boundary is not None:
                session.query(DiagnosticEvent).filter(
                    DiagnosticEvent.user_id == user_id, DiagnosticEvent.id < boundary
                ).delete(synchronize_session=False)
        session.commit()


def install_maintenance(app):
    """Start after the first HTTP request, after startup migrations have completed."""
    from drastic_server.extensions import socketio

    start_lock = Lock()
    started = False

    def maintain():
        while True:
            try:
                with app.app_context():
                    flush_proxy()
                    cleanup()
            except Exception:
                app.logger.warning("Diagnostic maintenance failed")
            socketio.sleep(10)

    @app.before_request
    def start():
        nonlocal started
        if app.testing or started:
            return
        with start_lock:
            if not started:
                started = True
                socketio.start_background_task(maintain)
