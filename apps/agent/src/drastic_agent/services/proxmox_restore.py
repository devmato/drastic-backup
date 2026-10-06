"""Local QEMU restore and short-lived, disk-only guest browsing workspaces."""

import json
import os
import platform
import re
import shutil
import time
from contextlib import contextmanager
from functools import cache
from pathlib import Path
from uuid import UUID

from drastic_agent.config import DefaultConfig, env_int, env_value
from drastic_agent.services.guest_backend import get_guest_file_backend
from drastic_common.process import run_pipeline, run_process

WORKSPACE_TTL = 3600


def workspace_root(*, create=True):
    data = env_value("DRASTIC_AGENT_DATA_DIR", DefaultConfig.AGENT_DATA_DIR)
    root = Path(env_value("DRASTIC_RESTORE_WORK_DIR", str(Path(data) / "restore-work")))
    from drastic_agent.services.restore import RestoreService

    RestoreService._normalize_restore_target(str(root))
    RestoreService._reject_symlink_components(str(root))
    if create:
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        root.chmod(0o700)
    return root


def remove_workspace(path):
    # Also recover private mounts left by an interrupted agent before walking the tree.
    get_guest_file_backend().cleanup_workspace(path)
    shutil.rmtree(path)


@contextmanager
def workspace(session_id, *, create=False, identity=None):
    path = workspace_root() / f"session-{UUID(session_id)}"
    if create:
        path.mkdir(mode=0o700)
        try:
            (path / "lock").touch(mode=0o600)
        except OSError:
            path.rmdir()
            raise
    if path.is_symlink():
        raise ValueError("Invalid restore workspace")
    try:
        fd = os.open(path / "lock", os.O_RDWR | os.O_NOFOLLOW)
    except FileNotFoundError as exc:
        raise ValueError("Restore workspace expired; prepare the snapshot again") from exc
    acquired = False
    try:
        try:
            get_guest_file_backend().lock_workspace(fd)
        except BlockingIOError as exc:
            raise ValueError("Restore workspace is busy") from exc
        if not create:
            if time.time() - (path / "lock").stat().st_mtime > WORKSPACE_TTL:
                raise ValueError("Restore workspace expired; prepare the snapshot again")
            metadata = json.loads((path / "session.json").read_text())
            if identity is not None and any(metadata.get(k) != v for k, v in identity.items()):
                raise ValueError("Restore workspace does not belong to this job and snapshot")
        os.utime(path / "lock", None)
        acquired = True
        yield path
    finally:
        try:
            if acquired and path.exists():
                os.utime(path / "lock", None)
        finally:
            os.close(fd)


def cleanup_workspaces(*, all_workspaces=False):
    """No mounts survive requests. Locked workspaces are never removed."""
    for path in workspace_root(create=False).glob("session-*"):
        try:
            UUID(path.name.removeprefix("session-"))
            if path.is_symlink():
                continue
            fd = os.open(path / "lock", os.O_RDWR | os.O_NOFOLLOW)
            try:
                get_guest_file_backend().lock_workspace(fd)
                if all_workspaces or time.time() - os.fstat(fd).st_mtime > WORKSPACE_TTL:
                    remove_workspace(path)
            finally:
                os.close(fd)
        except (FileNotFoundError, BlockingIOError, ValueError):
            continue


def host_options(cancelled=lambda: False):
    for tool in ("qmrestore", "vma", "pvesh"):
        if not shutil.which(tool):
            raise ValueError(f"{tool} is missing. Run the restore agent on the Proxmox host.")
    node = platform.node().split(".")[0]
    storages = json.loads(run_process(
        ["pvesh", "get", f"/nodes/{node}/storage", "--content", "images", "--enabled", "1",
         "--output-format", "json"], cancelled=cancelled, timeout=30))
    guests = json.loads(run_process(
        ["pvesh", "get", "/cluster/resources", "--type", "vm", "--output-format", "json"],
        cancelled=cancelled, timeout=30))
    used = {int(guest["vmid"]) for guest in guests}
    next_id = 100
    while next_id in used:
        next_id += 1
    return {"node": node, "storages": [s for s in storages if s.get("active")],
            "used_vmids": sorted(used), "next_vmid": next_id}


def guest_tools_available():
    get_guest_file_backend().check_available()


@cache
def guest_tools_error():
    """Probe once per agent process for the connection status; restore checks stay live."""
    try:
        guest_tools_available()
    except ValueError as exc:
        return str(exc)
    return None


def guest_request(work, action, *, agent, cancelled=lambda: False, **kwargs):
    descriptor = json.loads((work / "remote.json").read_text())
    return get_guest_file_backend().request(
        agent.resticapi, descriptor, work, action,
        cancelled=cancelled, **kwargs)


def session_action(action, session_id, identity, *, agent=None, repository=None,
                   volume=None, path="/", cancelled=lambda: False):
    with workspace(session_id, identity=identity) as work:
        if action == "close":
            remove_workspace(work)
            return {}
        if action == "entries":
            if repository is not None:
                agent.configure_repository(repository)
            return guest_request(work, "entries", agent=agent, cancelled=cancelled, volume=volume, path=path)
        raise ValueError("Unsupported restore session action")


def validate_tar_archive(agent, snapshot):
    tags = snapshot.get("tags") or []
    if (not {"source:proxmox", "guest_type:qemu", "backup_method:snapshot"}.issubset(tags)
            or "kind:manifest" in tags):
        raise ValueError("Select a Proxmox QEMU TAR snapshot")
    entries = agent.resticapi.ls(snapshot_id=snapshot["id"], path="/") or []
    if isinstance(entries, dict):
        entries = [entries]
    files = [e for e in entries if e.get("type") == "file"]
    if len(files) != 1 or not re.fullmatch(r"/?qemu-\d+\.tar", files[0].get("path", "")):
        raise ValueError("Snapshot must contain exactly one VM TAR archive")
    return files[0]


def vma_stream_command(work, disks):
    # stdout is a pipe in run_pipeline; the VMA writer recognizes its FIFO fd.
    command = ["vma", "create", "/proc/self/fd/1", "-c", str(work / "qemu-server.conf")]
    if (work / "qemu-server.fw").exists():
        command.extend(["-c", str(work / "qemu-server.fw")])
    config = work / "qemu-server.conf"
    text = "\n".join(line for line in config.read_text().splitlines() if not line.startswith("#qmdump#")) + "\n"
    for disk in sorted(disks):
        path = work / "disks" / f"disk-drive-{disk}.raw"
        device = f"drive-{disk}"
        if device == "drive-tpmstate0":
            device += "-backup"
        # qmrestore requires these native device hints, including the TPM alias.
        text += f"#qmdump#map:{disk}:{device}::raw:\n"
        command.extend(["-d", f"format=raw:{device}={path}"])
    config.write_text(text)
    return command


def run_proxmox_restore(agent, report, snapshot, *, mode, identity, vmid=None, storage=None,
                        unique=True, session_id=None, volume=None, include_paths=(),
                        restore_location=None, overwrite_policy="fail_if_exists"):
    cancelled = report.cancel_event.is_set
    tags = snapshot.get("tags") or []
    native = "backup_method:native" in tags
    if (not {"source:proxmox", "guest_type:qemu"}.issubset(tags) or "kind:manifest" in tags
            or not (native or "backup_method:snapshot" in tags)):
        raise ValueError("Proxmox VM/file restore supports only TAR and Native/CBT snapshots; legacy VMA backups are not supported")

    def phase(value):
        report.set_data("restore_phase", value)
        report.log_message(value)

    if mode == "proxmox_files":
        with workspace(session_id, identity=identity) as work:
            phase("Exporting guest files")
            report.set_data("destination_may_contain_restored_data", True)
            try:
                result = guest_request(work, "export", agent=agent, cancelled=cancelled, volume=volume,
                                       paths=include_paths, target=restore_location,
                                       overwrite_policy=overwrite_policy)
            finally:
                remove_workspace(work)
            report.set_data("restore_files_restored", result["files"])
            report.set_data("restore_bytes_restored", result["bytes"])
            if result["skipped_special_files"]:
                from drastic_agent.agent.enums import AgentReportState

                report.log_message(f"Skipped {result['skipped_special_files']} sockets/devices/FIFOs",
                                   final_state=AgentReportState.warning)
        return

    if mode == "proxmox_prepare":
        guest_tools_available()
    elif mode == "proxmox_vm":
        if not isinstance(unique, bool):
            raise ValueError("Unique MAC address selection must be a boolean")
        if isinstance(vmid, bool) or not isinstance(vmid, int) or not 100 <= vmid <= 999999999:
            raise ValueError("VMID must be an integer between 100 and 999999999")
        if not isinstance(storage, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]*", storage):
            raise ValueError("Invalid target storage")
        options = host_options(cancelled)
        if vmid in options["used_vmids"]:
            raise ValueError(f"VMID {vmid} already exists in the cluster")
        if storage not in {s["storage"] for s in options["storages"]}:
            raise ValueError("Target storage is not active or does not support VM images")
    else:
        raise ValueError("Unsupported Proxmox restore mode")

    descriptor = {"format": "native" if native else "tar", "snapshot_id": snapshot["id"]}
    if native:
        vmids = [tag.removeprefix("vmid:") for tag in tags if tag.startswith("vmid:")]
        if len(vmids) != 1 or not vmids[0].isdigit():
            raise ValueError("Native snapshot has no unique VM identity")
        descriptor["vmid"] = int(vmids[0])
    else:
        descriptor["archive"] = validate_tar_archive(agent, snapshot)["path"].lstrip("/")
    keep_workspace = False
    with workspace(report.uuid, create=True) as work:
        try:
            if mode == "proxmox_prepare":
                (work / "remote.json").write_text(json.dumps(descriptor))
                phase("Opening backup disks on demand")
                prepare_browser(agent, report, work, identity)
                keep_workspace = True
                return
            phase("Opening backup disks on demand")
            with get_guest_file_backend().disk_view(
                agent.resticapi, descriptor, work, cancelled=cancelled,
                timeout=env_int("DRASTIC_RESTIC_TIMEOUT_SECONDS", 86400), import_metadata=True,
            ) as disks:
                disk_size = sum(disks.values())
                options = host_options(cancelled)
                if vmid in options["used_vmids"]:
                    raise ValueError(f"VMID {vmid} already exists in the cluster")
                available = next((s.get("avail", 0) for s in options["storages"] if s["storage"] == storage), 0)
                if int(available) < disk_size:
                    raise ValueError("Target storage has insufficient free space for the restored disks")
                command = vma_stream_command(work, disks)
                phase(f"Streaming VM {vmid} to {storage}")
                report.set_data("restore_bytes_total", disk_size)
                report.set_data("restore_bytes_restored", 0)
                report.set_data("destination_may_contain_restored_data", True)
                report.set_data("target_vmid", vmid)

                def progress(line):
                    if match := re.search(r"progress \d+% \(read (\d+) bytes", line):
                        report.set_data("restore_bytes_restored", min(disk_size, int(match[1])))
                    if line:
                        report.log_message(line)

                run_pipeline(command, ["qmrestore", "-", str(vmid), "--storage", storage,
                             "--unique", "1" if unique else "0", "--start", "0", "--force", "0"],
                             cancelled=cancelled, timeout=env_int("DRASTIC_RESTIC_TIMEOUT_SECONDS", 86400),
                             on_output=progress, operation_uuid=report.uuid)
                report.set_data("restore_bytes_restored", disk_size)
            report.log_message(f"VM {vmid} restored on {options['node']}; VM is stopped")
        finally:
            if not keep_workspace:
                remove_workspace(work)


def prepare_browser(agent, report, work, identity):
    result = guest_request(work, "volumes", agent=agent, cancelled=report.cancel_event.is_set)
    if not any(not volume["error"] for volume in result["volumes"]):
        raise ValueError(f"No readable guest filesystems: {result['volumes']}")
    (work / "session.json").write_text(json.dumps(identity))
    report.set_data("session_id", report.uuid)
    report.set_data("volumes", result["volumes"])
    report.set_data("guest_files_on_demand", True)
    report.set_data("restore_phase", "Ready to browse (expires after 1 hour of inactivity)")
