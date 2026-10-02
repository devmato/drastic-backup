import os
import sys
import time
from pathlib import Path
from threading import Event, Timer

import pytest

from drastic_common.process import ProcessCancelledError, run_process
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
