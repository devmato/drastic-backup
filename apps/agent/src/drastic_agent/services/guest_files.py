"""Standalone libguestfs worker, executed by the host's /usr/bin/python3.

The system python3-guestfs package supplies the native binding. Guest filesystems
run in a read-only appliance, never in the host kernel or a persistent FUSE mount.
"""

import json
import os
import posixpath
import stat
import sys
from contextlib import contextmanager
from uuid import uuid4


def guest_path(value):
    if not isinstance(value, str) or not value.startswith("/") or "\x00" in value or ".." in value.split("/"):
        raise ValueError("Invalid guest path")
    return posixpath.normpath("/" + value.lstrip("/"))


def no_guest_symlink_parents(guest, path):
    parent = posixpath.dirname(path)
    while parent != "/":
        if not stat.S_ISDIR(guest.lstatns(parent)["st_mode"]):
            raise ValueError(f"Guest path traverses a non-directory: {parent}")
        parent = posixpath.dirname(parent)


@contextmanager
def directory_fd(path, parent_fd=None):
    """Walk host directories using dirfds so symlink swaps cannot escape the target."""
    fd = os.dup(parent_fd) if parent_fd is not None else os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in guest_path("/" + path.lstrip("/")).strip("/").split("/"):
            if not part:
                continue
            try:
                os.mkdir(part, mode=0o700, dir_fd=fd)
            except FileExistsError:
                pass
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        yield fd
    finally:
        os.close(fd)


def export_files(guest, request):
    paths = sorted(set(guest_path(p) for p in request["paths"]), key=lambda p: (len(p), p))
    paths = [p for i, p in enumerate(paths) if not any(p.startswith(q.rstrip("/") + "/") for q in paths[:i])]
    overwrite = request.get("overwrite_policy", "fail_if_exists")
    if overwrite not in ("fail_if_exists", "overwrite") or not paths:
        raise ValueError("Invalid file export selection")
    target = guest_path(request["target"])
    if target == "/":
        raise ValueError("Restoring to / is not allowed")
    counts = {"files": 0, "bytes": 0, "skipped_special_files": 0}

    def copy(path, parent_fd, name):
        info = guest.lstatns(path)
        mode = info["st_mode"]
        if stat.S_ISDIR(mode):
            with directory_fd("/" + name, parent_fd) as child_fd:
                for child in guest.ls(path):
                    if child in (".", "..") or "/" in child:
                        raise ValueError("Invalid guest directory entry")
                    copy(posixpath.join(path, child), child_fd, child)
            return
        if not (stat.S_ISREG(mode) or stat.S_ISLNK(mode)):
            counts["skipped_special_files"] += 1
            return
        temporary = f".drastic-{uuid4()}"
        try:
            if stat.S_ISLNK(mode):
                os.symlink(guest.readlink(path), temporary, dir_fd=parent_fd)
            else:
                fd = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW,
                             0o600, dir_fd=parent_fd)
                try:
                    guest.download(path, f"/proc/self/fd/{fd}")
                    os.fchmod(fd, mode & 0o777)
                    os.utime(fd, ns=(info["st_atime_sec"] * 10**9 + info["st_atime_nsec"],
                                     info["st_mtime_sec"] * 10**9 + info["st_mtime_nsec"]))
                finally:
                    os.close(fd)
                counts["bytes"] += info["st_size"]
            if overwrite == "overwrite":
                os.replace(temporary, name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
            else:
                os.link(temporary, name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd, follow_symlinks=False)
            counts["files"] += 1
        finally:
            try:
                os.unlink(temporary, dir_fd=parent_fd)
            except FileNotFoundError:
                pass

    with directory_fd(target) as root_fd:
        if paths == ["/"]:
            paths = ["/" + name for name in guest.ls("/")]
        for path in paths:
            no_guest_symlink_parents(guest, path)
            guest.lstatns(path)
            with directory_fd(posixpath.dirname(path), root_fd) as fd:
                if overwrite == "fail_if_exists":
                    try:
                        os.stat(posixpath.basename(path), dir_fd=fd, follow_symlinks=False)
                    except FileNotFoundError:
                        pass
                    else:
                        raise ValueError(f"Restore destination already exists: {path}")
        for path in paths:
            with directory_fd(posixpath.dirname(path), root_fd) as fd:
                copy(path, fd, posixpath.basename(path))
        return counts


def main(request):
    import guestfs

    guest = guestfs.GuestFS(python_return_dict=True)
    try:
        guest.set_backend("direct")
        guest.set_network(False)
        for disk in request["disks"]:
            guest.add_drive_opts(disk, readonly=True, format="raw")
        guest.launch()
        filesystems = guest.list_filesystems()
        supported = {"ext2", "ext3", "ext4", "xfs", "btrfs", "ntfs", "vfat", "exfat"}
        if request["action"] == "volumes":
            volumes = []
            for device, filesystem in filesystems.items():
                error = None
                if filesystem not in supported:
                    error = f"Unsupported or encrypted filesystem: {filesystem or 'unknown'}"
                else:
                    try:
                        guest.mount_ro(device, "/")
                        guest.umount_all()
                    except RuntimeError as exc:
                        error = str(exc)
                volumes.append({"device": device, "filesystem": filesystem, "error": error})
            return {"volumes": volumes}
        device = request["volume"]
        if filesystems.get(device) not in supported:
            raise ValueError("Unknown, unsupported or encrypted volume")
        guest.mount_ro(device, "/")
        if request["action"] == "entries":
            path = guest_path(request.get("path", "/"))
            no_guest_symlink_parents(guest, path)
            if not stat.S_ISDIR(guest.lstatns(path)["st_mode"]):
                raise ValueError("Guest path is not a directory")
            names = guest.ls(path)
            if len(names) > 10000:
                raise ValueError("Directory has more than 10000 entries; enter a more specific path")
            entries = []
            for name, info in zip(names, guest.lstatnslist(path, names), strict=True):
                mode = info["st_mode"]
                entries.append({"name": name, "path": posixpath.join(path, name),
                                "type": "dir" if stat.S_ISDIR(mode) else "file",
                                "size": info["st_size"],
                                "readable": stat.S_ISDIR(mode) or stat.S_ISREG(mode) or stat.S_ISLNK(mode)})
            return {"entries": entries}
        if request["action"] == "export":
            return export_files(guest, request)
        raise ValueError("Unknown guest file action")
    finally:
        guest.close()


if __name__ == "__main__":
    try:
        print(json.dumps(main(json.load(sys.stdin))))
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
