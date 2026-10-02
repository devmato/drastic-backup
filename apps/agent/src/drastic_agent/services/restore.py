import os
import posixpath

from drastic_agent.agent.enums import AgentReportState
from drastic_agent.agent.report import AgentReport
from drastic_common.process import ProcessCancelledError
from drastic_common.restic.exceptions import ResticCancelledError


class RestoreService:
    @staticmethod
    def _reject_symlink_components(path):
        current = "/"
        for component in path.strip("/").split("/"):
            if not component:
                continue
            current = os.path.join(current, component)
            if os.path.lexists(current) and os.path.islink(current):
                raise ValueError(f"Restore path contains a symbolic link: {current}")

    @staticmethod
    def _normalize_restore_target(value):
        if not isinstance(value, str) or "\x00" in value or not value.startswith("/"):
            raise ValueError("Restore location must be an absolute POSIX path")
        if ".." in value.split("/"):
            raise ValueError("Restore location must not contain traversal segments")
        normalized = posixpath.normpath("/" + value.lstrip("/"))
        if normalized == "/":
            raise ValueError("Restoring to / is not allowed")
        return normalized

    @staticmethod
    def _normalize_include_paths(paths):
        normalized = []
        for value in paths or []:
            if not isinstance(value, str) or not value or "\x00" in value:
                raise ValueError("Invalid restore include path")
            if ".." in value.split("/"):
                raise ValueError("Include paths must not contain traversal segments")
            path = posixpath.normpath("/" + value.lstrip("/"))
            if path not in normalized:
                normalized.append(path)
        if not normalized:
            raise ValueError("Restore requires at least one include path")
        return normalized

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
    def _restore_files(agent, report, snapshot_id, restore_location, include_paths, overwrite_policy):
        RestoreService._reject_symlink_components(restore_location)
        os.makedirs(restore_location, mode=0o700, exist_ok=True)
        RestoreService._reject_symlink_components(restore_location)
        for path in include_paths:
            destination = os.path.join(restore_location, path.lstrip("/"))
            RestoreService._reject_symlink_components(os.path.dirname(destination))
            if overwrite_policy == "fail_if_exists" and os.path.lexists(destination):
                raise ValueError(f"Restore destination already exists: {path}")
        report.data["destination_may_contain_restored_data"] = True
        status = agent.resticapi.restore(
            snapshot_id=snapshot_id, target=restore_location, include_paths=include_paths,
            overwrite_policy=overwrite_policy, callback=AgentReport.process_restore_status,
            callback_args={"operation_uuid": report.uuid}, callback_pid=True, callback_throttle=500,
        )
        AgentReport.process_restore_status(status, operation_uuid=report.uuid)

    @staticmethod
    def run_restore(
        agent,
        operation_uuid,
        job_id,
        job_uuid,
        repository_id,
        repository,
        snapshot_id,
        restore_location=None,
        include_paths=None,
        mode="plain_file",
        overwrite_policy="fail_if_exists",
        expected_job_tag=None,
        vmid=None,
        storage=None,
        unique=True,
        session_id=None,
        volume=None,
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
                "overwrite_policy": overwrite_policy,
                "target_vmid": vmid,
                "target_storage": storage,
            },
        )
        report.log_message(f"Starting {mode} restore from snapshot {snapshot_id}")

        try:
            if mode not in ("plain_file", "proxmox_vm", "proxmox_prepare", "proxmox_files"):
                raise ValueError("Unsupported restore mode")
            if mode in ("plain_file", "proxmox_files"):
                restore_location = RestoreService._normalize_restore_target(restore_location)
                include_paths = RestoreService._normalize_include_paths(include_paths)
            if overwrite_policy not in ("fail_if_exists", "overwrite"):
                raise ValueError("Unsupported overwrite policy")
            if expected_job_tag != f"job_uuid:{job_uuid}":
                raise ValueError("Restore job tag does not match the job")

            with agent.resticapi.operation_cancellation(report.cancel_event):
                agent.configure_repository(repository)
                snapshots = agent.resticapi.snapshots(tags=[expected_job_tag]) or []
                if isinstance(snapshots, dict):
                    snapshots = [snapshots]
                snapshot = next((s for s in snapshots if s.get("id") == snapshot_id
                                 and expected_job_tag in (s.get("tags") or [])), None)
                if snapshot is None:
                    raise ValueError("Snapshot does not belong to this job")
                if mode == "plain_file":
                    RestoreService._restore_files(agent, report, snapshot_id, restore_location,
                                                  include_paths, overwrite_policy)
                else:
                    from drastic_agent.services.proxmox_restore import run_proxmox_restore

                    run_proxmox_restore(
                        agent, report, snapshot, mode=mode,
                        identity={"job_uuid": job_uuid, "repository_id": repository_id, "snapshot_id": snapshot_id},
                        vmid=vmid, storage=storage, unique=unique, session_id=session_id, volume=volume,
                        include_paths=include_paths, restore_location=restore_location,
                        overwrite_policy=overwrite_policy)
                if report.cancel_event.is_set():
                    raise ProcessCancelledError("Restore cancelled")
                report.data["restore_completed"] = True
                report.data.pop("destination_may_contain_restored_data", None)
        except (ProcessCancelledError, ResticCancelledError) as exc:
            report.log_message(str(exc), final_state=AgentReportState.cancelled)
        except Exception as exc:
            state = AgentReportState.cancelled if report.cancel_event.is_set() else AgentReportState.failed
            report.log_message(f"Error during restore: {exc}", final_state=state)

        if report.data.get("destination_may_contain_restored_data"):
            report.data["partial_failure"] = True
            report.log_message("Destination may contain partially restored data; inspect it before retrying")

        report.log_message(f"Restore finished with state: {report.final_state.name}")
        return report.finish()
