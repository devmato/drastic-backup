from types import SimpleNamespace

from flask import Flask

from drastic_common.agent.commands import AGENT_PROTOCOL_VERSION, AgentCommandName
from drastic_server.models.agent import AgentOperationState
from drastic_server.services.agent import command as agent_command


def _agent():
    return SimpleNamespace(online=True, session=SimpleNamespace(request_sid="agent-sid"))


def test_run_job_awaits_short_admission_acknowledgement(monkeypatch):
    app = Flask(__name__)
    calls = []
    monkeypatch.setattr(
        agent_command,
        "call",
        lambda event, payload, **kwargs: calls.append((event, payload, kwargs))
        or {"type": "command", "state": "success", "logs": []},
    )

    with app.app_context():
        response = agent_command.AgentService.run_job(
            _agent(), job_id=1, repository_id=2, operation_uuid="operation-1"
        )

    assert response["state"] == AgentOperationState.success
    assert calls[0][2]["timeout"] == 5
    assert calls[0][1]["args"]["operation_uuid"] == "operation-1"


def test_cancel_job_sends_operation_uuid(monkeypatch):
    app = Flask(__name__)
    payloads = []
    monkeypatch.setattr(
        agent_command,
        "call",
        lambda _event, payload, **_kwargs: payloads.append(payload)
        or {"type": "command", "state": "success", "logs": []},
    )

    with app.app_context():
        agent_command.AgentService.cancel_job(
            _agent(), job_id=1, operation_uuid="operation-1"
        )

    assert payloads[0]["args"] == {"job_id": 1, "operation_uuid": "operation-1"}


def test_admission_timeout_is_a_running_timeout_report(monkeypatch):
    app = Flask(__name__)

    def timeout(*_args, **_kwargs):
        raise agent_command.TimeoutError()

    monkeypatch.setattr(agent_command, "call", timeout)

    with app.app_context():
        response = agent_command.AgentService.run_restore(_agent(), operation_uuid="operation-1")

    assert response["state"] == AgentOperationState.running
    assert agent_command.is_agent_timeout_response(response)


def test_protocol_gate_blocks_new_and_incompatible_commands_before_dispatch(monkeypatch):
    app = Flask(__name__)
    calls = []
    monkeypatch.setattr(agent_command, "call", lambda *_args, **_kwargs: calls.append(True))
    monkeypatch.setattr(agent_command, "emit", lambda *_args, **_kwargs: calls.append(True))
    agent = _agent()
    with app.app_context():
        for await_response in (True, False):
            for command in (
                AgentCommandName.update, AgentCommandName.get_proxmox_settings,
                AgentCommandName.update_proxmox_settings, AgentCommandName.test_proxmox_settings,
            ):
                response = agent_command.AgentService.send_command(
                    agent, command, await_response=await_response
                )
                assert response["state"] == AgentOperationState.failed
        agent.protocol_version = AGENT_PROTOCOL_VERSION + 1
        assert agent_command.AgentService.sync(agent)["state"] == AgentOperationState.failed
    assert not calls
