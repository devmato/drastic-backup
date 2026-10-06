import json
import os
from contextlib import contextmanager
from pathlib import Path
from threading import Event
from types import SimpleNamespace
from uuid import uuid4

import pytest

from drastic_agent.services import proxmox_restore as restore
from drastic_common.process import ProcessCancelledError

ARCHIVE = "qemu-101.tar"
IDENTITY = {"job_uuid": "job", "repository_id": 2, "snapshot_id": "snapshot"}
SNAPSHOT = {"id": "snapshot", "tags": ["job_uuid:job", "source:proxmox", "guest_type:qemu", "backup_method:snapshot"]}


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


def setup_restore(monkeypatch, tmp_path, fail_at=None, disks=None):
    monkeypatch.setenv("DRASTIC_RESTORE_WORK_DIR", str(tmp_path / "work"))
    monkeypatch.setattr(restore.shutil, "which", lambda _: "/usr/bin/tool")
    monkeypatch.setattr(restore, "guest_tools_available", lambda: None)
    monkeypatch.setattr(restore, "host_options", lambda *args: {
        "node": "pve", "used_vmids": [100], "storages": [{"storage": "local-lvm", "avail": 10**9}],
    })
    commands = []
    disks = disks or {"scsi0": 4096}

    def stream(producer, consumer, **kwargs):
        if kwargs.get("cancelled", lambda: False)():
            raise ProcessCancelledError("cancelled")
        commands.extend([producer, consumer])
        if fail_at and (fail_at in producer or fail_at in consumer):
            raise RuntimeError("tool failed")
        kwargs["on_output"](f"progress 50% (read {sum(disks.values()) // 2} bytes, duration 1 sec)")

    @contextmanager
    def disk_view(api, descriptor, work, **kwargs):
        assert kwargs["import_metadata"]
        commands.append(["disk-view", descriptor["format"]])
        if kwargs["cancelled"]():
            raise ProcessCancelledError("cancelled")
        (work / "qemu-server.conf").write_text("bios: ovmf\n#qmdump#obsolete\n")
        (work / "qemu-server.fw").write_text("[OPTIONS]\nenable: 1\n")
        yield disks

    backend = restore.get_guest_file_backend()
    monkeypatch.setattr(backend, "disk_view", disk_view)
    monkeypatch.setattr(restore, "get_guest_file_backend", lambda: backend)
    monkeypatch.setattr(restore, "run_pipeline", stream)
    agent = SimpleNamespace(resticapi=SimpleNamespace(
        ls=lambda **kw: [{"type": "file", "path": "/" + ARCHIVE, "size": 10240}],
        restore=lambda **kw: pytest.fail("Proxmox restore must not materialize backup data"),
    ))
    return agent, Report(), commands


def test_vm_restore_streams_with_safe_flags_and_cleans_work(monkeypatch, tmp_path):
    agent, report, commands = setup_restore(monkeypatch, tmp_path)
    restore.run_proxmox_restore(agent, report, SNAPSHOT, mode="proxmox_vm", identity=IDENTITY,
                               vmid=101, storage="local-lvm", unique=True)
    command = commands[-1]
    assert command[0] == "qmrestore"
    assert command[1] == "-"
    assert command[2:] == ["101", "--storage", "local-lvm", "--unique", "1", "--start", "0", "--force", "0"]
    assert commands[1][:2] == ["vma", "create"]
    assert commands[1][2] == "/proc/self/fd/1"
    assert report.data["restore_bytes_restored"] == report.data["restore_bytes_total"] == 4096
    assert report.data["target_vmid"] == 101
    assert list(restore.workspace_root().iterdir()) == []


@pytest.mark.parametrize("vmid,storage", [(100, "local-lvm"), (99, "local-lvm"), (True, "local-lvm"), (101, "missing"), (101, "--force")])
def test_invalid_vm_destination_never_downloads_or_imports(monkeypatch, tmp_path, vmid, storage):
    agent, report, commands = setup_restore(monkeypatch, tmp_path)
    with pytest.raises(ValueError):
        restore.run_proxmox_restore(agent, report, SNAPSHOT, mode="proxmox_vm", identity=IDENTITY,
                                   vmid=vmid, storage=storage)
    assert commands == []


@pytest.mark.parametrize("failure", ["create", "qmrestore"])
def test_tool_failure_cleans_workspace_without_deleting_vm(monkeypatch, tmp_path, failure):
    agent, report, commands = setup_restore(monkeypatch, tmp_path, fail_at=failure)
    with pytest.raises(RuntimeError, match="tool failed"):
        restore.run_proxmox_restore(agent, report, SNAPSHOT, identity=IDENTITY,
                                   mode="proxmox_vm",
                                   vmid=101, storage="local-lvm")
    assert list(restore.workspace_root().iterdir()) == []
    assert not any("destroy" in command for command in commands)
    assert report.data["destination_may_contain_restored_data"]


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


def test_bad_archive_or_insufficient_target_space_never_imports(monkeypatch, tmp_path):
    agent, report, commands = setup_restore(monkeypatch, tmp_path)
    with pytest.raises(ValueError, match="QEMU TAR"):
        restore.validate_tar_archive(agent, {**SNAPSHOT, "tags": [*SNAPSHOT["tags"], "kind:manifest"]})
    monkeypatch.setattr(restore, "host_options", lambda *args: {
        "node": "pve", "used_vmids": [], "storages": [{"storage": "local-lvm", "avail": 0}],
    })
    with pytest.raises(ValueError, match="space"):
        restore.run_proxmox_restore(agent, report, SNAPSHOT, mode="proxmox_vm", identity=IDENTITY,
                                   vmid=101, storage="local-lvm")
    assert commands == [["disk-view", "tar"]]
    assert list(restore.workspace_root().iterdir()) == []


def test_cancellation_between_view_and_import_cleans_workspace(monkeypatch, tmp_path):
    agent, report, commands = setup_restore(monkeypatch, tmp_path)
    backend = restore.get_guest_file_backend()
    original = backend.disk_view

    @contextmanager
    def cancel_view(*args, **kwargs):
        with original(*args, **kwargs) as disks:
            report.cancel_event.set()
            yield disks

    def options(cancelled):
        if cancelled():
            raise ProcessCancelledError("cancelled")
        return {"node": "pve", "used_vmids": [], "storages": [{"storage": "local-lvm", "avail": 10**9}]}

    monkeypatch.setattr(restore, "host_options", options)
    monkeypatch.setattr(backend, "disk_view", cancel_view)
    with pytest.raises(ProcessCancelledError):
        restore.run_proxmox_restore(agent, report, SNAPSHOT, mode="proxmox_vm", identity=IDENTITY,
                                   vmid=101, storage="local-lvm")
    assert commands == [["disk-view", "tar"]]
    assert list(restore.workspace_root().iterdir()) == []


def test_failed_export_removes_prepared_session(monkeypatch, tmp_path):
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


@pytest.mark.parametrize("mode", ["proxmox_vm", "proxmox_prepare", "proxmox_files"])
def test_legacy_vma_is_rejected_before_tools_or_workspace_creation(monkeypatch, tmp_path, mode):
    agent, report, commands = setup_restore(monkeypatch, tmp_path)
    snapshot = {"id": "snapshot", "tags": ["job_uuid:job", "source:proxmox", "guest_type:qemu", "backup_method:vzdump"]}

    def no_guest_tools():
        pytest.fail("Legacy VMA must fail before checking guest dependencies")

    monkeypatch.setattr(restore, "guest_tools_available", no_guest_tools)
    with pytest.raises(ValueError, match="legacy VMA"):
        restore.run_proxmox_restore(agent, report, snapshot, mode=mode, identity=IDENTITY,
                                   vmid=101, storage="local-lvm", session_id=report.uuid)
    assert commands == []
    assert not restore.workspace_root(create=False).exists()


@pytest.mark.parametrize("method", ["snapshot", "native"])
def test_vm_stream_maps_efi_tpm_firewall_with_small_workspace(monkeypatch, tmp_path, method):
    size = 1024**4
    disks = {"scsi0": size, "efidisk0": 4096, "tpmstate0": 4096}
    agent, report, commands = setup_restore(monkeypatch, tmp_path, disks=disks)
    snapshot = {"id": "snapshot", "tags": ["source:proxmox", "guest_type:qemu", f"backup_method:{method}", "vmid:101"]}
    original = restore.run_pipeline

    def stream(producer, consumer, **kwargs):
        work = Path(producer[producer.index("-c") + 1]).parent
        assert producer[:3] == ["vma", "create", "/proc/self/fd/1"] and consumer[:2] == ["qmrestore", "-"]
        assert {path.name for path in work.iterdir()} == {"lock", "qemu-server.conf", "qemu-server.fw"}
        config = (work / "qemu-server.conf").read_text()
        assert "bios: ovmf" in config and "#qmdump#obsolete" not in config
        for disk in disks:
            device = f"drive-{disk}" + ("-backup" if disk == "tpmstate0" else "")
            path = work / "disks" / f"disk-drive-{disk}.raw"
            assert f"#qmdump#map:{disk}:{device}::raw:" in config
            assert f"format=raw:{device}={path}" in producer
        assert str(work / "qemu-server.fw") in producer and producer.count("-c") == 2
        assert (work / "qemu-server.fw").read_text() == "[OPTIONS]\nenable: 1\n"
        assert sum(path.stat().st_size for path in work.iterdir()) < 4096
        original(producer, consumer, **kwargs)
        assert report.data["restore_bytes_restored"] == sum(disks.values()) // 2

    monkeypatch.setattr(restore, "run_pipeline", stream)
    monkeypatch.setattr(restore, "host_options", lambda *args: {
        "node": "pve", "used_vmids": [], "storages": [{"storage": "local-lvm", "avail": size * 2}],
    })
    monkeypatch.setattr(restore.shutil, "disk_usage", lambda _: SimpleNamespace(free=1024 * 1024))
    restore.run_proxmox_restore(agent, report, snapshot, mode="proxmox_vm", identity=IDENTITY,
                               vmid=101, storage="local-lvm")
    assert commands[0] == ["disk-view", "tar" if method == "snapshot" else "native"]
    assert report.data["restore_bytes_restored"] == report.data["restore_bytes_total"] == size + 8192
    assert not list(restore.workspace_root().iterdir())


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


@pytest.mark.parametrize("method", ["snapshot", "native"])
def test_on_demand_prepare_browse_export_never_materializes_disks(monkeypatch, tmp_path, method):
    agent, report, commands = setup_restore(monkeypatch, tmp_path)
    snapshot = {"id": "snapshot", "tags": ["source:proxmox", "guest_type:qemu", f"backup_method:{method}", "vmid:101"]}
    agent.resticapi.ls = lambda **kw: [{"type": "file", "path": "/qemu-101.tar", "size": 1024**4}]
    configured, calls = [], []
    agent.configure_repository = configured.append

    backend = restore.get_guest_file_backend()

    def request(api, descriptor, work, action, **kwargs):
        assert api is agent.resticapi
        assert descriptor["format"] == ("tar" if method == "snapshot" else "native")
        assert not (work / "disks").exists()
        calls.append(action)
        if kwargs["cancelled"]():
            raise ProcessCancelledError("cancelled")
        return {"volumes": [{"device": "/dev/sda1", "error": None}], "entries": [],
                "files": 1, "bytes": 6, "skipped_special_files": 0}

    monkeypatch.setattr(backend, "request", request)
    monkeypatch.setattr(restore, "get_guest_file_backend", lambda: backend)
    restore.run_proxmox_restore(agent, report, snapshot, mode="proxmox_prepare", identity=IDENTITY)
    assert report.data["guest_files_on_demand"]
    work = restore.workspace_root() / f"session-{report.uuid}"
    assert {p.name for p in work.iterdir()} == {"lock", "session.json", "remote.json"}
    restore.session_action("entries", report.uuid, IDENTITY, agent=agent, repository={"location": "repo"}, volume="/dev/sda1")
    assert configured == [{"location": "repo"}]
    restore.run_proxmox_restore(agent, report, snapshot, mode="proxmox_files", identity=IDENTITY,
                               session_id=report.uuid, volume="/dev/sda1", include_paths=["/etc/hostname"],
                               restore_location=str(tmp_path / "out"))
    assert calls == ["volumes", "entries", "export"]
    assert not commands
    assert not work.exists()


def test_on_demand_prepare_failure_cleans_session(monkeypatch, tmp_path):
    agent, report, _ = setup_restore(monkeypatch, tmp_path)
    snapshot = {"id": "snapshot", "tags": ["source:proxmox", "guest_type:qemu", "backup_method:snapshot"]}
    agent.resticapi.ls = lambda **kw: [{"type": "file", "path": "/qemu-101.tar", "size": 1024**4}]

    def fail(*args, **kwargs):
        raise ProcessCancelledError("cancelled")

    monkeypatch.setattr(restore, "guest_request", fail)
    with pytest.raises(ProcessCancelledError):
        restore.run_proxmox_restore(agent, report, snapshot, mode="proxmox_prepare", identity=IDENTITY)
    assert not list(restore.workspace_root().iterdir())


def test_file_browser_options_do_not_require_proxmox_host_tools(monkeypatch):
    from drastic_agent.agent.agent import Agent

    def no_host_tools(*args):
        pytest.fail("Guest file options must not query Proxmox VM import tools")

    monkeypatch.setattr(restore, "host_options", no_host_tools)
    monkeypatch.setattr(restore, "guest_tools_available", lambda: None)
    report = Agent.cmd_proxmox_restore(SimpleNamespace(), "options", mode="proxmox_files")
    assert report.data["guest_files_on_demand"]
    assert report.data["guest_files_error"] is None
