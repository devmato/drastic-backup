from concurrent.futures import ThreadPoolExecutor

from drastic_agent.agent.database import (
    agent_operation_artifacts,
    agent_operation_queue,
    agent_operations,
)
from drastic_agent.agent.enums import AgentOperationState
from drastic_agent.agent.operation_store import operation_store
from drastic_agent.agent.report import AgentReport


def setup_function():
    agent_operation_artifacts.delete()
    agent_operation_queue.delete()
    agent_operations.delete()
    AgentReport.pending_reports = []
    AgentReport.finished_reports.clear()
    AgentReport.running_operations = {}


def test_finished_report_survives_memory_queue_reset():
    report = AgentReport.job_report(job_id=4, repository_id=8, operation_uuid="durable-1")
    report.log_message("durable log")
    report.finish()

    AgentReport.pending_reports = []
    AgentReport.finished_reports.clear()
    AgentReport.load_queue()

    restored = AgentReport.finished_reports.popleft()
    assert restored.uuid == "durable-1"
    assert restored.log == "durable log"


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
