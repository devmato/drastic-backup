import pytest

from drastic_agent.agent.enums import AgentOperationType
from drastic_agent.agent.report import AgentReport
from drastic_agent.jobs.file_backup import FileBackupJobHandler
from drastic_common.restic.exceptions import ResticFailedError


class _FailingResticApi:
    @staticmethod
    def backup(**kwargs):
        raise ResticFailedError("backup failed", snapshot_id="partial-snap")


class _Report:
    @staticmethod
    def log_message(message):
        return None


def test_failed_file_artifact_keeps_snapshot_id():
    agent = type("Agent", (), {"resticapi": _FailingResticApi()})()
    handler = FileBackupJobHandler(
        agent=agent,
        job={"id": 7, "uuid": "job-uuid-7", "config": {"paths": [{"path": "/data"}]}},
        repository_id=1,
    )
    handler.operation = {"id": 1, "uuid": "operation-uuid-1"}
    artifact = {"uuid": "artifact-1", "artifact_key": "default"}
    handler.start_artifact = lambda artifact_key: artifact
    handler.finish_artifact = lambda current, **kwargs: current.update(kwargs) or current

    with pytest.raises(ResticFailedError, match="backup failed"):
        handler.run_backup(_Report())

    assert artifact["state"].name == "failed"
    assert artifact["snapshot_id"] == "partial-snap"


@pytest.mark.parametrize(("previous_status", "total_bytes", "total_files"), [
    (None, 1024, 1),
    ({"message_type": "status", "total_bytes": 2048, "total_files": 2, "bytes_done": 256, "files_done": 0}, 1024, 1),
    (None, 0, 0),
])
def test_file_backup_summary_sets_final_totals(previous_status, total_bytes, total_files):
    report = AgentReport(type=AgentOperationType.backup, persist=False)
    AgentReport._process_restic_status(report, [previous_status, {
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
