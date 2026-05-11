import json
import logging
import os
import pickle
from collections import deque
from datetime import datetime
from uuid import uuid4

from drastic_agent.agent.enums import (
    AgentOperationLogLevel,
    AgentOperationSource,
    AgentOperationState,
    AgentOperationType,
)


def _agent_data_path(*parts):
    base_dir = os.environ.get("DRASTIC_AGENT_DATA_DIR", "/app/data")
    return os.path.join(base_dir, *parts)


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
    ):
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

    @classmethod
    def get_operation(cls, **kwargs):
        for operation in cls.pending_reports:
            if all(hasattr(operation, key) and getattr(operation, key) == value for key, value in kwargs.items()):
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
        return operation

    check_report = check_operation

    @classmethod
    def restore_operation(cls, operation_uuid, job_id, repository_id, data=None):
        operation = cls(
            type=AgentOperationType.restore,
            job_id=job_id,
            repository_id=repository_id,
            data=data,
        )
        operation.uuid = operation_uuid
        cls.pending_reports.append(operation)
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
    def process_job_status(cls, status, job_id, pid=None):
        operation = cls.get_operation(job_id=job_id)
        cls._process_restic_status(operation=operation, status=status, pid=pid)

    @classmethod
    def process_restore_status(cls, status, operation_uuid=None, report_uuid=None, pid=None):
        operation_uuid = operation_uuid or report_uuid
        operation = cls.get_operation(uuid=operation_uuid)
        cls._process_restore_status(operation=operation, status=status, pid=pid)

    @classmethod
    def _process_restore_status(cls, operation, status, pid=None):
        if operation is None:
            return
        if pid:
            operation.data["pid"] = pid
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
    def _process_restic_status(cls, operation, status, pid=None):
        if operation is None:
            return
        if pid:
            operation.data["pid"] = pid
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
        if self.ended:
            return self._final_state
        return AgentOperationState.running

    @property
    def final_state(self):
        return self._final_state

    @final_state.setter
    def final_state(self, state):
        if self._final_state != AgentOperationState.failed and state != AgentOperationState.running:
            self._final_state = state

    def finish(self):
        if self in self.pending_reports:
            self.pending_reports.remove(self)
        self._full_log = True
        self.ended = datetime.now()
        if self.type != AgentOperationType.command:
            logging.info(f"Appending operation {self.uuid} of type {self.type} to operation queue")
            self.finished_reports.append(self)
        return self

    def log_message(self, message, final_state=None):
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
        for log in logs:
            message = log.get("message") if isinstance(log, dict) else str(log)
            self.log_message(message)
        self.sent = False

    @property
    def logs(self):
        if self._full_log:
            return list(self._logs)
        return list(self._logs[self._log_cursor :])

    @property
    def log_list(self):
        return [log["message"] for log in self._logs]

    @property
    def log(self):
        if self._full_log:
            return "\n".join(self.log_list)
        return "\n".join(log["message"] for log in self._logs[self._log_cursor :])

    def mark_logs_sent(self):
        if not self._full_log:
            self._log_cursor = len(self._logs)

    mark_log_sent = mark_logs_sent

    def set_data(self, key, value):
        logging.debug(f"Setting data attribute {key}:{value}")
        self.data[key] = value
        self.sent = False

    @classmethod
    def save_queue(cls):
        for operation in list(cls.pending_reports):
            operation.log_message("Failed due to agent shutdown", final_state=AgentOperationState.failed)
            operation.finish()
        logging.debug(f"Saving queue with {len(cls.finished_reports)} operations")
        os.makedirs(os.path.dirname(_agent_data_path("operations")), exist_ok=True)
        with open(_agent_data_path("operations"), "wb") as file:
            file.write(pickle.dumps(cls.finished_reports))

    @classmethod
    def load_queue(cls):
        if os.path.exists(_agent_data_path("operations")):
            with open(_agent_data_path("operations"), "rb") as file:
                cls.finished_reports = pickle.loads(file.read())
                cls.finished_operations = cls.finished_reports
