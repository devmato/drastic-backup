import os
import posixpath

from drastic_agent.agent.enums import AgentReportState
from drastic_agent.agent.report import AgentReport
from drastic_common.restic.exceptions import ResticError


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
        normalized = posixpath.normpath(value)
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
        overwrite_policy="fail_if_exists",
        expected_job_tag=None,
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
            },
        )
        report.log_message(
            f"Starting restore of {len(include_paths)} item(s) from snapshot {snapshot_id} to {restore_location}"
        )

        restore_started = False
        try:
            restore_location = RestoreService._normalize_restore_target(restore_location)
            include_paths = RestoreService._normalize_include_paths(include_paths)
            if overwrite_policy not in ("fail_if_exists", "overwrite"):
                raise ValueError("Unsupported overwrite policy")
            if expected_job_tag != f"job_uuid:{job_uuid}":
                raise ValueError("Restore job tag does not match the job")

            agent.configure_repository(repository)
            snapshots = agent.resticapi.snapshots(tags=[expected_job_tag]) or []
            if isinstance(snapshots, dict):
                snapshots = [snapshots]
            if not any(
                snapshot.get("id") == snapshot_id
                and expected_job_tag in (snapshot.get("tags") or [])
                for snapshot in snapshots
            ):
                raise ValueError("Snapshot does not belong to this job")

            RestoreService._reject_symlink_components(restore_location)
            os.makedirs(restore_location, mode=0o700, exist_ok=True)
            RestoreService._reject_symlink_components(restore_location)

            if overwrite_policy == "fail_if_exists":
                collisions = []
                for path in include_paths:
                    destination = os.path.join(restore_location, path.lstrip("/"))
                    RestoreService._reject_symlink_components(os.path.dirname(destination))
                    if os.path.lexists(destination):
                        collisions.append(path)
                if collisions:
                    raise ValueError(f"Restore destination already exists: {collisions[0]}")
            else:
                for path in include_paths:
                    destination = os.path.join(restore_location, path.lstrip("/"))
                    RestoreService._reject_symlink_components(os.path.dirname(destination))

            restore_started = True
            restic_status = agent.resticapi.restore(
                snapshot_id=snapshot_id,
                target=restore_location,
                include_paths=include_paths,
                overwrite_policy=overwrite_policy,
                callback=AgentReport.process_restore_status,
                callback_args={"operation_uuid": operation_uuid},
                callback_pid=True,
                callback_throttle=500,
            )
            AgentReport.process_restore_status(restic_status, operation_uuid=operation_uuid)
            report.data["restore_completed"] = True
        except (ResticError, ValueError) as exc:
            if restore_started:
                report.data["partial_failure"] = True
                report.data["destination_may_contain_restored_data"] = True
            report.log_message(f"Error during restore: {exc}", final_state=AgentReportState.failed)
        except Exception as exc:
            if restore_started:
                report.data["partial_failure"] = True
                report.data["destination_may_contain_restored_data"] = True
            report.log_message(
                f"Unexpected error during restore: {exc}", final_state=AgentReportState.failed
            )

        report.log_message(f"Restore finished with state: {report.final_state.name}")
        return report.finish()
