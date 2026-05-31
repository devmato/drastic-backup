from datetime import datetime
from uuid import uuid4

from drastic_agent.agent.database import (
    actions,
    agent_operation_artifacts,
    agent_operations,
    retentions,
)
from drastic_agent.agent.enums import AgentOperationSource, AgentOperationState
from drastic_agent.agent.report import AgentReport
from drastic_common.agent.enums import AgentRepositoryKind
from drastic_common.restic.exceptions import ResticError


class BackupJobHandler:
    def __init__(
        self,
        agent,
        job,
        repository_id,
        retention_id=None,
        operation_uuid=None,
        run_options=None,
    ):
        self.agent = agent
        self.job = job
        self.repository_id = repository_id
        self.retention_id = retention_id
        self.operation_uuid = operation_uuid
        self.run_options = run_options or {}
        self.repository = None
        self.operation = None

    def run(self):
        running_job = AgentReport.get_report(job_id=self.job["id"])
        self.operation = self._start_operation()
        report = AgentReport.job_report(
            self.job["id"],
            self.repository_id,
            operation_uuid=self.operation["uuid"],
        )
        report.uuid = self.operation["uuid"]
        report.source = AgentOperationSource[self.operation.get("source") or "manual"]
        report.schedule_id = self.operation.get("schedule_id")
        report.retention_id = self.operation.get("retention_id")
        self._sync_operation(report)

        if running_job:
            report.log_message(
                f"Cannot start job {self.job['id']} because it's already running",
                final_state=AgentOperationState.failed,
            )
            self._finish_operation(AgentOperationState.failed, report)
            return report.finish()

        try:
            self.repository = self.agent.set_repository(self.repository_id)
        except Exception as exc:
            report.log_message(
                f"Could not prepare repository {self.repository_id}: {exc}",
                final_state=AgentOperationState.failed,
            )
            self._finish_operation(AgentOperationState.failed, report)
            return report.finish()

        start_actions = actions.find(job_id=self.job["id"], hook="start")
        error_actions = actions.find(job_id=self.job["id"], hook="error")
        success_actions = actions.find(job_id=self.job["id"], hook="success")
        end_actions = actions.find(job_id=self.job["id"], hook="end")

        report.log_message(
            f"Starting job {self.job['id']} on repository {self.repository['location']}"
        )

        self.agent.execute_actions(report=report, actions=start_actions)
        self._ensure_repository_initialized(report)

        if report.final_state == AgentOperationState.failed:
            self.agent.execute_actions(report=report, actions=error_actions)
            self.agent.execute_actions(report=report, actions=end_actions)
            self._finish_operation(AgentOperationState.failed, report)
            return report.finish()

        try:
            self.run_backup(report)
        except ResticError as exc:
            report.log_message(f"Error during backup: {exc}", final_state=AgentOperationState.failed)
            self.agent.execute_actions(report=report, actions=error_actions)
            self.agent.execute_actions(report=report, actions=end_actions)
            self._finish_operation(AgentOperationState.failed, report)
            return report.finish()
        except Exception as exc:
            report.log_message(
                f"Unexpected error during backup: {exc}", final_state=AgentOperationState.failed
            )
            self.agent.execute_actions(report=report, actions=error_actions)
            self.agent.execute_actions(report=report, actions=end_actions)
            self._finish_operation(AgentOperationState.failed, report)
            return report.finish()

        self._run_post_backup_check(report)
        if report.final_state == AgentOperationState.failed:
            self.agent.execute_actions(report=report, actions=error_actions)
            self.agent.execute_actions(report=report, actions=end_actions)
            self._finish_operation(AgentOperationState.failed, report)
            return report.finish()

        retention = retentions.find_one(id=self.retention_id) if self.retention_id else None
        if retention:
            retention_report = self.agent.cmd_run_retention(
                retention_id=retention["id"],
                repository_id=self.repository["id"],
                job_id=self.job["id"],
                    current_operation_uuid=self.operation["uuid"],
                )
            report.append_logs(retention_report.log_list)

            if retention_report.state == AgentOperationState.failed:
                report.final_state = AgentOperationState.warning

        repository_report = self.agent.cmd_get_repository_stats(repository_id=self.repository["id"])
        report.append_logs(repository_report.log_list)
        if repository_report.state == AgentOperationState.failed:
            report.final_state = AgentOperationState.warning

        if report.final_state != AgentOperationState.failed:
            self.agent.execute_actions(report=report, actions=success_actions)

        self.agent.execute_actions(report=report, actions=end_actions)
        report.log_message(f"Job finished with state: {report.final_state.name}")
        self._finish_operation(report.final_state, report)
        return report.finish()

    def _start_operation(self):
        now = datetime.now()
        operation = {
            "uuid": self.operation_uuid or str(uuid4()),
            "type": "backup",
            "source": "schedule" if self.run_options.get("schedule_id") else "manual",
            "job_id": self.job["id"],
            "repository_id": self.repository_id,
            "schedule_id": self.run_options.get("schedule_id"),
            "retention_id": self.retention_id,
            "state": AgentOperationState.running.name,
            "started": now.isoformat(),
            "ended": None,
            "data": {},
        }
        operation_id = agent_operations.insert(operation)
        operation["id"] = operation_id
        return operation

    def _finish_operation(self, state, report):
        if not self.operation:
            return

        self.operation["state"] = state.name if hasattr(state, "name") else str(state)
        self.operation["ended"] = datetime.now().isoformat()
        agent_operations.update(self.operation, ["id"])
        self._sync_operation(report)

    def _sync_operation(self, report):
        if not self.operation:
            return

        for key in {"uuid", "job_id", "repository_id", "schedule_id", "retention_id"}:
            if key in self.operation:
                setattr(report, key, self.operation.get(key))
        report.artifacts = [
            self._artifact_payload(artifact)
            for artifact in agent_operation_artifacts.find(operation_id=self.operation["id"])
        ]
        report.sent = False

    def _artifact_payload(self, artifact):
        return {
            "uuid": artifact.get("uuid"),
            "artifact_key": artifact.get("artifact_key"),
            "snapshot_id": artifact.get("snapshot_id"),
            "state": artifact.get("state"),
            "data": artifact.get("data") or {},
            "forgotten_at": artifact.get("forgotten_at"),
        }

    def start_artifact(self, artifact_key, data=None):
        artifact = {
            "uuid": str(uuid4()),
            "operation_id": self.operation["id"],
            "artifact_key": artifact_key,
            "snapshot_id": None,
            "state": AgentOperationState.running.name,
            "data": data or {},
            "forgotten_at": None,
        }
        artifact_id = agent_operation_artifacts.insert(artifact)
        artifact["id"] = artifact_id
        return artifact

    def finish_artifact(self, artifact, state=AgentOperationState.success, snapshot_id=None, data=None):
        artifact["state"] = state.name if hasattr(state, "name") else str(state)
        artifact["snapshot_id"] = snapshot_id
        if data:
            artifact["data"] = {**(artifact.get("data") or {}), **data}
        agent_operation_artifacts.update(artifact, ["id"])
        return artifact

    def _ensure_repository_initialized(self, report):
        try:
            self.agent.resticapi.cat_config()
        except ResticError as exc:
            message = str(exc).lower()
            if "unable to open config file" in message or "is there a repository at" in message:
                if self.repository.get("kind") == AgentRepositoryKind.native.value:
                    report.log_message(
                        "Native repository is not initialized. Create it from the backend before running backups.",
                        final_state=AgentOperationState.failed,
                    )
                    return

                try:
                    report.log_message(
                        f"Repository seems uninitalized. Initializing new repository in location: {self.repository['location']}"
                    )
                    self.agent.initialize_repository_with_recovery(self.repository)
                except ResticError as init_exc:
                    report.log_message(
                        f"Repository initialitation failed with message: {init_exc}",
                        final_state=AgentOperationState.failed,
                    )
                except Exception as init_exc:
                    report.log_message(
                        f"Repository initialitation failed with message: {init_exc}",
                        final_state=AgentOperationState.failed,
                    )
            elif "wrong password" in message or "no key found" in message:
                try:
                    report.log_message(
                        "Stored agent repository key failed. Reprovisioning access from recovery envelope."
                    )
                    self.agent.recover_repository_access_with_recovery(self.repository)
                except Exception as recovery_exc:
                    report.log_message(
                        f"Repository access reprovisioning failed with message: {recovery_exc}",
                        final_state=AgentOperationState.failed,
                    )
            else:
                report.log_message(
                    f"Obtaining repository config failed with message: {exc}",
                    final_state=AgentOperationState.failed,
                )

    def _run_post_backup_check(self, report):
        config = self.run_options.get("repository_check") or {}
        if not config.get("enabled"):
            return

        read_data = str(config.get("read_data") or "").strip() or None
        read_data_subset = None if read_data == "100%" else read_data
        read_data_full = read_data == "100%"
        report.log_message("Running repository check after successful backup")
        if read_data_full:
            report.log_message("Reading all repository data")
        elif read_data_subset:
            report.log_message(f"Reading data subset: {read_data_subset}")

        try:
            report.data["check"] = self.agent.resticapi.check(
                read_data=read_data_full,
                read_data_subset=read_data_subset,
            )
        except ResticError as exc:
            report.log_message(
                f"Repository check failed after backup: {exc}",
                final_state=AgentOperationState.failed,
            )
            return

        report.log_message("Repository check finished successfully")

    def run_backup(self, report):
        raise NotImplementedError
