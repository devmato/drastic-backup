import io
import json
import tarfile
from types import SimpleNamespace

import pytest

from drastic_agent.guest_disks import DirectorySnapshot, snapshot_disks
from drastic_agent.proxmox_blocks import BLOCK_SIZE, block_path
from drastic_agent.proxmox_snapshot import write_archive


def test_large_tar_is_indexed_by_seeking_and_reads_only_requested_bytes(tmp_path):
    size = 1024**4  # A 1-TiB logical disk with a tiny physical test archive.
    path = tmp_path / "qemu-101.tar"
    member = tarfile.TarInfo("disks/disk-drive-scsi0.raw")
    member.size = size
    header = member.tobuf(format=tarfile.PAX_FORMAT)
    with path.open("wb") as output:
        output.write(header)
        output.write(b"first")
        output.seek(len(header) + size - 4)
        output.write(b"last")
        with tarfile.open(fileobj=output, mode="w") as archive:
            manifest = {"version": 1, "vmid": 101, "volumes": [{"disk": "scsi0", "size": size}]}
            for name, data in [("qemu-server.conf", b"memory: 512\n"), ("manifest.json", json.dumps(manifest).encode())]:
                info = tarfile.TarInfo(name)
                info.size = len(data)
                archive.addfile(info, io.BytesIO(data))

    class CountingFiles:
        def __init__(self):
            self.bytes_read = 0
            self.handles = []

        def open(self, name):
            owner = self

            class Reader(io.BufferedReader):
                def read(self, length=-1):
                    data = super().read(length)
                    owner.bytes_read += len(data)
                    return data

            reader = Reader(io.FileIO(tmp_path / name, "rb"))
            self.handles.append(reader)
            return reader

    files = CountingFiles()
    with snapshot_disks(files, {"format": "tar", "archive": path.name}) as disks:
        disk = disks["scsi0"]
        assert disk.size == size
        for _ in range(10):
            assert disk.read_at(0, 5) == b"first"
            assert disk.read_at(size - 4, 100) == b"last"
        assert disk.read_at(size, 10) == b""
        assert files.bytes_read < 64 * 1024
        assert path.stat().st_blocks * 512 < 64 * 1024
        assert len(files.handles) == 1 and not files.handles[0].closed
        with pytest.raises(ValueError, match="range"):
            disk.read_at(-1, 1)
        # Remote truncation must fail, never silently produce a partial/zero-filled disk.
        with path.open("r+b") as output:
            output.truncate(len(header) + 2)
        with pytest.raises(OSError, match="truncated"):
            disk.read_at(0, 5)
    assert files.handles[0].closed


def test_tar_disks_share_one_handle_and_close_it_on_request_failure(tmp_path):
    volumes = []
    for disk, data in (("scsi0", b"a" * 512), ("scsi1", b"b" * 512)):
        path = tmp_path / disk
        path.write_bytes(data)
        volumes.append({"disk": disk, "path": str(path), "size": len(data)})
    archive = io.BytesIO()
    write_archive({"vmid": 101, "config": "memory: 512\n", "volumes": volumes}, archive)
    archive.seek(0)
    opened = []

    def open_file(path):
        opened.append(path)
        return archive

    with pytest.raises(RuntimeError, match="cancelled"), snapshot_disks(
        SimpleNamespace(open=open_file), {"format": "tar", "archive": "qemu-101.tar"},
    ) as disks:
        for _ in range(10):
            assert disks["scsi0"].read_at(510, 5) == b"aa"
            assert disks["scsi1"].read_at(0, 3) == b"bbb"
        assert opened == ["qemu-101.tar"]
        raise RuntimeError("cancelled")
    assert archive.closed


def test_native_cross_block_reads_and_inaccessible_data_fail_closed(tmp_path):
    manifest = {"version": 2, "vmid": 101, "metadata_generation": 1,
                "block_size": BLOCK_SIZE, "volumes": [
                    {"disk": "scsi0", "size": BLOCK_SIZE + 512, "generations": [1, 2]}]}
    (tmp_path / "manifest.json").write_text(json.dumps(manifest))
    for index, data in enumerate((b"a" * BLOCK_SIZE, b"b" * 512)):
        path = tmp_path / block_path("scsi0", index)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    files = DirectorySnapshot(tmp_path)
    with snapshot_disks(files, {"format": "native", "vmid": 101}) as disks:
        disk = disks["scsi0"]
        assert disk.read_at(BLOCK_SIZE - 3, 7) == b"aaabbbb"
        assert disk.read_at(BLOCK_SIZE + 510, 9) == b"bb"
        second = tmp_path / block_path("scsi0", 1)
        second.write_bytes(b"short")
        with pytest.raises(OSError, match="invalid disk block"):
            disk.read_at(BLOCK_SIZE, 1)
        second.unlink()
        with pytest.raises(FileNotFoundError, match="Missing"):
            disk.read_at(BLOCK_SIZE, 1)
        second.symlink_to(tmp_path / "manifest.json")
        with pytest.raises(ValueError, match="symbolic link"):
            disk.read_at(BLOCK_SIZE, 1)
    with pytest.raises(ValueError, match="VM"), snapshot_disks(files, {"format": "native", "vmid": 102}):
        pass


@pytest.mark.parametrize("name,kind", [("../escape", tarfile.REGTYPE),
                                      ("qemu-server.conf", tarfile.SYMTYPE),
                                      ("disks/disk-drive-scsi0.raw", tarfile.BLKTYPE)])
def test_tar_mapping_rejects_unsafe_members(tmp_path, name, kind):
    path = tmp_path / "qemu-101.tar"
    with tarfile.open(path, "w") as archive:
        member = tarfile.TarInfo(name)
        member.type = kind
        archive.addfile(member)
    with pytest.raises(ValueError, match="Unsafe"), snapshot_disks(DirectorySnapshot(tmp_path), {"format": "tar", "archive": path.name}):
        pass


def test_tar_manifest_must_match_disks(tmp_path):
    path = tmp_path / "qemu-101.tar"
    with tarfile.open(path, "w") as archive:
        manifest = {"version": 1, "vmid": 101, "volumes": [{"disk": "scsi0", "size": 512}]}
        for name, data in [("qemu-server.conf", b""), ("manifest.json", json.dumps(manifest).encode())]:
            info = tarfile.TarInfo(name)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
    with pytest.raises(ValueError, match="do not match"), snapshot_disks(DirectorySnapshot(tmp_path), {"format": "tar", "archive": path.name}):
        pass


def test_native_import_metadata_is_bounded_optional_firewall_and_not_symlinks(tmp_path):
    manifest = {"version": 2, "vmid": 101, "metadata_generation": 1, "block_size": BLOCK_SIZE,
                "volumes": [{"disk": "scsi0", "size": 512, "generations": [1]}]}
    (tmp_path / "manifest.json").write_text(json.dumps(manifest))
    files = DirectorySnapshot(tmp_path)
    descriptor = {"format": "native", "vmid": 101}
    with pytest.raises(ValueError, match="no VM configuration"), snapshot_disks(files, descriptor, metadata={}):
        pass
    (tmp_path / "qemu-server.conf").write_bytes(b"x" * 65536)
    with pytest.raises(ValueError, match="too large"), snapshot_disks(files, descriptor, metadata={}):
        pass
    (tmp_path / "qemu-server.conf").write_text("memory: 512\n")
    metadata = {}
    with snapshot_disks(files, descriptor, metadata=metadata):
        assert metadata == {"qemu-server.conf": b"memory: 512\n"}
    (tmp_path / "qemu-server.fw").symlink_to(tmp_path / "qemu-server.conf")
    with pytest.raises(ValueError, match="symbolic link"), snapshot_disks(files, descriptor, metadata={}):
        pass
