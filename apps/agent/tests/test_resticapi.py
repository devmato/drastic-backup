import subprocess

import drastic_common.restic.parsers as parsers
from drastic_common.restic.client import ResticApi
from drastic_common.restic.repository import ResticRepository


class _FakeStream:
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


class FakePopen:
    last_cmd = None
    last_env = None

    def __init__(self, cmd, stdin=None, stdout=None, stderr=None, encoding=None, env=None):
        FakePopen.last_cmd = cmd
        FakePopen.last_env = env
        self.pid = 4242
        self.return_code = 0
        self.stdout = _FakeStream(
            lines=[
                '{"message_type":"status","files_done":1}\n',
                '{"message_type":"summary","snapshot_id":"snap-1"}\n',
            ]
        )
        self.stderr = _FakeStream(text="")

    def poll(self):
        if self.stdout._index < len(self.stdout._lines):
            return None
        return self.return_code

    def wait(self):
        return self.return_code

    def kill(self):
        self.return_code = -9


def test_default_parser_parses_line_delimited_json():
    result = parsers.default(['{"forget": 1}\n', '{"forget": 2}\n'])

    assert result == [{"forget": 1}, {"forget": 2}]


def test_restic_api_uses_repository_env_and_argument_list(monkeypatch):
    monkeypatch.setattr(subprocess, "Popen", FakePopen)
    api = ResticApi(binary_path="restic")
    api.set_repository(
        ResticRepository(
            location="/tmp/repo path",
            password="secret",
            env={"AWS_ACCESS_KEY_ID": "key-1"},
        )
    )

    result = api.backup(
        paths=["/tmp/source path"],
        exclude_patterns=["*.tmp"],
        tags=["job-1"],
    )

    assert FakePopen.last_cmd == [
        "restic",
        "--json",
        "-r",
        "/tmp/repo path",
        "backup",
        "/tmp/source path",
        "--exclude",
        "*.tmp",
        "--tag",
        "job-1",
        "--verbose",
    ]
    assert FakePopen.last_env["RESTIC_PASSWORD"] == "secret"
    assert FakePopen.last_env["AWS_ACCESS_KEY_ID"] == "key-1"
    assert result["snapshot_id"] == "snap-1"


def test_cancel_process_is_shared_across_instances():
    class KillableProcess:
        def __init__(self):
            self.killed = False

        def kill(self):
            self.killed = True

    ResticApi._processes.clear()
    process = KillableProcess()
    ResticApi._processes[123] = process

    api = ResticApi(binary_path="restic")

    assert api.cancel_process(123) is True
    assert process.killed is True
    assert 123 not in ResticApi._processes
