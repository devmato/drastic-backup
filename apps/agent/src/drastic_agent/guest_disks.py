"""Platform-neutral, seekable readers for Proxmox backup disks. No mounts or guest tools."""

import json
import re
import tarfile
from _thread import LockType
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from typing import BinaryIO, Protocol

from drastic_agent.proxmox_blocks import BLOCK_SIZE, DISK_NAME, block_path, validate_manifest


class SnapshotFiles(Protocol):
    """A provider returns seekable binary files; another platform can supply its own provider."""

    def open(self, path: str) -> BinaryIO: ...


class DiskReader(Protocol):
    size: int

    def read_at(self, offset: int, length: int) -> bytes: ...


@dataclass
class DirectorySnapshot:
    root: Path

    def open(self, path):
        # Backup trees are untrusted even when served by an authenticated repository.
        parts = path.split("/")
        if any(part in {"", ".", ".."} for part in parts):
            raise ValueError("Invalid snapshot file path")
        current = self.root
        for part in parts:
            current = current / part
            if current.is_symlink():
                raise ValueError("Snapshot file contains a symbolic link")
        if not current.exists():
            raise FileNotFoundError(f"Missing snapshot file: {path}")
        if not current.is_file():
            raise ValueError(f"Snapshot file is not a regular file: {path}")
        return current.open("rb")


def read_exact(source, offset, length):
    source.seek(offset)
    data = source.read(length)
    if len(data) != length:
        raise OSError("Backup disk data is truncated")
    return data


def read_length(size, offset, length):
    if offset < 0 or length < 0:
        raise ValueError("Invalid disk read range")
    return min(length, max(0, size - offset))


@dataclass
class ArchiveDisk:
    source: BinaryIO
    start: int
    size: int
    lock: LockType

    def read_at(self, offset, length):
        length = read_length(self.size, offset, length)
        if not length:
            return b""
        with self.lock:
            return read_exact(self.source, self.start + offset, length)


@dataclass
class NativeDisk:
    files: SnapshotFiles
    disk: str
    size: int

    def read_at(self, offset, length):
        remaining = read_length(self.size, offset, length)
        result = bytearray()
        while remaining:
            index, within = divmod(offset, BLOCK_SIZE)
            expected = min(BLOCK_SIZE, self.size - index * BLOCK_SIZE)
            count = min(remaining, expected - within)
            with self.files.open(block_path(self.disk, index)) as source:
                if source.seek(0, 2) != expected:
                    raise OSError("Native backup has an invalid disk block")
                result.extend(read_exact(source, within, count))
            offset += count
            remaining -= count
        return bytes(result)


@contextmanager
def snapshot_disks(files: SnapshotFiles, descriptor, *, metadata=None) -> Iterator[dict[str, DiskReader]]:
    """Validate and expose disks for one request, closing shared archive handles on exit."""
    if descriptor["format"] == "native":
        with files.open("manifest.json") as source:
            data = source.read(16 * 1024 * 1024 + 1)
        if len(data) > 16 * 1024 * 1024:
            raise ValueError("Native manifest is too large")
        manifest = json.loads(data)
        volumes = validate_manifest(manifest)
        if manifest["vmid"] != descriptor["vmid"]:
            raise ValueError("Native manifest does not match snapshot VM")
        if metadata is not None:
            for name in ("qemu-server.conf", "qemu-server.fw"):
                try:
                    with files.open(name) as source:
                        data = source.read(65536)
                except FileNotFoundError:
                    if name == "qemu-server.fw":
                        continue
                    raise ValueError("Native backup has no VM configuration") from None
                if len(data) > 65535:
                    raise ValueError("Native VM configuration is too large")
                metadata[name] = data
        yield {v["disk"]: NativeDisk(files, v["disk"], v["size"]) for v in volumes}
        return
    if descriptor["format"] != "tar":
        raise ValueError("Unsupported on-demand disk format")
    archive_name = descriptor["archive"]
    if not re.fullmatch(r"qemu-\d+\.tar", archive_name):
        raise ValueError("Invalid snapshot archive name")
    sizes, disks, manifest = {}, {}, None
    with files.open(archive_name) as source:
        # Restic builds a complete blob-offset index on each open. Share this
        # handle across all disk slices until the request ends, including indexing.
        lock = Lock()
        # r: (not r| or r:*) seeks past disk contents and accepts only uncompressed TAR.
        with tarfile.open(fileobj=source, mode="r:") as archive:
            for member in archive:
                name = member.name
                disk = re.fullmatch(rf"disks/disk-drive-({DISK_NAME})\.raw", name)
                if (not member.isfile() or member.sparse is not None or name in sizes or member.size < 0
                        or (not disk and name not in {"manifest.json", "qemu-server.conf", "qemu-server.fw"})
                        or (not disk and member.size > 65535) or len(sizes) >= 257):
                    raise ValueError("Unsafe or unsupported snapshot archive member")
                sizes[name] = member.size
                if disk:
                    if not member.size or member.size % 512:
                        raise ValueError("Invalid snapshot disk size")
                    disks[disk[1]] = ArchiveDisk(source, member.offset_data, member.size, lock)
                elif name == "manifest.json":
                    with archive.extractfile(member) as config_source:
                        manifest = json.load(config_source)
                elif metadata is not None:
                    with archive.extractfile(member) as config_source:
                        metadata[name] = config_source.read()
        if not {"manifest.json", "qemu-server.conf"}.issubset(sizes):
            raise ValueError("Snapshot archive lacks configuration or manifest")
        if (not isinstance(manifest, dict) or manifest.get("version") != 1
                or type(manifest.get("vmid")) is not int or archive_name != f"qemu-{manifest['vmid']}.tar"):
            raise ValueError("Unsupported snapshot manifest or VM mismatch")
        volumes = manifest.get("volumes")
        if not isinstance(volumes, list) or not 1 <= len(volumes) <= 254:
            raise ValueError("Snapshot manifest has no supported disks")
        expected = {}
        for item in volumes:
            if (not isinstance(item, dict) or not isinstance(item.get("disk"), str)
                    or not re.fullmatch(DISK_NAME, item["disk"]) or item["disk"] in expected
                    or type(item.get("size")) is not int or item["size"] <= 0):
                raise ValueError("Invalid snapshot disk manifest")
            expected[item["disk"]] = item["size"]
        if expected != {disk: reader.size for disk, reader in disks.items()}:
            raise ValueError("Snapshot disks do not match manifest")
        yield disks
