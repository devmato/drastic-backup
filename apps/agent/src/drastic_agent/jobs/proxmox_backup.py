import json
from datetime import datetime, timezone
from threading import Event, Thread

from drastic_agent.agent.enums import AgentOperationState
from drastic_agent.agent.report import AgentReport
from drastic_agent.jobs.base import BackupJobHandler
from drastic_agent.proxmox import (
    ProxmoxError,
    ensure_vzdump_available,
    get_proxmox_guest_driver,
)


class ProxmoxBackupJobHandler(BackupJobHandler):
    _STREAM_HEARTBEAT_SECONDS = 15

    def run_backup(self, report):
        config = self.job.get("config") or {}
        selection_mode = config.get("selection_mode") or "all"
        selected_guest_ids = {int(guest_id) for guest_id in config.get("guest_ids", [])}

        ensure_vzdump_available()

        api = self.agent.get_proxmox_client()
        if not api.configured:
            raise ProxmoxError("Proxmox API token is not configured on the agent")

        driver = get_proxmox_guest_driver()
        guests = driver.list_supported_guests(api)
        if selection_mode == "include":
            guests = [guest for guest in guests if int(guest["vmid"]) in selected_guest_ids]

        if not guests:
            raise ProxmoxError("No supported Proxmox guests matched this job configuration")

        report.set_data("guests", [guest["vmid"] for guest in guests])
        report.log_message(f"Launching Proxmox backup for {len(guests)} guest(s)")

        completed_guests = []
        failed_guests = []
        for guest in guests:
            vmid = int(guest["vmid"])
            try:
                self._backup_qemu_guest(report, api, driver, guest)
                completed_guests.append(vmid)
            except Exception as exc:
                failed_guests.append({"vmid": vmid, "error": str(exc)})
                report.log_message(f"Proxmox backup failed for VM {vmid}: {exc}")

        report.set_data("completed_guests", completed_guests)
        report.set_data("failed_guests", failed_guests)
        if failed_guests:
            report.set_data(
                "partial_failure",
                bool(completed_guests) or bool(report.data.get("partial_failure")),
            )
            failed_ids = ", ".join(str(item["vmid"]) for item in failed_guests)
            raise ProxmoxError(f"Backup failed for Proxmox guest(s): {failed_ids}")

    def _backup_qemu_guest(self, report, api, driver, guest):
        vmid = int(guest["vmid"])
        config = api.get_qemu_config(vmid)
        plan = driver.get_guest_backup_plan(config)

        if not plan["volumes"]:
            raise ProxmoxError(f"VM {vmid} has no backupable volumes")

        backup_started = datetime.now(timezone.utc)
        archive_filename = f"vzdump-qemu-{vmid}-{backup_started.strftime('%Y_%m_%d-%H_%M_%S')}.vma"

        manifest = {
            "job_id": self.job["id"],
            "type": "proxmox",
            "guest_type": "qemu",
            "vmid": vmid,
            "guest_name": guest.get("name"),
            "node": guest.get("node"),
            "backup_method": "vzdump",
            "archive_format": "vma",
            "archive_filename": archive_filename,
            "timestamp": backup_started.isoformat(),
            "volumes": [],
        }

        for volume in plan["volumes"]:
            manifest["volumes"].append(
                {
                    "disk": volume["disk"],
                    "volume": volume["volume"],
                    "export_format": volume["export_format"],
                }
            )

        tags = [
            f"job_uuid:{self.job['uuid']}",
            f"operation_uuid:{self.operation['uuid']}",
            "source:proxmox",
            "guest_type:qemu",
            f"vmid:{vmid}",
            "backup_method:vzdump",
        ]
        artifact = self.start_artifact(
            f"vm:{vmid}",
            data={"vmid": vmid, "guest_name": guest.get("name"), "archive_filename": archive_filename},
        )
        tags.extend([f"artifact_uuid:{artifact['uuid']}", f"artifact_key:{artifact['artifact_key']}"])

        report.log_message(
            f"Streaming VM {vmid} ({guest.get('name') or 'unnamed'}) to restic via vzdump"
        )
        try:
            restic_status = self._run_with_stream_heartbeat(
                report=report,
                heartbeat_message=(
                    f"VM {vmid} backup stream is still running via vzdump; waiting for restic summary"
                ),
                export_func=driver.export_qemu_backup_to_restic,
                api=api,
                resticapi=self.agent.resticapi,
                vmid=vmid,
                stdin_filename=archive_filename,
                tags=tags,
                callback=AgentReport.process_job_status,
                callback_args={"job_id": self.job["id"]},
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

        report.process_job_status(status=restic_status, job_id=self.job["id"])
        self.finish_artifact(
            artifact,
            snapshot_id=restic_status.get("snapshot_id") if isinstance(restic_status, dict) else None,
            data={"manifest": manifest},
        )

        report.log_message(f"Saving manifest for VM {vmid}")
        manifest_filename = f"qemu-{vmid}-manifest.json"
        manifest_tags = [
            f"job_uuid:{self.job['uuid']}",
            f"operation_uuid:{self.operation['uuid']}",
            "source:proxmox",
            "guest_type:qemu",
            f"vmid:{vmid}",
            "kind:manifest",
        ]
        manifest_artifact = self.start_artifact(f"vm:{vmid}:manifest", data={"vmid": vmid})
        manifest_tags.extend(
            [
                f"artifact_uuid:{manifest_artifact['uuid']}",
                f"artifact_key:{manifest_artifact['artifact_key']}",
            ]
        )
        try:
            manifest_status = self.agent.resticapi.backup_stdin(
                stdin_data=json.dumps(manifest, indent=2, sort_keys=True),
                stdin_filename=manifest_filename,
                tags=manifest_tags,
                callback=AgentReport.process_job_status,
                callback_args={"job_id": self.job["id"]},
                callback_pid=True,
                callback_throttle=500,
            )
        except Exception as exc:
            self.finish_artifact(
                manifest_artifact,
                state=AgentOperationState.failed,
                snapshot_id=getattr(exc, "snapshot_id", None),
            )
            report.set_data("partial_failure", True)
            raise

        report.process_job_status(status=manifest_status, job_id=self.job["id"])
        self.finish_artifact(
            manifest_artifact,
            snapshot_id=manifest_status.get("snapshot_id") if isinstance(manifest_status, dict) else None,
            data={"manifest_filename": manifest_filename},
        )

    def _run_with_stream_heartbeat(self, report, heartbeat_message, export_func, **kwargs):
        stop_event = Event()

        def heartbeat():
            while not stop_event.wait(self._STREAM_HEARTBEAT_SECONDS):
                report.log_message(heartbeat_message)

        heartbeat_thread = Thread(target=heartbeat, daemon=True)
        heartbeat_thread.start()

        try:
            return export_func(**kwargs)
        finally:
            stop_event.set()
            heartbeat_thread.join(timeout=0.1)
