import json
import logging
import platform
import sys
import tempfile
from pathlib import Path
from threading import Lock
from time import monotonic, time
from uuid import UUID

from drastic_agent.agent.database import proxmox_snapshots
from drastic_agent.agent.enums import AgentOperationState
from drastic_agent.jobs.base import BackupJobHandler
from drastic_agent.proxmox import ProxmoxError, QemuVolumeGuestDriver, ensure_proxmox_available
from drastic_agent.proxmox_snapshot import snapshot_info
from drastic_common.process import run_process
from drastic_common.restic.exceptions import ResticCancelledError

# ponytail: serialize local snapshot backups; per-VM locks if concurrency is needed.
PROXMOX_LOCK = Lock()


def cleanup_snapshots(report=None):
    for row in list(proxmox_snapshots.all()):
        try:
            if row["host_id"] != Path("/etc/machine-id").read_text().strip():
                raise ProxmoxError("Pending Proxmox snapshot belongs to another host")
            info = snapshot_info(row["vmid"], row["snapshot_name"], check=True)
            if info.get("lock"):
                raise ProxmoxError("VM is locked; snapshot cleanup remains pending")
            if info["exists"]:
                if info["description"] != row["owner"]:
                    raise ProxmoxError("Snapshot ownership mismatch; refusing deletion")
                run_process(["qm", "delsnapshot", str(row["vmid"]), row["snapshot_name"]], timeout=300)
            elif not row["confirmed"] and time() - row["created_at"] < 300:
                raise ProxmoxError("Snapshot creation outcome unknown; retry cleanup after five minutes")
            proxmox_snapshots.delete(id=row["id"])
        except Exception as exc:
            message = f"Proxmox cleanup pending for VM {row['vmid']}: {exc}"
            if report:
                report.log_message(message, final_state=AgentOperationState.warning)
            else:
                logging.warning(message)


def recover_snapshots():
    with PROXMOX_LOCK:
        cleanup_snapshots()


class ProxmoxBackupJobHandler(BackupJobHandler):
    def run_backup(self, report):
        ensure_proxmox_available()
        api = self.agent.get_proxmox_client()
        if not api.configured:
            raise ProxmoxError("Proxmox API token is not configured on the agent")
        if api.get_node() != platform.node().split(".")[0]:
            raise ProxmoxError("Snapshot backups must run on the configured Proxmox node")
        if not PROXMOX_LOCK.acquire(blocking=False):
            raise ProxmoxError("Another Proxmox snapshot backup is in progress")
        try:
            cleanup_snapshots(report)
            if proxmox_snapshots.count():
                raise ProxmoxError("Clean up pending Proxmox snapshots before another backup")
            config = self.job.get("config") or {}
            guests = QemuVolumeGuestDriver().list_supported_guests(api)
            if config.get("selection_mode") == "include":
                selected = {int(value) for value in config.get("guest_ids", [])}
                guests = [guest for guest in guests if int(guest["vmid"]) in selected]
            if not guests:
                raise ProxmoxError("No supported Proxmox guests matched this job configuration")
            # Preflight every selected guest before any snapshot or data transfer.
            for guest in guests:
                check_thin_pools(snapshot_info(guest["vmid"]))
            report.set_data("guests", [guest["vmid"] for guest in guests])
            report.set_data("backup_items_total", len(guests))
            completed, failed = [], []
            for index, guest in enumerate(guests, 1):
                self._check_cancelled(report)
                try:
                    self._backup_qemu_guest(report, guest, index)
                    completed.append(guest["vmid"])
                except ResticCancelledError:
                    raise
                except Exception as exc:
                    failed.append({"vmid": guest["vmid"], "error": str(exc)})
                    report.log_message(f"VM {guest['vmid']} backup failed: {exc}")
                if proxmox_snapshots.count():
                    failed.extend({"vmid": item["vmid"], "error": "Previous VM snapshot cleanup pending"}
                                  for item in guests[index:])
                    break
            report.set_data("completed_guests", completed)
            report.set_data("failed_guests", failed)
            if failed:
                report.set_data("partial_failure", bool(completed))
                raise ProxmoxError("Backup failed for Proxmox guest(s): " + ", ".join(str(item["vmid"]) for item in failed))
        finally:
            PROXMOX_LOCK.release()

    def _backup_qemu_guest(self, report, guest, index):
        vmid = int(guest["vmid"])
        name = f"drastic-{UUID(self.operation['uuid']).hex}"
        owner = f"drastic:{self.agent.identifier}:{self.operation['uuid']}"
        filename = f"qemu-{vmid}.tar"
        progress = {"vmid": vmid, "guest_index": index, "guests_total": len(report.data["guests"]),
                    "archive_filename": filename, "phase": "snapshots", "percent_done": None}
        report.set_data("proxmox_progress", progress)
        row = {"vmid": vmid, "snapshot_name": name, "owner": owner,
               "host_id": Path("/etc/machine-id").read_text().strip(), "confirmed": False, "created_at": time()}
        # Commit the cleanup intent before starting the synchronous Proxmox task.
        row["id"] = proxmox_snapshots.insert(row)
        artifact = None
        try:
            run_process(["qm", "snapshot", str(vmid), name, "--vmstate", "0", "--description", owner], timeout=300)
            row["confirmed"] = True
            proxmox_snapshots.update(row, ["id"])
            self._check_cancelled(report)
            plan = snapshot_info(vmid, name, owner)
            plan["vmid"] = vmid
            firewall = Path(f"/etc/pve/firewall/{vmid}.fw")
            if firewall.is_file():
                plan["firewall"] = firewall.read_text()
            progress.update(phase="backing_up", bytes_total=sum(item["size"] for item in plan["volumes"]), bytes_processed=0)
            report.set_data("proxmox_progress", dict(progress))
            artifact = self.start_artifact(f"vm:{vmid}", report=report, data={
                "vmid": vmid, "guest_name": guest.get("name"), "archive_filename": filename,
                "backup_method": "snapshot", "volumes": [{"disk": item["disk"], "size": item["size"]} for item in plan["volumes"]],
            })
            tags = [f"job_uuid:{self.job['uuid']}", f"operation_uuid:{self.operation['uuid']}",
                    "source:proxmox", "guest_type:qemu", f"vmid:{vmid}", "backup_method:snapshot",
                    f"artifact_uuid:{artifact['uuid']}", f"artifact_key:{artifact['artifact_key']}"]
            report.log_message(f"Streaming snapshot disks of VM {vmid} to restic")
            last_pool_check = 0

            def status_callback(status, **kwargs):
                nonlocal last_pool_check
                kwargs.pop("operation_uuid", None)
                if status is not None and monotonic() - last_pool_check >= 5:
                    check_thin_pools(plan)
                    last_pool_check = monotonic()
                report.process_backup_status(status, **kwargs)
                current = report.data.get("backup_progress") or {}
                done = min(current.get("bytes_processed", 0), progress["bytes_total"])
                progress.update(bytes_processed=done, percent_done=100 * done / progress["bytes_total"])
                if current.get("complete"):
                    progress["phase"] = "finalizing"
                report.set_data("proxmox_progress", dict(progress))

            # Only a small plan file is staged, never disk contents.
            with tempfile.TemporaryDirectory(prefix="drastic-proxmox-") as work:
                path = Path(work) / "plan.json"
                path.write_text(json.dumps(plan))
                with self.agent.resticapi.operation_cancellation(report.cancel_event):
                    status = self.agent.resticapi.backup_stdin_from_command(
                        [sys.executable, "-m", "drastic_agent.proxmox_snapshot", str(path)],
                        stdin_filename=filename, tags=tags, callback=status_callback,
                        callback_args={"operation_uuid": report.uuid},
                        callback_pid=True, callback_throttle=500,
                    )
            status_callback(status)
            self.finish_artifact(artifact, snapshot_id=status.get("snapshot_id"))
            progress.update(phase="complete", percent_done=100)
        except Exception as exc:
            progress["phase"] = "failed"
            if artifact:
                self.finish_artifact(artifact, state=AgentOperationState.failed, snapshot_id=getattr(exc, "snapshot_id", None))
            raise
        finally:
            report.set_data("proxmox_progress", {**progress, "phase": "cleanup"})
            cleanup_snapshots(report)
            report.set_data("proxmox_progress", dict(progress))

    @staticmethod
    def _check_cancelled(report):
        if report.cancel_event.is_set():
            raise ResticCancelledError("Proxmox backup cancelled")


def check_thin_pools(plan):
    for pool in {item["pool"] for item in plan["volumes"]}:
        result = json.loads(run_process(["lvs", pool, "--reportformat", "json", "-o", "data_percent,metadata_percent"], timeout=30))
        values = result["report"][0]["lv"]
        if len(values) != 1 or any(not values[0].get(key, "").strip() or float(values[0][key]) >= 95
                                   for key in ("data_percent", "metadata_percent")):
            raise ProxmoxError(f"Thin pool {pool} has insufficient data or metadata headroom (95% limit)")
