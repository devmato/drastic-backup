"""Local QEMU restore and short-lived, disk-only guest browsing workspaces."""

import json
import os
import platform
import re
import shutil
import tarfile
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from functools import cache
from pathlib import Path
from uuid import UUID

from drastic_agent.agent.report import AgentReport
from drastic_agent.config import DefaultConfig, env_value
from drastic_agent.services.guest_backend import get_guest_file_backend
from drastic_common.process import ProcessCancelledError, run_process

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


def unpack_snapshot(archive_path, work, cancelled):
    """Only known regular members are accepted; never extract links or arbitrary paths."""
    sizes = {}
    with tarfile.open(archive_path, mode="r|") as archive:
        for member in archive:
            name = member.name
            disk = re.fullmatch(r"disks/disk-drive-((?:ide|sata|scsi|virtio)\d+|efidisk0|tpmstate0)\.raw", name)
            if (not member.isfile() or name in sizes or member.size < 0
                    or (not disk and name not in {"manifest.json", "qemu-server.conf", "qemu-server.fw"})
                    or (not disk and member.size > 65535) or len(sizes) >= 257):
                raise ValueError("Unsafe or unsupported snapshot archive member")
            if disk and (member.size == 0 or member.size % 512):
                raise ValueError("Invalid snapshot disk size")
            require_space(work, member.size)
            target = work / name
            target.parent.mkdir(exist_ok=True)
            with archive.extractfile(member) as source, target.open("xb") as output:
                while chunk := source.read(1024 * 1024):
                    if cancelled():
                        raise ProcessCancelledError("Restore cancelled")
                    if disk and not any(chunk):
                        output.seek(len(chunk), 1)
                    else:
                        output.write(chunk)
                output.truncate(member.size)
            sizes[name] = member.size
    if not {"manifest.json", "qemu-server.conf"}.issubset(sizes):
        raise ValueError("Snapshot archive lacks configuration or manifest")
    manifest = json.loads((work / "manifest.json").read_text())
    if not isinstance(manifest, dict) or manifest.get("version") != 1:
        raise ValueError("Unsupported snapshot manifest")
    if type(manifest.get("vmid")) is not int or archive_path.name != f"qemu-{manifest['vmid']}.tar":
        raise ValueError("Snapshot manifest VM does not match archive")
    volumes = manifest.get("volumes")
    if not isinstance(volumes, list) or not volumes:
        raise ValueError("Snapshot manifest has no disks")
    expected = {}
    for item in volumes:
        if (not isinstance(item, dict) or not isinstance(item.get("disk"), str)
                or not re.fullmatch(r"(?:ide|sata|scsi|virtio)\d+|efidisk0|tpmstate0", item["disk"])
                or type(item.get("size")) is not int or item["size"] <= 0):
            raise ValueError("Invalid snapshot disk manifest")
        name = f"disks/disk-drive-{item['disk']}.raw"
        if name in expected:
            raise ValueError("Duplicate manifest disk")
        expected[name] = item["size"]
    if expected != {name: size for name, size in sizes.items() if name.startswith("disks/")}:
        raise ValueError("Snapshot disks do not match manifest")
    archive_path.unlink()
    return sum(expected.values())


def create_vma(work, cancelled):
    disks = sorted((work / "disks").glob("disk-drive-*.raw"))
    require_space(work, sum(path.stat().st_size for path in disks) + 16 * 1024 * 1024)
    manifest = work / "manifest.json"
    vmid = json.loads(manifest.read_text()).get("vmid", 100) if manifest.exists() else 100
    archive = work / f"vzdump-qemu-{vmid}-{datetime.now(timezone.utc).strftime('%Y_%m_%d-%H_%M_%S')}.vma"
    command = ["vma", "create", str(archive), "-c", str(work / "qemu-server.conf")]
    if (work / "qemu-server.fw").exists():
        command.extend(["-c", str(work / "qemu-server.fw")])
    config = work / "qemu-server.conf"
    text = "\n".join(line for line in config.read_text().splitlines() if not line.startswith("#qmdump#")) + "\n"
    for path in disks:
        device = path.name.removeprefix("disk-").removesuffix(".raw")
        disk = device.removeprefix("drive-")
        if device == "drive-tpmstate0":
            device += "-backup"
        # qmrestore requires these native device hints, including the TPM alias.
        text += f"#qmdump#map:{disk}:{device}::raw:\n"
        command.extend(["-d", f"format=raw:{device}={path}"])
    config.write_text(text)
    run_process(command, cancelled=cancelled)
    shutil.rmtree(work / "disks")
    run_process(["vma", "verify", str(archive)], cancelled=cancelled)
    return archive


def require_space(path, size):
    if shutil.disk_usage(path).free < size + 64 * 1024 * 1024:
        raise ValueError(f"Insufficient workspace space: at least {size} bytes plus 64 MiB required")


def restore_native_blocks(agent, report, snapshot, work, cancelled):
    from drastic_agent.proxmox_blocks import (
        BLOCK_SIZE,
        block_path,
        check_manifest_entry,
        validate_manifest,
    )
    from drastic_agent.services.restore import RestoreService

    target = work / "blocks"
    check_manifest_entry(agent.resticapi, snapshot["id"])
    agent.resticapi.restore(snapshot["id"], str(target), ["/manifest.json"])
    path = target / "manifest.json"
    RestoreService._reject_symlink_components(str(path))
    if not path.is_file() or path.stat().st_size > 16 * 1024 * 1024:
        raise ValueError("Native manifest is missing or too large")
    manifest = json.loads(path.read_text())
    volumes = validate_manifest(manifest)
    if f"vmid:{manifest['vmid']}" not in (snapshot.get("tags") or []):
        raise ValueError("Native manifest does not match snapshot VM")
    size = sum(volume["size"] for volume in volumes)
    require_space(work, size)
    agent.resticapi.restore(snapshot["id"], str(target), ["/disks", "/qemu-server.conf", "/qemu-server.fw"],
        callback=AgentReport.process_restore_status, callback_args={"operation_uuid": report.uuid},
        callback_pid=True, callback_throttle=500)
    for name in ("qemu-server.conf", "qemu-server.fw"):
        source = target / name
        if source.exists():
            RestoreService._reject_symlink_components(str(source))
            if not source.is_file() or source.stat().st_size > 65535:
                raise ValueError("Invalid native VM configuration")
            source.rename(work / name)
        elif name == "qemu-server.conf":
            raise ValueError("Native backup has no VM configuration")
    (work / "disks").mkdir()
    for volume in volumes:
        with (work / "disks" / f"disk-drive-{volume['disk']}.raw").open("xb") as output:
            for index in range(len(volume["generations"])):
                if cancelled():
                    raise ProcessCancelledError("Restore cancelled")
                source = target / block_path(volume["disk"], index)
                RestoreService._reject_symlink_components(str(source))
                expected = min(BLOCK_SIZE, volume["size"] - index * BLOCK_SIZE)
                if not source.is_file() or source.stat().st_size != expected:
                    raise ValueError("Native backup has a missing or invalid disk block")
                data = source.read_bytes()
                if not any(data):
                    output.seek(len(data), 1)
                else:
                    output.write(data)
                source.unlink()
            output.truncate(volume["size"])
    if any(path.is_file() or path.is_symlink() for path in (target / "disks").rglob("*")):
        raise ValueError("Unexpected native backup blocks")
    (target / "manifest.json").rename(work / "manifest.json")
    shutil.rmtree(target)
    return size


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

    archive = None if native else validate_tar_archive(agent, snapshot)
    keep_workspace = False
    with workspace(report.uuid, create=True) as work:
        try:
            if mode == "proxmox_prepare":
                descriptor = {"format": "native" if native else "tar", "snapshot_id": snapshot["id"]}
                if native:
                    vmids = [tag.removeprefix("vmid:") for tag in snapshot.get("tags", []) if tag.startswith("vmid:")]
                    if len(vmids) != 1 or not vmids[0].isdigit():
                        raise ValueError("Native snapshot has no unique VM identity")
                    descriptor["vmid"] = int(vmids[0])
                else:
                    descriptor["archive"] = archive["path"].lstrip("/")
                (work / "remote.json").write_text(json.dumps(descriptor))
                phase("Opening backup disks on demand")
                prepare_browser(agent, report, work, identity)
                keep_workspace = True
                return
            if native:
                phase("Restoring native VM blocks")
                disk_size = restore_native_blocks(agent, report, snapshot, work, cancelled)
            else:
                require_space(work, int(archive.get("size") or 0))
                phase("Restoring VM archive")
                status = agent.resticapi.restore(
                    snapshot_id=snapshot["id"], target=str(work), include_paths=[archive["path"]],
                    overwrite_policy="fail_if_exists", callback=AgentReport.process_restore_status,
                    callback_args={"operation_uuid": report.uuid}, callback_pid=True, callback_throttle=500)
                AgentReport.process_restore_status(status, operation_uuid=report.uuid)
                archive_path = work / archive["path"].lstrip("/")
                phase("Extracting snapshot disks")
                disk_size = unpack_snapshot(archive_path, work, cancelled)
            phase("Preparing Proxmox import")
            archive_path = create_vma(work, cancelled)
            options = host_options(cancelled)
            if vmid in options["used_vmids"]:
                raise ValueError(f"VMID {vmid} already exists in the cluster")
            available = next((s.get("avail", 0) for s in options["storages"] if s["storage"] == storage), 0)
            if int(available) < disk_size:
                raise ValueError("Target storage has insufficient free space for the restored disks")
            phase(f"Importing VM {vmid} into {storage}")
            report.set_data("destination_may_contain_restored_data", True)
            report.set_data("target_vmid", vmid)
            run_process(["qmrestore", str(archive_path), str(vmid), "--storage", storage,
                         "--unique", "1" if unique else "0", "--start", "0", "--force", "0"],
                        cancelled=cancelled)
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
