import pytest

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
