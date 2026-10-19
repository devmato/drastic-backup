import unittest

import pytest

import drastic_agent.jobs.base as base_module
from drastic_agent.agent.action import AgentAction
from drastic_agent.agent.enums import AgentReportState
from drastic_agent.agent.report import AgentReport
from drastic_agent.jobs.base import BackupJobHandler
from drastic_agent.jobs.registry import get_job_handler
from drastic_agent.runtime.agent import Agent
from drastic_common.restic.exceptions import ResticError, ResticFailedError


@pytest.fixture(autouse=True)
def isolate_completion_tagging(monkeypatch):
    # Restic rewrite/restart behavior is covered in test_backup_completion; isolate lifecycle outcomes here.
    monkeypatch.setattr(base_module, "complete_backup", lambda *args: None)


class _FakeReport:
    def __init__(self):
        self.final_state = None
        self.messages = []

    def log_message(self, message, final_state=None):
        self.messages.append(message)
        if final_state is not None:
            self.final_state = final_state


class _FakeResticApi:
    def __init__(self, config_error):
        self._config_error = config_error
        self.init_called = False

    def cat_config(self):
        raise self._config_error

    def init(self):
        self.init_called = True


class _FakeAgent:
    def __init__(self, resticapi):
        self.resticapi = resticapi


class _Handler(BackupJobHandler):
    def run_backup(self, report):
        artifact = self.start_artifact("default")
        self.finish_artifact(artifact, snapshot_id="snapshot-1")


class _FailingBackupHandler(BackupJobHandler):
    error = ResticError("backup failed")
    cancel = False

    def run_backup(self, report):
        if self.cancel:
            report.cancel_event.set()
        raise self.error


class _TrackingBackupHandler(BackupJobHandler):
    backup_calls = 0

    def run_backup(self, report):
        self.backup_calls += 1


class _FakeActions:
    @staticmethod
    def find(**kwargs):
        return []


class _FakeRetentions:
    @staticmethod
    def find_one(**kwargs):
        return None


class _ConfiguredRetentions:
    @staticmethod
    def find_one(**kwargs):
        return {"id": kwargs["id"], "name": "Daily"}


class _SuccessfulReport:
    type = base_module.AgentOperationType.retention
    state = AgentReportState.success
    log_list = ["retention ok"]
    data = {"cleanup_pending": False}


class _CheckingResticApi:
    def __init__(self, check_error=None):
        self.check_error = check_error
        self.check_calls = []
        self.stats_calls = 0

    @staticmethod
    def cat_config():
        return {"id": "repo-id"}

    def stats(self):
        self.stats_calls += 1
        return {"total_size": 123, "mode": "raw-data"}

    def check(self, **kwargs):
        self.check_calls.append(kwargs)
        if self.check_error:
            raise self.check_error
        return {"message_type": "summary", "errors": 0}


class _CheckingAgent:
    owner_uuid = "00000000-0000-0000-0000-000000000001"

    def __init__(self, resticapi):
        self.resticapi = resticapi

    @staticmethod
    def set_repository(repository_id):
        return {"id": repository_id, "location": "/repo"}

    @staticmethod
    def execute_actions(report, actions):
        return None

    @staticmethod
    def cmd_run_retention(**kwargs):
        return _SuccessfulReport()


class BackupJobHandlerTests(unittest.TestCase):
    def test_native_repository_is_not_initialized_by_agent(self):
        resticapi = _FakeResticApi(ResticError("unable to open config file"))
        handler = _Handler(_FakeAgent(resticapi), {"id": 1}, repository_id=1)
        handler.repository = {"id": 1, "kind": "native", "location": "native/repo"}
        report = _FakeReport()

        handler._ensure_repository_initialized(report)

        self.assertFalse(resticapi.init_called)
        self.assertEqual(report.final_state, AgentReportState.failed)
        self.assertIn("Native repository is not initialized", report.messages[0])


def test_repository_stats_failure_marks_completed_backup_warning(monkeypatch):
    AgentReport.pending_reports = []
    AgentReport.finished_reports.clear()
    monkeypatch.setattr(base_module, "actions", _FakeActions())
    monkeypatch.setattr(base_module, "retentions", _FakeRetentions())
    resticapi = _CheckingResticApi()

    def fail_stats():
        raise ResticFailedError("stats failed: connection reset by peer")

    monkeypatch.setattr(resticapi, "stats", fail_stats)
    handler = _Handler(
        _CheckingAgent(resticapi),
        {"id": 1, "uuid": "job-uuid-1"},
        repository_id=2,
        run_options={"schedule_id": 1, "retry": {"enabled": True}},
    )

    report = handler.run()

    assert report.final_state == AgentReportState.warning
    assert any("stats failed" in line for line in report.log_list)
    assert report.data["backup_data_complete"] is True
    assert report.data["backup_retryable"] is False
    assert "stats" not in report.data
    assert list(AgentReport.finished_reports) == [report]
    assert "another attempt is eligible" not in report.log


def test_backup_job_uses_server_operation_uuid(monkeypatch):
    AgentReport.pending_reports = []
    AgentReport.finished_reports.clear()
    monkeypatch.setattr(base_module, "actions", _FakeActions())
    monkeypatch.setattr(base_module, "retentions", _FakeRetentions())
    handler = _Handler(
        _CheckingAgent(_CheckingResticApi()),
        {"id": 1, "uuid": "job-uuid-1"},
        repository_id=2,
        operation_uuid="server-operation-uuid",
    )

    report = handler.run()

    assert report.uuid == "server-operation-uuid"
    assert handler.operation["uuid"] == "server-operation-uuid"


def test_job_registry_forwards_server_operation_uuid():
    handler = get_job_handler(
        agent=object(),
        job={"id": 1, "type": "file"},
        repository_id=2,
        operation_uuid="server-operation-uuid",
    )

    assert handler.operation_uuid == "server-operation-uuid"


@pytest.mark.parametrize("own_retention", [False, True])
@pytest.mark.parametrize("retention_state", [AgentReportState.success, AgentReportState.warning])
def test_post_backup_check_runs_before_retention_and_stats(monkeypatch, own_retention, retention_state):
    AgentReport.pending_reports = []
    AgentReport.finished_reports.clear()
    monkeypatch.setattr(base_module, "actions", _FakeActions())
    monkeypatch.setattr(base_module, "retentions", _ConfiguredRetentions())
    monkeypatch.setattr(_SuccessfulReport, "state", retention_state)
    monkeypatch.setattr(_SuccessfulReport, "log_list", ["Prune unavailable"])
    resticapi = _CheckingResticApi()
    handler = _Handler(
        _CheckingAgent(resticapi),
        {
            "id": 1,
            "uuid": "job-uuid-1",
            "retention_id": 3 if own_retention else None,
        },
        repository_id=2,
        run_options={"repository_check": {"enabled": True, "read_data": "1/10"}},
    )

    phases, targets = [], []

    def record_phase(expected):
        assert handler.report.state == AgentReportState.running
        assert handler.report.data["backup_phase"] == expected
        assert handler.report.artifacts[0]["state"] == "success"
        assert handler.report.data["backup_data_complete"] is True
        phases.append(expected)

    def check(**kwargs):
        record_phase("check")
        return _CheckingResticApi.check(resticapi, **kwargs)

    def retention(**kwargs):
        record_phase("retention")
        targets.append((kwargs["job_id"], kwargs["repository_id"]))
        return _SuccessfulReport()

    def stats(**kwargs):
        record_phase("statistics")
        return _CheckingResticApi.stats(resticapi)

    monkeypatch.setattr(resticapi, "check", check)
    monkeypatch.setattr(handler.agent, "cmd_run_retention", retention, raising=False)
    monkeypatch.setattr(resticapi, "stats", stats)
    report = handler.run()

    assert phases == ["check", "retention", "statistics"]
    assert targets == [(1, 2)]
    assert report.final_state == retention_state
    assert report.data["retention_logs_nested"] is True
    assert ("Retention did not complete: Prune unavailable" in report.log) is (retention_state == AgentReportState.warning)
    assert "Prune unavailable" not in report.log_list
    assert resticapi.check_calls == [{"read_data": False, "read_data_subset": "1/10"}]
    assert report.data["check"] == {"message_type": "summary", "errors": 0}
    assert report.data["stats"] == {"total_size": 123, "mode": "raw-data"}
    assert report.data["config"] == {"id": "repo-id"}
    assert list(AgentReport.finished_reports) == [report]


@pytest.mark.parametrize("locked", [False, True])
def test_post_backup_check_failure_warns_after_successful_backup(monkeypatch, locked):
    AgentReport.pending_reports = []
    AgentReport.finished_reports.clear()
    hook_calls = []

    class HookActions:
        @staticmethod
        def find(job_id, hook):
            return [{"hook": hook}]

    class CheckFailingAgent(_CheckingAgent):
        retention_calls = 0

        @classmethod
        def execute_actions(cls, report, actions):
            hook_calls.append(actions[0]["hook"])

        @classmethod
        def cmd_run_retention(cls, **kwargs):
            cls.retention_calls += 1
            return _SuccessfulReport()

    monkeypatch.setattr(base_module, "actions", HookActions())
    monkeypatch.setattr(base_module, "retentions", _ConfiguredRetentions())
    resticapi = _CheckingResticApi(check_error=ResticFailedError("check failed", return_code=11 if locked else 1))

    handler = _Handler(
        CheckFailingAgent(resticapi),
        {"id": 1, "uuid": "job-uuid-1", "retention_id": 3},
        repository_id=2,
        run_options={"repository_check": {"enabled": True, "read_data": None}},
    )

    report = handler.run()

    assert report.final_state == AgentReportState.warning
    assert CheckFailingAgent.retention_calls == int(locked)
    assert report.data.get("retention_check_failed", False) is (not locked)
    assert report.data.get("repository_locked", False) is locked
    assert resticapi.stats_calls == 1
    assert hook_calls == ["start", "success", "end"]
    assert any("Repository check failed after backup" in line for line in report.log_list)


@pytest.mark.parametrize("source", ["manual", "schedule", "chain", "cancel"])
def test_backup_failure_skips_retention(monkeypatch, source):
    AgentReport.pending_reports = []
    AgentReport.finished_reports.clear()
    monkeypatch.setattr(base_module, "actions", _FakeActions())
    monkeypatch.setattr(base_module, "retentions", _ConfiguredRetentions())

    class RetentionShouldNotRunAgent(_CheckingAgent):
        @staticmethod
        def cmd_run_retention(**kwargs):
            raise AssertionError("retention should not run after failed backup")

    options = {"retry": {"enabled": True}}
    if source != "manual":
        options["chain_run_id" if source == "chain" else "schedule_id"] = 1
    handler = _FailingBackupHandler(
        RetentionShouldNotRunAgent(_CheckingResticApi()),
        {"id": 1, "uuid": "job-uuid-1", "retention_id": 3},
        repository_id=2,
        run_options=options,
    )
    handler.error = ResticFailedError("connection reset by peer")
    handler.cancel = source == "cancel"
    monkeypatch.setattr(handler, "_run_post_backup", lambda *args: pytest.fail("Failed backups must not run success work"))

    report = handler.run()

    assert report.final_state == AgentReportState.failed
    assert any("Error during backup" in line for line in report.log_list)
    assert report.data["backup_data_complete"] is False
    assert report.data["backup_retryable"] is (source != "cancel")
    assert ("another attempt is eligible" in report.log) is (source in {"schedule", "chain"})


def test_start_hook_failure_prevents_backup_and_runs_error_and_end(monkeypatch):
    AgentReport.pending_reports = []
    AgentReport.finished_reports.clear()
    hook_calls = []

    class HookActions:
        @staticmethod
        def find(job_id, hook):
            return [{"hook": hook}]

    class HookAgent(_CheckingAgent):
        @staticmethod
        def execute_actions(report, actions):
            hook = actions[0]["hook"]
            hook_calls.append(hook)
            if hook == "start":
                report.log_message("start failed", final_state=AgentReportState.warning)

    monkeypatch.setattr(base_module, "actions", HookActions())
    monkeypatch.setattr(base_module, "retentions", _FakeRetentions())
    handler = _TrackingBackupHandler(
        HookAgent(_CheckingResticApi()),
        {"id": 1, "uuid": "job-uuid-1"},
        repository_id=2,
    )

    report = handler.run()

    assert handler.backup_calls == 0
    assert hook_calls == ["start", "error", "end"]
    assert report.final_state == AgentReportState.failed


def test_success_hook_failure_is_warning_and_end_hook_still_runs(monkeypatch):
    AgentReport.pending_reports = []
    AgentReport.finished_reports.clear()
    hook_calls = []

    class HookActions:
        @staticmethod
        def find(job_id, hook):
            return [{"hook": hook}]

    class HookAgent(_CheckingAgent):
        @staticmethod
        def execute_actions(report, actions):
            hook = actions[0]["hook"]
            hook_calls.append(hook)
            if hook == "success":
                raise RuntimeError("success hook failed")
            if hook == "end":
                report.log_message("end hook failed", final_state=AgentReportState.failed)

    monkeypatch.setattr(base_module, "actions", HookActions())
    monkeypatch.setattr(base_module, "retentions", _FakeRetentions())
    handler = _Handler(
        HookAgent(_CheckingResticApi()),
        {"id": 1, "uuid": "job-uuid-1"},
        repository_id=2,
    )

    report = handler.run()

    assert hook_calls == ["start", "success", "end"]
    assert report.final_state == AgentReportState.warning


@pytest.mark.parametrize("failed_hook", ["error", "end"])
def test_real_hook_failure_blocks_retry_and_runs_remaining_hooks(monkeypatch, failed_hook):
    AgentReport.pending_reports = []
    AgentReport.finished_reports.clear()
    hook_calls = []

    class HookActions:
        @staticmethod
        def find(job_id, hook):
            return [{"hook": hook, "module": "command", "data": {"command": hook}}]

    def command(report, command, **kwargs):
        hook_calls.append(command)
        if command == failed_hook:
            raise RuntimeError("hook failed")

    runtime = Agent.__new__(Agent)
    runtime._Agent__docker_client = None
    agent = _CheckingAgent(_CheckingResticApi())
    agent.execute_actions = runtime.execute_actions
    monkeypatch.setattr(AgentAction, "command", command)
    monkeypatch.setattr(base_module, "actions", HookActions())
    monkeypatch.setattr(base_module, "retentions", _FakeRetentions())
    handler = _FailingBackupHandler(
        agent,
        {"id": 1, "uuid": "job-uuid-1"},
        repository_id=2,
        run_options={"schedule_id": 1, "retry": {"enabled": True}},
    )
    handler.error = ResticFailedError("connection reset by peer")

    report = handler.run()

    assert hook_calls == ["start", "error", "end"]
    assert report.final_state == AgentReportState.failed
    assert not report.data["backup_retryable"]
    assert "another attempt is eligible" not in report.log


def test_repository_preparation_failure_runs_error_and_end_but_not_start(monkeypatch):
    AgentReport.pending_reports = []
    AgentReport.finished_reports.clear()
    hook_calls = []

    class HookActions:
        @staticmethod
        def find(job_id, hook):
            return [{"hook": hook}]

    class RepositoryFailingAgent:
        owner_uuid = "00000000-0000-0000-0000-000000000001"

        @staticmethod
        def set_repository(repository_id):
            raise RuntimeError("repository unavailable")

        @staticmethod
        def execute_actions(report, actions):
            hook_calls.append(actions[0]["hook"])

    monkeypatch.setattr(base_module, "actions", HookActions())
    handler = _TrackingBackupHandler(
        RepositoryFailingAgent(),
        {"id": 1, "uuid": "job-uuid-1"},
        repository_id=2,
    )

    report = handler.run()

    assert handler.backup_calls == 0
    assert hook_calls == ["error", "end"]
    assert report.final_state == AgentReportState.failed
    assert any("Could not prepare repository" in line for line in report.log_list)


def test_cancelled_backup_only_runs_end_hook(monkeypatch):
    AgentReport.pending_reports = []
    AgentReport.finished_reports.clear()
    hook_calls = []

    class HookActions:
        @staticmethod
        def find(job_id, hook):
            return [{"hook": hook}]

    class CancelledHandler(BackupJobHandler):
        def run_backup(self, report):
            report.log_message("cancelled", final_state=AgentReportState.cancelled)

    class CancelledAgent(_CheckingAgent):
        retention_calls = 0

        @classmethod
        def execute_actions(cls, report, actions):
            hook_calls.append(actions[0]["hook"])

        @classmethod
        def cmd_run_retention(cls, **kwargs):
            cls.retention_calls += 1
            return _SuccessfulReport()

    monkeypatch.setattr(base_module, "actions", HookActions())
    monkeypatch.setattr(base_module, "retentions", _ConfiguredRetentions())
    resticapi = _CheckingResticApi()
    handler = CancelledHandler(
        CancelledAgent(resticapi),
        {"id": 1, "uuid": "job-uuid-1", "retention_id": 3},
        repository_id=2,
        run_options={"repository_check": {"enabled": True}},
    )

    report = handler.run()

    assert report.final_state == AgentReportState.cancelled
    assert resticapi.check_calls == []
    assert CancelledAgent.retention_calls == 0
    assert resticapi.stats_calls == 0
    assert hook_calls == ["start", "end"]


if __name__ == "__main__":
    unittest.main()
