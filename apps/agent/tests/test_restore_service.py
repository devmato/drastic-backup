from contextlib import nullcontext
from threading import Event
from types import SimpleNamespace

import pytest

from drastic_agent.agent.enums import AgentReportState
from drastic_agent.services.restore import RestoreService
from drastic_common.restic.exceptions import ResticError


class FakeReport:
    def __init__(self):
        self.uuid = "operation-1"
        self.cancel_event = Event()
        self.final_state = AgentReportState.success
        self.logs = []
        self.data = {}

    def log_message(self, message, final_state=None):
        self.logs.append(message)
        if final_state is not None:
            self.final_state = final_state

    def finish(self):
        return self


class FakeRestic:
    def operation_cancellation(self, event):
        return nullcontext()

    def __init__(self, snapshots, restore_error=None):
        self._snapshots = snapshots
        self._restore_error = restore_error
        self.restore_calls = []

    def snapshots(self, tags=None):
        return self._snapshots

    def restore(self, **kwargs):
        self.restore_calls.append(kwargs)
        if self._restore_error:
            raise self._restore_error
        return '{"message_type":"summary"}'


@pytest.mark.parametrize("target", ["/", "//", "///"])
def test_agent_rejects_all_root_target_spellings(target):
    with pytest.raises(ValueError, match="Restoring to /"):
        RestoreService._normalize_restore_target(target)


def run_restore(monkeypatch, tmp_path, snapshots, **overrides):
    report = FakeReport()
    monkeypatch.setattr(
        "drastic_agent.services.restore.AgentReport.restore_report", lambda **kwargs: report
    )
    monkeypatch.setattr(
        "drastic_agent.services.restore.AgentReport.process_restore_status", lambda *args, **kwargs: None
    )
    restic = FakeRestic(snapshots, restore_error=overrides.pop("restore_error", None))
    agent = SimpleNamespace(resticapi=restic, configure_repository=lambda repository: None)
    kwargs = {
        "operation_uuid": "operation-1",
        "job_id": 1,
        "job_uuid": "job-uuid",
        "repository_id": 2,
        "repository": {},
        "snapshot_id": "snapshot-full-id",
        "restore_location": str(tmp_path / "restore"),
        "include_paths": ["etc//hosts"],
        "expected_job_tag": "job_uuid:job-uuid",
    }
    kwargs.update(overrides)
    RestoreService.run_restore(agent, **kwargs)
    return report, restic


def test_agent_requires_exact_snapshot_id_and_job_tag(monkeypatch, tmp_path):
    report, restic = run_restore(
        monkeypatch,
        tmp_path,
        [{"id": "snapshot-full-id", "tags": ["job_uuid:other-job"]}],
    )

    assert report.final_state == AgentReportState.failed
    assert restic.restore_calls == []


def test_agent_fails_when_restore_output_exists(monkeypatch, tmp_path):
    destination = tmp_path / "restore" / "etc"
    destination.mkdir(parents=True)
    (destination / "hosts").write_text("existing")
    report, restic = run_restore(
        monkeypatch,
        tmp_path,
        [{"id": "snapshot-full-id", "tags": ["job_uuid:job-uuid"]}],
    )

    assert report.final_state == AgentReportState.failed
    assert restic.restore_calls == []


def test_agent_normalizes_paths_and_allows_explicit_overwrite(monkeypatch, tmp_path):
    report, restic = run_restore(
        monkeypatch,
        tmp_path,
        [{"id": "snapshot-full-id", "tags": ["job_uuid:job-uuid"]}],
        overwrite_policy="overwrite",
    )

    assert report.final_state == AgentReportState.success
    assert restic.restore_calls[0]["include_paths"] == ["/etc/hosts"]
    assert restic.restore_calls[0]["overwrite_policy"] == "overwrite"


def test_agent_passes_fail_if_exists_policy_to_restic(monkeypatch, tmp_path):
    report, restic = run_restore(
        monkeypatch,
        tmp_path,
        [{"id": "snapshot-full-id", "tags": ["job_uuid:job-uuid"]}],
    )

    assert report.final_state == AgentReportState.success
    assert restic.restore_calls[0]["overwrite_policy"] == "fail_if_exists"


def test_agent_rejects_symlink_in_restore_target(monkeypatch, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (tmp_path / "linked").symlink_to(outside, target_is_directory=True)

    report, restic = run_restore(
        monkeypatch,
        tmp_path,
        [{"id": "snapshot-full-id", "tags": ["job_uuid:job-uuid"]}],
        restore_location=str(tmp_path / "linked" / "restore"),
    )

    assert report.final_state == AgentReportState.failed
    assert restic.restore_calls == []
    assert not (outside / "restore").exists()


def test_agent_marks_failed_started_restore_as_partial(monkeypatch, tmp_path):
    report, restic = run_restore(
        monkeypatch,
        tmp_path,
        [{"id": "snapshot-full-id", "tags": ["job_uuid:job-uuid"]}],
        restore_error=ResticError("restore interrupted"),
    )

    assert len(restic.restore_calls) == 1
    assert report.final_state == AgentReportState.failed
    assert report.data["partial_failure"] is True
    assert report.data["destination_may_contain_restored_data"] is True
