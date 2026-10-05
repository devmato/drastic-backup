from datetime import datetime, timezone

from apprise import Apprise
from flask import current_app, render_template
from jinja2 import TemplateNotFound
from sqlalchemy.exc import IntegrityError

from drastic_server.extensions import db
from drastic_server.models.agent import (
    Agent,
    AgentOperation,
    AgentOperationArtifact,
    AgentOperationLog,
    AgentOperationState,
    AgentOperationType,
)
from drastic_server.models.job import Job, JobSchedule
from drastic_server.models.notification import NotificationConfig, NotificationDelivery
from drastic_server.models.repository import Repository
from drastic_server.models.retention import Retention
from drastic_server.schemas.agent import AgentOperationSchema
from drastic_server.utils.realtime import emit_job_state, emit_operation_update

INGEST_NOTIFICATION_RETRY_LIMIT = 5
STARTUP_NOTIFICATION_RETRY_LIMIT = 25
_PENDING_PARENT_UUID_KEY = "_drastic_pending_parent_operation_uuid"


class AgentOperationService:
    def __init__(self, agent):
        self.agent = agent

    def ingest(self, operation_json):
        operation_data = AgentOperationSchema().load(operation_json)
        if operation_data["type"] == AgentOperationType.command and operation_data["data"].get("diagnostic"):
            from drastic_server.services.diagnostics import ingest_report
            return ingest_report(self.agent, operation_data)
        operation = AgentOperation.query.filter(
            AgentOperation.uuid == operation_data["uuid"]
        ).first()

        new_operation = False
        if operation:
            if operation.agent_id != self.agent.id:
                raise PermissionError("Operation does not belong to this agent")
        else:
            self._resolve_new_operation_references(operation_data)

        self._validate_artifact_references(operation_data)

        if not operation:
            operation = AgentOperation(uuid=operation_data["uuid"], agent_id=self.agent.id)
            new_operation = True
            db.session.add(operation)

        previous_state = operation.state
        if new_operation:
            operation.type = operation_data["type"]
            operation.source = operation_data["source"]
            operation.job_id = operation_data.get("job_id")
            operation.repository_id = operation_data.get("repository_id")
            operation.schedule_id = operation_data.get("schedule_id")
            operation.retention_id = operation_data.get("retention_id")
        if previous_state == AgentOperationState.running or previous_state is None:
            operation.state = operation_data["state"]
        pending_parent_uuid = (operation.data or {}).get(_PENDING_PARENT_UUID_KEY)
        operation_data_payload = dict(operation_data.get("data") or {})
        operation_data_payload.pop(_PENDING_PARENT_UUID_KEY, None)
        operation.data = operation_data_payload
        if new_operation:
            self._set_parent_operation(operation, operation_data.get("parent_operation_uuid"))
        elif pending_parent_uuid and operation.parent_operation_id is None:
            self._set_parent_operation(operation, pending_parent_uuid)
        operation.started = operation_data.get("started")
        if previous_state == AgentOperationState.running or previous_state is None:
            operation.ended = operation_data.get("ended")
        db.session.flush()

        self._upsert_logs(operation, operation_data.get("logs") or [])
        self._upsert_artifacts(operation, operation_data.get("artifacts") or [])
        self._mark_forgotten_artifacts(operation_data)
        self._link_waiting_children(operation)
        self._update_repository_from_operation(operation)
        new_delivery_ids = self._queue_notification_deliveries(
            operation,
            new_operation=new_operation,
        )
        db.session.commit()
        operation_id = operation.id

        if self.agent.user.debug_token_hash and operation_data.get("diagnostic_at"):
            from drastic_server.services.diagnostics import capture_operation
            capture_operation(operation, operation_data["diagnostic_at"])

        self._send_notifications(new_delivery_ids)
        self._emit_operation_event(
            lambda: self._emit_operation_events(operation, new_operation=new_operation),
            "operation events",
        )
        return operation_id

    def _set_parent_operation(self, operation, parent_uuid):
        if not parent_uuid:
            return
        parent = AgentOperation.query.filter(
            AgentOperation.uuid == parent_uuid,
            AgentOperation.agent_id == self.agent.id,
        ).first()
        if parent is not None:
            operation.parent_operation = parent
            return

        operation.data = {
            **(operation.data or {}),
            _PENDING_PARENT_UUID_KEY: parent_uuid,
        }

    def _link_waiting_children(self, parent):
        waiting_children = AgentOperation.query.filter(
            AgentOperation.agent_id == self.agent.id,
            AgentOperation.parent_operation_id.is_(None),
            AgentOperation.id != parent.id,
            AgentOperation.data[_PENDING_PARENT_UUID_KEY].as_string() == parent.uuid,
        ).all()
        for child in waiting_children:
            child.parent_operation = parent
            child_data = dict(child.data or {})
            child_data.pop(_PENDING_PARENT_UUID_KEY, None)
            child.data = child_data

    def _resolve_new_operation_references(self, operation_data):
        job_id = operation_data.get("job_id")
        if job_id:
            job = db.session.get(Job, job_id)
            if job is None:
                operation_data["job_id"] = None
            elif job.agent_id != self.agent.id:
                raise PermissionError("Job does not belong to this agent")

        repository_id = operation_data.get("repository_id")
        if repository_id:
            repository = db.session.get(Repository, repository_id)
            if repository is None:
                operation_data["repository_id"] = None
            elif not Repository.query.filter(
                Repository.id == repository_id,
                Repository.agents.any(Agent.id == self.agent.id),
            ).first():
                raise PermissionError("Repository is not assigned to this agent")

        parent_uuid = operation_data.get("parent_operation_uuid")
        if parent_uuid:
            parent = AgentOperation.query.filter(AgentOperation.uuid == parent_uuid).first()
            if parent is not None and parent.agent_id != self.agent.id:
                raise PermissionError("Parent operation does not belong to this agent")

        schedule_id = operation_data.get("schedule_id")
        if schedule_id:
            schedule = db.session.get(JobSchedule, schedule_id)
            if schedule is None:
                operation_data["schedule_id"] = None
            elif schedule.job.agent_id != self.agent.id:
                raise PermissionError("Schedule does not belong to this agent")
            elif operation_data.get("job_id") and schedule.job_id != operation_data["job_id"]:
                raise PermissionError("Schedule does not belong to this job")
            elif (
                operation_data.get("repository_id")
                and schedule.repository_id != operation_data["repository_id"]
            ):
                raise PermissionError("Schedule does not belong to this repository")

        retention_id = operation_data.get("retention_id")
        if retention_id:
            retention = db.session.get(Retention, retention_id)
            if retention is None:
                operation_data["retention_id"] = None
            elif retention.user_id != self.agent.user_id:
                raise PermissionError("Retention does not belong to this agent")

    def _validate_artifact_references(self, operation_data):
        artifact_uuids = {
            artifact_data.get("uuid")
            for artifact_data in operation_data.get("artifacts") or []
            if artifact_data.get("uuid")
        }
        artifact_uuids.update(
            artifact_data.get("uuid")
            for artifact_data in (operation_data.get("data") or {}).get("forgotten_artifacts") or []
            if artifact_data.get("uuid")
        )
        if not artifact_uuids:
            return

        foreign_artifact = (
            AgentOperationArtifact.query.join(AgentOperation)
            .filter(
                AgentOperationArtifact.uuid.in_(artifact_uuids),
                AgentOperation.agent_id != self.agent.id,
            )
            .first()
        )
        if foreign_artifact is not None:
            raise PermissionError("Artifact does not belong to this agent")

    @staticmethod
    def _upsert_logs(operation, logs):
        for log_data in logs:
            sequence = log_data.get("sequence")
            if sequence is None:
                continue
            log = AgentOperationLog.query.filter(
                AgentOperationLog.operation_id == operation.id,
                AgentOperationLog.sequence == sequence,
            ).first()
            if log is None:
                log = AgentOperationLog(operation_id=operation.id, sequence=sequence)
                db.session.add(log)
            log.level = log_data.get("level")
            log.created = log_data.get("created") or log.created or datetime.now(timezone.utc).replace(tzinfo=None)
            log.message = log_data.get("message") or ""
            log.data = log_data.get("data")

    @staticmethod
    def _upsert_artifacts(operation, artifacts):
        for artifact_data in artifacts:
            artifact_key = str(artifact_data.get("artifact_key") or "").strip()
            if not artifact_key:
                continue
            artifact = AgentOperationArtifact.query.filter(
                AgentOperationArtifact.operation_id == operation.id,
                AgentOperationArtifact.artifact_key == artifact_key,
            ).first()
            if artifact is None:
                artifact = AgentOperationArtifact(
                    operation_id=operation.id, artifact_key=artifact_key
                )
                db.session.add(artifact)

            if artifact_data.get("uuid"):
                artifact.uuid = artifact_data["uuid"]
            artifact.snapshot_id = artifact_data.get("snapshot_id")
            artifact.state = artifact_data.get("state") or "running"
            artifact.data = artifact_data.get("data") or {}
            artifact.forgotten_at = _parse_datetime(artifact_data.get("forgotten_at"))

    def _mark_forgotten_artifacts(self, operation_data):
        for artifact_data in (operation_data.get("data") or {}).get("forgotten_artifacts") or []:
            artifact_uuid = artifact_data.get("uuid")
            if not artifact_uuid:
                continue
            artifact = (
                AgentOperationArtifact.query.join(AgentOperation)
                .filter(
                    AgentOperationArtifact.uuid == artifact_uuid,
                    AgentOperation.agent_id == self.agent.id,
                )
                .first()
            )
            if artifact is None:
                continue
            artifact.forgotten_at = _parse_datetime(artifact_data.get("forgotten_at"))

    @staticmethod
    def _update_repository_from_operation(operation):
        if operation.type != AgentOperationType.repository_stats or not operation.repository_id:
            return

        repository = Repository.query.filter(
            Repository.id == operation.repository_id,
            Repository.restic_id.is_(None),
        ).first()
        if repository and operation.data and "config" in operation.data:
            repository.restic_id = operation.data["config"]["id"][0:8]

    @staticmethod
    def _send_notifications(delivery_ids):
        try:
            AgentOperationService._retry_open_notification_deliveries(
                delivery_ids=delivery_ids,
                limit=INGEST_NOTIFICATION_RETRY_LIMIT,
            )
        except Exception as exc:
            db.session.rollback()
            current_app.logger.warning(
                "Notification processing failed after operation commit (%s)",
                type(exc).__name__,
            )

    @staticmethod
    def _queue_notification_deliveries(operation, *, new_operation):
        if not new_operation and operation.state == AgentOperationState.running:
            return []

        configs = NotificationConfig.query.filter(
            NotificationConfig.user_id == operation.agent.user_id
        ).all()
        matching_configs = [
            config
            for config in configs
            if operation.type.name in (config.operation_types or [])
            and operation.state.name in (config.operation_states or [])
        ]
        new_deliveries = []
        for config in matching_configs:
            delivery = NotificationDelivery.query.filter_by(
                operation_id=operation.id,
                state=operation.state.name,
                notification_config_id=config.id,
            ).first()
            if delivery is None:
                try:
                    with db.session.begin_nested():
                        delivery = NotificationDelivery(
                            operation_id=operation.id,
                            state=operation.state.name,
                            notification_config_id=config.id,
                        )
                        db.session.add(delivery)
                        db.session.flush()
                except IntegrityError:
                    # A concurrent replay already created the unique delivery.
                    continue
                new_deliveries.append(delivery)

        return [delivery.id for delivery in new_deliveries]

    @staticmethod
    def _retry_open_notification_deliveries(*, delivery_ids=(), limit):
        delivery_ids = list(dict.fromkeys(delivery_ids))
        old_deliveries = NotificationDelivery.query.filter(
            NotificationDelivery.sent_at.is_(None)
        ).order_by(NotificationDelivery.created, NotificationDelivery.id)
        if delivery_ids:
            old_deliveries = old_deliveries.filter(NotificationDelivery.id.notin_(delivery_ids))
        old_delivery_ids = [
            delivery_id
            for (delivery_id,) in old_deliveries.with_entities(NotificationDelivery.id)
            .limit(max(0, limit))
            .all()
        ]

        retry_ids = delivery_ids + old_delivery_ids
        for delivery_id in retry_ids:
            try:
                AgentOperationService._deliver_notification(delivery_id)
            except Exception as exc:
                db.session.rollback()
                current_app.logger.warning(
                    "Notification delivery %s could not be processed (%s)",
                    delivery_id,
                    type(exc).__name__,
                )
        return len(retry_ids)

    @staticmethod
    def _deliver_notification(delivery_id):
        delivery = db.session.get(NotificationDelivery, delivery_id)
        if delivery is None or delivery.sent_at is not None:
            return

        claimed_attempt = delivery.attempts + 1
        claimed = NotificationDelivery.query.filter(
            NotificationDelivery.id == delivery_id,
            NotificationDelivery.sent_at.is_(None),
            NotificationDelivery.attempts == delivery.attempts,
        ).update(
            {NotificationDelivery.attempts: claimed_attempt},
            synchronize_session=False,
        )
        db.session.commit()
        if claimed != 1:
            return

        try:
            delivery = db.session.get(NotificationDelivery, delivery_id)
            operation = delivery.operation
            notification_config = delivery.notification_config
            context = {
                "operation": operation,
                "report": operation,
                "delivery_state": delivery.state,
                "notification_config": notification_config,
            }
            try:
                title = render_template(
                    f"notification/apprise/{operation.type.name}/title.html", **context
                )
            except TemplateNotFound:
                title = render_template("notification/apprise/default/title.html", **context)

            try:
                body = render_template(
                    f"notification/apprise/{operation.type.name}/body.html", **context
                )
            except TemplateNotFound:
                body = render_template("notification/apprise/default/body.html", **context)

            notification_url = notification_config.url
            db.session.expire_all()
            current_delivery = (
                db.session.query(
                    NotificationDelivery.sent_at,
                    NotificationDelivery.attempts,
                )
                .filter(NotificationDelivery.id == delivery_id)
                .one_or_none()
            )
            db.session.commit()
            if (
                current_delivery is None
                or current_delivery.sent_at is not None
                or current_delivery.attempts != claimed_attempt
            ):
                return

            apprise = Apprise()
            if not apprise.add(notification_url):
                raise RuntimeError("Notification provider rejected its configuration")
            if not apprise.notify(title=title, body=body):
                raise RuntimeError("Notification provider reported no successful delivery")

            NotificationDelivery.query.filter(
                NotificationDelivery.id == delivery_id,
                NotificationDelivery.sent_at.is_(None),
            ).update(
                {
                    NotificationDelivery.sent_at: datetime.now(),
                    NotificationDelivery.last_error: None,
                },
                synchronize_session=False,
            )
            db.session.commit()
        except Exception as exc:
            db.session.rollback()
            # Do not persist exception text: provider errors can contain credential-bearing URLs.
            NotificationDelivery.query.filter(
                NotificationDelivery.id == delivery_id,
                NotificationDelivery.sent_at.is_(None),
                NotificationDelivery.attempts == claimed_attempt,
            ).update(
                {
                    NotificationDelivery.last_error: (
                        f"Notification delivery failed ({type(exc).__name__})"
                    )
                },
                synchronize_session=False,
            )
            try:
                db.session.commit()
            except Exception:
                db.session.rollback()
            current_app.logger.warning(
                "Notification delivery %s failed (%s)", delivery_id, type(exc).__name__
            )

    @staticmethod
    def _emit_operation_events(operation, *, new_operation):
        if operation.job_id and (new_operation or operation.state != AgentOperationState.running):
            AgentOperationService._emit_operation_event(
                lambda: emit_job_state(operation.job),
                "job state",
            )

        AgentOperationService._emit_operation_event(
            lambda: emit_operation_update(operation),
            "operation update",
        )

    @staticmethod
    def _emit_operation_event(emit, event_name):
        try:
            emit()
        except Exception as exc:
            db.session.rollback()
            current_app.logger.warning(
                "Could not emit %s after operation commit (%s)",
                event_name,
                type(exc).__name__,
            )


def _parse_datetime(value):
    if not value:
        return None
    try:
        timestamp = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
        if timestamp.tzinfo is not None:
            timestamp = timestamp.astimezone(timezone.utc).replace(tzinfo=None)
        return timestamp
    except ValueError:
        return None
