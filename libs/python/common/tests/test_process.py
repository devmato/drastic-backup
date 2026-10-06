import hashlib
import io
import os
import sys
import time
from pathlib import Path
from threading import Event, Timer

import pytest

from drastic_common.process import (
    ProcessCancelledError,
    is_mounted,
    mounted_process,
    run_pipeline,
    run_process,
    unmount,
)
from drastic_common.restic.client import ResticApi
from drastic_common.restic.exceptions import ResticCancelledError


def test_tool_failure_drains_stderr_and_reports_bounded_diagnostics():
    with pytest.raises(RuntimeError, match="last error") as error:
        run_process([sys.executable, "-c", "import sys; sys.stderr.write('x'*100000 + 'last error'); sys.exit(2)"])
    assert len(str(error.value)) < 8500


def test_running_process_is_cancelled_and_next_phase_cannot_start():
    event = Event()
    timer = Timer(0.2, event.set)
    timer.start()
    started = time.monotonic()
    try:
        with pytest.raises(ProcessCancelledError):
            run_process([sys.executable, "-c", "import time; time.sleep(60)"], cancelled=event.is_set)
        with pytest.raises(ProcessCancelledError):
            run_process(["must-not-execute"], cancelled=event.is_set)
    finally:
        timer.cancel()
    assert time.monotonic() - started < 5


def test_process_timeout_and_json_input():
    assert run_process([sys.executable, "-c", "import sys; print(sys.stdin.read())"], input_text="hello").strip() == "hello"
    with pytest.raises(TimeoutError):
        run_process([sys.executable, "-c", "import time; time.sleep(60)"], timeout=0.1)


def test_restic_operation_cancellation_is_scoped_and_checks_before_spawn():
    api = ResticApi("must-not-execute")
    event = Event()
    event.set()
    with api.operation_cancellation(event), pytest.raises(ResticCancelledError):
        api.snapshots()
    assert not api._cancelled()


@pytest.mark.skipif(not hasattr(os, "fork"), reason="POSIX process groups")
def test_cancellation_kills_children_even_when_the_leader_exits_first(tmp_path):
    marker = tmp_path / "child-pid"
    program = (
        "import os, signal, time\n"
        "if os.fork() == 0:\n"
        " signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        f" open({str(marker)!r}, 'w').write(str(os.getpid()))\n"
        "time.sleep(60)\n"
    )
    with pytest.raises(ProcessCancelledError):
        run_process([sys.executable, "-c", program], cancelled=marker.exists, timeout=5)
    pid = int(marker.read_text())
    status = Path(f"/proc/{pid}/stat")
    for _ in range(50):
        if not status.exists() or status.read_text().split()[2] == "Z":
            break
        time.sleep(0.02)
    else:
        os.kill(pid, 9)
        pytest.fail("Restore child process survived cancellation")


@pytest.mark.parametrize("failure", ["exit", "timeout", "cancel"])
def test_mount_startup_failure_terminates_helper(tmp_path, monkeypatch, failure):
    import subprocess

    processes = []
    original = subprocess.Popen

    def spawn(*args, **kwargs):
        process = original(*args, **kwargs)
        processes.append(process)
        return process

    monkeypatch.setattr(subprocess, "Popen", spawn)
    started = time.monotonic()
    program = "import sys; sys.stderr.write('mount failed'); sys.exit(3)" if failure == "exit" else "import time; time.sleep(60)"
    error = {"exit": RuntimeError, "timeout": TimeoutError, "cancel": ProcessCancelledError}[failure]
    with pytest.raises(error), mounted_process(
        [sys.executable, "-c", program], tmp_path / "not-mounted", timeout=0.2,
        cancelled=lambda: failure == "cancel" and time.monotonic() - started > 0.05,
    ):
        pytest.fail("An unavailable mount must not be used")
    assert len(processes) == 1 and processes[0].poll() is not None


def test_disconnected_mount_is_detected_without_stat_and_lazy_unmounted(monkeypatch):
    path = "/tmp/restore work\tline\n\\disk"
    mountinfo = r"42 1 0:1 / /tmp/restore\040work\011line\012\134disk rw - fuse.test test rw" + "\n"
    original_open = open

    def read_mounts(name, *args, **kwargs):
        if name == "/proc/self/mountinfo":
            return io.StringIO(mountinfo)
        return original_open(name, *args, **kwargs)

    monkeypatch.setattr("builtins.open", read_mounts)
    monkeypatch.setattr(os.path, "ismount", lambda _: pytest.fail("Must not stat a FUSE mount"))
    commands = []

    def run(command, **kwargs):
        commands.append(command)
        if command[1] == "-u":
            raise RuntimeError("Transport endpoint is not connected")

    monkeypatch.setattr("drastic_common.process.run_process", run)
    assert is_mounted(path)
    assert not is_mounted(path + "-other")
    unmount(path)
    assert commands == [["fusermount3", "-u", path], ["fusermount3", "-uz", path]]


def test_pipeline_streams_binary_data_and_preserves_split_progress_lines():
    producer = [sys.executable, "-c", "import sys; sys.stdout.buffer.write(bytes(range(256))*32768)"]
    consumer = [sys.executable, "-c", (
        "import hashlib,sys,time\n"
        "h=hashlib.sha256()\n"
        "while data:=sys.stdin.buffer.read(65536): h.update(data)\n"
        "sys.stdout.write('progress 100% (read ');sys.stdout.flush();time.sleep(.15)\n"
        "print('8388608 bytes)');print(h.hexdigest())\n"
    )]
    output = []
    run_pipeline(producer, consumer, on_output=output.append, timeout=10)
    assert output == ["progress 100% (read 8388608 bytes)", hashlib.sha256(bytes(range(256))*32768).hexdigest()]


@pytest.mark.parametrize("failure", ["producer", "consumer", "spawn", "timeout", "cancel"])
def test_pipeline_failure_stops_both_processes(tmp_path, monkeypatch, failure):
    import subprocess

    marker = tmp_path / "started"
    processes = []
    original = subprocess.Popen

    def spawn(*args, **kwargs):
        process = original(*args, **kwargs)
        processes.append(process)
        return process

    monkeypatch.setattr(subprocess, "Popen", spawn)
    fail = "import sys;sys.stderr.write('injected failure');sys.exit(3)"
    producer = [sys.executable, "-c", fail if failure == "producer" else
                f"import time;from pathlib import Path;Path({str(marker)!r}).touch();time.sleep(60)"]
    consumer = [sys.executable, "-c", fail if failure == "consumer" else "import sys;sys.stdin.buffer.read()"]
    if failure == "spawn":
        consumer = [str(tmp_path / "missing-consumer")]
    error = {"spawn": FileNotFoundError, "timeout": TimeoutError, "cancel": ProcessCancelledError}.get(failure, RuntimeError)
    with pytest.raises(error):
        run_pipeline(producer, consumer, timeout=0.3,
                     cancelled=lambda: failure == "cancel" and marker.exists())
    assert processes and all(process.poll() is not None for process in processes)


@pytest.mark.skipif(not hasattr(os, "fork"), reason="POSIX process groups")
def test_pipeline_cleans_children_after_producer_leader_exits(tmp_path):
    marker = tmp_path / "child-pid"
    producer = [sys.executable, "-c", (
        "import os,signal,time\n"
        "if os.fork()==0:\n"
        " signal.signal(signal.SIGTERM,signal.SIG_IGN)\n"
        f" open({str(marker)!r},'w').write(str(os.getpid()))\n"
        " time.sleep(60)\n"
    )]
    consumer = [sys.executable, "-c", "import sys;sys.stdin.buffer.read()"]
    with pytest.raises(TimeoutError):
        run_pipeline(producer, consumer, timeout=0.3)
    pid = int(marker.read_text())
    status = Path(f"/proc/{pid}/stat")
    for _ in range(50):
        if not status.exists() or status.read_text().split()[2] == "Z":
            break
        time.sleep(0.02)
    else:
        os.kill(pid, 9)
        pytest.fail("Pipeline child survived after its producer leader exited")
