import json
from datetime import datetime, timezone

from drastic_agent.agent.enums import AgentOperationState
from drastic_agent.jobs.base import BackupJobHandler
from drastic_agent.proxmox import (
    ProxmoxError,
    QemuVolumeGuestDriver,
    ensure_vzdump_available,
)


class ProxmoxBackupJobHandler(BackupJobHandler):
    def run_backup(self, report):
        config = self.job.get("config") or {}
        selection_mode = config.get("selection_mode") or "all"
        selected_guest_ids = {int(guest_id) for guest_id in config.get("guest_ids", [])}

        ensure_vzdump_available()

        api = self.agent.get_proxmox_client()
        if not api.configured:
            raise ProxmoxError("Proxmox API token is not configured on the agent")

        driver = QemuVolumeGuestDriver()
        guests = driver.list_supported_guests(api)
        if selection_mode == "include":
            guests = [guest for guest in guests if int(guest["vmid"]) in selected_guest_ids]

        if not guests:
            raise ProxmoxError("No supported Proxmox guests matched this job configuration")

        report.set_data("guests", [guest["vmid"] for guest in guests])
        report.set_data("backup_items_total", len(guests) * 2)
        report.log_message(f"Launching Proxmox backup for {len(guests)} guest(s)")

        completed_guests = []
        failed_guests = []
        for guest_index, guest in enumerate(guests, start=1):
            vmid = int(guest["vmid"])
            try:
                self._backup_qemu_guest(report, api, driver, guest, guest_index)
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

    def _backup_qemu_guest(self, report, api, driver, guest, guest_index):
        vmid = int(guest["vmid"])
        config = api.get_qemu_config(vmid)
        plan = driver.get_guest_backup_plan(config)

        if not plan["volumes"]:
            raise ProxmoxError(f"VM {vmid} has no backupable volumes")

        backup_started = datetime.now(timezone.utc)
        archive_filename = f"vzdump-qemu-{vmid}-{backup_started.strftime('%Y_%m_%d-%H_%M_%S')}.vma"
        report.set_data("proxmox_progress", {
            "vmid": vmid,
            "guest_index": guest_index,
            "guests_total": len(report.data.get("guests") or [vmid]),
            "archive_filename": archive_filename,
            "phase": "backing_up",
            "percent_done": None,
            "bytes_processed": 0,
            "bytes_total": None,
        })

        def update_progress(progress):
            report.set_data("proxmox_progress", {
                **report.data["proxmox_progress"],
                **progress,
            })

        def process_progress(progress):
            update_progress({
                **progress,
                "phase": "finalizing" if progress["percent_done"] >= 100 else "backing_up",
            })

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
            report=report,
            data={"vmid": vmid, "guest_name": guest.get("name"), "archive_filename": archive_filename},
        )
        tags.extend([f"artifact_uuid:{artifact['uuid']}", f"artifact_key:{artifact['artifact_key']}"])

        report.log_message(
            f"Streaming VM {vmid} ({guest.get('name') or 'unnamed'}) to restic via vzdump"
        )
        try:
            restic_status = driver.export_qemu_backup_to_restic(
                api=api,
                resticapi=self.agent.resticapi,
                vmid=vmid,
                stdin_filename=archive_filename,
                tags=tags,
                callback=report.process_backup_status,
                callback_args={},
                callback_pid=True,
                callback_throttle=500,
                progress_callback=process_progress,
            )
        except Exception as exc:
            update_progress({"phase": "failed"})
            self.finish_artifact(
                artifact,
                state=AgentOperationState.failed,
                snapshot_id=getattr(exc, "snapshot_id", None),
            )
            raise

        report.process_backup_status(restic_status)
        update_progress({
            "phase": "manifest",
            "archive_bytes": restic_status.get("total_bytes_processed") if isinstance(restic_status, dict) else None,
        })
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
        manifest_artifact = self.start_artifact(f"vm:{vmid}:manifest", data={"vmid": vmid}, report=report)
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
                callback=report.process_backup_status,
                callback_pid=True,
                callback_throttle=500,
            )
        except Exception as exc:
            update_progress({"phase": "failed"})
            self.finish_artifact(
                manifest_artifact,
                state=AgentOperationState.failed,
                snapshot_id=getattr(exc, "snapshot_id", None),
            )
            report.set_data("partial_failure", True)
            raise

        report.process_backup_status(manifest_status)
        self.finish_artifact(
            manifest_artifact,
            snapshot_id=manifest_status.get("snapshot_id") if isinstance(manifest_status, dict) else None,
            data={"manifest_filename": manifest_filename},
        )
        update_progress({"phase": "complete"})
