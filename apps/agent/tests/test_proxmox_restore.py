import io
import json
import os
import tarfile
from pathlib import Path
from threading import Event
from types import SimpleNamespace
from uuid import uuid4

import pytest

from drastic_agent.proxmox_snapshot import write_archive
from drastic_agent.services import proxmox_restore as restore
from drastic_common.process import ProcessCancelledError

ARCHIVE = "vzdump-qemu-100-2026_10_02-12_00_00.vma"
IDENTITY = {"job_uuid": "job", "repository_id": 2, "snapshot_id": "snapshot"}
SNAPSHOT = {"id": "snapshot", "tags": ["job_uuid:job", "source:proxmox", "guest_type:qemu", "backup_method:vzdump"]}
LISTING = "CFG: size: 100 name: qemu-server.conf\nCFG: size: 12 name: qemu-server.fw\nDEV: dev_id=1 size: 4096 devname: drive-scsi0\nDEV: dev_id=2 size: 4096 devname: drive-tpmstate0-backup\n"


class Report:
    def __init__(self):
        self.uuid = str(uuid4())
        self.data = {}
        self.logs = []
        self.cancel_event = Event()

    def set_data(self, key, value):
        self.data[key] = value

    def log_message(self, message, **kwargs):
        self.logs.append(message)


def setup_restore(monkeypatch, tmp_path, fail_at=None):
    monkeypatch.setenv("DRASTIC_RESTORE_WORK_DIR", str(tmp_path / "work"))
    monkeypatch.setattr(restore.shutil, "which", lambda _: "/usr/bin/tool")
    monkeypatch.setattr(restore, "guest_tools_available", lambda: None)
    monkeypatch.setattr(restore, "host_options", lambda *args: {
        "node": "pve", "used_vmids": [100], "storages": [{"storage": "local-lvm", "avail": 10**9}],
    })
    commands = []

    def run(command, **kwargs):
        if kwargs.get("cancelled", lambda: False)():
            raise ProcessCancelledError("cancelled")
        commands.append(command)
        if fail_at and fail_at in command:
            raise RuntimeError("tool failed")
        if command[:2] == ["vma", "list"]:
            return LISTING
        if command[:2] == ["vma", "extract"]:
            Path(command[-1]).mkdir()
            (Path(command[-1]) / "disk-drive-scsi0.raw").touch()
        return ""

    def restic_restore(**kwargs):
        commands.append(["restic", "restore"])
        (Path(kwargs["target"]) / ARCHIVE).write_bytes(b"archive")

    monkeypatch.setattr(restore, "run_process", run)
    agent = SimpleNamespace(resticapi=SimpleNamespace(
        ls=lambda **kw: [{"type": "file", "path": "/" + ARCHIVE, "size": 7}],
        restore=restic_restore,
    ))
    return agent, Report(), commands


def test_vm_restore_verifies_archive_uses_safe_flags_and_cleans_work(monkeypatch, tmp_path):
    agent, report, commands = setup_restore(monkeypatch, tmp_path)
    restore.run_proxmox_restore(agent, report, SNAPSHOT, mode="proxmox_vm", identity=IDENTITY,
                               vmid=101, storage="local-lvm", unique=True)
    command = commands[-1]
    assert command[0] == "qmrestore"
    assert command[2:] == ["101", "--storage", "local-lvm", "--unique", "1", "--start", "0", "--force", "0"]
    assert ["vma", "verify"] == commands[1][:2]
    assert report.data["target_vmid"] == 101
    assert list(restore.workspace_root().iterdir()) == []


@pytest.mark.parametrize("vmid,storage", [(100, "local-lvm"), (99, "local-lvm"), (True, "local-lvm"), (101, "missing"), (101, "--force")])
def test_invalid_vm_destination_never_downloads_or_imports(monkeypatch, tmp_path, vmid, storage):
    agent, report, commands = setup_restore(monkeypatch, tmp_path)
    with pytest.raises(ValueError):
        restore.run_proxmox_restore(agent, report, SNAPSHOT, mode="proxmox_vm", identity=IDENTITY,
                                   vmid=vmid, storage=storage)
    assert commands == []


@pytest.mark.parametrize("failure", ["verify", "extract", "qmrestore"])
def test_tool_failure_cleans_workspace_without_deleting_vm(monkeypatch, tmp_path, failure):
    agent, report, commands = setup_restore(monkeypatch, tmp_path, fail_at=failure)
    with pytest.raises(RuntimeError, match="tool failed"):
        restore.run_proxmox_restore(agent, report, SNAPSHOT, identity=IDENTITY,
                                   mode="proxmox_prepare" if failure == "extract" else "proxmox_vm",
                                   vmid=101, storage="local-lvm")
    assert list(restore.workspace_root().iterdir()) == []
    assert not any("destroy" in command for command in commands)
    assert bool(report.data.get("destination_may_contain_restored_data")) == (failure == "qmrestore")


def test_prepare_browse_export_reuses_images_and_removes_session(monkeypatch, tmp_path):
    agent, report, commands = setup_restore(monkeypatch, tmp_path)
    calls = []

    def guest(work, action, **kwargs):
        calls.append(action)
        assert (work / "disks" / "disk-drive-scsi0.raw").exists()
        assert not (work / ARCHIVE).exists()
        return {"volumes": [{"device": "/dev/vg/root", "filesystem": "ext4", "error": None}],
                "entries": [{"path": "/etc", "type": "dir"}], "files": 1, "bytes": 6, "skipped_special_files": 0}

    monkeypatch.setattr(restore, "guest_request", guest)
    restore.run_proxmox_restore(agent, report, SNAPSHOT, mode="proxmox_prepare", identity=IDENTITY)
    session_id = report.data["session_id"]
    result = restore.session_action("entries", session_id, IDENTITY, volume="/dev/vg/root")
    assert result["entries"][0]["path"] == "/etc"
    restore.run_proxmox_restore(agent, report, SNAPSHOT, mode="proxmox_files", identity=IDENTITY,
                               session_id=session_id, volume="/dev/vg/root", include_paths=["/etc"],
                               restore_location=str(tmp_path / "out"))
    assert calls == ["volumes", "entries", "export"]
    assert commands.count(["restic", "restore"]) == 1
    assert list(restore.workspace_root().iterdir()) == []


def test_workspace_identity_expiry_and_active_lock(monkeypatch, tmp_path):
    monkeypatch.setenv("DRASTIC_RESTORE_WORK_DIR", str(tmp_path / "work"))
    session_id = str(uuid4())
    with restore.workspace(session_id, create=True) as work:
        (work / "session.json").write_text(json.dumps(IDENTITY))
        with pytest.raises(ValueError, match="busy"), restore.workspace(session_id, identity=IDENTITY):
            pass
        restore.cleanup_workspaces(all_workspaces=True)
        assert work.exists()
    with pytest.raises(ValueError, match="does not belong"), restore.workspace(session_id, identity={**IDENTITY, "job_uuid": "other"}):
        pass
    os.utime(work / "lock", (0, 0))
    with pytest.raises(ValueError, match="expired"), restore.workspace(session_id, identity=IDENTITY):
        pass
    assert (work / "lock").stat().st_mtime == 0
    restore.cleanup_workspaces()
    assert not work.exists()


def test_bad_archive_or_insufficient_space_never_imports(monkeypatch, tmp_path):
    agent, report, commands = setup_restore(monkeypatch, tmp_path)
    with pytest.raises(ValueError, match="QEMU archive"):
        restore.validate_archive(agent, {**SNAPSHOT, "tags": [*SNAPSHOT["tags"], "kind:manifest"]})
    with pytest.raises(ValueError, match="unsafe"):
        restore.archive_devices(LISTING.replace("drive-scsi0", "../../escape"))
    monkeypatch.setattr(restore.shutil, "disk_usage", lambda _: SimpleNamespace(free=0))
    with pytest.raises(ValueError, match="space"):
        restore.run_proxmox_restore(agent, report, SNAPSHOT, mode="proxmox_vm", identity=IDENTITY,
                                   vmid=101, storage="local-lvm")
    assert commands == []
    assert list(restore.workspace_root().iterdir()) == []


def test_cancellation_between_download_and_import_cleans_workspace(monkeypatch, tmp_path):
    agent, report, commands = setup_restore(monkeypatch, tmp_path)
    original = agent.resticapi.restore

    def download(**kwargs):
        original(**kwargs)
        report.cancel_event.set()

    agent.resticapi.restore = download
    with pytest.raises(ProcessCancelledError):
        restore.run_proxmox_restore(agent, report, SNAPSHOT, mode="proxmox_vm", identity=IDENTITY,
                                   vmid=101, storage="local-lvm")
    assert commands == [["restic", "restore"]]
    assert list(restore.workspace_root().iterdir()) == []


def test_failed_export_removes_prepared_images(monkeypatch, tmp_path):
    agent, report, _ = setup_restore(monkeypatch, tmp_path)
    with restore.workspace(report.uuid, create=True) as work:
        (work / "session.json").write_text(json.dumps(IDENTITY))

    def fail(*args, **kwargs):
        raise ProcessCancelledError("cancelled")

    monkeypatch.setattr(restore, "guest_request", fail)
    with pytest.raises(ProcessCancelledError):
        restore.run_proxmox_restore(agent, report, SNAPSHOT, mode="proxmox_files", identity=IDENTITY,
                                   session_id=report.uuid, volume="/dev/sda1", include_paths=["/etc"],
                                   restore_location=str(tmp_path / "out"))
    assert list(restore.workspace_root().iterdir()) == []
    assert report.data["destination_may_contain_restored_data"]


@pytest.mark.parametrize("mode", ["proxmox_vm", "proxmox_prepare"])
def test_snapshot_restore_reuses_native_import_and_guest_browser(monkeypatch, tmp_path, mode):
    agent, report, commands = setup_restore(monkeypatch, tmp_path)
    raw = tmp_path / "source.raw"
    raw.write_bytes(b"x" * 4096)
    plan = {"vmid": 101, "config": "bios: ovmf\nscsi0: local-lvm:vm-101-disk-0\n", "volumes": [
        {"disk": disk, "path": str(raw), "size": 4096} for disk in ("scsi0", "efidisk0", "tpmstate0")
    ]}
    snapshot = {"id": "snapshot", "tags": ["source:proxmox", "guest_type:qemu", "backup_method:snapshot"]}
    agent.resticapi.ls = lambda **kw: [{"type": "file", "path": "/qemu-101.tar", "size": 20480}]

    def download(**kwargs):
        with (Path(kwargs["target"]) / "qemu-101.tar").open("wb") as output:
            write_archive(plan, output)

    agent.resticapi.restore = download
    original = restore.run_process

    def run(command, **kwargs):
        if command[:2] == ["vma", "create"]:
            config = Path(command[command.index("-c") + 1]).read_text()
            assert "#qmdump#map:scsi0:drive-scsi0::raw:" in config
            assert "#qmdump#map:tpmstate0:drive-tpmstate0-backup::raw:" in config
            assert any(arg.startswith("format=raw:drive-tpmstate0-backup=") for arg in command)
            assert all((Path(command[2]).parent / "disks" / f"disk-drive-{disk}.raw").read_bytes() == raw.read_bytes()
                       for disk in ("scsi0", "efidisk0", "tpmstate0"))
            Path(command[2]).write_bytes(b"vma")
        return original(command, **kwargs)

    monkeypatch.setattr(restore, "run_process", run)

    def guest(work, action, **kwargs):
        assert (work / "disks/disk-drive-scsi0.raw").read_bytes() == raw.read_bytes()
        assert not (work / "qemu-101.tar").exists()
        return {"volumes": [{"device": "/dev/sda1", "error": None}]}

    monkeypatch.setattr(restore, "guest_request", guest)
    restore.run_proxmox_restore(agent, report, snapshot, mode=mode, identity=IDENTITY,
                               vmid=102, storage="local-lvm")
    if mode == "proxmox_vm":
        assert commands[-1][0] == "qmrestore"
        assert list(restore.workspace_root().iterdir()) == []
    else:
        assert not commands
        restore.cleanup_workspaces(all_workspaces=True)


@pytest.mark.parametrize("name,kind", [("../escape", tarfile.REGTYPE), ("qemu-server.conf", tarfile.SYMTYPE),
                                      ("disks/disk-drive-scsi0.raw", tarfile.BLKTYPE)])
def test_snapshot_tar_rejects_unsafe_members_before_extraction(tmp_path, name, kind):
    path = tmp_path / "qemu-101.tar"
    with tarfile.open(path, "w") as archive:
        member = tarfile.TarInfo(name)
        member.type = kind
        member.linkname = "/etc/passwd"
        archive.addfile(member)
    with pytest.raises(ValueError, match="Unsafe"):
        restore.unpack_snapshot(path, tmp_path, lambda: False)
    assert not (tmp_path.parent / "escape").exists()


def test_snapshot_tar_rejects_manifest_mismatch_and_cancellation(tmp_path):
    raw = tmp_path / "source.raw"
    raw.write_bytes(b"x" * 4096)
    plan = {"vmid": 101, "config": "memory: 512\n", "volumes": [{"disk": "scsi0", "path": str(raw), "size": 4096}]}
    path = tmp_path / "qemu-101.tar"
    with path.open("wb") as output:
        write_archive(plan, output)
    with pytest.raises(ProcessCancelledError):
        restore.unpack_snapshot(path, tmp_path, lambda: True)
    (tmp_path / "disks/disk-drive-scsi0.raw").unlink()
    with tarfile.open(path, "w") as archive:
        for name, data in [("qemu-server.conf", b"memory: 512\n"),
                           ("manifest.json", json.dumps({"version": 1, "vmid": 101, "volumes": [{"disk": "scsi0", "size": 4096}]}).encode())]:
            info = tarfile.TarInfo(name)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
    with pytest.raises(ValueError, match="do not match"):
        restore.unpack_snapshot(path, tmp_path, lambda: False)


def test_agent_status_reports_cached_guest_tools_error(monkeypatch):
    from drastic_agent.agent.agent import Agent
    from drastic_common.truenas import AgentConnectionsSchema

    calls = []

    def probe():
        calls.append(True)
        raise ValueError("GuestFS dependencies missing")

    monkeypatch.setattr(restore, "guest_tools_available", probe)
    monkeypatch.setattr("drastic_agent.agent.agent.shutil.which", lambda _: "/usr/sbin/vzdump")
    client = SimpleNamespace(public_settings={"configured": True})
    agent = SimpleNamespace(get_proxmox_client=lambda: client, get_truenas_client=lambda: client, os_clean="linux")
    restore.guest_tools_error.cache_clear()
    try:
        status = Agent.connection_status(agent)
        assert AgentConnectionsSchema().load(status)["proxmox"]["guest_files_error"] == "GuestFS dependencies missing"
        assert Agent.connection_status(agent) == status
        assert len(calls) == 1
        monkeypatch.setattr(restore, "guest_tools_available", lambda: None)
        restore.guest_tools_error.cache_clear()  # A new agent process rechecks after installation.
        assert Agent.connection_status(agent)["proxmox"]["guest_files_error"] is None
    finally:
        restore.guest_tools_error.cache_clear()
