"""Ingest replayable agent reports and reconcile operation artifacts and logs."""

from datetime import datetime, timezone

from apprise import Apprise
from flask import current_app, render_template
from jinja2 import TemplateNotFound
from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import joinedload

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
        if not Agent.query.filter_by(id=self.agent.id, user_id=self.agent.user_id).with_for_update().first():
            raise PermissionError("Agent no longer exists")
        if operation_data.get("repository_id"):
            # Serialize artifact arrival and deletion acknowledgments in this repository,
            # not the remote cleanup itself. Always lock agent -> repository -> operation.
            Repository.query.filter_by(id=operation_data["repository_id"], user_id=self.agent.user_id).populate_existing().with_for_update().first()
        operation = AgentOperation.query.filter(
            AgentOperation.uuid == operation_data["uuid"]
        ).populate_existing().with_for_update().first()

        new_operation = False
        if operation:
            if operation.agent_id != self.agent.id:
                raise PermissionError("Operation does not belong to this agent")
            if ((operation_data.get("data", {}).get("scope") == "repository" or (operation.data or {}).get("scope") == "repository")
                    and operation.repository_id != operation_data.get("repository_id")):
                raise PermissionError("Retention repository changed during report replay")
            if (operation.data or {}).get("manual_delete"):
                data = operation_data["data"]
                data.update(manual_delete=True, scope="repository", deleted_snapshot_ids=sorted(
                    set(operation.data.get("deleted_snapshot_ids", [])) | set(data.get("deleted_snapshot_ids", []))))
        else:
            self._resolve_new_operation_references(operation_data)

        self._validate_artifact_references(operation_data)
        self._apply_retention_artifacts(operation_data)

        if not operation:
            operation = AgentOperation(uuid=operation_data["uuid"], agent=self.agent)
            new_operation = True
            db.session.add(operation)

        previous_state = operation.state
        previous_data = dict(operation.data or {})
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
        if (operation_data["type"] == AgentOperationType.repository_check and operation_data["state"] == AgentOperationState.failed
                and not operation_data_payload.get("repository_locked")):
            operation_data_payload["retention_check_failed"] = True
        if previous_data.get("job_uuid"):
            operation_data_payload["job_uuid"] = previous_data["job_uuid"]
        if previous_data.get("backup_data_complete"):
            # A delayed progress report cannot retract an acknowledged complete backup.
            operation_data_payload["backup_data_complete"] = True
            if "backup_items_total" in previous_data:
                operation_data_payload["backup_items_total"] = previous_data["backup_items_total"]
        for key in ("retention_check_failed", "retention_check_token"):
            if previous_data.get(key):
                operation_data_payload[key] = previous_data[key]
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
        self._link_waiting_children(operation)
        self._apply_retention_results(operation)
        self._update_repository_from_operation(operation, previous_data)
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

    @staticmethod
    def _apply_retention_results(operation):
        if operation.type not in {AgentOperationType.backup, AgentOperationType.retention}:
            return
        parent = (AgentOperation.query.filter_by(id=operation.parent_operation_id).populate_existing().with_for_update().first()
                  if operation.type == AgentOperationType.retention else operation)
        job_uuid = (operation.data or {}).get("job_uuid")
        if operation.job_id is None and not job_uuid:
            return  # Historical deleted jobs without identity must not share cleanup status.
        scope = [
            AgentOperation.agent_id == operation.agent_id,
            AgentOperation.job_id == operation.job_id,
            AgentOperation.repository_id == operation.repository_id,
        ]
        if job_uuid:
            scope.append(AgentOperation.data["job_uuid"].as_string() == job_uuid)
        latest = AgentOperation.query.filter(
            *scope,
            AgentOperation.type == AgentOperationType.retention,
            AgentOperation.state != AgentOperationState.running,
            AgentOperation.ended.is_not(None),
        ).order_by(AgentOperation.ended.desc(), AgentOperation.id.desc()).populate_existing().with_for_update().first()
        if latest:
            cleanup_pending = bool((latest.data or {}).get("cleanup_pending", latest.state != AgentOperationState.success))
            if (parent is not None and parent.type == AgentOperationType.backup
                    and parent.started is not None and latest.ended >= parent.started):
                parent.data = {**(parent.data or {}), "retention_state": latest.state.name,
                               "cleanup_pending": cleanup_pending}
            if not cleanup_pending:
                # New retention work supersedes older pending work for the same job/repo.
                for backup in AgentOperation.query.filter(*scope, AgentOperation.type == AgentOperationType.backup).populate_existing().with_for_update().all():
                    if (backup.data or {}).get("cleanup_pending") and backup.started and backup.started <= latest.ended:
                        backup.data = {**backup.data, "retention_state": latest.state.name, "cleanup_pending": False}

    def _set_parent_operation(self, operation, parent_uuid):
        if not parent_uuid:
            return
        parent = AgentOperation.query.filter(
            AgentOperation.uuid == parent_uuid,
            AgentOperation.agent_id == self.agent.id,
        ).populate_existing().with_for_update().first()
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
        ).populate_existing().with_for_update().all()
        for child in waiting_children:
            child.parent_operation = parent
            child_data = dict(child.data or {})
            child_data.pop(_PENDING_PARENT_UUID_KEY, None)
            child.data = child_data

    def _resolve_new_operation_references(self, operation_data):
        job_id = operation_data.get("job_id")
        if job_id:
            job = Job.query.filter_by(id=job_id).populate_existing().with_for_update().first()
            if job is None:
                operation_data["job_id"] = None
            elif job.agent_id != self.agent.id:
                raise PermissionError("Job does not belong to this agent")
            elif (operation_data.get("data") or {}).get("job_uuid") not in (None, job.uuid):
                operation_data["job_id"] = None  # Numeric IDs can be reused after hard deletion.

        repository_id = operation_data.get("repository_id")
        if repository_id:
            repository = Repository.query.filter_by(id=repository_id).populate_existing().with_for_update().first()
            if repository is None:
                operation_data["repository_id"] = None
            elif not Repository.query.join(Repository.agents).filter(
                Repository.id == repository_id,
                Agent.id == self.agent.id,
            ).populate_existing().with_for_update().first():
                raise PermissionError("Repository is not assigned to this agent")

        parent_uuid = operation_data.get("parent_operation_uuid")
        if parent_uuid:
            parent = AgentOperation.query.filter(AgentOperation.uuid == parent_uuid).populate_existing().with_for_update().first()
            if parent is not None and parent.agent_id != self.agent.id:
                raise PermissionError("Parent operation does not belong to this agent")

        schedule_id = operation_data.get("schedule_id")
        if schedule_id:
            schedule = JobSchedule.query.filter_by(id=schedule_id).populate_existing().with_for_update().first()
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
            retention = Retention.query.filter_by(id=retention_id).populate_existing().with_for_update().first()
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
        if not artifact_uuids:
            return

        foreign_artifact = (
            AgentOperationArtifact.query.join(AgentOperation)
            .filter(
                AgentOperationArtifact.uuid.in_(artifact_uuids),
                or_(AgentOperation.agent_id.is_(None), AgentOperation.agent_id != self.agent.id),
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
            ).populate_existing().with_for_update().first()
            if log is None:
                log = AgentOperationLog(operation_id=operation.id, sequence=sequence)
                db.session.add(log)
            log.level = log_data.get("level")
            log.created = log_data.get("created") or log.created or datetime.now(timezone.utc).replace(tzinfo=None)
            log.message = log_data.get("message") or ""
            log.data = log_data.get("data")

    @staticmethod
    def _upsert_artifacts(operation, artifacts):
        """Caller holds the repository lock; locking reads see receipts committed while it waited."""
        deleted_ids = None
        for artifact_data in artifacts:
            artifact_key = str(artifact_data.get("artifact_key") or "").strip()
            if not artifact_key:
                continue
            artifact = AgentOperationArtifact.query.filter(
                AgentOperationArtifact.operation_id == operation.id,
                AgentOperationArtifact.artifact_key == artifact_key,
            ).populate_existing().with_for_update().first()
            if artifact is None:
                artifact = AgentOperationArtifact(
                    operation_id=operation.id, artifact_key=artifact_key
                )
                db.session.add(artifact)

            previous_snapshot_id = artifact.snapshot_id
            if artifact_data.get("uuid"):
                artifact.uuid = artifact_data["uuid"]
            if artifact.state != "success" or not artifact.snapshot_id:
                artifact.snapshot_id = artifact_data.get("snapshot_id")
                artifact.state = artifact_data.get("state") or "running"
            artifact.data = artifact_data.get("data") or {}
            # Late/replayed backup reports must not resurrect snapshots removed by another agent.
            artifact.forgotten_at = artifact.forgotten_at or _parse_datetime(artifact_data.get("forgotten_at"))
            if operation.repository_id and artifact.snapshot_id and artifact.snapshot_id != previous_snapshot_id:
                if deleted_ids is None:
                    acknowledgments = AgentOperation.query.with_entities(AgentOperation.data).filter_by(
                        user_id=operation.user_id, repository_id=operation.repository_id, type=AgentOperationType.retention
                    ).filter(AgentOperation.data["manual_delete"].as_boolean().is_(True)).with_for_update().all()
                    deleted_ids = {identity for (data,) in acknowledgments for identity in data.get("deleted_snapshot_ids", [])}
                if artifact.snapshot_id in deleted_ids:
                    artifact.forgotten_at = artifact.forgotten_at or datetime.now(timezone.utc).replace(tzinfo=None)

    def _apply_retention_artifacts(self, operation_data):
        data = operation_data.get("data") or {}
        deleted_ids = data.get("deleted_snapshot_ids") or []
        if deleted_ids:
            if operation_data["type"] != AgentOperationType.retention or not data.get("manual_delete"):
                raise PermissionError("Only restore point deletion may report removed snapshots")
            repository = Repository.query.filter(Repository.id == operation_data.get("repository_id"),
                Repository.user_id == self.agent.user_id, Repository.agents.any(Agent.id == self.agent.id)).first()
            if not repository:
                raise PermissionError("Repository is not assigned to this agent")
            artifacts = AgentOperationArtifact.query.join(AgentOperation).filter(
                AgentOperation.user_id == self.agent.user_id, AgentOperation.repository_id == repository.id,
                AgentOperationArtifact.snapshot_id.in_(deleted_ids)).populate_existing().with_for_update().all()
            for artifact in artifacts:
                artifact.forgotten_at = artifact.forgotten_at or datetime.now(timezone.utc).replace(tzinfo=None)
        reported = {item["uuid"]: item for item in data.get("forgotten_artifacts", []) if item.get("uuid")}
        if not reported:
            return
        repository_scope = operation_data["type"] == AgentOperationType.retention and data.get("scope") == "repository"
        if repository_scope:
            repository = Repository.query.filter(Repository.id == operation_data.get("repository_id"),
                Repository.user_id == self.agent.user_id, Repository.agents.any(Agent.id == self.agent.id)).first()
            if not repository:
                raise PermissionError("Repository is not assigned to this agent")
        artifacts = AgentOperationArtifact.query.options(joinedload(AgentOperationArtifact.operation)).filter(
            AgentOperationArtifact.uuid.in_(reported)).populate_existing().with_for_update().all()
        for artifact in artifacts:
            source = artifact.operation
            item = reported[artifact.uuid]
            if repository_scope:
                if (source.user_id != self.agent.user_id or source.repository_id != repository.id
                        or source.uuid != item.get("operation_uuid") or artifact.snapshot_id != item.get("snapshot_id")):
                    raise PermissionError("Artifact does not belong to this repository")
            elif source.agent_id != self.agent.id:
                raise PermissionError("Artifact does not belong to this agent")
            artifact.forgotten_at = artifact.forgotten_at or _parse_datetime(item.get("forgotten_at"))

    @staticmethod
    def _update_repository_from_operation(operation, previous_data):
        if not operation.repository_id:
            return
        repository = db.session.get(Repository, operation.repository_id)
        if operation.type == AgentOperationType.backup and not repository.restic_id and "config" in operation.data:
            repository.restic_id = operation.data["config"]["id"][0:8]
        if operation.data.get("retention_check_failed") and not previous_data.get("retention_check_failed"):
            repository.retention_check_token = operation.uuid
        elif not operation.data.get("retention_check_failed") and (operation.data.get("retention_check_full") or (
                operation.type == AgentOperationType.repository_check and operation.state == AgentOperationState.success
                and operation.data.get("read_data") == "100%" and "check" in operation.data)):
            token = operation.data.get("retention_check_verified") or previous_data.get("retention_check_token")
            if token:
                # A passing check must not clear a failure reported after that check began.
                Repository.query.filter_by(id=repository.id, retention_check_token=token).update(
                    {"retention_check_token": None}, synchronize_session="fetch")

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
            NotificationConfig.user_id == operation.user_id
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
