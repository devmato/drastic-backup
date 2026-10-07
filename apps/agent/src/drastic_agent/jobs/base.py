from uuid import uuid4

from drastic_agent.agent.database import (
    actions,
    agent_operation_artifacts,
    retentions,
)
from drastic_agent.agent.database import agent as agent_settings
from drastic_agent.agent.enums import AgentOperationSource, AgentOperationState
from drastic_agent.agent.report import AgentReport
from drastic_common import diagnostics
from drastic_common.agent.enums import AgentRepositoryKind
from drastic_common.restic.exceptions import ResticCancelledError, ResticError


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
        self.report = None

    def run(self):
        running_job = AgentReport.get_report(job_id=self.job["id"])
        report = AgentReport.backup_operation(
            job_id=self.job["id"],
            repository_id=self.repository_id,
            operation_uuid=self.operation_uuid,
            schedule_id=self.run_options.get("schedule_id"),
            retention_id=self.retention_id,
            source=(
                AgentOperationSource.triggered
                if self.run_options.get("chain_run_id")
                else AgentOperationSource.schedule
                if self.run_options.get("schedule_id")
                else AgentOperationSource.manual
            ),
        )
        self.operation = report.history_operation
        self.report = report
        if self.run_options.get("chain_run_id"):
            report.set_data("chain_run_id", self.run_options["chain_run_id"])
        report.set_data("backup_phase", "preparing")
        self._sync_operation(report)

        hook_actions = {}
        for hook_name in ("start", "error", "success", "end"):
            try:
                hook_actions[hook_name] = actions.find(
                    job_id=self.job["id"], hook=hook_name
                )
            except Exception as exc:
                hook_actions[hook_name] = []
                report.log_message(
                    f"Could not load {hook_name} actions: {exc}",
                    final_state=AgentOperationState.failed,
                )

        start_actions = hook_actions["start"]
        error_actions = hook_actions["error"]
        success_actions = hook_actions["success"]
        end_actions = hook_actions["end"]

        if running_job:
            report.log_message(
                f"Cannot start job {self.job['id']} because it's already running",
                final_state=AgentOperationState.failed,
            )
            self._finish_operation(AgentOperationState.failed, report)
            return report.finish()

        backup_completed = False
        try:
            self.repository = self.agent.set_repository(self.repository_id)
        except Exception as exc:
            report.log_message(
                f"Could not prepare repository {self.repository_id}: {exc}",
                final_state=AgentOperationState.failed,
            )
            self._execute_actions_safely(report, error_actions, "error", post_backup=False)
            self._execute_actions_safely(report, end_actions, "end", post_backup=False)
            self._finish_operation(AgentOperationState.failed, report)
            return report.finish()

        report.log_message(
            f"Starting job {self.job['id']} on repository {self.repository['location']}"
        )
        if diagnostics.active():
            diagnostics.record("operation.configuration", {
                "job": self.job, "run_options": self.run_options,
                "repository": {key: self.repository.get(key) for key in ("id", "kind", "location")},
                "retention": retentions.find_one(id=self.retention_id) if self.retention_id else None,
            }, operation_uuid=report.uuid)

        try:
            if report.final_state != AgentOperationState.failed:
                try:
                    self.agent.execute_actions(report=report, actions=start_actions)
                except Exception as exc:
                    report.log_message(
                        f"Failed to execute start actions: {exc}",
                        final_state=AgentOperationState.failed,
                    )
            if report.final_state not in {
                AgentOperationState.success,
                AgentOperationState.cancelled,
            }:
                report.log_message(
                    "Backup prerequisites or start actions failed; backup will not be started",
                    final_state=AgentOperationState.failed,
                )

            if report.cancel_event.is_set():
                report.final_state = AgentOperationState.cancelled
            if report.final_state == AgentOperationState.success:
                self._ensure_repository_initialized(report)

            if report.final_state == AgentOperationState.success:
                try:
                    report.set_data("backup_phase", "backup")
                    self.run_backup(report)
                    backup_completed = self._has_completed_snapshot()
                except ResticCancelledError as exc:
                    report.log_message(str(exc), final_state=AgentOperationState.cancelled)
                except ResticError as exc:
                    report.log_message(
                        f"Error during backup: {exc}",
                        final_state=AgentOperationState.failed,
                    )
                except Exception as exc:
                    report.log_message(
                        f"Unexpected error during backup: {exc}",
                        final_state=AgentOperationState.failed,
                    )
                finally:
                    report.set_data("backup_phase", "finalizing")

            if report.final_state == AgentOperationState.failed:
                self._execute_actions_safely(report, error_actions, "error", post_backup=False)
            elif backup_completed and report.final_state in {
                AgentOperationState.success,
                AgentOperationState.warning,
            }:
                try:
                    self._run_post_backup(report, success_actions)
                except Exception as exc:
                    report.log_message(
                        f"Unexpected post-backup error: {exc}",
                        final_state=AgentOperationState.warning,
                    )
        finally:
            self._execute_actions_safely(
                report,
                end_actions,
                "end",
                post_backup=backup_completed,
            )

        if backup_completed:
            report.downgrade_failure_to_warning()

        report.log_message(f"Job finished with state: {report.final_state.name}")
        self._finish_operation(report.final_state, report)
        return report.finish()

    def _run_post_backup(self, report, success_actions):
        if report.cancel_event.is_set():
            report.final_state = AgentOperationState.cancelled
            return
        check_succeeded = self._run_post_backup_check(report)

        if check_succeeded:
            try:
                retention = retentions.find_one(id=self.retention_id) if self.retention_id else None
                if retention:
                    report.set_data("backup_phase", "retention")
                    retention_report = self.agent.cmd_run_retention(
                        retention_id=retention["id"],
                        repository_id=self.repository["id"],
                        job_id=self.job["id"],
                        current_operation_uuid=self.operation["uuid"],
                    )
                    report.append_logs(retention_report.log_list)
                    report.set_data("retention_state", retention_report.state.name)
                    report.set_data("cleanup_pending", retention_report.state != AgentOperationState.success)
                    if retention_report.state != AgentOperationState.success:
                        report.final_state = AgentOperationState.warning
            except Exception as exc:
                report.log_message(
                    f"Retention failed after backup: {exc}",
                    final_state=AgentOperationState.warning,
                )
                report.set_data("retention_state", "failed")
                report.set_data("cleanup_pending", True)

        try:
            report.set_data("backup_phase", "statistics")
            repository_report = self.agent.cmd_get_repository_stats(
                repository_id=self.repository["id"]
            )
            report.append_logs(repository_report.log_list)
            if repository_report.state != AgentOperationState.success:
                report.final_state = AgentOperationState.warning
        except Exception as exc:
            report.log_message(
                f"Repository statistics failed after backup: {exc}",
                final_state=AgentOperationState.warning,
            )

        self._execute_actions_safely(report, success_actions, "success", post_backup=True)

    def _execute_actions_safely(self, report, hook_actions, hook_name, post_backup):
        try:
            report.set_data("backup_phase", "hooks")
            self.agent.execute_actions(report=report, actions=hook_actions)
        except Exception as exc:
            report.log_message(
                f"Failed to execute {hook_name} actions: {exc}",
                final_state=(
                    AgentOperationState.warning
                    if post_backup
                    else AgentOperationState.failed
                ),
            )

    def _finish_operation(self, state, report):
        if not self.operation:
            return
        report.final_state = state
        self._sync_operation(report)

    def _sync_operation(self, report):
        if not self.operation:
            return

        for key in {"uuid", "job_id", "repository_id", "schedule_id", "retention_id"}:
            if key in self.operation:
                setattr(report, key, self.operation.get(key))
        report.set_artifacts(
            self._artifact_payload(artifact)
            for artifact in agent_operation_artifacts.find(operation_id=self.operation["id"])
        )
        report.set_data("backup_data_complete", len(report.artifacts) == report.data.get("backup_items_total", 1)
                        and all(artifact["state"] == AgentOperationState.success.name and artifact["snapshot_id"]
                                for artifact in report.artifacts))
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

    def _has_completed_snapshot(self):
        return any(
            artifact.get("state") == AgentOperationState.success.name
            and artifact.get("snapshot_id")
            for artifact in agent_operation_artifacts.find(operation_id=self.operation["id"])
        )

    def start_artifact(self, artifact_key, data=None, report=None):
        if report is not None:
            self.report = report
        if self.report is not None:
            self.report.begin_backup_artifact(artifact_key)
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
        if self.report is not None:
            self._sync_operation(self.report)
        return artifact

    def finish_artifact(self, artifact, state=AgentOperationState.success, snapshot_id=None, data=None):
        artifact["state"] = state.name if hasattr(state, "name") else str(state)
        artifact["snapshot_id"] = snapshot_id
        if data:
            artifact["data"] = {**(artifact.get("data") or {}), **data}
        agent_operation_artifacts.update(artifact, ["id"])
        if self.report is not None:
            self._sync_operation(self.report)
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
            return True

        report.set_data("backup_phase", "check")
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
        except Exception as exc:
            report.log_message(
                f"Repository check failed after backup: {exc}",
                final_state=AgentOperationState.warning,
            )
            check_key = f"retention_check_failed:{self.repository_id}"
            previous = agent_settings.find_one(name=check_key)
            # A new sample can miss the damaged packs. Any failed data check
            # requires a full read, including legacy partial-check blocks.
            required = "100%" if read_data or (previous and previous.get("settings")) else ""
            agent_settings.upsert({"name": check_key, "settings": required}, ["name"])
            return False

        report.log_message("Repository check finished successfully")
        blocked_check = agent_settings.find_one(name=f"retention_check_failed:{self.repository_id}")
        if blocked_check and (read_data == "100%" or not blocked_check.get("settings")):
            agent_settings.delete(name=f"retention_check_failed:{self.repository_id}")
        return True

    def run_backup(self, report):
        raise NotImplementedError
