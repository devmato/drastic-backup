"""One native backup path, with CBT as optional reuse of a confirmed checkpoint."""
import hashlib
import json
import os
import selectors
import shutil
import signal
import subprocess
import tempfile
import time
from functools import cache
from pathlib import Path
from uuid import UUID

from drastic_agent.agent.database import proxmox_checkpoints, proxmox_native_runs
from drastic_agent.agent.enums import AgentOperationState
from drastic_agent.config import DefaultConfig, env_value
from drastic_agent.proxmox import ProxmoxError
from drastic_agent.proxmox_blocks import check_manifest_entry, validate_manifest, view_metadata
from drastic_common.process import run_process
from drastic_common.restic.exceptions import ResticCancelledError

ADAPTER = Path(__file__).parents[1] / "proxmox_native.pl"
VIEW = Path(__file__).parents[1] / "proxmox_blocks.py"


def check_dependencies():
    for binary in ("perl", "fusermount3", "vzdump"):
        if not shutil.which(binary):
            raise ProxmoxError(f"Native backup requires {binary} on the Proxmox host")
    try:
        run_process(["/usr/bin/python3", "-c", "import fuse, nbd"], timeout=15)
    except (OSError, RuntimeError) as exc:
        raise ProxmoxError("Native backup requires python3-fuse, python3-libnbd and libnbd-bin") from exc


@cache
def dependency_error():
    try:
        check_dependencies()
        run_process(["perl", "-MPVE::VZDump", "-e",
                     "die 'Native provider API unavailable' unless PVE::VZDump::QemuServer->can('archive_external')"], timeout=15)
    except (OSError, RuntimeError, TimeoutError, ProxmoxError) as exc:
        return str(exc)
    return None


def preflight(vmid, storage=""):
    from drastic_agent.jobs.proxmox_backup import check_thin_pools

    check_dependencies()
    with tempfile.NamedTemporaryFile("w") as file:
        json.dump({"vmid": vmid, "fleecing_storage": storage, "check": True}, file)
        file.flush()
        info = json.loads(run_process(["perl", str(ADAPTER), file.name], timeout=60))
    candidates, errors = [], []
    source_storages = {volume.split(":", 1)[0] for volume in info["sources"].values()}
    for candidate in info.pop("storages"):
        try:
            free = check_thin_pools({"volumes": [candidate]})[candidate["pool"]]
        except (ProxmoxError, OSError, RuntimeError, TimeoutError) as exc:
            errors.append(f"{candidate['id']}: {exc}")
            continue
        candidates.append((candidate["id"] not in source_storages, -free, candidate["id"], candidate["pool"]))
    if not candidates:
        reason = "; ".join(errors) or "No active local LVM-thin storage for VM images"
        raise ProxmoxError(f"No suitable temporary backup storage{f' ({storage})' if storage else ''}: {reason}")
    _, _, selected, pool = min(candidates)
    return {**info, "fleecing_storage": selected, "pools": [{"pool": pool}]}


def _session_process(pid):
    return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[19]


def recover_runs(report=None):
    """Never silently use a checkpoint from an interrupted or unconfirmed native run."""
    for row in list(proxmox_native_runs.all()):
        data = row["data"]
        try:
            if data["host"] != Path("/etc/machine-id").read_text().strip():
                raise ProxmoxError("Native cleanup belongs to another host")
            work = Path(data["work"])
            expected = Path(env_value("DRASTIC_AGENT_DATA_DIR", DefaultConfig.AGENT_DATA_DIR)) / "native-work" / str(UUID(row["key"]))
            if work != expected or work.is_symlink():
                raise ProxmoxError("Native cleanup workspace identity mismatch")
            if not data.get("pid"):
                controller = work / "controller.json"
                if controller.exists():
                    data = {**data, **json.loads(controller.read_text())}
                elif time.time() - data.get("created_at", 0) < 120:
                    raise ProxmoxError("Native helper launch outcome unknown; retry cleanup after two minutes")
            if os.path.ismount(work / "mount"):
                run_process(["fusermount3", "-u", str(work / "mount")], timeout=15)
            # The controller's closed pipe makes Proxmox run its normal failure cleanup.
            if data.get("pid"):
                try:
                    if _session_process(data["pid"]) == data["process_start"]:
                        os.killpg(data["pid"], signal.SIGTERM)
                        for _ in range(100):
                            if not Path(f"/proc/{data['pid']}").exists():
                                break
                            time.sleep(.1)
                        else:
                            raise ProxmoxError("Native cleanup helper is still running")
                except FileNotFoundError:
                    pass
            request = work / "request.json"
            if request.exists():
                run_process(["perl", str(ADAPTER), str(request), "recover"], timeout=60)
            if work.exists():
                shutil.rmtree(work)
            proxmox_native_runs.delete(key=row["key"])
        except Exception as exc:
            message = f"Native backup cleanup pending for VM {data['vmid']}: {exc}"
            if report:
                report.log_message(message, final_state=AgentOperationState.warning)
            else:
                import logging

                logging.warning(message)


def run_native(handler, report, guest, index, info=None):
    from drastic_agent.jobs.proxmox_backup import check_thin_pools

    vmid = int(guest["vmid"])
    mode = handler.job["config"]["backup_mode"]
    progress = {"vmid": vmid, "guest_index": index, "guests_total": len(report.data["guests"]),
                "phase": "snapshots", "percent_done": None, "backup_mode": mode}
    report.set_data("proxmox_progress", progress)
    repository_id = handler.agent.resticapi.cat_config()["id"]
    key = hashlib.sha256(f"{handler.job['uuid']}:{repository_id}:{vmid}".encode()).hexdigest()
    previous = proxmox_checkpoints.find_one(key=key)
    checkpoint = previous["data"] if previous else {}
    storage = handler.job["config"].get("fleecing_storage", "")
    info = dict(info if info is not None else preflight(vmid, storage))
    # Progress estimates are not part of the confirmed CBT source identity.
    planned_bytes = info.pop("disk_bytes")
    report.log_message(f"VM {vmid}: temporary backup storage {info['fleecing_storage']} ({'configured' if storage else 'automatic'})")
    check_thin_pools({"volumes": info["pools"]}, report=report)
    old_manifest, parent, reason = None, None, "CBT disabled" if mode == "native" else "No confirmed checkpoint"
    snapshots = handler.agent.resticapi.snapshots(tags=[f"job_uuid:{handler.job['uuid']},vmid:{vmid},backup_method:native"]) or []
    if (mode == "native_cbt" and checkpoint.get("valid") and checkpoint.get("info") == info
            and info["session"] != "stopped" and any(s["id"] == checkpoint["snapshot_id"] for s in snapshots)):
        with tempfile.TemporaryDirectory(prefix="drastic-parent-") as directory:
            check_manifest_entry(handler.agent.resticapi, checkpoint["snapshot_id"])
            handler.agent.resticapi.restore(checkpoint["snapshot_id"], directory, ["/manifest.json"])
            path = Path(directory) / "manifest.json"
            if path.stat().st_size > 16 * 1024 * 1024:
                raise ProxmoxError("Native parent manifest is too large")
            old_manifest = json.loads(path.read_text())
            validate_manifest(old_manifest)
            if old_manifest["vmid"] != vmid:
                raise ProxmoxError("Native parent manifest belongs to another VM")
        parent, reason = checkpoint["snapshot_id"], ""
    elif mode == "native_cbt" and checkpoint:
        reason = "Tracking, disk identity or matching parent is unavailable"
    # Invalidate BEFORE setup/teardown: any crash window causes a full reinitialization.
    proxmox_checkpoints.upsert({"key": key, "data": {**checkpoint, "valid": False}}, ["key"])
    operation = str(UUID(handler.operation["uuid"]))
    root = Path(env_value("DRASTIC_AGENT_DATA_DIR", DefaultConfig.AGENT_DATA_DIR)) / "native-work"
    root.mkdir(mode=0o700, exist_ok=True)
    work = root / operation
    work.mkdir(mode=0o700)
    (work / "mount").mkdir()
    request = {"vmid": vmid, "work": str(work.resolve()), "target": "drastic-" + key[:24],
               "fleecing_storage": info["fleecing_storage"]}
    (work / "request.json").write_text(json.dumps(request))
    journal = {"host": Path("/etc/machine-id").read_text().strip(), "vmid": vmid, "work": str(work), "created_at": time.time()}
    proxmox_native_runs.insert({"key": operation, "data": journal})
    process = view = artifact = status = None
    log = (work / "native.log").open("w+")
    metrics = {}

    last_pool_check = 0

    def monitor():
        nonlocal last_pool_check
        if report.cancel_event.is_set():
            raise ResticCancelledError("Native backup cancelled")
        if time.monotonic() - last_pool_check >= 5:
            check_thin_pools({"volumes": info["pools"]})
            last_pool_check = time.monotonic()
        if process and process.poll() is not None:
            raise ProxmoxError("Native snapshot provider stopped unexpectedly")
        if view and view.poll() is not None:
            raise ProxmoxError("Native block view stopped unexpectedly")
        path = work / "metrics.json"
        if path.exists():
            metrics.update(json.loads(path.read_text()))
            if metrics.get("error"):
                raise ProxmoxError(metrics["error"])
            progress.update(bytes_processed=metrics["bytes_read"], percent_done=(
                min(99, 100 * metrics["bytes_read"] / progress["bytes_total"]) if progress.get("bytes_total") else None))
            report.set_data("proxmox_progress", dict(progress))

    def receive():
        deadline = time.monotonic() + 120
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            while time.monotonic() < deadline:
                if selector.select(timeout=1):
                    line = process.stdout.readline()
                    if not line:
                        raise ProxmoxError("Native provider disconnected")
                    return json.loads(line)
                monitor()
        raise ProxmoxError("Native provider timed out")

    def reply(**data):
        process.stdin.write(json.dumps({"ok": True, **data}) + "\n")
        process.stdin.flush()

    try:
        process = subprocess.Popen(["perl", str(ADAPTER), str(work / "request.json")], stdin=subprocess.PIPE,
                                   stdout=subprocess.PIPE, stderr=log, text=True, start_new_session=True)
        journal.update(pid=process.pid, process_start=_session_process(process.pid))
        proxmox_native_runs.update({"key": operation, "data": journal}, ["key"])
        query = receive()
        if (query["event"] != "query" or query["info"]["sources"] != info["sources"]
                or query["info"]["host"] != info["host"] or query["info"]["vm_identity"] != info["vm_identity"]
                or query["info"]["pools"] != info["pools"] or query["info"]["fleecing_storage"] != info["fleecing_storage"]
                or info["session"] != "stopped" and query["info"]["session"] != info["session"]):
            raise ProxmoxError("Native source identity changed during setup")
        info = query["info"]
        if old_manifest and {v["disk"]: v["size"] for v in old_manifest["volumes"]} != {
                name.removeprefix("drive-").removesuffix("-backup"): v["size"] for name, v in query["devices"].items()}:
            parent = old_manifest = None
            reason = "Disk layout changed"
        reply(modes={name: ("use" if parent else "new") for name in query["devices"]
                     if mode == "native_cbt" and not name.startswith(("drive-efidisk", "drive-tpmstate"))})
        ready = receive()
        if ready["event"] != "ready":
            raise ProxmoxError("Native provider did not supply disk exports")
        (work / "prepare.json").write_text(json.dumps({"vmid": vmid, "volumes": ready["volumes"], "previous": old_manifest}))
        run_process(["/usr/bin/python3", str(VIEW), "prepare", str(work / "prepare.json"), str(work / "prepared.json")], timeout=120)
        if (work / "prepared.json").stat().st_size > 16 * 1024 * 1024:
            raise ProxmoxError("Native disk map is too large")
        prepared = json.loads((work / "prepared.json").read_text())
        volumes, manifest, bytes_to_read = prepared["sources"], prepared["manifest"], prepared["bytes_to_read"]
        if parent and any(v["bitmap-mode"] == "new" for v in volumes if v["disk"].startswith(("ide", "sata", "scsi", "virtio"))):
            reason = "Native bitmap was missing or recreated; affected disks are read fully"
        reused = any(v["bitmap-mode"] == "reuse" for v in volumes)
        details = {"vmid": vmid, "guest_name": guest.get("name"), "backup_method": "native", "requested_mode": mode,
                   "effective_mode": "native_cbt" if parent and reused else "native", "fallback_reason": reason,
                   "disk_bytes": sum(v["size"] for v in volumes), "bytes_to_read": bytes_to_read}
        details["bytes_reused"] = details["disk_bytes"] - bytes_to_read
        report.log_message(f"VM {vmid}: {details['effective_mode']}" + (f" — {reason}" if reason else ""))
        artifact = handler.start_artifact(f"vm:{vmid}", report=report, data=details)
        plan = {"manifest": manifest, "sources": [{**v, "dirty": sorted(v["dirty"])} for v in volumes],
                 "config": ready["config"], "firewall": ready.get("firewall"), "metrics": str(work / "metrics.json")}
        if report.data.get("proxmox_bytes_total") is not None:
            # Use the frozen export sizes, including the exact metadata Restic reads.
            logical_bytes = details["disk_bytes"] + sum(len(data) for data in view_metadata(plan).values())
            report.set_data("proxmox_bytes_total", report.data["proxmox_bytes_total"] - planned_bytes + logical_bytes)
        (work / "view.json").write_text(json.dumps(plan))
        # System Python provides FUSE/libnbd, keeping them out of the agent's venv.
        view = subprocess.Popen(["/usr/bin/python3", str(VIEW), str(work / "view.json"), str(work / "mount")],
                                stdout=log, stderr=log, start_new_session=True)
        deadline = time.monotonic() + 15
        while not os.path.ismount(work / "mount") and time.monotonic() < deadline:
            monitor()
            time.sleep(.1)
        if not os.path.ismount(work / "mount"):
            raise ProxmoxError("Native block view could not be mounted")
        progress.update(phase="backing_up", bytes_total=bytes_to_read, bytes_processed=0, disk_bytes=details["disk_bytes"])
        report.set_data("proxmox_progress", dict(progress))
        tags = [f"job_uuid:{handler.job['uuid']}", f"operation_uuid:{operation}", "source:proxmox", "guest_type:qemu",
                f"vmid:{vmid}", "backup_method:native", f"artifact_uuid:{artifact['uuid']}", f"artifact_key:vm:{vmid}"]
        with handler.agent.resticapi.operation_cancellation(report.cancel_event):
            status = handler.agent.resticapi.backup(paths=["."], cwd=str(work / "mount"), parent=parent, force=not bool(parent),
                tags=tags, callback=report.process_backup_status, callback_pid=True, callback_throttle=500,
                monitor_callback=monitor)
        monitor()
        report.process_backup_status(status)
        progress["phase"] = "cleanup"
        report.set_data("proxmox_progress", dict(progress))
        run_process(["fusermount3", "-u", str(work / "mount")], timeout=15)
        view.wait(timeout=15)
        view = None
        metrics.update(json.loads((work / "metrics.json").read_text()))
        if metrics.get("error"):
            raise ProxmoxError(metrics["error"])
        reply()
        if receive()["event"] != "done":
            raise ProxmoxError("Native provider did not confirm cleanup")
        process.wait(timeout=15)
        if process.returncode:
            raise ProxmoxError("Native provider failed during cleanup")
        proxmox_checkpoints.upsert({"key": key, "data": {"valid": mode == "native_cbt", "info": info,
            "snapshot_id": status["snapshot_id"]}}, ["key"])
        handler.finish_artifact(artifact, snapshot_id=status["snapshot_id"], data={**metrics,
            "data_added_packed": status.get("data_added_packed"), "cleanup": "complete"})
        report.set_data("proxmox_progress", {**progress, "phase": "complete", "percent_done": 100})
    except Exception as exc:
        report.set_data("proxmox_progress", {**progress, "phase": "failed"})
        if artifact and status and status.get("snapshot_id"):
            handler.finish_artifact(artifact, snapshot_id=status["snapshot_id"], data={**metrics, "cleanup": "pending",
                "data_added_packed": status.get("data_added_packed")})
            report.log_message(f"Native backup saved, but checkpoint/cleanup remains unconfirmed: {exc}",
                               final_state=AgentOperationState.warning)
        elif artifact:
            handler.finish_artifact(artifact, state=AgentOperationState.failed, snapshot_id=getattr(exc, "snapshot_id", None))
        log.flush()
        log.seek(0, 2)
        log.seek(max(0, log.tell() - 8000))
        report.log_message(log.read())
        if not status or not status.get("snapshot_id"):
            raise
    finally:
        if process and process.poll() is None:
            try:
                process.stdin.write(json.dumps({"ok": False, "error": "Drastic backup aborted"}) + "\n")
                process.stdin.flush()
                process.stdin.close()
                process.wait(timeout=60)
            except (OSError, subprocess.TimeoutExpired):
                process.terminate()
        if os.path.ismount(work / "mount"):
            try:
                run_process(["fusermount3", "-u", str(work / "mount")], timeout=15)
            except Exception as exc:
                report.log_message(f"Native mount cleanup pending: {exc}", final_state=AgentOperationState.warning)
        if view and view.poll() is None:
            view.terminate()
            view.wait(timeout=10)
        log.close()
        recover_runs(report)
