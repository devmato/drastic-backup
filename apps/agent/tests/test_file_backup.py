import pytest

from drastic_agent.agent.enums import AgentOperationType
from drastic_agent.agent.report import AgentReport
from drastic_agent.jobs.file_backup import FileBackupJobHandler
from drastic_common.restic.exceptions import ResticFailedError


class _FailingResticApi:
    @staticmethod
    def backup(**kwargs):
        raise ResticFailedError("backup failed", snapshot_id="partial-snap")


def test_failed_file_artifact_keeps_snapshot_id():
    agent = type("Agent", (), {"resticapi": _FailingResticApi()})()
    handler = FileBackupJobHandler(
        agent=agent,
        job={"id": 7, "uuid": "job-uuid-7", "config": {"paths": [{"path": "/data"}]}},
        repository_id=1,
    )
    handler.operation = {"id": 1, "uuid": "operation-uuid-1"}
    artifact = {"uuid": "artifact-1", "artifact_key": "default"}
    handler.start_artifact = lambda artifact_key, report=None: artifact
    handler.finish_artifact = lambda current, **kwargs: current.update(kwargs) or current

    with pytest.raises(ResticFailedError, match="backup failed"):
        handler.run_backup(AgentReport.command_report())

    assert artifact["state"].name == "failed"
    assert artifact["snapshot_id"] == "partial-snap"


@pytest.mark.parametrize(("previous_status", "total_bytes", "total_files"), [
    (None, 1024, 1),
    ({"message_type": "status", "total_bytes": 2048, "total_files": 2, "bytes_done": 256, "files_done": 0}, 1024, 1),
    (None, 0, 0),
])
def test_file_backup_summary_sets_final_totals(previous_status, total_bytes, total_files):
    report = AgentReport(type=AgentOperationType.backup, persist=False)
    report.process_backup_status([previous_status, {
        "message_type": "summary",
        "total_bytes_processed": total_bytes,
        "total_files_processed": total_files,
        "data_added": total_bytes,
        "data_added_packed": total_bytes // 2,
    }])
    assert report.data["bytes_total"] == report.data["bytes_processed"] == total_bytes
    assert report.data["files_total"] == report.data["files_processed"] == total_files
    assert report.data["data_added"] == total_bytes
    assert report.data["data_added_packed"] == total_bytes // 2
    assert report.data["backup_bytes_total"] == total_bytes
    assert report.data["backup_bytes_total_estimated"] is False
    assert report.data["backup_data_complete"] is False  # A summary alone does not confirm success.


def test_backup_progress_requires_finished_scan_and_aggregates_artifacts_once():
    report = AgentReport(type=AgentOperationType.backup, persist=False)
    report.set_data("backup_items_total", 2)
    report.begin_backup_artifact("dataset:large")
    report.process_backup_status({"message_type": "status", "total_bytes": 100, "total_files": 10,
                                  "bytes_done": 20, "files_done": 2, "current_files": ["file"]})
    assert report.data["bytes_total"] is None
    assert report.data["backup_progress"]["total_known"] is False
    report.process_backup_status({"message_type": "verbose_status", "action": "scan_finished",
                                  "data_size": 1000, "total_files": 100})
    assert report.data["backup_progress"]["bytes_total"] == 1000
    assert report.data["bytes_total"] is None  # The next dataset has not been scanned.
    summary = {"message_type": "summary", "total_bytes_processed": 1000, "total_files_processed": 100,
               "data_added": 50, "snapshot_id": "large"}
    report.process_backup_status([summary, summary])
    assert report.data["bytes_processed"] == 1000
    assert report.data["current_files"] == []
    assert "snapshot_id" not in report.data

    report.begin_backup_artifact("dataset:small")
    report.process_backup_status({"message_type": "status", "total_bytes": 10})
    assert report.data["bytes_processed"] == 1000
    assert report.data["backup_progress"]["bytes_processed"] == 0
    assert report.data["backup_progress"]["total_known"] is False
    report.process_backup_status({"message_type": "verbose_status", "action": "scan_finished",
                                  "data_size": 10, "total_files": 1})
    assert report.data["bytes_total"] == 1010
    report.process_backup_status({"message_type": "summary", "total_bytes_processed": 10,
                                  "total_files_processed": 1, "data_added": 5})
    assert report.data["bytes_processed"] == report.data["bytes_total"] == 1010
    assert report.data["files_processed"] == report.data["files_total"] == 101
    assert report.data["data_added"] == 55


def test_failed_unscanned_artifact_keeps_job_total_unknown():
    report = AgentReport(type=AgentOperationType.backup, persist=False)
    report.set_data("backup_items_total", 2)
    report.begin_backup_artifact("failed")
    report.process_backup_status({"message_type": "status", "bytes_done": 5})
    report.begin_backup_artifact("empty")
    report.process_backup_status({"message_type": "summary", "total_bytes_processed": 0, "total_files_processed": 0})
    assert report.data["bytes_processed"] == 5
    assert report.data["bytes_total"] is None
    assert report.data["backup_progress"]["bytes_total"] == 0


def test_size_estimates_are_corrected_without_losing_unstarted_or_failed_artifacts():
    report = AgentReport(type=AgentOperationType.backup, persist=False)
    report.set_data("backup_items_total", 2)
    report.set_backup_size_estimates({"vm:large": 1000, "vm:small": 100})
    assert report.data["backup_bytes_total"] == 1100
    assert report.data["bytes_total"] is None
    assert report.data["backup_bytes_total_estimated"] is True
    report.begin_backup_artifact("vm:large")
    report.process_backup_status({"message_type": "summary", "total_bytes_processed": 2000})
    assert report.data["backup_bytes_total"] == 2100
    report.set_backup_size_estimates({"vm:small": 200})
    assert report.data["backup_bytes_total"] == 2200
    report.begin_backup_artifact("vm:small")
    report.process_backup_status({"message_type": "status", "bytes_done": 50})
    assert report.data["bytes_processed"] == 2050
    assert report.data["backup_bytes_total"] == 2200
    assert report.data["backup_data_complete"] is False
