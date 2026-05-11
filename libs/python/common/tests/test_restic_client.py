import os
import subprocess
from shutil import which

import pytest

from drastic_common.restic.client import ResticApi
from drastic_common.restic.exceptions import ResticFailedError
from drastic_common.restic.repository import ResticRepository


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


def test_backup_stdin_from_command_streams_via_restic_stdin(monkeypatch):
    popen_calls = []

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
    ]
    assert callback_calls[0]["kwargs"] == {"report_uuid": "report-1", "pid": 3301}
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
    backup_result = api.backup(paths=[str(source_path)], tags=["smoke-test"])
    assert backup_result["snapshot_id"]

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
