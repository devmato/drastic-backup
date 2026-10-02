"""Run local tools with bounded diagnostics and process-group cancellation."""

import os
import signal
import subprocess
import tempfile
import time


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
