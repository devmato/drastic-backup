"""Native backup layout and read-only, demand-loaded 4-MiB disk block files."""
import errno
import json
import os
import re
import stat
import sys
import time
from pathlib import Path

BLOCK_SIZE = 4 * 1024 * 1024
DISK_NAME = r"(?:ide|sata|scsi|virtio)\d+|efidisk0|tpmstate0"


def block_path(disk, index):
    return f"disks/{disk}/{index // 256:06d}/{index:010d}.raw"


def validate_manifest(manifest):
    if (not isinstance(manifest, dict) or manifest.get("version") != 2
            or manifest.get("block_size") != BLOCK_SIZE or type(manifest.get("vmid")) is not int
            or not 100 <= manifest["vmid"] <= 999999999
            or type(manifest.get("metadata_generation")) is not int or not 1 <= manifest["metadata_generation"] < 2**32):
        raise ValueError("Unsupported native VM manifest")
    volumes = manifest.get("volumes")
    if not isinstance(volumes, list) or not 1 <= len(volumes) <= 254:
        raise ValueError("Native manifest has no supported disks")
    names = set()
    for volume in volumes:
        if (not isinstance(volume, dict) or not isinstance(volume.get("disk"), str)
                or not re.fullmatch(DISK_NAME, volume["disk"]) or volume["disk"] in names
                or type(volume.get("size")) is not int or volume["size"] <= 0 or volume["size"] % 512):
            raise ValueError("Invalid native disk manifest")
        generations = volume.get("generations")
        if (not isinstance(generations, list) or len(generations) != (volume["size"] + BLOCK_SIZE - 1) // BLOCK_SIZE
                or any(type(value) is not int or not 1 <= value < 2**32 for value in generations)):
            raise ValueError("Invalid native block generations")
        names.add(volume["disk"])
    return volumes


def check_manifest_entry(api, snapshot_id):
    entries = api.ls(snapshot_id=snapshot_id, path="/manifest.json")
    entries = [entries] if isinstance(entries, dict) else entries or []
    files = [entry for entry in entries if entry.get("type") == "file" and entry.get("path") == "/manifest.json"]
    if len(files) != 1 or type(files[0].get("size")) is not int or not 0 < files[0]["size"] <= 16 * 1024 * 1024:
        raise ValueError("Native manifest is missing or too large")


def dirty_blocks(handle, size, bitmap):
    """Validate the complete NBD bitmap map; malformed or incomplete maps fail closed."""
    if not bitmap:
        return set(range((size + BLOCK_SIZE - 1) // BLOCK_SIZE))
    result, offset = set(), 0
    while offset < size:
        end = offset

        def receive(context, start, entries, error):
            nonlocal end
            if context != f"qemu:dirty-bitmap:{bitmap}":
                return 0
            if start != end or len(entries) % 2:
                raise ValueError("Unexpected NBD dirty bitmap extent")
            for length, flags in zip(entries[::2], entries[1::2], strict=True):
                if length <= 0 or end + length > size or end % BLOCK_SIZE or flags not in (0, 1):
                    raise ValueError("Invalid NBD dirty bitmap extent")
                if flags & 1:
                    result.update(range(end // BLOCK_SIZE, (end + length + BLOCK_SIZE - 1) // BLOCK_SIZE))
                end += length
            return 0

        handle.block_status(min(size - offset, 2**30), offset, receive)
        if end <= offset:
            raise ValueError("Incomplete NBD dirty bitmap")
        offset = end
    return result


def make_manifest(vmid, volumes, previous=None):
    old = {v["disk"]: v for v in validate_manifest(previous)} if previous else {}
    generation = max((g for v in old.values() for g in v["generations"]), default=1700000000) + 1
    result, changed = [], 0
    for volume in volumes:
        disk, size = volume["disk"], volume["size"]
        prior = old.get(disk)
        reuse = volume.get("bitmap-mode") == "reuse" and prior is not None and prior["size"] == size
        generations = list(prior["generations"]) if reuse else [generation] * ((size + BLOCK_SIZE - 1) // BLOCK_SIZE)
        dirty = volume["dirty"] if reuse else set(range(len(generations)))
        for index in dirty:
            generations[index] = generation
            changed += min(BLOCK_SIZE, size - index * BLOCK_SIZE)
        result.append({"disk": disk, "size": size, "generations": generations})
    manifest = {"version": 2, "vmid": vmid, "block_size": BLOCK_SIZE, "volumes": result,
                "metadata_generation": max(generation, (previous or {}).get("metadata_generation", 0) + 1)}
    validate_manifest(manifest)
    return manifest, changed


def view_metadata(plan):
    files = {"manifest.json": json.dumps(plan["manifest"], separators=(",", ":")).encode(),
             "qemu-server.conf": plan["config"].encode()}
    if plan.get("firewall") is not None:
        files["qemu-server.fw"] = plan["firewall"].encode()
    return files


def mount_view(plan, mountpoint):
    import fuse
    import nbd

    fuse.fuse_python_api = (0, 2)

    class View(fuse.Fuse):
        def __init__(self):
            super().__init__(dash_s_do="setsingle")
            self.volumes = {v["disk"]: v for v in validate_manifest(plan["manifest"])}
            self.handles, self.dirty, self.read_end = {}, {}, {}
            self.metrics = {"bytes_read": 0, "blocks_read": 0}
            self.last_publish = 0
            self.files = view_metadata(plan)
            for source in plan["sources"]:
                handle = nbd.NBD()
                handle.set_export_name(source["device"])
                if source.get("bitmap-name"):
                    handle.add_meta_context(f"qemu:dirty-bitmap:{source['bitmap-name']}")
                handle.connect_unix(source["nbd-path"])
                self.handles[source["disk"]] = handle
                self.dirty[source["disk"]] = set(source["dirty"])

        def resolve(self, path):
            parts = path.strip("/").split("/")
            if path == "/" or path == "/disks":
                return "dir", None, 0
            if path[1:] in self.files:
                return "meta", path[1:], 0
            if len(parts) >= 2 and parts[0] == "disks" and parts[1] in self.volumes:
                disk = parts[1]
                count = len(self.volumes[disk]["generations"])
                if len(parts) == 2:
                    return "dir", disk, 0
                if len(parts) == 3 and parts[2].isdigit() and int(parts[2]) < (count + 255) // 256:
                    return "dir", disk, int(parts[2])
                if len(parts) == 4 and re.fullmatch(r"\d{10}\.raw", parts[3]):
                    index = int(parts[3][:-4])
                    if index < count and parts[2] == f"{index // 256:06d}":
                        return "block", disk, index
            raise FileNotFoundError(path)

        def getattr(self, path):
            try:
                kind, disk, index = self.resolve(path)
            except FileNotFoundError:
                return -errno.ENOENT
            size, stamp = 0, 1
            if kind == "block":
                volume = self.volumes[disk]
                size = min(BLOCK_SIZE, volume["size"] - index * BLOCK_SIZE)
                stamp = volume["generations"][index]
            elif kind == "meta":
                size = len(self.files[disk])
                # Metadata must always be read, even when length is unchanged.
                stamp = plan["manifest"]["metadata_generation"]
            import hashlib

            inode = int.from_bytes(hashlib.sha256(path.encode()).digest()[:7], "big") + 1
            return fuse.Stat(st_mode=(stat.S_IFDIR | 0o500) if kind == "dir" else (stat.S_IFREG | 0o400),
                             st_ino=inode, st_nlink=2 if kind == "dir" else 1, st_size=size,
                             st_mtime=stamp, st_ctime=stamp, st_atime=stamp)

        def readdir(self, path, offset):
            for name in (".", ".."):
                yield fuse.Direntry(name, ino=1, type=stat.S_IFDIR)
            if path == "/":
                entries = [(name, stat.S_IFREG) for name in self.files] + [("disks", stat.S_IFDIR)]
            elif path == "/disks":
                entries = [(name, stat.S_IFDIR) for name in sorted(self.volumes)]
            else:
                kind, disk, bucket = self.resolve(path)
                count = len(self.volumes[disk]["generations"])
                entries = ([(f"{i:06d}", stat.S_IFDIR) for i in range((count + 255) // 256)]
                           if len(path.strip("/").split("/")) == 2 else
                           [(f"{i:010d}.raw", stat.S_IFREG) for i in range(bucket * 256, min((bucket + 1) * 256, count))])
            for name, mode in entries:
                inode = self.getattr(path.rstrip("/") + "/" + name).st_ino
                yield fuse.Direntry(name, ino=inode, type=mode)

        def open(self, path, flags):
            if flags & os.O_ACCMODE != os.O_RDONLY:
                return -errno.EROFS
            return fuse.FuseFileInfo(direct_io=True, keep_cache=False)

        def publish(self, force=False):
            if force or time.monotonic() - self.last_publish >= 1:
                path = Path(plan["metrics"])
                temporary = path.with_suffix(".tmp")
                temporary.write_text(json.dumps(self.metrics))
                temporary.replace(path)
                self.last_publish = time.monotonic()

        def read(self, path, size, offset):
            kind, disk, index = self.resolve(path)
            if kind == "meta":
                return self.files[disk][offset:offset + size]
            length = min(BLOCK_SIZE, self.volumes[disk]["size"] - index * BLOCK_SIZE)
            size = min(size, max(0, length - offset))
            if not size:
                return b""
            if index not in self.dirty[disk]:
                return -errno.EIO  # never substitute zeros for inaccessible clean data
            data = self.handles[disk].pread(size, index * BLOCK_SIZE + offset)
            self.metrics["bytes_read"] += len(data)
            end = self.read_end.get(path, 0)
            if offset <= end:
                self.read_end[path] = max(end, offset + len(data))
            self.publish()
            return data

        def release(self, path, flags):
            kind, disk, index = self.resolve(path)
            if kind == "block" and self.read_end.get(path, 0) == self.getattr(path).st_size:
                try:
                    self.handles[disk].trim(self.getattr(path).st_size, index * BLOCK_SIZE)
                except Exception as exc:
                    self.metrics["error"] = f"Native block discard failed: {exc}"
                    self.publish(force=True)
                    return -errno.EIO
                self.dirty[disk].discard(index)
                self.read_end.pop(path, None)
                self.metrics["blocks_read"] += 1
                self.publish()

    view = View()
    view.parse(["-f", "-s", "-o", "ro,use_ino", mountpoint])
    try:
        view.main()
    finally:
        view.publish(force=True)
        for handle in view.handles.values():
            handle.shutdown()


def prepare_view(request):
    import nbd

    volumes = []
    for device, source in sorted(request["volumes"].items()):
        handle = nbd.NBD()
        handle.set_export_name(device)
        if source.get("bitmap-name"):
            handle.add_meta_context(f"qemu:dirty-bitmap:{source['bitmap-name']}")
        handle.connect_unix(source["nbd-path"])
        try:
            dirty = dirty_blocks(handle, source["size"], source.get("bitmap-name"))
        finally:
            handle.shutdown()
        volumes.append({**source, "device": device, "disk": device.removeprefix("drive-").removesuffix("-backup"),
                        "dirty": dirty})
    manifest, count = make_manifest(request["vmid"], volumes, request.get("previous"))
    return {"manifest": manifest, "sources": [{**v, "dirty": sorted(v["dirty"])} for v in volumes], "bytes_to_read": count}


if __name__ == "__main__":
    if sys.argv[1] == "prepare":
        request = json.loads(Path(sys.argv[2]).read_text())
        Path(sys.argv[3]).write_text(json.dumps(prepare_view(request)))
    else:
        mount_view(json.loads(Path(sys.argv[1]).read_text()), sys.argv[2])
