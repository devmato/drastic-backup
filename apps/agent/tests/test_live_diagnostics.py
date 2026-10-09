import json
import logging
import os
from threading import Event, Thread

import pytest

from drastic_agent.agent.enums import AgentReportType
from drastic_agent.agent.report import AgentReport
from drastic_agent.runtime.agent import Agent
from drastic_agent.runtime.execution import ExecutionManager
from drastic_agent.services import diagnostics as live
from drastic_common import diagnostics


@pytest.fixture
def agent(tmp_path, monkeypatch):
    monkeypatch.setenv("DRASTIC_AGENT_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(AgentReport, "pending_reports", [])
    monkeypatch.setattr(AgentReport, "finished_reports", [])
    monkeypatch.setattr(AgentReport, "running_operations", {})
    instance = Agent.__new__(Agent)
    instance.client = None
    instance._Agent__execution = ExecutionManager(max_workers=1)
    instance._diagnostic_report = AgentReport.command_report(data={"diagnostic": True})
    live.install_log(tmp_path)
    try:
        yield instance
    finally:
        diagnostics.configure()
        instance._Agent__execution.shutdown()
        if live._handler:
            logging.getLogger().removeHandler(live._handler)
            live._handler.close()
            live._handler = None


def query(agent, section, enabled=True, **kwargs):
    return agent._Agent__handle_execute_command({
        "command": "debug_state", "args": {"enabled": enabled, "section": section, **kwargs},
    })


def test_direct_reads_survive_blocked_report_and_worker_locks(agent, monkeypatch, tmp_path):
    report = AgentReport(type=AgentReportType.backup, persist=False)
    AgentReport.running_operations[report.uuid] = report
    query(agent, None)
    diagnostics.remember_secrets({"password": "private-value"})
    logging.warning("Report failed with password=private-value")
    entered, release = Event(), Event()

    def blocked():
        with report._lock, agent._diagnostic_report._lock, agent._Agent__execution._lock, AgentReport._registry_lock:
            entered.set()
            release.wait()

    holder = Thread(target=blocked, daemon=True)
    holder.start()
    assert entered.wait(1)
    monkeypatch.setattr(agent, "_Agent__send_request", lambda *_args, **_kwargs: {"success": False})
    sender = Thread(target=agent._Agent__send_report, args=(report,), daemon=True)
    sender.start()
    for _ in range(100):
        if getattr(agent, "_debug_report", None):
            break
        Event().wait(0.001)
    results = []

    def inspect():
        for section in ("runtime", "threads", "logs"):
            results.append(query(agent, section).data)

    reader = Thread(target=inspect, daemon=True)
    try:
        reader.start()
        reader.join(2)
        assert not reader.is_alive(), "The live command must not wait for report/worker locks"
        state, stacks, logs = [result["data"] for result in results]
        assert state["execution"]["unavailable"] == "lock_busy"
        assert state["operations"]["unavailable"] == "lock_busy"
        assert state["report_delivery"]["phase"] == "serializing"
        assert any(frame["function"] == "blocked" for thread in stacks["items"] for frame in thread["stack"])
        assert "Report failed" in json.dumps(logs) and "private-value" not in json.dumps(logs)
        assert "private-value" not in (tmp_path / live.LOG_NAME).read_text()
    finally:
        release.set()
        holder.join(2)
        sender.join(2)
        reader.join(2)


def test_logs_are_opt_in_bounded_and_readable_after_reopening(agent, tmp_path):
    query(agent, None, enabled=False)
    logging.warning("not recorded")
    assert not (tmp_path / live.LOG_NAME).exists()
    query(agent, None)
    live._handler.maxBytes = 512
    for number in range(20):
        logging.warning("recorded %s", number)
    path = tmp_path / live.LOG_NAME
    assert path.exists() and path.with_name(live.LOG_NAME + ".1").exists()
    assert os.stat(path).st_mode & 0o777 == 0o600
    live._handler.close()
    assert "recorded 19" in json.dumps(query(agent, "logs").data)
    query(agent, None, enabled=False)
    before = path.read_bytes()
    logging.warning("disabled again")
    assert path.read_bytes() == before
    denied = query(agent, "logs", enabled=False)
    assert denied.data == {"enabled": False}
    assert query(agent, "../../etc/passwd").state.name == "failed"
    assert query(agent, "runtime", limit=201).state.name == "failed"
