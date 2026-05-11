import json

import drastic_agent.jobs.proxmox_backup as proxmox_backup_module
from drastic_agent.jobs.proxmox_backup import ProxmoxBackupJobHandler


class _FakeReport:
    def __init__(self):
        self.messages = []
        self.data = {}
        self.statuses = []

    def set_data(self, key, value):
        self.data[key] = value

    def log_message(self, message, final_state=None):
        self.messages.append(message)

    def process_job_status(self, status, job_id):
        self.statuses.append({"status": status, "job_id": job_id})


class _FakeResticApi:
    def __init__(self):
        self.backup_stdin_calls = []

    def backup_stdin(self, **kwargs):
        self.backup_stdin_calls.append(kwargs)
        return {"snapshot_id": "manifest-snap"}


class _FakeAgent:
    def __init__(self):
        self.resticapi = _FakeResticApi()


class _FakeApi:
    configured = True

    def get_qemu_config(self, vmid):
        assert vmid == 101
        return {
            "scsi0": "local-lvm:vm-101-disk-1,size=64G",
        }


class _FakeDriver:
    def __init__(self):
        self.export_calls = []

    def list_supported_guests(self, api):
        return [{"vmid": 101, "name": "srv-docker", "node": "pve", "type": "qemu"}]

    def get_guest_backup_plan(self, config):
        assert "scsi0" in config
        return {
            "volumes": [
                {
                    "disk": "scsi0",
                    "volume": "local-lvm:vm-101-disk-1",
                    "export_format": "raw+size",
                }
            ]
        }

    def export_qemu_backup_to_restic(self, **kwargs):
        self.export_calls.append(kwargs)
        return {"snapshot_id": "backup-snap"}


def test_proxmox_backup_uses_vzdump_archive_and_manifest(monkeypatch):
    fake_driver = _FakeDriver()
    monkeypatch.setattr(proxmox_backup_module, "ensure_vzdump_available", lambda: None)
    monkeypatch.setattr(proxmox_backup_module, "ProxmoxApiClient", lambda: _FakeApi())
    monkeypatch.setattr(proxmox_backup_module, "get_proxmox_guest_driver", lambda: fake_driver)

    handler = ProxmoxBackupJobHandler(
        agent=_FakeAgent(),
        job={"id": 7, "uuid": "job-uuid-7", "config": {"selection_mode": "all", "guest_ids": []}},
        repository_id=1,
    )
    artifacts = []
    handler.operation = {"id": 1, "uuid": "operation-uuid-1"}
    handler.start_artifact = lambda artifact_key, data=None: artifacts.append(
        {"id": len(artifacts) + 1, "uuid": f"artifact-{len(artifacts) + 1}", "artifact_key": artifact_key, "data": data or {}}
    ) or artifacts[-1]
    handler.finish_artifact = lambda artifact, **kwargs: artifact.update(kwargs) or artifact
    report = _FakeReport()

    handler.run_backup(report)

    assert len(fake_driver.export_calls) == 1
    export_call = fake_driver.export_calls[0]
    assert export_call["vmid"] == 101
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
