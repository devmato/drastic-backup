import csv
import json
import os
import subprocess
import threading
from contextlib import contextmanager
from pathlib import Path
from shutil import which
from types import SimpleNamespace

import pytest

from drastic_common.restic.client import ResticApi
from drastic_common.restic.exceptions import (
    ResticCancelledError,
    ResticFailedError,
    ResticTimeoutError,
)
from drastic_common.restic.repository import ResticRepository


def test_mount_defers_snapshot_lookup_to_supervised_consumer(monkeypatch, tmp_path):
    @contextmanager
    def mounted(*args, **kwargs):
        yield SimpleNamespace(pid=12345)

    def forbid_lookup(path):
        pytest.fail("Snapshot lookup must not block the agent worker")

    monkeypatch.setattr("drastic_common.restic.client.mounted_process", mounted)
    monkeypatch.setattr(Path, "is_dir", forbid_lookup)
    api = ResticApi("restic")
    mountpoint = tmp_path / "repository"
    with api.mount("snapshot", mountpoint) as root:
        assert root == mountpoint / "ids/snapshot"
        assert 12345 in api.diagnostic_processes()
    assert not mountpoint.exists()
    assert 12345 not in api.diagnostic_processes()


class _FakeTextStream:
    def __init__(self, lines=None, text=""):
        self._lines = list(lines or [])
        self._index = 0
        self._text = text

    def readline(self):
        if self._index >= len(self._lines):
            return ""

        line = self._lines[self._index]
        self._index += 1
        return line

    def read(self):
        return self._text

    def close(self):
        return None


class _FakeBinaryStream:
    def __init__(self, chunks=None):
        self._chunks = list(chunks or [])
        self._index = 0

    def read(self, size=-1):
        if self._index >= len(self._chunks):
            return b""

        chunk = self._chunks[self._index]
        self._index += 1
        return chunk

    def close(self):
        return None


class _FakeProducerProcess:
    def __init__(self, cmd, return_code=0, stderr_chunks=None):
        self.cmd = cmd
        self.pid = 2201
        self.return_code = return_code
        self.stdout = _FakeBinaryStream(chunks=[b"binary backup stream"])
        self.stderr = _FakeBinaryStream(chunks=stderr_chunks or [])

    def poll(self):
        return self.return_code

    def wait(self):
        return self.return_code

    def kill(self):
        self.return_code = -9


class _FakeResticProcess:
    def __init__(self, cmd, stdin=None, return_code=0, stderr_text=""):
        self.cmd = cmd
        self.stdin = stdin
        self.pid = 3301
        self.return_code = return_code
        self.stdout = _FakeTextStream(
            lines=[
                '{"message_type":"status","files_done":1}\n',
                '{"message_type":"summary","snapshot_id":"snap-1"}\n',
            ]
        )
        self.stderr = _FakeTextStream(text=stderr_text)

    def poll(self):
        if self.stdout._index < len(self.stdout._lines):
            return None
        return self.return_code

    def wait(self):
        return self.return_code

    def kill(self):
        self.return_code = -9


@pytest.mark.parametrize("temp_name", ["O'Brien tmp", 'tmp with "quotes"'])
def test_sftp_uses_managed_ssh_config(monkeypatch, tmp_path, temp_name):
    if not which("ssh"):
        pytest.skip("ssh binary is not available")

    known_hosts = tmp_path / "agent data" / "known_hosts"
    temp_dir = tmp_path / temp_name
    temp_dir.mkdir()
    monkeypatch.setattr("drastic_common.restic.client.tempfile.tempdir", str(temp_dir))
    api = ResticApi("restic", ResticRepository(
        location="sftp://user@example.test:23/backup", password="secret",
        ssh_private_key="agent-private-key", ssh_known_hosts_path=str(known_hosts),
    ))
    cmd = ["restic"]
    with api._ResticApi__restic_env(cmd) as env:
        [option] = next(csv.reader([cmd[cmd.index("-o") + 1]]))
        assert option.startswith("sftp.args=-F ")
        quoted_path = option.removeprefix("sftp.args=-F ")
        assert quoted_path[0] in "\"'" and quoted_path[-1] == quoted_path[0]
        assert quoted_path[0] not in quoted_path[1:-1]  # Restic cannot join quoted fragments.
        config = Path(quoted_path[1:-1])
        key = config.with_name("identity")
        assert key.read_text(encoding="utf-8") == "agent-private-key\n"
        assert key.stat().st_mode & 0o777 == 0o600
        # Ask real OpenSSH which settings it uses, without opening a connection.
        settings = subprocess.run(
            ["ssh", "-G", "-F", str(config), "user@example.test"],
            env=env, capture_output=True, text=True, check=True,
        ).stdout
        assert f"identityfile {key}\n" in settings
        assert f"userknownhostsfile {known_hosts}\n" in settings
        assert "identitiesonly yes\n" in settings
        assert "stricthostkeychecking accept-new\n" in settings


def test_backup_stdin_from_command_streams_via_restic_stdin(monkeypatch):
    popen_calls = []
    diagnostic_operations = []
    monkeypatch.setattr("drastic_common.restic.client.diagnostics.record",
                        lambda event, data, **kw: diagnostic_operations.append(kw.get("operation_uuid")))

    def fake_popen(cmd, stdin=None, stdout=None, stderr=None, encoding=None, env=None):
        popen_calls.append({"cmd": cmd, "stdin": stdin, "encoding": encoding, "env": env})
        if cmd[0] == "vzdump":
            return _FakeProducerProcess(cmd)
        return _FakeResticProcess(cmd, stdin=stdin)

    monkeypatch.setattr(subprocess, "Popen", fake_popen)

    result = ResticApi(binary_path="restic").backup_stdin_from_command(
        command=["vzdump", "101", "--stdout"],
        stdin_filename="archive.vma",
        tags=["job-1"],
        callback_args={"operation_uuid": "stream-operation"},
    )

    assert popen_calls[0]["cmd"] == ["vzdump", "101", "--stdout"]
    assert popen_calls[1]["cmd"] == [
        "restic",
        "--json",
        "backup",
        "--stdin",
        "--stdin-filename",
        "archive.vma",
        "--tag",
        "job-1",
    ]
    assert result["snapshot_id"] == "snap-1"
    assert diagnostic_operations == ["stream-operation", "stream-operation"]


def test_backup_delivers_scan_and_summary_despite_throttle(monkeypatch):
    scan = {"message_type": "verbose_status", "action": "scan_finished", "data_size": 100, "total_files": 2}
    summary = {"message_type": "summary", "snapshot_id": "snap-1", "total_bytes_processed": 100}

    def popen(cmd, **kwargs):
        assert "--verbose" in cmd
        process = _FakeResticProcess(cmd)
        process.stdout = _FakeTextStream(lines=[json.dumps(message) + "\n" for message in [
            {"message_type": "status", "bytes_done": 1},
            {"message_type": "verbose_status", "action": "new", "item": "file"},
            scan,
            {"message_type": "status", "bytes_done": 50},
            summary,
        ]])
        return process

    monkeypatch.setattr(subprocess, "Popen", popen)
    events = []
    result = ResticApi("restic").backup(["/data"], callback=lambda line: events.append(json.loads(line)), callback_throttle=60000)
    assert events == [{"message_type": "status", "bytes_done": 1}, scan, summary]
    assert result == summary


def test_partial_backup_keeps_snapshot_identity_without_reporting_success(monkeypatch):
    process = _FakeResticProcess(["restic"], return_code=3)
    process.stdout._lines.append('{"message_type":"exit_error","code":3}\n')
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **kw: process)
    with pytest.raises(ResticFailedError) as failure:
        ResticApi("restic").backup(["/data"])
    assert failure.value.snapshot_id == "snap-1"


def test_backup_stdin_from_command_surfaces_producer_stderr(monkeypatch):
    def fake_popen(cmd, stdin=None, stdout=None, stderr=None, encoding=None, env=None):
        if cmd[0] == "vzdump":
            return _FakeProducerProcess(
                cmd,
                return_code=255,
                stderr_chunks=[b"cannot export a snapshot in PVE::Storage::LvmThinPlugin\n"],
            )
        return _FakeResticProcess(cmd, stdin=stdin, return_code=1, stderr_text="Fatal: unable to save")

    monkeypatch.setattr(subprocess, "Popen", fake_popen)

    with pytest.raises(ResticFailedError) as exc_info:
        ResticApi(binary_path="restic").backup_stdin_from_command(
            command=["vzdump", "101", "--stdout"],
            stdin_filename="archive.vma",
        )

    assert "Backup source command failed with exit code 255" in str(exc_info.value)
    assert "cannot export a snapshot in PVE::Storage::LvmThinPlugin" in str(exc_info.value)


def test_backup_stdin_from_command_attaches_snapshot_id_to_producer_failure(monkeypatch):
    def fake_popen(cmd, stdin=None, stdout=None, stderr=None, encoding=None, env=None):
        if cmd[0] == "vzdump":
            return _FakeProducerProcess(cmd, return_code=2, stderr_chunks=[b"cleanup failed\n"])
        return _FakeResticProcess(cmd, stdin=stdin)

    monkeypatch.setattr(subprocess, "Popen", fake_popen)

    with pytest.raises(ResticFailedError) as exc_info:
        ResticApi(binary_path="restic").backup_stdin_from_command(
            command=["vzdump", "101", "--stdout"],
            stdin_filename="archive.vma",
        )

    assert exc_info.value.snapshot_id == "snap-1"


def test_snapshots_builds_command_with_job_uuid_tag(monkeypatch):
    popen_calls = []

    def fake_popen(cmd, stdin=None, stdout=None, stderr=None, encoding=None, env=None):
        popen_calls.append({"cmd": cmd, "env": env})
        process = _FakeResticProcess(cmd, stdin=stdin)
        process.stdout = _FakeTextStream(lines=['[{"id":"snap-1"}]\n'])
        return process

    monkeypatch.setattr(subprocess, "Popen", fake_popen)

    result = ResticApi(
        binary_path="restic",
        repository=ResticRepository(location="rest:http://repo", password="secret"),
    ).snapshots(tags=["job_uuid:job-1"])

    assert popen_calls[0]["cmd"] == [
        "restic",
        "--json",
        "-r",
        "rest:http://repo",
        "snapshots",
        "--tag",
        "job_uuid:job-1",
    ]
    assert popen_calls[0]["env"]["RESTIC_PASSWORD"] == "secret"
    assert result == [{"id": "snap-1"}]


def test_check_builds_command_with_read_data_subset(monkeypatch):
    popen_calls = []

    def fake_popen(cmd, stdin=None, stdout=None, stderr=None, encoding=None, env=None):
        popen_calls.append({"cmd": cmd, "env": env})
        process = _FakeResticProcess(cmd, stdin=stdin)
        process.stdout = _FakeTextStream(lines=['{"message_type":"summary","errors":0}\n'])
        return process

    monkeypatch.setattr(subprocess, "Popen", fake_popen)

    result = ResticApi(
        binary_path="restic",
        repository=ResticRepository(location="/repo", password="secret"),
    ).check(read_data_subset="1/10")

    assert popen_calls[0]["cmd"] == [
        "restic",
        "--json",
        "-r",
        "/repo",
        "check",
        "--read-data-subset",
        "1/10",
    ]
    assert popen_calls[0]["env"]["RESTIC_PASSWORD"] == "secret"
    assert result == {"message_type": "summary", "errors": 0}


def test_check_builds_command_with_read_data(monkeypatch):
    popen_calls = []

    def fake_popen(cmd, stdin=None, stdout=None, stderr=None, encoding=None, env=None):
        popen_calls.append({"cmd": cmd, "env": env})
        process = _FakeResticProcess(cmd, stdin=stdin)
        process.stdout = _FakeTextStream(lines=['{"message_type":"summary","errors":0}\n'])
        return process

    monkeypatch.setattr(subprocess, "Popen", fake_popen)

    ResticApi(
        binary_path="restic",
        repository=ResticRepository(location="/repo", password="secret"),
    ).check(read_data=True, read_data_subset="1/10")

    assert popen_calls[0]["cmd"] == [
        "restic",
        "--json",
        "-r",
        "/repo",
        "check",
        "--read-data",
    ]


def test_stats_measures_stored_data_and_records_mode(monkeypatch):
    def fake_popen(cmd, **kwargs):
        assert cmd == ["restic", "--json", "-r", "/repo", "stats", "--mode", "raw-data"]
        process = _FakeResticProcess(cmd)
        process.stdout = _FakeTextStream(lines=['{"total_size":1024,"total_blob_count":2}\n'])
        return process

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    result = ResticApi(
        binary_path="restic",
        repository=ResticRepository(location="/repo", password="secret"),
    ).stats()

    assert result == {"total_size": 1024, "total_blob_count": 2, "mode": "raw-data"}


def test_forget_prunes_by_default(monkeypatch):
    popen_calls = []

    def fake_popen(cmd, stdin=None, stdout=None, stderr=None, encoding=None, env=None):
        popen_calls.append(cmd)
        process = _FakeResticProcess(cmd, stdin=stdin)
        process.stdout = _FakeTextStream(lines=['[{"remove":[{"id":"snap-1"}]}]\n'])
        return process

    monkeypatch.setattr(subprocess, "Popen", fake_popen)

    result = ResticApi(binary_path="restic").forget(keep_last=3, keep_tags=["job-1"])

    assert popen_calls[0] == [
        "restic",
        "--json",
        "forget",
        "--keep-last",
        "3",
        "--keep-tag",
        "job-1",
        "--prune",
    ]
    assert result == [{"remove": [{"id": "snap-1"}]}]


def test_prune_builds_separate_command(monkeypatch):
    popen_calls = []

    def fake_popen(cmd, stdin=None, stdout=None, stderr=None, encoding=None, env=None):
        popen_calls.append(cmd)
        return _FakeResticProcess(cmd, stdin=stdin)

    monkeypatch.setattr(subprocess, "Popen", fake_popen)

    ResticApi(binary_path="restic").prune()

    assert popen_calls == [["restic", "--json", "prune"]]


def test_ls_builds_command_for_recursive_snapshot_path(monkeypatch):
    popen_calls = []

    def fake_popen(cmd, stdin=None, stdout=None, stderr=None, encoding=None, env=None):
        popen_calls.append(cmd)
        process = _FakeResticProcess(cmd, stdin=stdin)
        process.stdout = _FakeTextStream(lines=['{"path":"/etc","type":"dir"}\n'])
        return process

    monkeypatch.setattr(subprocess, "Popen", fake_popen)

    result = ResticApi(binary_path="restic").ls("snap-1", path="/etc", recursive=True)

    assert popen_calls[0] == [
        "restic",
        "--json",
        "ls",
        "snap-1",
        "/etc",
        "--recursive",
    ]
    assert result == {"path": "/etc", "type": "dir"}


def test_restore_builds_command_with_target_and_include_paths(monkeypatch):
    popen_calls = []
    callback_calls = []

    def fake_popen(cmd, stdin=None, stdout=None, stderr=None, encoding=None, env=None):
        popen_calls.append(cmd)
        process = _FakeResticProcess(cmd, stdin=stdin)
        process.stdout = _FakeTextStream(
            lines=[
                '{"message_type":"status","files_done":1}\n',
                '{"message_type":"summary","files_restored":1}\n',
            ]
        )
        return process

    monkeypatch.setattr(subprocess, "Popen", fake_popen)

    result = ResticApi(binary_path="restic").restore(
        snapshot_id="snap-1",
        target="/restore",
        include_paths=["/etc/hosts", "/var/www"],
        callback=lambda status, **kwargs: callback_calls.append({"status": status, "kwargs": kwargs}),
        callback_args={"report_uuid": "report-1"},
        callback_pid=True,
    )

    assert popen_calls[0] == [
        "restic",
        "--json",
        "restore",
        "snap-1",
        "--target",
        "/restore",
        "--include",
        "/etc/hosts",
        "--include",
        "/var/www",
        "--overwrite",
        "never",
    ]
    assert callback_calls[0]["kwargs"] == {"report_uuid": "report-1", "pid": 3301}
    assert callback_calls[0]["status"] is None
    assert callback_calls[-1] == {
        "status": None,
        "kwargs": {"report_uuid": "report-1", "pid": None},
    }
    assert result == [
        {"message_type": "status", "files_done": 1},
        {"message_type": "summary", "files_restored": 1},
    ]


def test_restore_rejects_empty_include_paths():
    with pytest.raises(ValueError):
        ResticApi(binary_path="restic").restore(
            snapshot_id="snap-1",
            target="/restore",
            include_paths=[],
        )


def test_restore_maps_overwrite_policy_to_restic(monkeypatch):
    popen_calls = []

    def fake_popen(cmd, stdin=None, stdout=None, stderr=None, encoding=None, env=None):
        popen_calls.append(cmd)
        return _FakeResticProcess(cmd, stdin=stdin)

    monkeypatch.setattr(subprocess, "Popen", fake_popen)

    ResticApi(binary_path="restic").restore(
        snapshot_id="snap-1",
        target="/restore",
        include_paths=["/etc/hosts"],
        overwrite_policy="overwrite",
    )

    assert popen_calls[0][-2:] == ["--overwrite", "always"]


def _write_executable(path, source):
    path.write_text(f"#!/usr/bin/env python3\n{source}", encoding="utf-8")
    path.chmod(0o755)


def test_cancellation_monitors_silent_process_and_cleans_registry(tmp_path):
    restic = tmp_path / "restic"
    _write_executable(restic, "import time\ntime.sleep(60)\n")
    cancellation_event = threading.Event()
    api = ResticApi(
        binary_path=str(restic),
        cancellation_event=cancellation_event,
        terminate_grace=0.01,
    )
    timer = threading.Timer(0.1, cancellation_event.set)
    timer.start()
    try:
        with pytest.raises(ResticCancelledError):
            api.snapshots()
    finally:
        timer.cancel()

    assert api._processes == {}


def test_timeout_monitors_silent_process_and_cleans_registry(tmp_path):
    restic = tmp_path / "restic"
    _write_executable(restic, "import time\ntime.sleep(60)\n")
    api = ResticApi(binary_path=str(restic), timeout=0.1, terminate_grace=0.01)

    with pytest.raises(ResticTimeoutError):
        api.snapshots()

    assert api._processes == {}


def test_pipeline_timeout_stops_producer_and_restic(tmp_path):
    producer_pid = tmp_path / "producer.pid"
    restic_pid = tmp_path / "restic.pid"
    producer = tmp_path / "producer"
    restic = tmp_path / "restic"
    _write_executable(
        producer,
        f"import os, time\nopen({str(producer_pid)!r}, 'w').write(str(os.getpid()))\ntime.sleep(60)\n",
    )
    _write_executable(
        restic,
        f"import os, time\nopen({str(restic_pid)!r}, 'w').write(str(os.getpid()))\ntime.sleep(60)\n",
    )
    api = ResticApi(binary_path=str(restic), timeout=0.2, terminate_grace=0.01)

    with pytest.raises(ResticTimeoutError):
        api.backup_stdin_from_command([str(producer)], "backup.data")

    for pid_file in (producer_pid, restic_pid):
        pid = int(pid_file.read_text(encoding="utf-8"))
        with pytest.raises(ProcessLookupError):
            os.kill(pid, 0)
    assert api._processes == {}


def test_pipeline_restic_exit_stops_blocking_producer(tmp_path):
    producer_pid_path = tmp_path / "producer.pid"
    producer = tmp_path / "producer"
    restic = tmp_path / "restic"
    _write_executable(
        producer,
        (
            "import os, signal, time\n"
            f"open({str(producer_pid_path)!r}, 'w').write(str(os.getpid()))\n"
            "signal.signal(signal.SIGPIPE, signal.SIG_IGN)\n"
            "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
            "while True:\n"
            "    try:\n"
            "        os.write(1, b'x' * 65536)\n"
            "    except BrokenPipeError:\n"
            "        time.sleep(60)\n"
        ),
    )
    _write_executable(
        restic,
        (
            "import os, time\n"
            f"while not os.path.exists({str(producer_pid_path)!r}):\n"
            "    time.sleep(0.001)\n"
            "raise SystemExit(1)\n"
        ),
    )
    api = ResticApi(binary_path=str(restic), terminate_grace=0.01)
    errors = []

    def run_backup():
        try:
            api.backup_stdin_from_command([str(producer)], "backup.data")
        except Exception as exc:  # noqa: BLE001 - the worker must report every failure
            errors.append(exc)

    worker = threading.Thread(target=run_backup, daemon=True)
    worker.start()
    worker.join(timeout=1.0)
    completed_without_external_cleanup = not worker.is_alive()

    if not completed_without_external_cleanup and producer_pid_path.exists():
        os.killpg(int(producer_pid_path.read_text(encoding="utf-8")), 9)
        worker.join(timeout=1.0)

    assert completed_without_external_cleanup
    assert len(errors) == 1
    assert isinstance(errors[0], ResticFailedError)
    producer_pid = int(producer_pid_path.read_text(encoding="utf-8"))
    with pytest.raises(ProcessLookupError):
        os.kill(producer_pid, 0)
    assert api._processes == {}


def test_pipeline_allows_producer_cleanup_after_successful_restic_exit(tmp_path):
    cleanup_marker = tmp_path / "producer-cleanup"
    producer = tmp_path / "producer"
    restic = tmp_path / "restic"
    _write_executable(
        producer,
        (
            "import os, time\n"
            "os.write(1, b'backup stream')\n"
            "os.close(1)\n"
            "time.sleep(0.1)\n"
            f"open({str(cleanup_marker)!r}, 'w').write('complete')\n"
        ),
    )
    _write_executable(
        restic,
        (
            "import sys\n"
            "sys.stdin.buffer.read()\n"
            "print('{\"message_type\":\"summary\",\"snapshot_id\":\"snap-cleanup\"}')\n"
        ),
    )

    result = ResticApi(binary_path=str(restic), terminate_grace=0.01).backup_stdin_from_command(
        [str(producer)], "backup.data"
    )

    assert result["snapshot_id"] == "snap-cleanup"
    assert cleanup_marker.read_text(encoding="utf-8") == "complete"


def test_pipeline_delivers_source_progress_before_completion_and_keeps_diagnostics(tmp_path):
    marker = tmp_path / "progress-received"
    producer = tmp_path / "producer"
    restic = tmp_path / "restic"
    _write_executable(producer, (
        "import os, time\n"
        "os.write(2, b'INFO: 25% (16 GiB of ')\n"
        "os.write(2, b'64 GiB) in 1s\\n')\n"
        "deadline = time.monotonic() + 2\n"
        f"while not os.path.exists({str(marker)!r}) and time.monotonic() < deadline:\n"
        "    time.sleep(0.01)\n"
        f"assert os.path.exists({str(marker)!r}), 'progress was buffered'\n"
        "os.write(1, b'archive data')\n"
        "os.write(2, b'cleanup failed\\n')\n"
        "raise SystemExit(2)\n"
    ))
    _write_executable(restic, (
        "import sys\n"
        "sys.stdin.buffer.read()\n"
        "print('{\"message_type\":\"summary\",\"snapshot_id\":\"snap-progress\"}')\n"
    ))
    lines = []

    def on_stderr(line):
        lines.append(line)
        if line.startswith("INFO:"):
            marker.touch()

    api = ResticApi(binary_path=str(restic), timeout=4, terminate_grace=0.01)
    with pytest.raises(ResticFailedError) as exc_info:
        api.backup_stdin_from_command(
            [str(producer)], "backup.vma", producer_stderr_callback=on_stderr,
        )

    assert lines == ["INFO: 25% (16 GiB of 64 GiB) in 1s\n", "cleanup failed\n"]
    assert "cleanup failed" in str(exc_info.value)
    assert exc_info.value.snapshot_id == "snap-progress"
    assert api._processes == {}


def test_cancel_process_uses_configured_termination_grace():
    class ManagedProcess:
        def __init__(self):
            self.terminate_calls = []
            self.killed = False

        def terminate(self, grace):
            self.terminate_calls.append(grace)

        def kill(self):
            self.killed = True

    ResticApi._processes.clear()
    process = ManagedProcess()
    ResticApi._processes[123] = process
    api = ResticApi(binary_path="restic", terminate_grace=0.25)

    assert api.cancel_process(123) is True
    assert process.terminate_calls == [0.25]
    assert process.killed is False
    assert api.cancel_process(123) is False


def test_real_restic_backup_restore_smoke(tmp_path):
    restic_binary = os.environ.get("RESTIC_BINARY") or os.environ.get("DRASTIC_RESTIC_BINARY") or which("restic")
    if not restic_binary:
        pytest.skip("restic binary is not available")

    repo_path = tmp_path / "repo"
    source_path = tmp_path / "source"
    restore_path = tmp_path / "restore"
    source_path.mkdir()
    (source_path / "nested").mkdir()
    (source_path / "hello.txt").write_text("hello backup\n", encoding="utf-8")
    (source_path / "nested" / "data.txt").write_text("nested data\n", encoding="utf-8")

    api = ResticApi(
        binary_path=restic_binary,
        repository=ResticRepository(location=str(repo_path), password="test-password"),
    )

    api.init()
    events = []
    backup_result = api.backup(paths=[str(source_path)], tags=["smoke-test"],
                               callback=lambda line: events.append(json.loads(line)), callback_throttle=60000)
    assert backup_result["snapshot_id"]
    scan = next(event for event in events if event.get("action") == "scan_finished")
    assert scan["data_size"] == backup_result["total_bytes_processed"] == 25
    assert scan["total_files"] == backup_result["total_files_processed"] == 2
    assert events[-1] == backup_result

    stats = api.stats()
    assert stats["mode"] == "raw-data"
    assert stats["total_size"] > 0
    assert stats["total_blob_count"] > 0

    (source_path / "hello.txt").write_text("changed after backup\n", encoding="utf-8")
    (source_path / "nested" / "data.txt").unlink()

    api.restore(
        snapshot_id=backup_result["snapshot_id"],
        target=str(restore_path),
        include_paths=[str(source_path)],
    )
    restored_source = restore_path.joinpath(*source_path.resolve().parts[1:])

    assert (restored_source / "hello.txt").read_text(encoding="utf-8") == "hello backup\n"
    assert (restored_source / "nested" / "data.txt").read_text(encoding="utf-8") == "nested data\n"
    api.check()
