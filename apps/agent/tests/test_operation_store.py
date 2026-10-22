import fcntl
import json
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from threading import Event
from types import SimpleNamespace

import pytest

from drastic_agent.agent.enums import AgentOperationState
from drastic_agent.agent.report import AgentReport
from drastic_agent.runtime import reporting
from drastic_agent.storage.database import (
    agent_operation_artifacts,
    agent_operation_queue,
    agent_operations,
)
from drastic_agent.storage.operation_store import operation_store
from drastic_common.agent.schemas import AgentOperationSchema


def setup_function():
    agent_operation_artifacts.delete()
    agent_operation_queue.delete()
    agent_operations.delete()
    AgentReport.pending_reports = []
    AgentReport.finished_reports.clear()
    AgentReport.running_operations = {}


def test_finished_report_survives_memory_queue_reset(berlin_timezone):
    report = AgentReport.job_report(job_id=4, repository_id=8, operation_uuid="durable-1")
    report.log_message("durable log")
    report.set_artifacts([{"uuid": "durable-artifact", "artifact_key": "default", "state": "success",
                           "forgotten_at": report.started.isoformat()}])
    report.finish()

    AgentReport.pending_reports = []
    AgentReport.finished_reports.clear()
    AgentReport.load_queue()

    restored = AgentReport.finished_reports.popleft()
    assert restored.uuid == "durable-1"
    assert restored.log == "durable log"
    assert restored.started == report.started
    assert restored.ended == report.ended
    assert restored.started.utcoffset().total_seconds() == 0
    payload = AgentOperationSchema().dump(restored)
    for value in (payload["started"], payload["ended"], payload["logs"][0]["created"]):
        assert value.endswith("+00:00")
    assert payload["artifacts"][0]["forgotten_at"] == payload["started"]


@pytest.mark.parametrize("month, utc_hour", [(7, 7), (1, 8)])
def test_legacy_outbox_uses_agents_local_timezone(berlin_timezone, month, utc_hour):
    report = AgentReport.job_report(job_id=4, repository_id=8, operation_uuid="legacy-time")
    report.log_message("legacy log")
    report.finish()
    row = agent_operation_queue.find_one(uuid=report.uuid)
    payload = json.loads(row["payload"])
    payload["started"] = f"2026-{month:02d}-04T09:54:41"
    payload["ended"] = f"2026-{month:02d}-04T09:54:47"
    payload["logs"][0]["created"] = payload["started"]
    row["payload"] = json.dumps(payload)
    agent_operation_queue.update(row, ["id"])

    restored = operation_store.load(AgentReport)[0]
    assert restored.started == datetime(2026, month, 4, utc_hour, 54, 41, tzinfo=timezone.utc)
    assert (restored.ended - restored.started).total_seconds() == 6
    assert restored.logs[0]["created"] == restored.started


def test_running_report_is_failed_during_recovery():
    AgentReport.job_report(job_id=4, repository_id=8, operation_uuid="interrupted-1")

    assert agent_operation_queue.find_one(uuid="interrupted-1") is None

    AgentReport.pending_reports = []
    AgentReport.finished_reports.clear()
    AgentReport.recover_interrupted()
    AgentReport.load_queue()

    restored = AgentReport.finished_reports.popleft()
    assert restored.uuid == "interrupted-1"
    assert restored.final_state.name == "failed"
    assert "stopped before" in restored.log
    assert agent_operations.find_one(uuid="interrupted-1")["state"] == "failed"

    AgentReport.recover_interrupted()
    assert len(list(agent_operation_queue.find(uuid="interrupted-1"))) == 1


@pytest.mark.parametrize("outcome", ["success", "failed", "interrupted"])
@pytest.mark.parametrize("os_name", ["linux", "darwin"])
def test_update_report_survives_restart_and_retries_delivery(tmp_path, monkeypatch, outcome, os_name):
    import drastic_agent.runtime.agent as agent_module

    monkeypatch.setattr(agent_module.updates, "sys", SimpleNamespace(platform=os_name))
    root = tmp_path / "installation"
    root.mkdir()
    data = tmp_path / "mounted-data"
    data.mkdir()
    monkeypatch.setattr(agent_module, "AGENT_INSTALL_ROOT", root)
    monkeypatch.setenv("DRASTIC_AGENT_DATA_DIR", str(data))
    path = data / "update-update-1.json"
    status = {
        "uuid": "update-1", "state": "running", "started": "2026-10-05T09:00:00+00:00", "ended": None,
        "logs": [{"sequence": 1, "level": "info", "created": "2026-10-05T09:00:00+00:00",
                  "message": "Agent update started; preparing release"}],
    }
    path.write_text(json.dumps(status))
    agent = agent_module.Agent.__new__(agent_module.Agent)
    payloads = []
    accepted = True

    def send(action, **kwargs):
        assert action == "operation"
        payloads.append(kwargs["operation_json"])
        return {"success": accepted}

    monkeypatch.setattr(agent, "_Agent__send_request", send)
    descriptor = (os.open(root / ".installer.lock", os.O_RDWR | os.O_CREAT, 0o600)
                  if os_name == "darwin" else os.open(root, os.O_RDONLY | os.O_DIRECTORY))
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        agent._Agent__read_update_reports()
        agent._Agent__flush_report_queue()
        assert payloads[-1]["state"] == "running"
        assert payloads[-1]["type"] == "agent_update"
        assert payloads[-1]["started"] == status["started"]
        AgentReport.cancel_running()
        AgentReport.save_queue()
        assert agent_operations.find_one(uuid="update-1")["state"] == "running"
        AgentReport.recover_interrupted()
        AgentReport.load_queue()
        assert not AgentReport.finished_reports
        agent._Agent__read_update_reports()
        agent._Agent__read_update_reports()
        assert len(AgentReport.pending_reports) == 1
        assert len(AgentReport.pending_reports[0].logs) == 1
        if outcome != "interrupted":
            status.update(state=outcome, ended="2026-10-05T09:01:00+00:00")
            status["logs"].append({"sequence": 2, "level": "error" if outcome == "failed" else "info",
                                   "created": status["ended"], "message": f"Update {outcome}"})
            path.write_text(json.dumps(status))
    finally:
        os.close(descriptor)

    accepted = False
    agent._Agent__read_update_reports()
    assert not path.exists()
    AgentReport.load_queue()  # Terminal report is durable before removing the installer status.
    agent._Agent__flush_report_queue()
    assert len(AgentReport.finished_reports) == 1
    accepted = True
    agent._Agent__flush_report_queue()
    assert not AgentReport.finished_reports
    assert agent_operation_queue.find_one(uuid="update-1") is None
    assert payloads[-1] == payloads[-2]
    assert payloads[-1]["state"] == ("failed" if outcome == "interrupted" else outcome)
    assert payloads[-1]["uuid"] == "update-1"
    assert [log["sequence"] for log in payloads[-1]["logs"]] == [1, 2]
    if outcome == "interrupted":
        assert "interrupted" in payloads[-1]["logs"][-1]["message"]
    else:
        assert payloads[-1]["ended"] == status["ended"]
        assert payloads[-1]["logs"][-1]["level"] == status["logs"][-1]["level"]
    # A crash between committing the outbox and deleting the status must not enqueue it again.
    path.write_text(json.dumps(status))
    agent._Agent__read_update_reports()
    assert not path.exists()
    assert not AgentReport.finished_reports


def test_uuid_registry_tracks_running_report():
    report = AgentReport.restore_report("restore-1", job_id=4, repository_id=8)
    assert AgentReport.get_report(uuid="restore-1") is report

    report.finish()
    assert AgentReport.get_report(uuid="restore-1") is None


def test_running_restore_is_history_but_not_an_outbox_entry():
    report = AgentReport.restore_report("restore-persisted", job_id=4, repository_id=8)

    assert agent_operation_queue.find_one(uuid="restore-persisted") is None
    assert agent_operations.find_one(uuid="restore-persisted")["state"] == "running"
    assert AgentReport.get_report(uuid="restore-persisted") is report


def test_progress_updates_do_not_persist_the_report_to_outbox():
    report = AgentReport.job_report(job_id=4, repository_id=8, operation_uuid="progress-1")

    report.set_data("bytes_processed", 10)
    report.log_message("progress")
    report.mark_logs_sent()

    assert agent_operation_queue.find_one(uuid="progress-1") is None


def test_large_reports_batch_logs_retry_and_replay_before_acknowledging_completion():
    report = AgentReport.job_report(job_id=4, repository_id=8, operation_uuid="large-report")
    for index in range(250):
        report.log_message(f"{index}:" + '\n"ä' * 1000)
    report.set_data("backup_data_complete", True)
    report.finish()
    payloads, received = [], {}
    accepted = False

    def request(_action, *, operation_json):
        payloads.append(operation_json)
        assert len(json.dumps(operation_json).encode()) < 1_000_000
        if accepted:
            received.update({log["sequence"]: log["message"] for log in operation_json["logs"]})
        return {"success": accepted}

    def send(item):
        return reporting.send(item, request=request, debug={})

    reporting.flush(send)
    assert agent_operation_queue.find_one(uuid=report.uuid) is not None
    accepted = True
    reporting.flush(send)
    assert payloads[0] == payloads[1] and payloads[1]["state"] == "running"
    assert agent_operation_queue.find_one(uuid=report.uuid) is not None
    AgentReport.load_queue()
    for _ in range(10):
        if not AgentReport.finished_reports:
            break
        reporting.flush(send)
    assert not AgentReport.finished_reports
    assert payloads[2] == payloads[1]  # A restart replays acknowledged batches safely.
    assert len(received) == 250
    assert all(payload["ended"] is None and payload["state"] == "running" for payload in payloads[:-1])
    assert payloads[-1]["state"] == "success" and payloads[-1]["ended"]
    assert payloads[-1]["data"]["backup_data_complete"]
    assert agent_operation_queue.find_one(uuid=report.uuid) is None


def test_old_oversized_log_entries_do_not_poison_the_durable_queue():
    report = AgentReport.job_report(job_id=4, repository_id=8, operation_uuid="legacy-large-report")
    report.log_message("x" * 1_100_000)
    assert len(report.logs[0]["message"]) < 8300 and report.logs[0]["message"].endswith("[log message truncated]")
    report._logs[0]["message"] = "x" * 1_100_000
    report.finish()
    AgentReport.load_queue()
    sent = []
    reporting.flush(lambda item: reporting.send(item, request=lambda _action, **args:
        sent.append(args["operation_json"]) or {"success": True}, debug={}))
    assert not AgentReport.finished_reports
    assert len(json.dumps(sent[0])) < 1_000_000
    assert sent[0]["logs"][0]["message"].endswith("[log message truncated]")


def test_concurrent_delivery_cannot_overtake_an_acknowledgement_or_hide_new_logs():
    report = AgentReport.job_report(job_id=4, repository_id=8, operation_uuid="concurrent-delivery")
    report.log_message("first")
    entered, release = Event(), Event()
    sent = []

    def request(_action, *, operation_json):
        sent.append(operation_json)
        entered.set()
        assert release.wait(5)
        return {"success": True}

    with ThreadPoolExecutor(max_workers=1) as executor:
        first = executor.submit(reporting.send, report, request=request, debug={})
        try:
            assert entered.wait(5)
            assert reporting.send(report, request=request, debug={}) is False
            report.log_message("second")
            assert len(sent) == 1
        finally:
            release.set()
        assert first.result(timeout=5)
    assert not report.sent
    assert [log["message"] for log in report.logs] == ["second"]


def test_invalid_check_input_does_not_prepare_or_mark_the_repository(monkeypatch):
    from drastic_agent.runtime.agent import Agent

    agent = Agent.__new__(Agent)
    monkeypatch.setattr(agent, "_Agent__set_repository", lambda _: pytest.fail("Invalid input must not touch storage"))
    report = agent.cmd_check_repository(repository_id=8, read_data_subset="banana")
    assert report.final_state == AgentOperationState.failed
    assert report.data["invalid_check_input"]
    assert not report.data.get("retention_check_failed")


def test_recovery_fails_running_artifacts_and_enqueues_once():
    report = AgentReport.job_report(
        job_id=4,
        repository_id=8,
        operation_uuid="artifact-interrupted-1",
    )
    agent_operation_artifacts.insert(
        {
            "uuid": "artifact-1",
            "operation_id": report.history_operation["id"],
            "artifact_key": "default",
            "snapshot_id": None,
            "state": "running",
            "data": {},
            "forgotten_at": None,
        }
    )

    AgentReport.pending_reports = []
    AgentReport.running_operations = {}
    AgentReport.recover_interrupted()
    AgentReport.recover_interrupted()

    artifact = agent_operation_artifacts.find_one(uuid="artifact-1")
    queued = agent_operation_queue.find_one(uuid="artifact-interrupted-1")
    assert artifact["state"] == "failed"
    assert queued["status"] == "finished"
    assert len(list(agent_operation_queue.find(uuid="artifact-interrupted-1"))) == 1


def test_recovery_outbox_includes_local_snapshot_artifacts():
    report = AgentReport.job_report(
        job_id=4,
        repository_id=8,
        operation_uuid="artifact-snapshot-interrupted-1",
    )
    agent_operation_artifacts.insert(
        {
            "uuid": "artifact-snapshot-1",
            "operation_id": report.history_operation["id"],
            "artifact_key": "vm:101",
            "snapshot_id": "local-snapshot-1",
            "state": "running",
            "data": {"vmid": 101},
            "forgotten_at": None,
        }
    )

    AgentReport.pending_reports = []
    AgentReport.running_operations = {}
    AgentReport.recover_interrupted()
    AgentReport.load_queue()

    restored = AgentReport.finished_reports.popleft()
    assert restored.final_state == AgentOperationState.failed
    assert restored.artifacts == [
        {
            "uuid": "artifact-snapshot-1",
            "artifact_key": "vm:101",
            "snapshot_id": "local-snapshot-1",
            "state": "failed",
            "data": {"vmid": 101},
            "forgotten_at": None,
        }
    ]


def test_shutdown_finalization_preserves_cancelled_state():
    report = AgentReport.job_report(
        job_id=4,
        repository_id=8,
        operation_uuid="shutdown-cancelled-1",
    )

    AgentReport.cancel_running()
    AgentReport.save_queue()

    assert report.final_state == AgentOperationState.cancelled
    assert agent_operations.find_one(uuid=report.uuid)["state"] == "cancelled"
    assert agent_operation_queue.find_one(uuid=report.uuid)["status"] == "finished"


def test_corrupt_persisted_report_does_not_block_recovery():
    agent_operation_queue.insert(
        {"uuid": "corrupt-1", "status": "finished", "payload": "not-json"}
    )

    assert operation_store.load(AgentReport) == []
    assert agent_operation_queue.find_one(uuid="corrupt-1") is None


def test_cancelled_report_cannot_regress_to_failed():
    report = AgentReport.job_report(job_id=4, repository_id=8, operation_uuid="cancelled-1")

    report.final_state = AgentOperationState.cancelled
    report.log_message("restic exited after cancellation", final_state=AgentOperationState.failed)

    assert report.final_state == AgentOperationState.cancelled


def test_parallel_operation_updates_are_persisted_as_consistent_snapshot():
    report = AgentReport.job_report(
        job_id=4,
        repository_id=8,
        operation_uuid="parallel-updates-1",
    )

    def update(index):
        report.log_message(f"log-{index}")
        report.set_data(f"value-{index}", index)
        report.set_artifacts(
            [
                {
                    "uuid": f"artifact-{index}",
                    "snapshot_id": f"snapshot-{index}",
                    "data": {"index": index},
                }
            ]
        )

    with ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(update, range(100)))

    report.finish()
    AgentReport.finished_reports.clear()
    AgentReport.load_queue()
    restored = AgentReport.finished_reports.popleft()

    assert [log["sequence"] for log in restored.logs] == list(range(1, 101))
    assert restored.data == {f"value-{index}": index for index in range(100)}
    artifact = restored.artifacts[0]
    index = artifact["data"]["index"]
    assert artifact["uuid"] == f"artifact-{index}"
    assert artifact["snapshot_id"] == f"snapshot-{index}"
