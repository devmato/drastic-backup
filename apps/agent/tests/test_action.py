from threading import Event
from types import SimpleNamespace

import pytest

from drastic_agent.agent.action import AgentAction


class FakeReport:
    def __init__(self):
        self.cancel_event = Event()

    def log_message(self, message):
        pass


def test_docker_command_checks_exit_code_and_restores_timeout(monkeypatch):
    monkeypatch.setenv("DRASTIC_TASK_TIMEOUT_SECONDS", "17")

    class Container:
        @staticmethod
        def exec_run(command):
            assert command == "false"
            assert client.api.timeout == 17
            return SimpleNamespace(exit_code=9, output=b"command failed")

    client = SimpleNamespace(
        api=SimpleNamespace(timeout=60),
        containers=SimpleNamespace(get=lambda name: Container()),
    )

    with pytest.raises(RuntimeError, match="code 9.*command failed"):
        AgentAction.docker(
            FakeReport(), client, container="app", action="command", command="false"
        )

    assert client.api.timeout == 60


@pytest.mark.parametrize("hook", ["start", "end", "error"])
def test_shell_hooks_use_supervision_and_keep_cleanup_possible_after_cancellation(monkeypatch, hook):
    report = FakeReport()
    report.cancel_event.set()
    calls = []

    def run(command, *, timeout, cancelled, truncate_output_at):
        calls.append((command, timeout, cancelled(), truncate_output_at))
        return "bounded output"

    logs = []
    report.log_message = logs.append
    monkeypatch.setattr("drastic_agent.agent.action.run_process", run)
    monkeypatch.setenv("DRASTIC_TASK_TIMEOUT_SECONDS", "17")
    AgentAction.command(report, "echo test", hook=hook)
    assert calls == [(["/bin/sh", "-c", "echo test"], 17, hook == "start", 8192)]
    assert logs[-1] == "Command finished:\n bounded output"
