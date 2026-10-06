"""Linux read-only disk presentation and isolated libguestfs worker."""

import errno
import json
import os
import re
import shutil
import stat
import sys
import time
from pathlib import Path

# Executed by system Python for its FUSE binding, outside the agent virtualenv.
if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from drastic_agent.guest_disks import DirectorySnapshot, snapshot_disks


class LinuxGuestFileBackend:
    def lock_workspace(self, fd):
        import fcntl

        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)

    def cleanup_workspace(self, path):
        from drastic_common.process import unmount

        for name in ("disks", "repository"):
            unmount(path / name)

    def check_available(self):
        from drastic_common.process import run_process

        if not shutil.which("fusermount3") or not Path("/dev/fuse").exists():
            raise ValueError("Guest file restore requires FUSE and fusermount3 on the Linux agent host")
        try:
            run_process(["/usr/bin/python3", "-c", "import guestfs, fuse"], timeout=15)
        except (OSError, RuntimeError, TimeoutError) as exc:
            raise ValueError("Guest file restore requires python3-guestfs, libguestfs-tools and python3-fuse on the agent host") from exc

    def request(self, resticapi, descriptor, work, action, *, cancelled, **kwargs):
        from drastic_common.process import is_mounted, mounted_process

        if descriptor is None:
            return self.local_request(work, action, cancelled=cancelled, **kwargs)

        from drastic_agent.config import env_int

        # Browsing is a synchronous command with a 120-second backend response budget.
        deadline = time.monotonic() + (110 if action == "entries" else env_int("DRASTIC_RESTIC_TIMEOUT_SECONDS", 86400))
        self.check_available()

        def remaining():
            value = deadline - time.monotonic()
            if value <= 0:
                raise TimeoutError("Guest file request timed out")
            return value

        helper = Path(__file__)
        mountpoint = work / "disks"
        plan = work / "disk-view.json"
        try:
            with resticapi.mount(descriptor["snapshot_id"], work / "repository", cancelled=cancelled,
                                 timeout=min(120, remaining())) as root:
                plan.write_text(json.dumps({"root": str(root), "descriptor": descriptor}))
                mountpoint.mkdir(mode=0o700)
                with mounted_process(["/usr/bin/python3", str(helper), str(plan), str(mountpoint)],
                                     mountpoint, cancelled=cancelled, timeout=min(120, remaining())):
                    return self.local_request(work, action, cancelled=cancelled, timeout=remaining(), **kwargs)
        finally:
            plan.unlink(missing_ok=True)
            if not is_mounted(mountpoint) and mountpoint.exists():
                mountpoint.rmdir()

    def local_request(self, work, action, *, cancelled, timeout=None, **kwargs):
        from drastic_agent.config import env_int
        from drastic_common.process import run_process

        disks = sorted(str(p) for p in (work / "disks").glob("disk-*.raw")
                       if re.fullmatch(r"disk-drive-(?:ide|sata|scsi|virtio)\d+\.raw", p.name)
                       and p.is_file() and not p.is_symlink())
        if not disks:
            raise ValueError("Backup contains no supported guest disks")
        result = run_process(["/usr/bin/python3", str(Path(__file__).with_name("guest_files.py"))],
                             cancelled=cancelled, input_text=json.dumps({"action": action, "disks": disks, **kwargs}),
                             timeout=timeout if timeout is not None else (90 if action == "entries" else env_int("DRASTIC_RESTIC_TIMEOUT_SECONDS", 86400)))
        return json.loads(result)


def mount_disks(plan, mountpoint):
    with snapshot_disks(DirectorySnapshot(Path(plan["root"])), plan["descriptor"]) as disks:
        serve_disks(disks, mountpoint)


def serve_disks(disks, mountpoint):
    import fuse

    fuse.fuse_python_api = (0, 2)
    readers = {f"/disk-drive-{disk}.raw": reader for disk, reader in disks.items()
               if re.fullmatch(r"(?:ide|sata|scsi|virtio)\d+", disk)}

    class View(fuse.Fuse):
        def getattr(self, path):
            if path == "/":
                return fuse.Stat(st_mode=stat.S_IFDIR | 0o500, st_nlink=2)
            if path not in readers:
                return -errno.ENOENT
            return fuse.Stat(st_mode=stat.S_IFREG | 0o400, st_nlink=1, st_size=readers[path].size)

        def readdir(self, path, offset):
            for name in [".", "..", *(path[1:] for path in sorted(readers))]:
                yield fuse.Direntry(name)

        def open(self, path, flags):
            if path not in readers:
                return -errno.ENOENT
            if flags & os.O_ACCMODE != os.O_RDONLY:
                return -errno.EROFS
            return fuse.FuseFileInfo(direct_io=True, keep_cache=False)

        def read(self, path, size, offset):
            try:
                return readers[path].read_at(offset, size)
            except (OSError, ValueError) as exc:
                print(f"Backup disk read failed: {exc}", file=sys.stderr, flush=True)
                return -errno.EIO

    view = View(dash_s_do="setsingle")
    view.parse(["-f", "-s", "-o", "ro", mountpoint])
    view.main()


if __name__ == "__main__":
    mount_disks(json.loads(Path(sys.argv[1]).read_text()), sys.argv[2])
