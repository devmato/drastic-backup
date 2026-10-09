"""File backups with job-scoped parent selection."""

from datetime import datetime

from drastic_agent.agent.enums import AgentOperationState
from drastic_agent.jobs.base import BackupJobHandler


class FileBackupJobHandler(BackupJobHandler):
    def run_backup(self, report):
        config = self.job.get("config") or {}
        paths = [path["path"] for path in config.get("paths", [])]
        exclude_patterns = [pattern["path"] for pattern in config.get("exclude_patterns", [])]
        artifact = self.start_artifact("default", report=report)
        tags = [
            f"job_uuid:{self.job['uuid']}",
            f"operation_uuid:{self.operation['uuid']}",
            f"artifact_uuid:{artifact['uuid']}",
            "artifact_key:default",
        ]

        report.log_message(f"Launching backup for job {self.job['id']}")
        report.log_message(f"Backing up paths: {', '.join(paths)}")
        if exclude_patterns:
            report.log_message(f"Exclusions: {', '.join(exclude_patterns)}")

        try:
            with self.agent.resticapi.operation_cancellation(report.cancel_event):
                previous = self.agent.resticapi.snapshots(tags=[f"job_uuid:{self.job['uuid']},artifact_key:default"])
                parent = max(previous, key=lambda item: datetime.fromisoformat(item["time"]))["id"] if previous else None
                report.log_message(f"Restic parent: {parent}" if parent else "No matching Restic parent; reading all files")
                restic_status = self.agent.resticapi.backup(
                    paths=paths,
                    exclude_patterns=exclude_patterns,
                    tags=tags,
                    parent=parent,
                    force=parent is None,
                    callback=report.process_backup_status,
                    callback_pid=True,
                    callback_throttle=500,
                )
        except Exception as exc:
            self.finish_artifact(
                artifact,
                state=AgentOperationState.failed,
                snapshot_id=getattr(exc, "snapshot_id", None),
            )
            raise

        report.process_backup_status(restic_status)
        self.finish_artifact(
            artifact,
            snapshot_id=restic_status.get("snapshot_id") if isinstance(restic_status, dict) else None,
            data={"paths": paths},
        )
