import json
import logging
from collections import deque
from datetime import datetime
from threading import RLock
from uuid import uuid4

from drastic_agent.agent.database import agent_operation_artifacts, agent_operations, db
from drastic_agent.agent.enums import (
    AgentOperationLogLevel,
    AgentOperationSource,
    AgentOperationState,
    AgentOperationType,
)
from drastic_agent.agent.operation_store import operation_store

_PID_UNSET = object()


class AgentOperation:
    uuid = None
    type = None
    source = None
    sent = None
    started = None
    ended = None
    job_id = None
    repository_id = None
    schedule_id = None
    retention_id = None
    parent_operation_uuid = None
    data = None
    artifacts = None

    _final_state = None
    _logs = None
    _log_cursor = None
    _full_log = None

    pending_operations = []
    finished_operations = deque([])
    pending_reports = pending_operations
    finished_reports = finished_operations
    running_operations = {}
    _registry_lock = RLock()

    def __init__(
        self,
        type,
        operation_uuid=None,
        job_id=None,
        repository_id=None,
        retention_id=None,
        schedule_id=None,
        parent_operation_uuid=None,
        source=AgentOperationSource.manual,
        data=None,
        persist=True,
    ):
        self._lock = RLock()
        self.uuid = operation_uuid or str(uuid4())
        self.type = type
        self.source = source
        self.job_id = job_id
        self.repository_id = repository_id
        self.retention_id = retention_id
        self.schedule_id = schedule_id
        self.parent_operation_uuid = parent_operation_uuid
        self.data = data if data is not None else {}
        self.artifacts = []
        self._logs = []
        self._log_cursor = 0
        self.started = datetime.now()
        self.ended = None
        self._final_state = AgentOperationState.success
        self._full_log = False
        self.sent = False
        if persist and self.type != AgentOperationType.command:
            self._history_operation = self._start_history_operation()
            if self._history_operation.get("started"):
                self.started = datetime.fromisoformat(str(self._history_operation["started"]))
        else:
            self._history_operation = None

    def _start_history_operation(self):
        operation, _ = self.reserve_history_operation(
            type=self.type,
            operation_uuid=self.uuid,
            job_id=self.job_id,
            repository_id=self.repository_id,
            retention_id=self.retention_id,
            schedule_id=self.schedule_id,
            parent_operation_uuid=self.parent_operation_uuid,
            source=self.source,
            data=self.data,
            started=self.started,
        )
        return operation

    @classmethod
    def reserve_history_operation(
        cls,
        type,
        operation_uuid,
        job_id=None,
        repository_id=None,
        retention_id=None,
        schedule_id=None,
        parent_operation_uuid=None,
        source=AgentOperationSource.manual,
        data=None,
        started=None,
    ):
        existing = agent_operations.find_one(uuid=operation_uuid)
        if existing:
            return existing, False

        operation = {
            "uuid": operation_uuid,
            "type": _enum_name(type),
            "source": _enum_name(source),
            "job_id": job_id,
            "repository_id": repository_id,
            "schedule_id": schedule_id,
            "retention_id": retention_id,
            "parent_operation_uuid": parent_operation_uuid,
            "state": AgentOperationState.running.name,
            "started": (started or datetime.now()).isoformat(),
            "ended": None,
            "data": data or {},
        }
        operation["id"] = agent_operations.insert(operation)
        return operation, True

    @classmethod
    def discard_history_reservation(cls, operation):
        current = agent_operations.find_one(id=operation["id"])
        if current and current.get("state") == AgentOperationState.running.name:
            agent_operations.delete(id=operation["id"])

    @property
    def history_operation(self):
        return self._history_operation

    def _finish_history_operation(self):
        if not self._history_operation:
            return
        self._history_operation.update(
            {
                "state": self._final_state.name,
                "ended": self.ended.isoformat(),
                "data": self.data,
            }
        )
        agent_operations.update(self._history_operation, ["id"])

    @classmethod
    def get_operation(cls, **kwargs):
        with cls._registry_lock:
            if set(kwargs) == {"uuid"}:
                return cls.running_operations.get(kwargs["uuid"])
            for operation in cls.pending_reports:
                if all(
                    hasattr(operation, key) and getattr(operation, key) == value
                    for key, value in kwargs.items()
                ):
                    return operation

    get_report = get_operation

    @classmethod
    def backup_operation(
        cls,
        job_id,
        repository_id,
        operation_uuid=None,
        schedule_id=None,
        retention_id=None,
        source=AgentOperationSource.manual,
    ):
        operation = cls(
            type=AgentOperationType.backup,
            operation_uuid=operation_uuid,
            job_id=job_id,
            repository_id=repository_id,
            schedule_id=schedule_id,
            retention_id=retention_id,
            source=source,
        )
        cls.pending_reports.append(operation)
        with cls._registry_lock:
            cls.running_operations[operation.uuid] = operation
        return operation

    @classmethod
    def job_report(cls, job_id, repository_id, operation_uuid=None):
        return cls.backup_operation(
            job_id=job_id,
            repository_id=repository_id,
            operation_uuid=operation_uuid,
        )

    @classmethod
    def check_operation(
        cls,
        repository_id,
        operation_uuid=None,
        job_id=None,
        parent_operation_uuid=None,
        data=None,
    ):
        operation = cls(
            type=AgentOperationType.repository_check,
            operation_uuid=operation_uuid,
            job_id=job_id,
            repository_id=repository_id,
            parent_operation_uuid=parent_operation_uuid,
            source=AgentOperationSource.triggered if parent_operation_uuid else AgentOperationSource.manual,
            data=data,
        )
        cls.pending_reports.append(operation)
        with cls._registry_lock:
            cls.running_operations[operation.uuid] = operation
        return operation

    check_report = check_operation

    @classmethod
    def restore_operation(cls, operation_uuid, job_id, repository_id, data=None):
        operation = cls(
            type=AgentOperationType.restore,
            operation_uuid=operation_uuid,
            job_id=job_id,
            repository_id=repository_id,
            data=data,
        )
        cls.pending_reports.append(operation)
        with cls._registry_lock:
            cls.running_operations[operation.uuid] = operation
        return operation

    @classmethod
    def restore_report(cls, report_uuid, job_id, repository_id, data=None):
        return cls.restore_operation(
            operation_uuid=report_uuid,
            job_id=job_id,
            repository_id=repository_id,
            data=data,
        )

    @classmethod
    def command_operation(cls, data=None):
        return cls(type=AgentOperationType.command, data=data)

    command_report = command_operation

    @classmethod
    def process_job_status(cls, status, job_id, pid=_PID_UNSET):
        operation = cls.get_operation(job_id=job_id)
        cls._process_restic_status(operation=operation, status=status, pid=pid)

    @classmethod
    def process_restore_status(
        cls, status, operation_uuid=None, report_uuid=None, pid=_PID_UNSET
    ):
        operation_uuid = operation_uuid or report_uuid
        operation = cls.get_operation(uuid=operation_uuid)
        cls._process_restore_status(operation=operation, status=status, pid=pid)

    @classmethod
    def _process_restore_status(cls, operation, status, pid=_PID_UNSET):
        if operation is None:
            return
        if pid is not _PID_UNSET:
            if pid is None:
                operation.remove_data("pid")
            else:
                operation.set_data("pid", pid)
        if not status:
            return
        if isinstance(status, list):
            for item in status:
                cls._process_restore_status(operation=operation, status=item)
            return
        status_dict = json.loads(status) if isinstance(status, str) else status
        if not isinstance(status_dict, dict):
            return
        restore_param_dict = {
            "total_files": "restore_files_total",
            "files_restored": "restore_files_restored",
            "total_bytes": "restore_bytes_total",
            "bytes_restored": "restore_bytes_restored",
            "percent_done": "restore_percent_done",
        }
        for parameter, target_key in restore_param_dict.items():
            if parameter in status_dict:
                operation.set_data(target_key, status_dict[parameter])

    @classmethod
    def _process_restic_status(cls, operation, status, pid=_PID_UNSET):
        if operation is None:
            return
        if pid is not _PID_UNSET:
            if pid is None:
                operation.remove_data("pid")
            else:
                operation.set_data("pid", pid)
        param_dict = {
            "status": {
                "total_files": "files_total",
                "files_done": "files_processed",
                "total_bytes": "bytes_total",
                "bytes_done": "bytes_processed",
                "current_files": "current_files",
            },
            "summary": {
                "total_files_processed": "files_processed",
                "files_new": "files_new",
                "files_changed": "files_changed",
                "files_unmodified": "files_unmodified",
                "dirs_new": "dirs_new",
                "dirs_changed": "dirs_changed",
                "dirs_unmodified": "dirs_unmodified",
                "total_bytes_processed": "bytes_processed",
                "total_duration": "duration",
                "snapshot_id": "snapshot_id",
            },
        }
        if not status:
            return
        if isinstance(status, list):
            for item in status:
                cls._process_restic_status(operation=operation, status=item)
            return
        status_dict = json.loads(status) if isinstance(status, str) else status
        message_param_dict = param_dict.get(status_dict.get("message_type"), {})
        for parameter in message_param_dict:
            if parameter in status_dict:
                operation.set_data(message_param_dict[parameter], status_dict[parameter])
            elif parameter == "current_files":
                operation.set_data("current_files", [])

    @property
    def state(self):
        with self._lock:
            if self.ended:
                return self._final_state
            return AgentOperationState.running

    @property
    def final_state(self):
        with self._lock:
            return self._final_state

    @final_state.setter
    def final_state(self, state):
        with self._lock:
            if self._final_state not in (
                AgentOperationState.failed,
                AgentOperationState.cancelled,
            ) and state != AgentOperationState.running:
                self._final_state = state

    def downgrade_failure_to_warning(self):
        with self._lock:
            if self._final_state == AgentOperationState.failed:
                self._final_state = AgentOperationState.warning

    def finish(self):
        with self._lock:
            if self.ended:
                return self
            self._full_log = True
            self.ended = datetime.now()
            try:
                if self.type != AgentOperationType.command:
                    with db:
                        self._finish_history_operation()
                        operation_store.save(self)
            except Exception:
                self.ended = None
                self._full_log = False
                raise

        if self in self.pending_reports:
            self.pending_reports.remove(self)
        with self._registry_lock:
            self.running_operations.pop(self.uuid, None)
        if self.type != AgentOperationType.command:
            logging.info(f"Appending operation {self.uuid} of type {self.type} to operation queue")
            self.finished_reports.append(self)
        return self

    def log_message(self, message, final_state=None):
        with self._lock:
            level = AgentOperationLogLevel.info
            if final_state == AgentOperationState.failed:
                logging.error(message)
                level = AgentOperationLogLevel.error
            elif final_state == AgentOperationState.warning:
                logging.warning(message)
                level = AgentOperationLogLevel.warning
            else:
                logging.info(message)
            if final_state:
                self.final_state = final_state
            self._logs.append(
                {
                    "sequence": len(self._logs) + 1,
                    "level": level,
                    "created": datetime.now(),
                    "message": str(message),
                    "data": None,
                }
            )
            self.sent = False

    def append_logs(self, logs):
        with self._lock:
            for log in logs:
                message = log.get("message") if isinstance(log, dict) else str(log)
                self.log_message(message)
            self.sent = False

    @property
    def logs(self):
        with self._lock:
            if self._full_log:
                return list(self._logs)
            return list(self._logs[self._log_cursor :])

    @property
    def log_list(self):
        with self._lock:
            return [log["message"] for log in self._logs]

    @property
    def log(self):
        with self._lock:
            if self._full_log:
                return "\n".join(self.log_list)
            return "\n".join(log["message"] for log in self._logs[self._log_cursor :])

    def mark_logs_sent(self):
        with self._lock:
            if not self._full_log:
                self._log_cursor = len(self._logs)

    mark_log_sent = mark_logs_sent

    def set_data(self, key, value):
        with self._lock:
            logging.debug(f"Setting data attribute {key}:{value}")
            self.data[key] = value
            self.sent = False

    def remove_data(self, key):
        with self._lock:
            if key in self.data:
                self.data.pop(key)
                self.sent = False

    def set_artifacts(self, artifacts):
        with self._lock:
            self.artifacts = list(artifacts)
            self.sent = False

    @classmethod
    def cancel_running(cls, message="Cancelled due to agent shutdown"):
        with cls._registry_lock:
            operations = list(cls.running_operations.values())
        for operation in operations:
            operation.log_message(message, final_state=AgentOperationState.cancelled)

    @classmethod
    def save_queue(cls):
        for operation in list(cls.pending_reports):
            operation.log_message("Failed due to agent shutdown", final_state=AgentOperationState.failed)
            operation.finish()
        logging.debug(f"Saved queue with {len(cls.finished_reports)} operations")

    @classmethod
    def load_queue(cls):
        cls.pending_reports = []
        cls.pending_operations = cls.pending_reports
        cls.running_operations = {}
        cls.finished_reports = deque()
        cls.finished_operations = cls.finished_reports
        cls.finished_reports.extend(operation_store.load(cls))

    @classmethod
    def recover_interrupted(cls):
        for row in list(agent_operations.find(state=AgentOperationState.running.name)):
            operation = cls(
                type=AgentOperationType[row["type"]],
                operation_uuid=row["uuid"],
                job_id=row.get("job_id"),
                repository_id=row.get("repository_id"),
                retention_id=row.get("retention_id"),
                schedule_id=row.get("schedule_id"),
                parent_operation_uuid=row.get("parent_operation_uuid"),
                source=AgentOperationSource[row.get("source") or "manual"],
                data=row.get("data") or {},
                persist=False,
            )
            operation._history_operation = row
            if row.get("started"):
                operation.started = datetime.fromisoformat(str(row["started"]))

            artifacts = []
            for artifact in agent_operation_artifacts.find(operation_id=row["id"]):
                if artifact.get("state") == AgentOperationState.running.name:
                    artifact["state"] = AgentOperationState.failed.name
                    agent_operation_artifacts.update(artifact, ["id"])
                artifacts.append(
                    {
                        "uuid": artifact.get("uuid"),
                        "artifact_key": artifact.get("artifact_key"),
                        "snapshot_id": artifact.get("snapshot_id"),
                        "state": artifact.get("state"),
                        "data": artifact.get("data") or {},
                        "forgotten_at": artifact.get("forgotten_at"),
                    }
                )
            operation.set_artifacts(artifacts)
            operation.log_message(
                "Failed because the agent stopped before the operation completed",
                final_state=AgentOperationState.failed,
            )
            operation.finish()


def _enum_name(value):
    return value.name if hasattr(value, "name") else str(value)
