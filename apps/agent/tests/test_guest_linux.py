"""Real Restic/FUSE reads without Proxmox or libguestfs; skip if FUSE is unavailable."""

import errno
import json
import os
import shutil
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

from drastic_agent.proxmox_blocks import BLOCK_SIZE, block_path
from drastic_agent.proxmox_snapshot import write_archive
from drastic_agent.services.guest_linux import LinuxGuestFileBackend
from drastic_common.process import ProcessCancelledError, is_mounted, mounted_process
from drastic_common.restic.client import ResticApi
from drastic_common.restic.repository import ResticRepository


@pytest.mark.parametrize("layout", ["tar", "native"])
def test_real_on_demand_disk_view_and_request_cleanup(tmp_path, monkeypatch, layout):
    restic = shutil.which("restic") or str(Path(sys.executable).with_name("restic"))
    if (sys.platform != "linux" or not Path("/dev/fuse").exists() or not shutil.which("fusermount3")
            or not Path(restic).is_file()):
        pytest.skip("Requires Linux, Restic and FUSE")
    if subprocess.run(["/usr/bin/python3", "-c", "import fuse"], capture_output=True).returncode:
        pytest.skip("Requires the system python3-fuse binding")
    api = ResticApi(restic, repository=ResticRepository(location=str(tmp_path / "repo"), password="test-only"))
    api.init()
    source = tmp_path / "source"
    source.mkdir()
    # Non-zero disk content; sparse-zero handling cannot make this check pass.
    data = b"a" * BLOCK_SIZE + b"b" * 512
    descriptor = {"format": layout, "vmid": 101}
    if layout == "tar":
        raw = tmp_path / "disk.raw"
        raw.write_bytes(data)
        with (source / "qemu-101.tar").open("wb") as output:
            write_archive({"vmid": 101, "config": "memory: 512\n", "volumes": [
                {"disk": "scsi0", "size": len(data), "path": str(raw)}]}, output)
        descriptor["archive"] = "qemu-101.tar"
    else:
        (source / "manifest.json").write_text(json.dumps({
            "version": 2, "vmid": 101, "metadata_generation": 1, "block_size": BLOCK_SIZE,
            "volumes": [{"disk": "scsi0", "size": len(data), "generations": [1, 1]}]}))
        for index, chunk in enumerate((data[:BLOCK_SIZE], data[BLOCK_SIZE:])):
            path = source / block_path("scsi0", index)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(chunk)
    descriptor["snapshot_id"] = api.backup(["."], cwd=str(source))["snapshot_id"]
    backend = LinuxGuestFileBackend()
    monkeypatch.setattr(backend, "check_available", lambda: None)
    work = tmp_path / "work"
    work.mkdir()
    helpers = []
    original_popen = subprocess.Popen

    def spawn(command, **kwargs):
        process = original_popen(command, **kwargs)
        if len(command) > 1 and Path(command[1]).name == "guest_linux.py":
            helpers.append(process)
        return process

    monkeypatch.setattr(subprocess, "Popen", spawn)

    def read_disk(path, action, **kwargs):
        assert os.path.ismount(path / "repository")
        assert os.path.ismount(path / "disks")
        disk = path / "disks/disk-drive-scsi0.raw"
        assert disk.stat().st_size == len(data)
        with disk.open("rb") as file:
            file.seek(BLOCK_SIZE - 3)
            assert file.read(7) == b"aaabbbb"
            file.seek(len(data) - 2)
            assert file.read(9) == b"bb"
        with pytest.raises(OSError) as error:
            disk.open("wb")
        assert error.value.errno == errno.EROFS
        if action == "cancel":
            raise ProcessCancelledError("cancelled")
        if action == "crash":
            helpers[-1].kill()
            helpers[-1].wait()
            with pytest.raises(OSError) as error:
                (path / "disks/uncached-after-crash").stat()
            assert error.value.errno == errno.ENOTCONN
            # Root attributes may still be in the kernel's one-second FUSE cache.
            for _ in range(30):
                if not os.path.ismount(path / "disks"):
                    break
                time.sleep(0.1)
            assert not os.path.ismount(path / "disks")
            assert is_mounted(path / "disks")
            raise RuntimeError("Disk helper crashed")
        return {"entries": []}

    monkeypatch.setattr(backend, "local_request", read_disk)
    assert backend.request(api, descriptor, work, "entries", cancelled=lambda: False) == {"entries": []}
    assert not list(work.iterdir())
    assert not ResticApi.diagnostic_processes()
    with pytest.raises(ProcessCancelledError):
        backend.request(api, descriptor, work, "cancel", cancelled=lambda: False)
    assert not list(work.iterdir())
    assert not ResticApi.diagnostic_processes()
    with pytest.raises(RuntimeError, match="Disk helper crashed"):
        backend.request(api, descriptor, work, "crash", cancelled=lambda: False)
    assert not list(work.iterdir())
    assert not ResticApi.diagnostic_processes()
    assert all(helper.poll() is not None for helper in helpers)


@pytest.mark.parametrize("failure", ["timeout", "cancel"])
@pytest.mark.skipif(sys.platform != "linux" or not Path("/usr/bin/python3").exists(), reason="Linux disk helper")
def test_blocked_snapshot_lookup_is_supervised_and_cleaned(tmp_path, monkeypatch, failure):
    """Block metadata open in the real disk helper before it mounts; the parent must stay responsive."""
    marker = tmp_path / "lookup-started"
    helpers, closed = [], []
    original_popen = subprocess.Popen

    def spawn(command, **kwargs):
        helper, plan, mountpoint = command[1:]
        program = (
            "import sys, time, runpy\n"
            "from pathlib import Path\n"
            f"sys.path.insert(0, {str(Path(helper).parents[2])!r})\n"
            "from drastic_agent.guest_disks import DirectorySnapshot\n"
            "def blocked_open(self, path):\n"
            f" Path({str(marker)!r}).touch()\n"
            " time.sleep(60)\n"
            "DirectorySnapshot.open = blocked_open\n"
            f"sys.argv = {[helper, plan, mountpoint]!r}\n"
            f"runpy.run_path({helper!r}, run_name='__main__')\n"
        )
        process = original_popen([command[0], "-c", program], **kwargs)
        helpers.append(process)
        return process

    @contextmanager
    def mount(*args, **kwargs):
        try:
            yield tmp_path / "snapshot"
        finally:
            closed.append(True)

    def short_mount(command, mountpoint, **kwargs):
        return mounted_process(command, mountpoint, **{**kwargs, "timeout": 1})

    monkeypatch.setattr(subprocess, "Popen", spawn)
    monkeypatch.setattr("drastic_common.process.mounted_process", short_mount)
    backend = LinuxGuestFileBackend()
    monkeypatch.setattr(backend, "check_available", lambda: None)
    work = tmp_path / "work"
    work.mkdir()
    error = TimeoutError if failure == "timeout" else ProcessCancelledError
    with pytest.raises(error):
        backend.request(SimpleNamespace(mount=mount), {"format": "native", "vmid": 101, "snapshot_id": "snapshot"},
                        work, "entries", cancelled=lambda: failure == "cancel" and marker.exists())
    assert marker.exists()
    assert closed == [True]
    assert len(helpers) == 1 and helpers[0].poll() is not None
    assert not list(work.iterdir())
