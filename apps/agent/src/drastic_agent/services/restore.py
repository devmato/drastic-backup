import os

from drastic_agent.agent.enums import AgentReportState
from drastic_agent.agent.report import AgentReport
from drastic_common.restic.exceptions import ResticError


class RestoreService:
    @staticmethod
    def list_snapshots(agent, repository, tags):
        agent.configure_repository(repository)
        snapshots = agent.resticapi.snapshots(tags=tags)
        if isinstance(snapshots, dict):
            snapshots = [snapshots]
        return snapshots or []

    @staticmethod
    def list_entries(agent, repository, snapshot_id, path="/"):
        agent.configure_repository(repository)
        entries = agent.resticapi.ls(snapshot_id=snapshot_id, path=path or "/")
        if isinstance(entries, dict):
            entries = [entries]

        normalized = []
        for entry in entries or []:
            if entry.get("message_type") == "snapshot":
                continue
            entry_path = entry.get("path") or entry.get("name")
            if not entry_path:
                continue
            normalized.append(
                {
                    "name": os.path.basename(str(entry_path).rstrip("/")) or str(entry_path),
                    "path": entry_path,
                    "type": entry.get("type"),
                    "size": entry.get("size"),
                }
            )
        return normalized

    @staticmethod
    def run_restore(
        agent,
        operation_uuid,
        job_id,
        job_uuid,
        repository_id,
        repository,
        snapshot_id,
        restore_location,
        include_paths,
        mode="plain_file",
    ):
        report = AgentReport.restore_report(
            report_uuid=operation_uuid,
            job_id=job_id,
            repository_id=repository_id,
            data={
                "mode": mode,
                "job_uuid": job_uuid,
                "snapshot_id": snapshot_id,
                "restore_location": restore_location,
                "include_paths": include_paths,
            },
        )
        report.log_message(
            f"Starting restore of {len(include_paths)} item(s) from snapshot {snapshot_id} to {restore_location}"
        )

        try:
            agent.configure_repository(repository)
            restic_status = agent.resticapi.restore(
                snapshot_id=snapshot_id,
                target=restore_location,
                include_paths=include_paths,
                callback=AgentReport.process_restore_status,
                callback_args={"operation_uuid": operation_uuid},
                callback_pid=True,
                callback_throttle=500,
            )
            AgentReport.process_restore_status(restic_status, operation_uuid=operation_uuid)
        except (ResticError, ValueError) as exc:
            report.log_message(f"Error during restore: {exc}", final_state=AgentReportState.failed)
        except Exception as exc:
            report.log_message(
                f"Unexpected error during restore: {exc}", final_state=AgentReportState.failed
            )

        report.log_message(f"Restore finished with state: {report.final_state.name}")
        return report.finish()
