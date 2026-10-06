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


def run_pipeline(producer_command, consumer_command, *, cancelled=lambda: False, timeout=86400,
                 on_output=None, operation_uuid=None):
    """Stream binary data directly between tools; both exit codes determine success."""
    from drastic_common import diagnostics

    if cancelled():
        raise ProcessCancelledError("Restore cancelled")
    producer = consumer = None
    started = time.monotonic()
    with tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as producer_errors, tempfile.TemporaryFile() as consumer_errors:
        offset, pending = 0, b""

        def progress():
            nonlocal offset, pending
            if on_output is None:
                return
            while data := os.pread(output.fileno(), 65536, offset):
                if cancelled():
                    raise ProcessCancelledError("Restore cancelled")
                if time.monotonic() - started >= timeout:
                    raise TimeoutError("Restore pipeline timed out")
                offset += len(data)
                lines = (pending + data).split(b"\n")
                pending = lines.pop()[-8192:]
                for line in lines:
                    on_output(line[:8192].decode(errors="replace"))

        def check_exit_codes():
            failures = []
            for process, command, errors in ((producer, producer_command, producer_errors),
                                              (consumer, consumer_command, consumer_errors)):
                if process.poll() not in (None, 0):
                    errors.seek(0, 2)
                    errors.seek(max(0, errors.tell() - 8192))
                    failures.append(f"{command[0]} failed ({process.returncode}): {errors.read().decode(errors='replace')}")
            if failures:
                raise RuntimeError("; ".join(failures))

        try:
            producer = subprocess.Popen(producer_command, stdin=subprocess.DEVNULL,
                                        stdout=subprocess.PIPE, stderr=producer_errors, start_new_session=True)
            consumer = subprocess.Popen(consumer_command, stdin=producer.stdout,
                                        stdout=output, stderr=consumer_errors, start_new_session=True)
            producer.stdout.close()
            diagnostics.record("process.started", {"program": consumer_command[0], "pid": consumer.pid,
                               "producer": producer_command[0], "producer_pid": producer.pid}, operation_uuid=operation_uuid)
            while producer.poll() is None or consumer.poll() is None:
                if cancelled():
                    raise ProcessCancelledError("Restore cancelled")
                if time.monotonic() - started >= timeout:
                    raise TimeoutError("Restore pipeline timed out")
                progress()
                check_exit_codes()
                time.sleep(0.1)
            if cancelled():
                raise ProcessCancelledError("Restore cancelled")
            progress()
            check_exit_codes()
        finally:
            if producer is not None:
                producer.stdout.close()
            for process in (consumer, producer):
                if process is not None:
                    try:
                        terminate_process(process)
                    finally:
                        # An exited leader can leave children holding the stream open.
                        try:
                            os.killpg(process.pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass
            if consumer is not None:
                diagnostics.record("process.finished", {"program": consumer_command[0], "pid": consumer.pid,
                                   "exit_code": consumer.poll(), "producer_exit_code": producer.poll(),
                                   "duration_seconds": time.monotonic() - started}, operation_uuid=operation_uuid)


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
