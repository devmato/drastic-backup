from types import SimpleNamespace

from flask import Flask

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
