import unittest

import drastic_agent.jobs.base as base_module
from drastic_agent.agent.enums import AgentReportState
from drastic_agent.agent.report import AgentReport
from drastic_agent.jobs.base import BackupJobHandler
from drastic_common.restic.exceptions import ResticError


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
        return None


class _FailingBackupHandler(BackupJobHandler):
    def run_backup(self, report):
        raise ResticError("backup failed")


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


class _SuccessfulResticApi:
    @staticmethod
    def cat_config():
        return {"id": "repo-id"}


class _StatsFailedReport:
    state = AgentReportState.failed
    log_list = ["stats failed"]


class _StatsFailingAgent:
    resticapi = _SuccessfulResticApi()

    @staticmethod
    def set_repository(repository_id):
        return {"id": repository_id, "location": "/repo"}

    @staticmethod
    def execute_actions(report, actions):
        return None

    @staticmethod
    def cmd_get_repository_stats(repository_id):
        return _StatsFailedReport()


class _SuccessfulReport:
    state = AgentReportState.success
    log_list = ["stats ok"]


class _CheckingResticApi:
    def __init__(self, check_error=None):
        self.check_error = check_error
        self.check_calls = []

    @staticmethod
    def cat_config():
        return {"id": "repo-id"}

    def check(self, **kwargs):
        self.check_calls.append(kwargs)
        if self.check_error:
            raise self.check_error
        return {"message_type": "summary", "errors": 0}


class _CheckingAgent:
    def __init__(self, resticapi):
        self.resticapi = resticapi

    @staticmethod
    def set_repository(repository_id):
        return {"id": repository_id, "location": "/repo"}

    @staticmethod
    def execute_actions(report, actions):
        return None

    @staticmethod
    def cmd_get_repository_stats(repository_id):
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
    handler = _Handler(
        _StatsFailingAgent(),
        {"id": 1, "uuid": "job-uuid-1"},
        repository_id=2,
    )

    report = handler.run()

    assert report.final_state == AgentReportState.warning
    assert any("stats failed" in line for line in report.log_list)


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


def test_post_backup_check_runs_before_retention_and_stats(monkeypatch):
    AgentReport.pending_reports = []
    AgentReport.finished_reports.clear()
    monkeypatch.setattr(base_module, "actions", _FakeActions())
    monkeypatch.setattr(base_module, "retentions", _FakeRetentions())
    resticapi = _CheckingResticApi()
    handler = _Handler(
        _CheckingAgent(resticapi),
        {
            "id": 1,
            "uuid": "job-uuid-1",
        },
        repository_id=2,
        run_options={"repository_check": {"enabled": True, "read_data": "1/10"}},
    )

    report = handler.run()

    assert report.final_state == AgentReportState.success
    assert resticapi.check_calls == [{"read_data": False, "read_data_subset": "1/10"}]
    assert report.data["check"] == {"message_type": "summary", "errors": 0}


def test_post_backup_check_failure_fails_job_and_skips_stats(monkeypatch):
    AgentReport.pending_reports = []
    AgentReport.finished_reports.clear()
    monkeypatch.setattr(base_module, "actions", _FakeActions())
    monkeypatch.setattr(base_module, "retentions", _FakeRetentions())
    resticapi = _CheckingResticApi(check_error=ResticError("check failed"))

    class StatsShouldNotRunAgent(_CheckingAgent):
        @staticmethod
        def cmd_get_repository_stats(repository_id):
            raise AssertionError("stats should not run after failed check")

    handler = _Handler(
        StatsShouldNotRunAgent(resticapi),
        {"id": 1, "uuid": "job-uuid-1"},
        repository_id=2,
        run_options={"repository_check": {"enabled": True, "read_data": None}},
    )

    report = handler.run()

    assert report.final_state == AgentReportState.failed
    assert any("Repository check failed after backup" in line for line in report.log_list)


def test_backup_failure_skips_retention(monkeypatch):
    AgentReport.pending_reports = []
    AgentReport.finished_reports.clear()
    monkeypatch.setattr(base_module, "actions", _FakeActions())
    monkeypatch.setattr(base_module, "retentions", _ConfiguredRetentions())

    class RetentionShouldNotRunAgent(_CheckingAgent):
        @staticmethod
        def cmd_run_retention(**kwargs):
            raise AssertionError("retention should not run after failed backup")

    handler = _FailingBackupHandler(
        RetentionShouldNotRunAgent(_CheckingResticApi()),
        {"id": 1, "uuid": "job-uuid-1"},
        repository_id=2,
        retention_id=3,
    )

    report = handler.run()

    assert report.final_state == AgentReportState.failed
    assert any("Error during backup" in line for line in report.log_list)


if __name__ == "__main__":
    unittest.main()
