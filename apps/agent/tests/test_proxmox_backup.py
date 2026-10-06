import io
import json
import os
import shutil
import sys
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

import dataset
import pytest

import drastic_agent.jobs.base as base
import drastic_agent.jobs.proxmox_backup as backup
from drastic_agent.agent.report import AgentReport
from drastic_agent.guest_disks import DirectorySnapshot, snapshot_disks
from drastic_agent.proxmox import ProxmoxError, QemuVolumeGuestDriver
from drastic_agent.proxmox_blocks import make_manifest, view_metadata
from drastic_agent.proxmox_snapshot import write_archive
from drastic_common.restic.client import ResticApi
from drastic_common.restic.exceptions import ResticCancelledError, ResticFailedError
from drastic_common.restic.repository import ResticRepository


def test_guest_discovery_requires_at_least_one_backupable_volume():
    configs = {
        100: {}, 101: {"ide2": "local:iso/test.iso,media=cdrom"},
        102: {"scsi0": "local-lvm:vm-102-disk-0,backup=0"},
        103: {"scsi0": "/dev/disk/by-id/disk"}, 104: {"unused0": "local-lvm:vm-104-disk-0"},
        105: {"scsi0": "none", "scsi1": "local-lvm:vm-105-disk-0"},
        106: {"efidisk0": "local-lvm:vm-106-disk-0"},
        107: {"tpmstate0": "local-lvm:vm-107-disk-0"},
    }
    api = SimpleNamespace(list_qemu_guests=lambda: [{"vmid": vmid} for vmid in configs],
                          get_qemu_config=configs.get, get_node=lambda: "pve")
    assert [guest["vmid"] for guest in QemuVolumeGuestDriver().list_supported_guests(api)] == [105, 106, 107]


@pytest.fixture
def job(monkeypatch):
    monkeypatch.delenv("DRASTIC_PROXMOX_MIN_FREE_GIB", raising=False)
    db = dataset.connect("sqlite:///:memory:")
    monkeypatch.setattr(base, "agent_operation_artifacts", db["artifacts"])
    monkeypatch.setattr(backup, "proxmox_snapshots", db["snapshots"])
    monkeypatch.setattr(backup, "ensure_proxmox_available", lambda: None)
    monkeypatch.setattr(backup.platform, "node", lambda: "pve")
    snapshots, commands, exports = {}, [], []

    def info(vmid, name="", owner="", check=False):
        if check:
            return {"exists": name in snapshots, "description": snapshots.get(name), "lock": None}
        if name:
            assert snapshots[name] == owner
        return {"config": "bios: ovmf\nscsi0: local-lvm:vm-101-disk-0\n",
                "volumes": [{"disk": "scsi0", "path": "/dev/pve/snapshot", "size": 4096, "pool": "pve/data"}]}

    def run(command, **kwargs):
        commands.append(command)
        if command[0] == "lvs":
            assert command[command.index("--units") + 1] == "b" and "--nosuffix" in command
            return json.dumps({"report": [{"lv": [{"lv_size": str(1710.01 * 1024**3), "data_percent": "96.17", "metadata_percent": "3.55"}]}]})
        if command[1] == "snapshot":
            snapshots[command[3]] = command[-1]
        else:
            del snapshots[command[3]]
        return ""

    def stream(command, **kwargs):
        assert command[:3] == [sys.executable, "-m", "drastic_agent.proxmox_snapshot"]
        exports.append({**kwargs, "plan": json.loads(Path(command[-1]).read_text())})
        status = {"message_type": "summary", "snapshot_id": f"snap-{len(exports)}",
                  "total_bytes_processed": 10240, "total_files_processed": 1}
        kwargs["callback"](status, pid=123, **kwargs["callback_args"])
        kwargs["callback"](None, pid=None, **kwargs["callback_args"])
        return status

    monkeypatch.setattr(backup, "snapshot_info", info)
    monkeypatch.setattr(backup, "run_process", run)
    api = SimpleNamespace(configured=True, get_node=lambda: "pve", list_qemu_guests=lambda: [{"vmid": 101}],
                          get_qemu_config=lambda _: {"scsi0": "local-lvm:vm-101-disk-0"})
    restic = SimpleNamespace(backup_stdin_from_command=stream, operation_cancellation=lambda _: nullcontext())
    handler = backup.ProxmoxBackupJobHandler(SimpleNamespace(identifier=1, resticapi=restic,
        get_proxmox_client=lambda: api), {"id": 1, "uuid": "job-1", "config": {}}, 1)
    report = AgentReport.command_report()
    handler.operation = {"id": 1, "uuid": report.uuid}
    yield handler, report, commands, exports, snapshots
    db.engine.dispose()


def test_snapshot_stream_and_cleanup_use_one_artifact_per_vm(job):
    handler, report, commands, exports, snapshots = job
    handler.run_backup(report)
    assert len(exports) == len(report.artifacts) == 1
    assert exports[0]["stdin_filename"] == "qemu-101.tar"
    assert exports[0]["callback_args"] == {"operation_uuid": report.uuid}
    assert "backup_method:snapshot" in exports[0]["tags"]
    assert exports[0]["plan"]["volumes"][0]["path"] == "/dev/pve/snapshot"
    assert report.artifacts[0]["snapshot_id"] == "snap-1"
    assert report.data["backup_items_total"] == 1
    assert report.data["bytes_processed"] == 10240
    assert report.data["completed_guests"] == [101]
    assert report.data["proxmox_progress"]["phase"] == "complete"
    assert report.log.count("~65.5 GiB free; minimum reserve 20 GiB; data 96.17%; metadata 3.55%") == 1
    assert [command[1] for command in commands if command[0] == "qm"] == ["snapshot", "delsnapshot"]
    create, delete = [command for command in commands if command[0] == "qm"]
    assert create[3] == f"drastic-backup-{report.uuid.replace('-', '')[:12]}"
    assert len(create[3]) <= 40
    assert create[create.index("--description") + 1] == (
        f"Temporary Drastic backup snapshot; removed after backup. drastic:1:{report.uuid}"
    )
    assert delete[3] == create[3]
    assert not snapshots and not backup.proxmox_snapshots.count()


def test_multiple_vm_snapshots_aggregate_once_and_clean_between_guests(job):
    handler, report, _, exports, snapshots = job
    handler.agent.get_proxmox_client().list_qemu_guests = lambda: [{"vmid": 101}, {"vmid": 102}]
    handler.run_backup(report)
    assert len(exports) == len(report.artifacts) == report.data["backup_items_total"] == 2
    assert report.data["bytes_processed"] == report.data["bytes_total"] == 20480
    assert report.data["completed_guests"] == [101, 102]
    assert not snapshots and not backup.proxmox_snapshots.count()


@pytest.mark.parametrize("fail_second", [False, True])
def test_native_job_progress_uses_logical_sizes_and_keeps_failed_vm_in_total(job, monkeypatch, tmp_path, fail_second):
    from drastic_agent.jobs import proxmox_native as native

    handler, report, _, _, _ = job
    handler.job["config"]["backup_mode"] = "native_cbt"
    handler.agent.get_proxmox_client().list_qemu_guests = lambda: [{"vmid": 101}, {"vmid": 102}]
    db = dataset.connect("sqlite:///:memory:")
    monkeypatch.setattr(native, "proxmox_checkpoints", db["checkpoints"])
    monkeypatch.setattr(native, "proxmox_native_runs", db["runs"])
    monkeypatch.setattr(backup, "proxmox_native_runs", db["runs"])
    def recover(*args):
        for row in db["runs"].all():
            shutil.rmtree(row["data"]["work"])
        db["runs"].delete()

    monkeypatch.setattr(native, "recover_runs", recover)
    monkeypatch.setattr(native, "_session_process", lambda *a: "test")
    monkeypatch.setenv("DRASTIC_AGENT_DATA_DIR", str(tmp_path))
    plans = {vmid: {"disk_bytes": size, "sources": {"scsi0": f"local-lvm:vm-{vmid}-disk-0"},
                   "host": "test", "vm_identity": {}, "session": "42:7", "pools": [{"pool": "pve/data"}],
                   "fleecing_storage": "local-lvm"} for vmid, size in ((101, 1024), (102, 4096))}
    monkeypatch.setattr(native, "preflight", lambda vmid, storage: plans[vmid])
    monkeypatch.setattr(native.os.path, "ismount", lambda *a: True)
    selector = SimpleNamespace(register=lambda *a: None, select=lambda **kw: True)
    monkeypatch.setattr(native.selectors, "DefaultSelector", lambda: nullcontext(selector))

    def launch(command, **kwargs):
        if command[0] == "perl":
            vmid = json.loads(Path(command[-1]).read_text())["vmid"]
            info = {key: value for key, value in plans[vmid].items() if key != "disk_bytes"}
            # VM 102 changed size after preflight; its frozen export is authoritative.
            volumes = {"drive-scsi0": {"disk": "scsi0", "size": 2048 if vmid == 102 else 1024,
                                      "dirty": [0], "bitmap-mode": "new"}}
            messages = [{"event": "query", "info": info, "devices": volumes},
                        {"event": "ready", "volumes": volumes, "config": "name: größe\n", "firewall": ""},
                        {"event": "done"}]
        else:
            plan = json.loads(Path(command[-2]).read_text())
            Path(plan["metrics"]).write_text(json.dumps({"bytes_read": 0}))
            messages = []
        process = SimpleNamespace(pid=123, returncode=None, stdin=io.StringIO(),
                                  stdout=io.StringIO("".join(json.dumps(message) + "\n" for message in messages)))
        process.poll = lambda: process.returncode
        process.wait = lambda **kw: setattr(process, "returncode", 0)
        return process

    def run(command, **kwargs):
        if "prepare" in command:
            request = json.loads(Path(command[-2]).read_text())
            manifest, count = make_manifest(request["vmid"], list(request["volumes"].values()))
            Path(command[-1]).write_text(json.dumps({"manifest": manifest, "sources": list(request["volumes"].values()),
                                                    "bytes_to_read": count}))

    monkeypatch.setattr(native.subprocess, "Popen", launch)
    monkeypatch.setattr(native, "run_process", run)
    restic = handler.agent.resticapi
    restic.cat_config = lambda: {"id": "repo"}
    restic.snapshots = lambda **kw: []
    totals, processed = [], []

    def save(**kwargs):
        plan = json.loads((Path(kwargs["cwd"]).parent / "view.json").read_text())
        size = sum(volume["size"] for volume in plan["manifest"]["volumes"]) + sum(map(len, view_metadata(plan).values()))
        totals.append(report.data["proxmox_bytes_total"])
        processed.append(report.data.get("bytes_processed", 0))
        kwargs["callback"]({"message_type": "status", "bytes_done": size // 2})
        if fail_second and len(totals) == 2:
            raise ResticFailedError("injected partial read")
        summary = {"message_type": "summary", "total_bytes_processed": size, "snapshot_id": f"snap-{len(totals)}"}
        kwargs["callback"](summary)
        return summary  # Processing the summary twice must not double-count it.

    restic.backup = save
    try:
        try:
            handler.run_backup(report)
        except ProxmoxError:
            assert fail_second, report.log
        else:
            assert not fail_second, report.log
        assert len(totals) == 2, report.log
        assert processed == [0, totals[0] - 4096]
        assert totals[1] < totals[0]  # Resize corrected, not hidden behind a fixed estimate.
        assert report.data["proxmox_bytes_total"] == totals[1]
        if fail_second:
            assert processed[1] < report.data["bytes_processed"] < totals[1]
            assert report.data["failed_guests"][0]["vmid"] == 102
            assert report.data["proxmox_progress"]["phase"] == "failed"
        else:
            assert report.data["bytes_processed"] == totals[1]
            assert report.data["completed_guests"] == [101, 102]
        assert all("disk_bytes" not in row["data"].get("info", {}) for row in db["checkpoints"].all())
        assert plans[101]["disk_bytes"] == 1024  # No mutation of preflight/CBT identity.
    finally:
        db.engine.dispose()


@pytest.mark.parametrize("config,expected", [
    ({}, [101, 102, 103]),
    ({"selection_mode": "all", "guest_ids": [101]}, [101, 102, 103]),
    ({"selection_mode": "all", "exclude_guest_ids": [101, 999]}, [102, 103]),
    ({"selection_mode": "include", "guest_ids": [101]}, [101]),
    ({"selection_mode": "include", "guest_ids": [101], "exclude_guest_ids": [101]}, [101]),
])
def test_guest_selection_filters_before_preflight_and_snapshot(job, monkeypatch, config, expected):
    handler, report, _, exports, _ = job
    handler.job["config"] = config
    handler.agent.get_proxmox_client().list_qemu_guests = lambda: [{"vmid": vmid} for vmid in [101, 102, 103]]
    checked = []
    original = backup.snapshot_info

    def info(vmid, *args, **kwargs):
        checked.append(vmid)
        return original(vmid, *args, **kwargs)

    monkeypatch.setattr(backup, "snapshot_info", info)
    handler.run_backup(report)
    assert set(checked) == set(expected)
    assert report.data["guests"] == report.data["completed_guests"] == expected
    assert [export["plan"]["vmid"] for export in exports] == expected


def test_excluding_every_guest_fails_without_starting_a_snapshot(job):
    handler, report, commands, exports, _ = job
    handler.job["config"] = {"selection_mode": "all", "exclude_guest_ids": [101]}
    with pytest.raises(ProxmoxError, match="No supported Proxmox guests matched"):
        handler.run_backup(report)
    assert not commands and not exports and not backup.proxmox_snapshots.count()


def test_pool_filling_during_stream_stops_backup_and_cleans_snapshot(job, monkeypatch):
    handler, report, _, _, snapshots = job
    original = backup.run_process
    checks = 0

    def run(command, **kwargs):
        nonlocal checks
        if command[0] == "lvs":
            checks += 1
            if checks > 1:
                return json.dumps({"report": [{"lv": [{"lv_size": str(100 * 1024**3), "data_percent": "96", "metadata_percent": "10"}]}]})
        return original(command, **kwargs)

    monkeypatch.setattr(backup, "run_process", run)
    with pytest.raises(ProxmoxError):
        handler.run_backup(report)
    assert report.artifacts[0]["state"] == "failed"
    assert not snapshots and not backup.proxmox_snapshots.count()


def test_cleanup_failure_keeps_successful_backup_but_stops_remaining_guests(job, monkeypatch):
    handler, report, _, exports, snapshots = job
    handler.agent.get_proxmox_client().list_qemu_guests = lambda: [{"vmid": 101}, {"vmid": 102}]
    original = backup.run_process

    def run(command, **kwargs):
        if command[:2] == ["qm", "delsnapshot"]:
            raise RuntimeError("busy snapshot")
        return original(command, **kwargs)

    monkeypatch.setattr(backup, "run_process", run)
    with pytest.raises(ProxmoxError):
        handler.run_backup(report)
    assert len(exports) == 1
    assert report.artifacts[0]["state"] == "success"
    assert report.data["partial_failure"] is True
    assert report.data["completed_guests"] == [101]
    assert report.data["failed_guests"] == [{"vmid": 102, "error": "Previous VM snapshot cleanup pending"}]
    assert snapshots and backup.proxmox_snapshots.count() == 1


@pytest.mark.parametrize("error", [ResticFailedError("short read", snapshot_id="partial"), ResticCancelledError("cancelled")])
def test_stream_failure_or_cancellation_removes_snapshot_and_marks_artifact_failed(job, error):
    handler, report, commands, _, snapshots = job

    def fail(*args, **kwargs):
        raise error

    handler.agent.resticapi.backup_stdin_from_command = fail
    with pytest.raises((ProxmoxError, ResticCancelledError)):
        handler.run_backup(report)
    assert report.artifacts[0]["state"] == "failed"
    assert report.artifacts[0]["snapshot_id"] == getattr(error, "snapshot_id", None)
    assert not snapshots and not backup.proxmox_snapshots.count()
    assert commands[-1][1] == "delsnapshot"


def test_unsupported_storage_fails_before_snapshot_or_transfer(job, monkeypatch):
    handler, report, commands, exports, _ = job
    monkeypatch.setattr(backup, "snapshot_info", lambda *a, **kw: (_ for _ in ()).throw(ProxmoxError("requires LVM-thin")))
    with pytest.raises(ProxmoxError, match="LVM-thin"):
        handler.run_backup(report)
    assert not commands and not exports and not backup.proxmox_snapshots.count()


def test_recovery_keeps_foreign_or_locked_snapshots_and_retries_own_snapshot(job, monkeypatch):
    _, _, commands, _, snapshots = job
    row = {"vmid": 101, "snapshot_name": "drastic-test", "owner": "own",
           "host_id": Path("/etc/machine-id").read_text().strip(), "confirmed": True, "created_at": 0}
    backup.proxmox_snapshots.insert(row)
    snapshots["drastic-test"] = "foreign"
    backup.recover_snapshots()
    assert backup.proxmox_snapshots.count() == 1 and not commands
    snapshots["drastic-test"] = "own"
    original = backup.snapshot_info
    monkeypatch.setattr(backup, "snapshot_info", lambda *a, **kw: {"exists": True, "description": "own", "lock": "snapshot"})
    backup.recover_snapshots()
    assert backup.proxmox_snapshots.count() == 1 and not commands
    monkeypatch.setattr(backup, "snapshot_info", original)
    backup.recover_snapshots()
    assert not backup.proxmox_snapshots.count() and not snapshots


def test_unknown_snapshot_creation_is_not_forgotten_early(job, monkeypatch):
    handler, report, _, exports, _ = job

    def fail(*args, **kwargs):
        raise TimeoutError("snapshot timed out")

    monkeypatch.setattr(backup, "run_process", fail)
    monkeypatch.setattr(backup, "check_thin_pools", lambda *a, **kw: None)
    with pytest.raises(ProxmoxError):
        handler.run_backup(report)
    assert not exports and backup.proxmox_snapshots.count() == 1


def test_thin_pool_headroom_is_checked(job, monkeypatch):
    handler, report, _, exports, _ = job
    monkeypatch.setattr(backup, "run_process", lambda *a, **kw: json.dumps({"report": [{"lv": [{"lv_size": str(100 * 1024**3), "data_percent": "96", "metadata_percent": "10"}]}]}))
    with pytest.raises(ProxmoxError, match="4.0 GiB free.*reserve 20 GiB.*data reserve below minimum"):
        handler.run_backup(report)
    assert not exports and not backup.proxmox_snapshots.count()


@pytest.mark.parametrize("reserve,changes,error", [
    (20, {}, None), (21, {}, "data reserve below minimum"),
    (10, {"metadata_percent": "95"}, "metadata usage at or above limit"),
    (20, {"lv_size": None}, "invalid LVM measurements"),
    (20, {"lv_size": "inf"}, "invalid LVM measurements"),
    (20, {"data_percent": "nan"}, "invalid LVM measurements"),
    (20, {"data_percent": "101"}, "invalid LVM measurements"),
    (20, {"metadata_percent": ""}, "invalid LVM measurements"),
])
def test_pool_reserve_metadata_and_invalid_measurements(monkeypatch, reserve, changes, error):
    monkeypatch.setenv("DRASTIC_PROXMOX_MIN_FREE_GIB", str(reserve))
    values = {"lv_size": str(40 * 1024**3), "data_percent": "50", "metadata_percent": "3.55", **changes}
    monkeypatch.setattr(backup, "run_process", lambda *a, **kw: json.dumps({"report": [{"lv": [values]}]}))
    with pytest.raises(ProxmoxError, match=error) if error else nullcontext():
        backup.check_thin_pools({"volumes": [{"pool": "pve/data"}]})


@pytest.mark.parametrize("output", ["not json", "{}", '{"report":[{"lv":[]}]}', '{"report":[{"lv":[{},{}]}]}'])
def test_missing_or_ambiguous_pool_report_is_a_measurement_error(monkeypatch, output):
    monkeypatch.setattr(backup, "run_process", lambda *a, **kw: output)
    with pytest.raises(ProxmoxError, match="missing or invalid LVM measurements"):
        backup.check_thin_pools({"volumes": [{"pool": "pve/data"}]})


def test_tar_is_deterministic_and_rejects_a_short_source(tmp_path):
    disk = tmp_path / "disk.raw"
    disk.write_bytes(os.urandom(4096))
    plan = {"vmid": 101, "config": "memory: 512\n", "volumes": [{"disk": "scsi0", "path": str(disk), "size": 4096}]}
    first, second = io.BytesIO(), io.BytesIO()
    write_archive(plan, first)
    write_archive(plan, second)
    assert first.getvalue() == second.getvalue()
    disk.write_bytes(b"short")
    with pytest.raises(OSError, match="unexpected end"):
        write_archive(plan, io.BytesIO())


def test_real_restic_deduplicates_snapshot_stream_and_restores_exact_disks(tmp_path):
    binary = shutil.which("restic") or str(Path(__file__).parents[1] / "data/bin/restic_0.18.1_linux_amd64")
    if not Path(binary).is_file():
        pytest.skip("Install restic to run the deduplication round trip")
    disk = tmp_path / "disk.raw"
    data = os.urandom(32 * 1024 * 1024)
    disk.write_bytes(data)
    plan = {"vmid": 101, "config": "memory: 512\n", "volumes": [{"disk": "scsi0", "path": str(disk), "size": len(data)}]}
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(json.dumps(plan))
    api = ResticApi(binary, repository=ResticRepository(location=str(tmp_path / "repo"), password="test-only"))
    api.init()

    def save():
        return api.backup_stdin_from_command([sys.executable, "-m", "drastic_agent.proxmox_snapshot", str(plan_path)], stdin_filename="qemu-101.tar")

    first, unchanged = save(), save()
    assert first["data_added"] > len(data)
    assert unchanged["data_added"] < 65536
    with disk.open("r+b") as source:
        source.seek(16 * 1024 * 1024)
        source.write(b"x" * 4096)
    changed = save()
    assert 0 < changed["data_added"] < 8 * 1024 * 1024
    restored = tmp_path / "restored"
    api.restore(changed["snapshot_id"], str(restored), ["/qemu-101.tar"])
    with snapshot_disks(DirectorySnapshot(restored), {"format": "tar", "archive": "qemu-101.tar"}) as disks:
        assert disks["scsi0"].size == len(data)
        assert disks["scsi0"].read_at(0, len(data)) == disk.read_bytes()
    api.check(read_data=True)
