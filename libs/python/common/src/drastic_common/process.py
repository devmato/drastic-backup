"""Run local tools with bounded diagnostics and process-group cancellation."""

import os
import re
import signal
import subprocess
import tempfile
import time
from contextlib import contextmanager


class ProcessCancelledError(RuntimeError):
    pass


def terminate_process(process, grace=5):
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
        process.wait(timeout=grace)
    except subprocess.TimeoutExpired:
        pass
    except ProcessLookupError:
        pass
    finally:
        # The leader may exit before its children (for example the guest appliance).
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait()


def run_process(command, *, cancelled=lambda: False, timeout=86400, input_text=None):
    """Use files rather than pipes so a verbose tool cannot deadlock or exhaust RAM."""
    if cancelled():
        raise ProcessCancelledError("Restore cancelled")
    with tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as errors, tempfile.TemporaryFile() as stdin:
        if input_text is not None:
            stdin.write(input_text.encode())
            stdin.seek(0)
        with subprocess.Popen(command, stdin=stdin, stdout=output, stderr=errors,
                              start_new_session=True) as process:
            deadline = time.monotonic() + timeout
            try:
                while process.poll() is None:
                    if cancelled():
                        raise ProcessCancelledError("Restore cancelled")
                    if time.monotonic() >= deadline:
                        raise TimeoutError(f"{command[0]} timed out")
                    time.sleep(0.1)
                if cancelled():
                    raise ProcessCancelledError("Restore cancelled")
                if process.returncode:
                    errors.seek(max(0, errors.tell() - 8192))
                    detail = errors.read().decode(errors="replace")
                    raise RuntimeError(f"{command[0]} failed ({process.returncode}): {detail}")
                output.seek(0)
                result = output.read(4 * 1024 * 1024 + 1)
                if len(result) > 4 * 1024 * 1024:
                    raise ValueError(f"{command[0]} returned too much output")
                return result.decode(errors="replace")
            finally:
                terminate_process(process)


def is_mounted(path):
    """Read Linux's mount table without touching a potentially blocked/disconnected FUSE server."""
    target = os.path.abspath(os.fsdecode(path))
    with open("/proc/self/mountinfo", encoding="utf-8", errors="surrogateescape") as mounts:
        for line in mounts:
            mountpoint = re.sub(r"\\([0-7]{3})", lambda match: chr(int(match[1], 8)), line.split()[4])
            if mountpoint == target:
                return True
    return False


def unmount(path):
    """Detach a private FUSE mount, including a failed/disconnected server."""
    if is_mounted(path):
        try:
            run_process(["fusermount3", "-u", str(path)], timeout=15)
        except (RuntimeError, TimeoutError):
            run_process(["fusermount3", "-uz", str(path)], timeout=15)


@contextmanager
def mounted_process(command, mountpoint, *, env=None, cancelled=lambda: False, timeout=120):
    """Linux FUSE lifecycle; no mounts or helper processes survive a request."""
    if cancelled():
        raise ProcessCancelledError("Restore cancelled")
    with tempfile.TemporaryFile() as log:
        process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                   env=env, start_new_session=True)
        try:
            deadline = time.monotonic() + timeout
            while not is_mounted(mountpoint):
                if cancelled():
                    raise ProcessCancelledError("Restore cancelled")
                if process.poll() is not None:
                    log.seek(0, 2)
                    log.seek(max(0, log.tell() - 8192))
                    raise RuntimeError(f"{command[0]} could not mount: {log.read().decode(errors='replace')}")
                if time.monotonic() >= deadline:
                    raise TimeoutError(f"{command[0]} mount timed out")
                time.sleep(0.1)
            yield process
            if cancelled():
                raise ProcessCancelledError("Restore cancelled")
            if process.poll() is not None:
                raise RuntimeError(f"{command[0]} mount stopped unexpectedly")
        finally:
            try:
                unmount(mountpoint)
            finally:
                terminate_process(process)
                # A server killed during startup can leave a disconnected mount.
                unmount(mountpoint)
