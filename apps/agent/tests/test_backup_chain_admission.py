from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Event

import pytest

from drastic_agent.agent.enums import AgentOperationState
from drastic_agent.agent.report import AgentReport
from drastic_agent.runtime.agent import Agent
from drastic_agent.runtime.execution import ExecutionManager
from drastic_agent.storage.database import (
    agent_operation_artifacts,
    agent_operation_queue,
    agent_operations,
    jobs,
    repositories,
)


@pytest.fixture
def agent(monkeypatch):
    tables = (agent_operation_artifacts, agent_operation_queue, agent_operations, jobs, repositories)
    for table in tables:
        table.delete()
    AgentReport.pending_reports = []
    AgentReport.finished_reports.clear()
    AgentReport.running_operations = {}
    jobs.insert({"id": 17, "type": "file", "uuid": "job-17", "config": {}})
    repositories.insert({"id": 27, "location": "/repo", "kind": "custom"})
    agent = Agent.__new__(Agent)
    agent._Agent__execution = ExecutionManager(max_workers=1, max_pending=1)
    yield agent
    agent._Agent__execution.shutdown()
    for table in tables:
        table.delete()
    AgentReport.pending_reports = []
    AgentReport.finished_reports.clear()
    AgentReport.running_operations = {}


def request(uuid="chain-step"):
    return {"command": "run_job", "args": {"job_id": 17, "repository_id": 27, "operation_uuid": uuid,
            "run_options": {"chain_run_id": 1, "start_deadline": (datetime.now(timezone.utc) + timedelta(minutes=1)).isoformat()}}}


def test_duplicate_admission_before_after_completion_and_restart(agent, monkeypatch):
    started = Event()
    finish = Event()
    calls = []

    def backup(**kwargs):
        calls.append(kwargs["operation_uuid"])
        report = AgentReport.backup_operation(job_id=17, repository_id=27, operation_uuid=kwargs["operation_uuid"])
        started.set()
        assert finish.wait(5)
        return report.finish()

    monkeypatch.setattr(agent, "cmd_run_job", backup)
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(agent._Agent__handle_execute_command, [request(), request()]))
    assert started.wait(5)
    assert all(result.final_state == AgentOperationState.success for result in results)
    assert calls == ["chain-step"]
    finish.set()
    agent._Agent__execution.shutdown()
    assert agent._Agent__handle_execute_command(request()).data["operation_state"] == "success"
    restarted = Agent.__new__(Agent)
    assert restarted._Agent__handle_execute_command(request()).data["known"] is True
    assert calls == ["chain-step"]


def test_expired_start_and_cancellation_tombstone_prevent_delayed_execution(agent):
    expired = request("expired-step")
    expired["args"]["run_options"]["start_deadline"] = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    assert agent._Agent__handle_execute_command(expired).final_state == AgentOperationState.failed
    assert agent_operations.find_one(uuid="expired-step") is None
    result = agent._Agent__handle_execute_command({"command": "cancel_job", "args": {
        "job_id": 17, "operation_uuid": "delayed-step", "cancel_if_missing": True,
    }})
    assert result.final_state == AgentOperationState.success
    assert agent_operations.find_one(uuid="delayed-step")["state"] == "cancelled"
    assert agent._Agent__handle_execute_command(request("delayed-step")).final_state == AgentOperationState.failed
    assert agent_operations.find_one(uuid="delayed-step")["state"] == "cancelled"


def test_uuid_cannot_be_reused_for_a_different_repository(agent):
    AgentReport.backup_operation(job_id=17, repository_id=28, operation_uuid="chain-step").finish()
    result = agent._Agent__handle_execute_command(request())
    assert result.final_state == AgentOperationState.failed
    assert "another request" in result.log


def test_deadline_is_checked_again_when_an_admitted_job_leaves_the_queue(agent, monkeypatch):
    started = Event()
    release = Event()

    def occupy_worker():
        started.set()
        assert release.wait(5)

    agent._Agent__execution.submit(occupy_worker)
    assert started.wait(5)
    result = agent._Agent__handle_execute_command(request())
    assert result.final_state == AgentOperationState.success
    monkeypatch.setattr(agent, "_Agent__validate_run_job_admission", lambda args: "Chain step start deadline expired")
    release.set()
    agent._Agent__execution.shutdown()
    history = agent_operations.find_one(uuid="chain-step")
    assert history["state"] == "failed"
    assert history["data"]["start_skipped"] is True


@pytest.mark.parametrize("reading_command", ["list_restore_entries", "proxmox_restore"])
@pytest.mark.parametrize("cancel_if_missing", [False, True])
def test_stalled_read_does_not_block_admission_sync_status_or_cancellation(agent, monkeypatch, reading_command, cancel_if_missing):
    reading, release, backup_started = Event(), Event(), Event()

    def slow_read(**kwargs):
        reading.set()
        assert release.wait(5)
        return AgentReport.command_report().finish()

    def backup(**kwargs):
        report = AgentReport.backup_operation(job_id=17, repository_id=27, operation_uuid=kwargs["operation_uuid"])
        backup_started.set()
        assert report.cancel_event.wait(5)
        return report.finish()

    monkeypatch.setattr(agent, f"cmd_{reading_command}", slow_read)
    monkeypatch.setattr(agent, "cmd_run_job", backup)
    monkeypatch.setattr(agent, "cmd_sync", lambda: AgentReport.command_report().finish())
    with ThreadPoolExecutor(max_workers=2) as clients:
        slow = clients.submit(agent._Agent__handle_execute_command, {"command": reading_command, "args": {}})
        try:
            assert reading.wait(2)
            result = clients.submit(agent._Agent__handle_execute_command, request()).result(timeout=2)
            assert result.final_state == AgentOperationState.success
            assert backup_started.wait(2)
            status = clients.submit(agent._Agent__handle_execute_command, {
                "command": "get_operation_status", "args": {"operation_uuid": "chain-step"},
            }).result(timeout=2)
            assert status.data["known"] is True
            assert clients.submit(agent._Agent__handle_execute_command, {"command": "sync"}).result(timeout=2).final_state == AgentOperationState.success
            cancelled = clients.submit(agent._Agent__handle_execute_command, {
                "command": "cancel_job", "args": {"job_id": 17, "operation_uuid": "chain-step", "cancel_if_missing": cancel_if_missing},
            }).result(timeout=2)
            assert cancelled.final_state == AgentOperationState.success
            assert not slow.done()
        finally:
            release.set()
        slow.result(timeout=2)
