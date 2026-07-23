import configparser

import pytest

import drastic_agent.agent.agent as agent_module
from drastic_agent.agent.agent import Agent
from drastic_agent.agent.database import agent_operation_queue, agent_operations
from drastic_agent.agent.enums import AgentReportState, AgentReportType
from drastic_agent.agent.report import AgentReport


@pytest.fixture(autouse=True)
def clean_operations():
    agent_operation_queue.delete()
    agent_operations.delete()
    AgentReport.pending_reports = []
    AgentReport.finished_reports.clear()
    AgentReport.running_operations = {}
    yield
    agent_operation_queue.delete()
    agent_operations.delete()
    AgentReport.pending_reports = []
    AgentReport.finished_reports.clear()
    AgentReport.running_operations = {}


def build_agent():
    agent = Agent.__new__(Agent)
    agent.identifier = "agent-17"
    agent.client = None
    agent._Agent__secret = "secret-17"
    agent._Agent__server = "http://server.test"
    agent._Agent__config = configparser.ConfigParser()
    return agent


class ImmediateExecution:
    @staticmethod
    def submit(target, *args, **kwargs):
        target(*args)
        return object()


class DeferredExecution:
    def __init__(self):
        self.runner = None

    def submit(self, target, *args, **kwargs):
        self.runner = target
        return object()


def test_run_job_is_rejected_before_async_admission_when_local_sync_is_missing(monkeypatch):
    agent = build_agent()

    class MissingJobs:
        @staticmethod
        def find_one(**kwargs):
            return None

    class SyncedRepositories:
        @staticmethod
        def find_one(**kwargs):
            return {"id": kwargs["id"]}

    monkeypatch.setattr(agent_module, "jobs", MissingJobs())
    monkeypatch.setattr(agent_module, "repositories", SyncedRepositories())
    monkeypatch.setattr(
        agent,
        "_Agent__run_command_async",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("rejected jobs must not reach async admission")
        ),
    )

    report = Agent._Agent__handle_execute_command(
        agent,
        {
            "command": "run_job",
            "args": {
                "job_id": 30,
                "repository_id": 80,
                "operation_uuid": "missing-sync-operation",
            },
        },
    )

    assert report.final_state == AgentReportState.failed
    assert "not available in local sync data" in report.log


def test_accepted_async_worker_exception_creates_terminal_failed_report(monkeypatch):
    agent = build_agent()
    agent._Agent__execution = ImmediateExecution()

    class SyncedTable:
        @staticmethod
        def find_one(**kwargs):
            return {"id": next(iter(kwargs.values()))}

    monkeypatch.setattr(agent_module, "jobs", SyncedTable())
    monkeypatch.setattr(agent_module, "repositories", SyncedTable())
    monkeypatch.setattr(
        agent,
        "cmd_run_job",
        lambda **kwargs: (_ for _ in ()).throw(RuntimeError("worker boom")),
    )

    acknowledgement = Agent._Agent__handle_execute_command(
        agent,
        {
            "command": "run_job",
            "args": {
                "job_id": 31,
                "repository_id": 81,
                "operation_uuid": "worker-failure-operation",
            },
        },
    )

    assert acknowledgement.final_state == AgentReportState.success
    reports = [
        report
        for report in AgentReport.finished_reports
        if report.uuid == "worker-failure-operation"
    ]
    assert len(reports) == 1
    assert reports[0].final_state == AgentReportState.failed
    assert reports[0].ended is not None
    assert "worker boom" in reports[0].log


def test_async_worker_exception_does_not_duplicate_handler_report(monkeypatch):
    agent = build_agent()
    agent._Agent__execution = ImmediateExecution()

    def reported_then_failed(**kwargs):
        operation = AgentReport.backup_operation(
            job_id=kwargs["job_id"],
            repository_id=kwargs["repository_id"],
            operation_uuid=kwargs["operation_uuid"],
        )
        operation.log_message("handler reported", final_state=AgentReportState.failed)
        operation.finish()
        raise RuntimeError("failure after report")

    monkeypatch.setattr(agent, "cmd_run_job", reported_then_failed)

    Agent._Agent__run_command_async(
        agent,
        "run_job",
        {
            "job_id": 32,
            "repository_id": 82,
            "operation_uuid": "already-reported-operation",
        },
    )

    reports = [
        report
        for report in AgentReport.finished_reports
        if report.uuid == "already-reported-operation"
    ]
    assert len(reports) == 1
    assert reports[0].log == "handler reported"


def test_queued_reservation_is_recovered_after_restart(monkeypatch):
    agent = build_agent()
    execution = DeferredExecution()
    agent._Agent__execution = execution

    acknowledgement = Agent._Agent__handle_execute_command(
        agent,
        {
            "command": "check_repository",
            "args": {
                "repository_id": 83,
                "operation_uuid": "queued-restart-operation",
            },
        },
    )

    assert acknowledgement.final_state == AgentReportState.success
    assert agent_operations.find_one(uuid="queued-restart-operation")["state"] == "running"
    assert agent_operation_queue.find_one(uuid="queued-restart-operation") is None

    AgentReport.pending_reports = []
    AgentReport.running_operations = {}
    AgentReport.finished_reports.clear()
    AgentReport.recover_interrupted()
    AgentReport.load_queue()

    recovered = AgentReport.finished_reports.popleft()
    assert recovered.uuid == "queued-restart-operation"
    assert recovered.final_state == AgentReportState.failed
    assert agent_operation_queue.find_one(uuid=recovered.uuid)["status"] == "finished"


def test_job_deleted_after_ack_creates_terminal_failed_operation(monkeypatch):
    agent = build_agent()
    execution = DeferredExecution()
    agent._Agent__execution = execution
    job_available = True

    class Jobs:
        @staticmethod
        def find_one(**kwargs):
            return {"id": kwargs["id"]} if job_available else None

    class Repositories:
        @staticmethod
        def find_one(**kwargs):
            return {"id": kwargs["id"]}

    monkeypatch.setattr(agent_module, "jobs", Jobs())
    monkeypatch.setattr(agent_module, "repositories", Repositories())

    acknowledgement = Agent._Agent__handle_execute_command(
        agent,
        {
            "command": "run_job",
            "args": {
                "job_id": 34,
                "repository_id": 84,
                "operation_uuid": "deleted-after-ack-operation",
            },
        },
    )
    job_available = False
    execution.runner()

    assert acknowledgement.final_state == AgentReportState.success
    operation = agent_operations.find_one(uuid="deleted-after-ack-operation")
    assert operation["state"] == "failed"
    reports = [
        report
        for report in AgentReport.finished_reports
        if report.uuid == "deleted-after-ack-operation"
    ]
    assert len(reports) == 1
    assert "Job with id 34 not found" in reports[0].log


def test_submit_rejection_removes_reservation_without_outbox():
    agent = build_agent()

    class RejectingExecution:
        @staticmethod
        def submit(*args, **kwargs):
            return None

    agent._Agent__execution = RejectingExecution()
    acknowledgement = Agent._Agent__handle_execute_command(
        agent,
        {
            "command": "unlock_repository",
            "args": {
                "repository_id": 85,
                "operation_uuid": "rejected-admission-operation",
            },
        },
    )

    assert acknowledgement.final_state == AgentReportState.failed
    assert agent_operations.find_one(uuid="rejected-admission-operation") is None
    assert agent_operation_queue.find_one(uuid="rejected-admission-operation") is None


def test_normal_handler_adopts_reservation_without_duplicate_history(monkeypatch):
    agent = build_agent()
    agent._Agent__execution = ImmediateExecution()

    def run_check(repository_id, operation_uuid):
        report = AgentReport.check_operation(
            repository_id=repository_id,
            operation_uuid=operation_uuid,
        )
        report.log_message("checked")
        return report.finish()

    monkeypatch.setattr(agent, "cmd_check_repository", run_check)

    acknowledgement = Agent._Agent__handle_execute_command(
        agent,
        {
            "command": "check_repository",
            "args": {
                "repository_id": 86,
                "operation_uuid": "adopted-reservation-operation",
            },
        },
    )

    assert acknowledgement.final_state == AgentReportState.success
    assert len(list(agent_operations.find(uuid="adopted-reservation-operation"))) == 1
    assert len(list(agent_operation_queue.find(uuid="adopted-reservation-operation"))) == 1


def test_finished_outbox_sends_offline_backup_before_triggered_retention(monkeypatch):
    agent = build_agent()
    sent = []
    parent = AgentReport(
        AgentReportType.backup,
        operation_uuid="offline-backup",
        persist=False,
    )
    child = AgentReport(
        AgentReportType.retention,
        operation_uuid="offline-retention",
        parent_operation_uuid=parent.uuid,
        persist=False,
    )
    AgentReport.finished_reports.extend([child, parent])

    monkeypatch.setattr(
        agent,
        "_Agent__send_request",
        lambda action, **kwargs: sent.append(kwargs["operation_json"]["uuid"])
        or {"success": True},
    )
    monkeypatch.setattr(agent_module.operation_store, "delete", lambda uuid: None)

    Agent._Agent__flush_report_queue(agent)
    Agent._Agent__flush_report_queue(agent)

    assert sent == ["offline-backup", "offline-retention"]
    assert list(AgentReport.finished_reports) == []


def test_failed_finished_report_rotates_behind_next_report(monkeypatch):
    agent = build_agent()
    attempts = []
    first = AgentReport(
        AgentReportType.backup,
        operation_uuid="failing-first-report",
        persist=False,
    )
    second = AgentReport(
        AgentReportType.restore,
        operation_uuid="successful-second-report",
        persist=False,
    )
    AgentReport.finished_reports.extend([first, second])

    def send_request(action, **kwargs):
        operation_uuid = kwargs["operation_json"]["uuid"]
        attempts.append(operation_uuid)
        return {"success": operation_uuid == second.uuid}

    monkeypatch.setattr(agent, "_Agent__send_request", send_request)
    monkeypatch.setattr(agent_module.operation_store, "delete", lambda uuid: None)

    Agent._Agent__flush_report_queue(agent)
    Agent._Agent__flush_report_queue(agent)

    assert attempts == [first.uuid, second.uuid]
    assert list(AgentReport.finished_reports) == [first]
