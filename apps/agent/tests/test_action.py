from types import SimpleNamespace

import pytest

from drastic_agent.agent.action import AgentAction


class FakeReport:
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
