"""Opt-in round trips on a Proxmox test host; see docs/backup-and-restore.md."""

import hashlib
import os
import re
import shutil
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from drastic_agent.services.proxmox_restore import cleanup_workspaces, session_action
from drastic_agent.services.restore import RestoreService
from drastic_common.process import run_process
from drastic_common.restic.client import ResticApi
from drastic_common.restic.repository import ResticRepository


@pytest.fixture
def roundtrip(tmp_path, monkeypatch):
    archive = os.environ.get("DRASTIC_TEST_VMA")
    if not archive:
        pytest.skip("Set DRASTIC_TEST_VMA to a Linux or Windows test VM archive")
    assert Path(archive).is_file()
    for tool in ("restic", "vma", "qmrestore", "pvesh"):
        assert shutil.which(tool), f"Missing {tool} on the test host"
    monkeypatch.setenv("DRASTIC_RESTORE_WORK_DIR", str(tmp_path / "work"))
    api = ResticApi(shutil.which("restic"), repository=ResticRepository(
        location=str(tmp_path / "repository"), password="integration-test-only"))
    api.init()
    job_uuid = str(uuid4())
    tags = [f"job_uuid:{job_uuid}", "source:proxmox", "guest_type:qemu", "backup_method:vzdump"]
    api.backup_stdin_from_command(["/bin/cat", archive],
                                 stdin_filename="vzdump-qemu-100-2026_10_02-00_00_00.vma", tags=tags)
    snapshot_id = api.snapshots(tags=tags)[0]["id"]
    agent = SimpleNamespace(resticapi=api, configure_repository=lambda _: None)
    source = dict(job_id=1, job_uuid=job_uuid, repository_id=1, repository={},
                  snapshot_id=snapshot_id, expected_job_tag=f"job_uuid:{job_uuid}")

    def restore(**kwargs):
        result = RestoreService.run_restore(agent, operation_uuid=str(uuid4()), **source, **kwargs)
        assert result.final_state.name == "success", result.log
        return result

    yield restore, source, tmp_path
    cleanup_workspaces(all_workspaces=True)


def test_real_guest_file_roundtrip(roundtrip):
    restore, source, target = roundtrip
    volume = os.environ.get("DRASTIC_TEST_GUEST_VOLUME")
    path = os.environ.get("DRASTIC_TEST_GUEST_FILE")
    checksum = os.environ.get("DRASTIC_TEST_GUEST_SHA256")
    if not all((volume, path, checksum)):
        pytest.skip("Set DRASTIC_TEST_GUEST_VOLUME, DRASTIC_TEST_GUEST_FILE and DRASTIC_TEST_GUEST_SHA256")
    prepared = restore(mode="proxmox_prepare")
    assert any(v["device"] == volume and not v["error"] for v in prepared.data["volumes"])
    identity = {k: source[k] for k in ("job_uuid", "repository_id", "snapshot_id")}
    entries = session_action("entries", prepared.uuid, identity, volume=volume, path=str(Path(path).parent))
    assert any(entry["path"] == path for entry in entries["entries"])
    restore(mode="proxmox_files", session_id=prepared.uuid, volume=volume,
            include_paths=[path], restore_location=str(target / "files"))
    with (target / "files" / path.lstrip("/")).open("rb") as file:
        assert hashlib.file_digest(file, "sha256").hexdigest() == checksum.lower()
    assert not list((target / "work").iterdir())


def test_real_vm_roundtrip(roundtrip):
    restore, _, target = roundtrip
    vmid = os.environ.get("DRASTIC_TEST_RESTORE_VMID")
    storage = os.environ.get("DRASTIC_TEST_RESTORE_STORAGE")
    if not vmid or not storage:
        pytest.skip("Set a free DRASTIC_TEST_RESTORE_VMID and DRASTIC_TEST_RESTORE_STORAGE")
    restore(mode="proxmox_vm", vmid=int(vmid), storage=storage, unique=True)
    assert "stopped" in run_process(["qm", "status", vmid], timeout=30)
    assert re.search(r"^(?:ide|sata|scsi|virtio)\d+:", run_process(["qm", "config", vmid], timeout=30), re.MULTILINE)
    assert not list((target / "work").iterdir())
    # Keep the stopped VM for manual boot verification; never destroy VM data in a test.
