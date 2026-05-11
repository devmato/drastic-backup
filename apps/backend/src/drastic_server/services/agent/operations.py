from datetime import datetime

from apprise import Apprise
from flask import render_template
from jinja2 import TemplateNotFound

from drastic_server.extensions import db
from drastic_server.models.agent import (
    AgentOperation,
    AgentOperationArtifact,
    AgentOperationLog,
    AgentOperationState,
    AgentOperationType,
)
from drastic_server.models.notification import NotificationConfig
from drastic_server.models.repository import Repository
from drastic_server.schemas.agent import AgentOperationSchema
from drastic_server.utils.realtime import emit_job_state, emit_operation_update


class AgentOperationService:
    def __init__(self, agent):
        self.agent = agent

    def ingest(self, operation_json):
        operation_data = AgentOperationSchema().load(operation_json)
        operation = AgentOperation.query.filter(AgentOperation.uuid == operation_data["uuid"]).first()

        new_operation = False
        if not operation:
            operation = AgentOperation(uuid=operation_data["uuid"], agent_id=self.agent.id)
            new_operation = True
            db.session.add(operation)

        previous_state = operation.state
        operation.agent_id = self.agent.id
        operation.type = operation_data["type"]
        operation.state = operation_data["state"]
        operation.source = operation_data["source"]
        operation.job_id = operation_data.get("job_id")
        operation.repository_id = operation_data.get("repository_id")
        operation.schedule_id = operation_data.get("schedule_id")
        operation.retention_id = operation_data.get("retention_id")
        operation.parent_operation = self._parent_operation(operation_data)
        operation.data = operation_data.get("data") or {}
        operation.started = operation_data.get("started")
        operation.ended = operation_data.get("ended")
        db.session.flush()

        self._upsert_logs(operation, operation_data.get("logs") or [])
        self._upsert_artifacts(operation, operation_data.get("artifacts") or [])
        self._mark_forgotten_artifacts(operation_data)
        db.session.commit()

        self._update_repository_from_operation(operation)
        self._send_notifications(operation, new_operation, previous_state)
        self._emit_operation_events(operation, new_operation=new_operation)
        return operation.id

    def _parent_operation(self, operation_data):
        parent_uuid = operation_data.get("parent_operation_uuid")
        if not parent_uuid:
            return None
        return AgentOperation.query.filter(AgentOperation.uuid == parent_uuid).first()

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
            log.created = log_data.get("created") or log.created
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
                artifact = AgentOperationArtifact(operation_id=operation.id, artifact_key=artifact_key)
                db.session.add(artifact)

            if artifact_data.get("uuid"):
                artifact.uuid = artifact_data["uuid"]
            artifact.snapshot_id = artifact_data.get("snapshot_id")
            artifact.state = artifact_data.get("state") or "running"
            artifact.data = artifact_data.get("data") or {}
            artifact.forgotten_at = _parse_datetime(artifact_data.get("forgotten_at"))

    @staticmethod
    def _mark_forgotten_artifacts(operation_data):
        for artifact_data in (operation_data.get("data") or {}).get("forgotten_artifacts") or []:
            artifact_uuid = artifact_data.get("uuid")
            if not artifact_uuid:
                continue
            artifact = AgentOperationArtifact.query.filter(
                AgentOperationArtifact.uuid == artifact_uuid
            ).first()
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
            db.session.commit()

    @staticmethod
    def _send_notifications(operation, new_operation, previous_state):
        if not new_operation and operation.state == AgentOperationState.running:
            return
        if previous_state == operation.state and not new_operation:
            return

        notification_configs = NotificationConfig.query.filter(
            NotificationConfig.user_id == operation.agent.user_id,
            NotificationConfig.operation_types.contains(operation.type.name),
            NotificationConfig.operation_states.contains(operation.state.name),
        ).all()

        for notification_config in notification_configs:
            try:
                title = render_template(
                    f"notification/apprise/{operation.type.name}/title.html",
                    operation=operation,
                    report=operation,
                    notification_config=notification_config,
                )
            except TemplateNotFound:
                title = render_template(
                    "notification/apprise/default/title.html",
                    operation=operation,
                    report=operation,
                    notification_config=notification_config,
                )

            try:
                body = render_template(
                    f"notification/apprise/{operation.type.name}/body.html",
                    operation=operation,
                    report=operation,
                    notification_config=notification_config,
                )
            except TemplateNotFound:
                body = render_template(
                    "notification/apprise/default/body.html",
                    operation=operation,
                    report=operation,
                    notification_config=notification_config,
                )

            apprise = Apprise()
            apprise.add(notification_config.url)
            apprise.notify(title=title, body=body)

    @staticmethod
    def _emit_operation_events(operation, *, new_operation):
        if operation.job_id and (new_operation or operation.state != AgentOperationState.running):
            emit_job_state(operation.job)

        emit_operation_update(operation)


def _parse_datetime(value):
    if isinstance(value, datetime):
        return value
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None
