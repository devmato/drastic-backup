import json

import pytest

import drastic_agent.jobs.proxmox_backup as proxmox_backup_module
from drastic_agent.jobs.proxmox_backup import ProxmoxBackupJobHandler
from drastic_agent.proxmox import QemuVolumeGuestDriver
from drastic_common.restic.exceptions import ResticFailedError


class _FakeReport:
    def __init__(self):
        self.messages = []
        self.data = {}
        self.statuses = []
        self.progress = []

    def set_data(self, key, value):
        self.data[key] = value
        if key == "proxmox_progress":
            self.progress.append(value)

    def log_message(self, message, final_state=None):
        self.messages.append(message)

    def process_job_status(self, status, job_id):
        self.statuses.append({"status": status, "job_id": job_id})


class _FakeResticApi:
    def __init__(self):
        self.backup_stdin_calls = []
        self.backup_stdin_from_command_calls = []

    def backup_stdin_from_command(self, **kwargs):
        self.backup_stdin_from_command_calls.append(kwargs)
        for line in ("INFO: starting backup", "INFO: 25% (16 GiB of 64 GiB) in 1s", "INFO: 100% (64 GiB of 64 GiB) in 2s"):
            kwargs["producer_stderr_callback"](line)
        return {"snapshot_id": "backup-snap", "total_bytes_processed": 20 * 1024**3}

    def backup_stdin(self, **kwargs):
        self.backup_stdin_calls.append(kwargs)
        return {"snapshot_id": "manifest-snap", "total_bytes_processed": 512}


class _FakeAgent:
    def __init__(self):
        self.resticapi = _FakeResticApi()
        self.proxmox_client = _FakeApi()

    def get_proxmox_client(self):
        return self.proxmox_client


class _FakeApi:
    configured = True
    guest_ids = (101,)

    def get_node(self):
        return "pve"

    def list_qemu_guests(self):
        return [{"vmid": vmid, "name": "srv-docker"} for vmid in self.guest_ids]

    def get_qemu_config(self, vmid):
        assert vmid in self.guest_ids
        return {
            "scsi0": f"local-lvm:vm-{vmid}-disk-1,size=64G",
        }


@pytest.mark.parametrize("guest_ids", [(101,), (101, 102)])
def test_proxmox_backup_uses_vzdump_archive_and_manifest(monkeypatch, guest_ids):
    driver = QemuVolumeGuestDriver()
    monkeypatch.setattr(proxmox_backup_module, "ensure_vzdump_available", lambda: None)
    monkeypatch.setattr(proxmox_backup_module, "get_proxmox_guest_driver", lambda: driver)

    handler = ProxmoxBackupJobHandler(
        agent=_FakeAgent(),
        job={"id": 7, "uuid": "job-uuid-7", "config": {"selection_mode": "all", "guest_ids": []}},
        repository_id=1,
    )
    handler.agent.proxmox_client.guest_ids = guest_ids
    artifacts = []
    handler.operation = {"id": 1, "uuid": "operation-uuid-1"}
    handler.start_artifact = lambda artifact_key, data=None: artifacts.append(
        {"id": len(artifacts) + 1, "uuid": f"artifact-{len(artifacts) + 1}", "artifact_key": artifact_key, "data": data or {}}
    ) or artifacts[-1]
    handler.finish_artifact = lambda artifact, **kwargs: artifact.update(kwargs) or artifact
    report = _FakeReport()

    handler.run_backup(report)

    export_calls = handler.agent.resticapi.backup_stdin_from_command_calls
    assert len(export_calls) == len(guest_ids)
    export_call = export_calls[0]
    assert export_call["command"] == ["vzdump", "101", "--mode", "snapshot", "--stdout", "--compress", "0", "--node", "pve"]
    assert export_call["stdin_filename"].startswith("vzdump-qemu-101-")
    assert export_call["stdin_filename"].endswith(".vma")
    assert export_call["tags"] == [
        "job_uuid:job-uuid-7",
        "operation_uuid:operation-uuid-1",
        "source:proxmox",
        "guest_type:qemu",
        "vmid:101",
        "backup_method:vzdump",
        "artifact_uuid:artifact-1",
        "artifact_key:vm:101",
    ]

    manifest_call = handler.agent.resticapi.backup_stdin_calls[0]
    assert manifest_call["stdin_filename"] == "qemu-101-manifest.json"
    manifest = json.loads(manifest_call["stdin_data"])
    assert manifest["backup_method"] == "vzdump"
    assert manifest["archive_format"] == "vma"
    assert manifest["archive_filename"] == export_call["stdin_filename"]
    assert manifest["volumes"] == [
        {
            "disk": "scsi0",
            "volume": "local-lvm:vm-101-disk-1",
            "export_format": "raw+size",
        }
    ]
    assert artifacts[0]["snapshot_id"] == "backup-snap"
    assert artifacts[1]["snapshot_id"] == "manifest-snap"
    assert all("snapshot" not in message.lower() for message in report.messages)
    assert not any("waiting for restic summary" in message for message in report.messages)
    assert report.data["proxmox_progress"]["phase"] == "complete"
    assert report.data["proxmox_progress"]["bytes_total"] == 64 * 1024**3
    assert report.data["proxmox_progress"]["archive_bytes"] == 20 * 1024**3
    assert [(stage["vmid"], stage["guest_index"]) for stage in report.progress if stage["percent_done"] is None] == [(vmid, index) for index, vmid in enumerate(guest_ids, start=1)]
    assert all(stage["guests_total"] == len(guest_ids) for stage in report.progress)
    assert any(stage["phase"] == "finalizing" for stage in report.progress)
    assert report.data["completed_guests"] == list(guest_ids)


@pytest.mark.parametrize(("line", "expected"), [
    ("INFO:  25% (16.0 GiB of 64.0 GiB) in 1m, read: 1 GiB/s", {
        "percent_done": 25, "bytes_processed": 16 * 1024**3, "bytes_total": 64 * 1024**3,
    }),
    ("INFO: 100% (512 B of 512 B) in 1s", {
        "percent_done": 100, "bytes_processed": 512, "bytes_total": 512,
    }),
    ("INFO: 50% (1.5 GB of 3 GB) in 1s", {
        "percent_done": 50, "bytes_processed": 1500000000, "bytes_total": 3000000000,
    }),
    ("INFO: include disk 'scsi0' 'local:disk' 64G", None),
    ("INFO: 1% (0 B of 0 B) in 1s", None),
    ("INFO: 101% (2 GiB of 1 GiB) in 1s", None),
    ("INFO: 25% (1..5 GiB of 64 GiB) in 1s", None),
])
def test_vzdump_progress_parser(line, expected):
    assert QemuVolumeGuestDriver.parse_backup_progress(line) == expected


def test_manifest_failure_keeps_successful_archive_artifact_visible():
    driver = QemuVolumeGuestDriver()
    agent = _FakeAgent()

    def fail_manifest(**kwargs):
        raise RuntimeError("manifest failed")

    agent.resticapi.backup_stdin = fail_manifest
    handler = ProxmoxBackupJobHandler(
        agent=agent,
        job={"id": 7, "uuid": "job-uuid-7", "config": {}},
        repository_id=1,
    )
    handler.operation = {"id": 1, "uuid": "operation-uuid-1"}
    artifacts = []
    handler.start_artifact = lambda artifact_key, data=None: artifacts.append(
        {
            "id": len(artifacts) + 1,
            "uuid": f"artifact-{len(artifacts) + 1}",
            "artifact_key": artifact_key,
            "data": data or {},
        }
    ) or artifacts[-1]
    handler.finish_artifact = lambda artifact, **kwargs: artifact.update(kwargs) or artifact
    report = _FakeReport()

    with pytest.raises(RuntimeError, match="manifest failed"):
        handler._backup_qemu_guest(report, agent.proxmox_client, driver, driver.list_supported_guests(agent.proxmox_client)[0], 1)

    assert artifacts[0]["snapshot_id"] == "backup-snap"
    assert artifacts[1]["state"].name == "failed"
    assert report.data["partial_failure"] is True
    assert report.data["proxmox_progress"]["phase"] == "failed"


def test_failed_archive_artifact_keeps_snapshot_id():
    driver = QemuVolumeGuestDriver()
    agent = _FakeAgent()

    def fail_archive(**kwargs):
        raise ResticFailedError("producer failed", snapshot_id="partial-snap")

    agent.resticapi.backup_stdin_from_command = fail_archive
    handler = ProxmoxBackupJobHandler(
        agent=agent,
        job={"id": 7, "uuid": "job-uuid-7", "config": {}},
        repository_id=1,
    )
    handler.operation = {"id": 1, "uuid": "operation-uuid-1"}
    artifacts = []
    handler.start_artifact = lambda artifact_key, data=None: artifacts.append(
        {"uuid": "artifact-1", "artifact_key": artifact_key, "data": data or {}}
    ) or artifacts[-1]
    handler.finish_artifact = lambda artifact, **kwargs: artifact.update(kwargs) or artifact

    with pytest.raises(ResticFailedError, match="producer failed"):
        handler._backup_qemu_guest(
            _FakeReport(), agent.proxmox_client, driver, driver.list_supported_guests(agent.proxmox_client)[0], 1
        )

    assert artifacts[0]["state"].name == "failed"
    assert artifacts[0]["snapshot_id"] == "partial-snap"
