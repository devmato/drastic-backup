import bz2
import configparser
import hashlib
import json
import os
import stat
import subprocess
from datetime import datetime, timedelta
from threading import Event
from types import SimpleNamespace

import pytest

import drastic_agent.agent.agent as agent_module
from drastic_agent.agent.agent import Agent
from drastic_agent.agent.enums import AgentReportState, AgentReportType
from drastic_agent.agent.exceptions import AgentExeption
from drastic_agent.agent.execution import ExecutionManager
from drastic_agent.agent.report import AgentReport


def build_agent(server="http://server.test", identifier=None, secret=None):
    agent = Agent.__new__(Agent)
    agent.identifier = identifier
    agent.client = None
    agent._Agent__secret = secret
    agent._Agent__server = server
    agent._Agent__config = configparser.ConfigParser()
    agent._Agent__config["SERVER"] = {"url": server}
    agent._Agent__not_configured_logged = False
    agent._Agent__managed_repo_rewrite_warning_logged = False
    agent._Agent__next_register_attempt_at = datetime.min
    agent._Agent__next_reconnect_attempt_at = datetime.min
    agent._Agent__register_retry_interval = timedelta(seconds=15)
    agent._Agent__reconnect_retry_interval = timedelta(seconds=15)
    agent._Agent__schedule_run_slots = {}
    agent._Agent__repository_passwords = {}
    agent._Agent__secret_values = {}
    agent._diagnostic_report = AgentReport.command_report(data={"diagnostic": True})
    return agent


def test_diagnostics_use_server_consent_and_retry_unaccepted_events(monkeypatch):
    from drastic_common import diagnostics

    diagnostics.configure()
    agent = build_agent()
    sent = []
    enabled, success = True, True
    during_send = False

    def send(action, **kwargs):
        assert action == "operation"
        sent.append(kwargs["operation_json"])
        if during_send:
            diagnostics.record("during_send", {})
        return {"success": success, "result": {"enabled": enabled}}

    monkeypatch.setattr(agent, "_Agent__send_request", send)
    monkeypatch.setattr(diagnostics, "system_snapshot", lambda pids: {"scope": "host"})
    monkeypatch.setattr(AgentReport, "pending_reports", [])
    monkeypatch.setattr(AgentReport, "finished_reports", [])
    monkeypatch.setattr(AgentReport, "running_operations", {})
    try:
        agent._Agent__sample_diagnostics()
        agent._Agent__flush_report_queue()
        report = agent._diagnostic_report
        assert sent[0]["logs"] == []  # No collection before the server grants consent.
        diagnostics.record("test", {"value": 42})
        success = False
        agent._Agent__sample_diagnostics()
        agent._Agent__flush_report_queue()
        unaccepted = sent[-1]["logs"]
        assert any(log["message"] == "agent.sample" for log in unaccepted)
        success, during_send = True, True
        agent._Agent__flush_report_queue()
        assert sent[-1]["logs"] == unaccepted
        assert report.logs[0]["message"] == "during_send"
        during_send = False
        agent._Agent__flush_report_queue()
        assert report.logs == []
        for _ in range(diagnostics.MAX_EVENTS + 1):
            diagnostics.record("overflow", {"text": "ä" * 7000})
        assert len(report._logs) == diagnostics.MAX_EVENTS
        assert report.data["dropped_events"] == 1
        last_sequence = report._logs[-1]["sequence"]
        enabled = False
        agent._Agent__flush_report_queue()
        assert len(json.dumps(sent[-1]).encode()) < 256 * 1024
        assert not diagnostics.active()
        assert report.logs == []
        enabled = True
        agent._Agent__sample_diagnostics()
        agent._Agent__flush_report_queue()
        diagnostics.record("after_reenable", {})
        assert report.logs[0]["sequence"] > last_sequence
    finally:
        diagnostics.configure()
        if hasattr(agent, "_Agent__execution"):
            agent._Agent__execution.shutdown()


@pytest.fixture
def managed_agent(tmp_path, monkeypatch):
    root = tmp_path / "agent"
    monkeypatch.setattr(agent_module, "AGENT_INSTALL_ROOT", root)
    monkeypatch.setattr(agent_module, "AGENT_COMMAND", root / "bin/drastic-agent")
    monkeypatch.setenv("DRASTIC_AGENT_DATA_DIR", str(root / "data"))
    monkeypatch.setenv("DRASTIC_AGENT_DEPLOYMENT", "native")
    monkeypatch.setenv("DRASTIC_AGENT_INSTALL_SOURCE", "git")
    agent = build_agent(identifier="agent-17", secret="secret-17")
    yield agent
    if hasattr(agent, "_Agent__execution"):
        agent._Agent__execution.shutdown()


def test_update_uses_installer_and_keeps_work_paused_until_unit_finishes(managed_agent, monkeypatch):
    agent = managed_agent
    calls = []
    state = "activating"
    launch_error = False

    def run(command, **kwargs):
        calls.append((command, kwargs))
        if launch_error and command[0] == "systemd-run":
            raise subprocess.TimeoutExpired(command, 10)
        return SimpleNamespace(returncode=0, stdout=state, stderr="")

    monkeypatch.setattr(agent_module.subprocess, "run", run)
    assert agent.cmd_update().state == AgentReportState.success
    assert calls[0][0] == [str(agent_module.AGENT_COMMAND), "status"]
    assert calls[1][0] == [
        "systemd-run", "--collect", "--unit=drastic-agent-update.service",
        "--", str(agent_module.AGENT_COMMAND), "update",
    ]
    manager = agent._Agent__execution
    assert manager.submit(lambda: None) is None
    assert agent.cmd_update().state == AgentReportState.failed
    assert len(calls) == 2  # Busy/duplicate updates must not even run the lifecycle preflight.
    for unit_state in ("activating", "active", "deactivating"):
        state = unit_state
        agent._Agent__next_update_check_at = 0
        assert agent._Agent__check_update()
    restarted = build_agent()
    try:
        assert restarted._Agent__check_update(startup=True)
        assert restarted._Agent__execution.submit(lambda: None) is None
        state = "inactive"
        restarted._Agent__next_update_check_at = 0
        assert not restarted._Agent__check_update()
    finally:
        restarted._Agent__execution.shutdown()
    agent._Agent__next_update_check_at = 0
    assert not agent._Agent__check_update()
    launch_error = True
    assert agent.cmd_update().state == AgentReportState.failed
    assert manager.maintenance  # A launch timeout may have started the independent unit.
    agent._Agent__next_update_check_at = 0
    assert not agent._Agent__check_update()
    manager.submit(lambda: None).result(timeout=2)


@pytest.mark.parametrize("failure", ["busy", "manual", "docker", "wrong-data", "preflight"])
def test_update_rejects_busy_and_unmanaged_agents(managed_agent, monkeypatch, failure):
    agent = managed_agent
    calls = []
    def run(command, **kwargs):
        calls.append(command)
        if failure in {"preflight", "docker"}:
            raise subprocess.CalledProcessError(1, command)
    monkeypatch.setattr(agent_module.subprocess, "run", run)
    release = Event()
    if failure == "busy":
        agent._Agent__execution_manager().submit(lambda: release.wait(2))
    elif failure in {"manual", "docker"}:
        monkeypatch.setenv("DRASTIC_AGENT_INSTALL_SOURCE", "manual")
        if failure == "docker":
            monkeypatch.setenv("DRASTIC_AGENT_DEPLOYMENT", "docker")
    elif failure == "wrong-data":
        monkeypatch.setenv("DRASTIC_AGENT_DATA_DIR", "/another-agent/data")
    try:
        assert agent.cmd_update().state == AgentReportState.failed
        assert all(command == [str(agent_module.AGENT_COMMAND), "status"] for command in calls)
        assert not agent._Agent__execution_manager().maintenance
    finally:
        release.set()


def test_container_update_reuses_installer_and_pauses_until_launcher_finishes(managed_agent, monkeypatch):
    agent = managed_agent
    monkeypatch.setenv("DRASTIC_AGENT_DEPLOYMENT", "docker")
    monkeypatch.setenv("DRASTIC_AGENT_DATA_DIR", "/app/data")
    request = agent_module.AGENT_INSTALL_ROOT / "update-request.json"
    request.parent.mkdir()
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        if command[-1] == "update":
            request.write_text("{}")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(agent_module.subprocess, "run", run)
    assert agent.cmd_update().state == AgentReportState.success
    assert calls == [[str(agent_module.AGENT_COMMAND), "status"], [str(agent_module.AGENT_COMMAND), "update"]]
    assert agent.cmd_update().state == AgentReportState.failed
    assert agent._Agent__execution.submit(lambda: None) is None
    restarted = build_agent()
    try:
        assert restarted._Agent__check_update(startup=True)
        request.unlink()
        restarted._Agent__next_update_check_at = 0
        assert not restarted._Agent__check_update()
    finally:
        restarted._Agent__execution.shutdown()
    agent._Agent__next_update_check_at = 0
    assert not agent._Agent__check_update()
    agent._Agent__execution.submit(lambda: None).result(timeout=2)


def file_mode(path):
    return stat.S_IMODE(os.stat(path).st_mode)


class FakeRepositorySecrets:
    @staticmethod
    def find_one(**kwargs):
        assert kwargs == {"repository_id": 1}
        return {"repository_id": 1, "password": "secret"}


def test_set_repository_initializes_before_agent_key_provisioning(monkeypatch):
    agent = build_agent(identifier="agent-17", secret="secret-17")
    agent._Agent__config["AGENT"] = {"private_key": "private-key", "public_key": "public-key"}
    calls = []

    class FakeRepositories:
        @staticmethod
        def find_one(**kwargs):
            assert kwargs == {"id": 1}
            return {
                "id": 1,
                "location": "rest:http://repo.test/repo",
                "environment": {},
                "encrypted_recovery_key": {"v": 1},
            }

        @staticmethod
        def update(row, keys, **kwargs):
            calls.append(("update_repository", row, keys))

    class FakeRepositorySecrets:
        @staticmethod
        def delete(**kwargs):
            calls.append(("delete_secret", kwargs))

    class FakeResticApi:
        def set_repository(self, repository):
            calls.append(("set_repository", repository.password))

        def init(self):
            calls.append(("init",))
            return "repo-id"

        def key_add(self, password):
            calls.append(("key_add", password))

    monkeypatch.setattr(agent_module, "repositories", FakeRepositories())
    monkeypatch.setattr(agent_module, "repository_secrets", FakeRepositorySecrets())
    monkeypatch.setattr(agent_module, "decrypt_with_private_key", lambda envelope, key: "recovery-password")
    monkeypatch.setattr(agent_module, "encrypt_for_public_key", lambda plaintext, key: {"sealed": plaintext})
    monkeypatch.setattr(agent_module.secrets, "token_urlsafe", lambda length: "agent-password")
    monkeypatch.setattr(
        agent,
        "_Agent__send_request",
        lambda action, **kwargs: calls.append(("request", action, kwargs)) or {"success": True, "result": {}},
    )
    agent._Agent__resticapi = FakeResticApi()

    Agent._Agent__set_repository(agent, 1)

    assert calls == [
        ("set_repository", "recovery-password"),
        ("init",),
        ("key_add", "agent-password"),
        (
            "update_repository",
            {"id": 1, "encrypted_restic_access_key": {"sealed": "agent-password"}},
            ["id"],
        ),
        (
            "request",
            "store_repository_agent_key",
            {"repository_id": 1, "encrypted_agent_key": {"sealed": "agent-password"}},
        ),
        ("delete_secret", {"repository_id": 1}),
        ("set_repository", "agent-password"),
        ("set_repository", "agent-password"),
    ]


def test_save_config_restricts_agent_state_permissions(monkeypatch, tmp_path):
    monkeypatch.setenv("DRASTIC_AGENT_DATA_DIR", str(tmp_path))
    agent = build_agent(identifier="agent-17", secret="secret-17")
    agent._Agent__config["AGENT"] = {
        "identifier": "agent-17",
        "secret": "secret-17",
        "private_key": "private-key",
        "public_key": "public-key",
    }

    Agent.save_config(agent)

    assert file_mode(tmp_path) == 0o700
    assert file_mode(tmp_path / "config.ini") == 0o600


def test_set_repository_uses_agent_key_envelope_without_persisting_secret(monkeypatch):
    agent = build_agent(identifier="agent-17", secret="secret-17")
    agent._Agent__config["AGENT"] = {"private_key": "private-key"}
    calls = []

    class FakeRepositories:
        @staticmethod
        def find_one(**kwargs):
            return {
                "id": 1,
                "location": "rest:http://repo.test/repo",
                "environment": {},
                "encrypted_restic_access_key": {"v": 1},
            }

    class FakeRepositorySecrets:
        @staticmethod
        def delete(**kwargs):
            calls.append(("delete", kwargs))

    class FakeResticApi:
        def set_repository(self, repository):
            calls.append(("set_repository", repository.password))

    monkeypatch.setattr(agent_module, "repositories", FakeRepositories())
    monkeypatch.setattr(agent_module, "repository_secrets", FakeRepositorySecrets())
    monkeypatch.setattr(agent_module, "decrypt_with_private_key", lambda envelope, key: "agent-password")
    agent._Agent__resticapi = FakeResticApi()

    Agent._Agent__set_repository(agent, 1)

    assert calls == [("set_repository", "agent-password")]
    assert agent._Agent__repository_passwords == {1: "agent-password"}


def test_initialize_repository_with_recovery_uses_canonical_password(monkeypatch):
    agent = build_agent(identifier="agent-17", secret="secret-17")
    agent._Agent__config["AGENT"] = {"private_key": "private-key", "public_key": "public-key"}
    agent._Agent__repository_passwords[1] = "existing-agent-password"
    calls = []
    repository = {
        "id": 1,
        "location": "rest:http://repo.test/repo",
        "environment": {},
        "encrypted_recovery_key": {"v": 1},
    }

    class FakeRepositorySecrets:
        @staticmethod
        def delete(**kwargs):
            calls.append(("delete_secret", kwargs))

    class FakeRepositories:
        @staticmethod
        def update(row, keys, **kwargs):
            calls.append(("update_repository", row, keys))

    class FakeResticApi:
        def set_repository(self, repository):
            calls.append(("set_repository", repository.password))

        def init(self):
            calls.append(("init",))

        def key_add(self, password):
            calls.append(("key_add", password))

    monkeypatch.setattr(agent_module, "repository_secrets", FakeRepositorySecrets())
    monkeypatch.setattr(agent_module, "repositories", FakeRepositories())
    monkeypatch.setattr(agent_module, "decrypt_with_private_key", lambda envelope, key: "recovery-password")
    monkeypatch.setattr(agent_module, "encrypt_for_public_key", lambda plaintext, key: {"sealed": plaintext})
    monkeypatch.setattr(
        agent,
        "_Agent__send_request",
        lambda action, **kwargs: calls.append(("request", action, kwargs)) or {"success": True, "result": {}},
    )
    agent._Agent__resticapi = FakeResticApi()

    Agent.initialize_repository_with_recovery(agent, repository)

    assert calls == [
        ("set_repository", "recovery-password"),
        ("init",),
        ("key_add", "existing-agent-password"),
        (
            "update_repository",
            {"id": 1, "encrypted_restic_access_key": {"sealed": "existing-agent-password"}},
            ["id"],
        ),
        (
            "request",
            "store_repository_agent_key",
            {"repository_id": 1, "encrypted_agent_key": {"sealed": "existing-agent-password"}},
        ),
        ("delete_secret", {"repository_id": 1}),
        ("set_repository", "existing-agent-password"),
    ]


def test_set_repository_ignores_already_initialized_during_provisioning(monkeypatch):
    agent = build_agent(identifier="agent-17", secret="secret-17")
    agent._Agent__config["AGENT"] = {"private_key": "private-key", "public_key": "public-key"}
    key_add_calls = []

    class FakeRepositories:
        @staticmethod
        def find_one(**kwargs):
            return {
                "id": 1,
                "location": "rest:http://repo.test/repo",
                "environment": {},
                "encrypted_recovery_key": {"v": 1},
            }

        @staticmethod
        def update(row, keys, **kwargs):
            return None

    class FakeRepositorySecrets:
        @staticmethod
        def delete(**kwargs):
            pass

    class FakeResticApi:
        def set_repository(self, repository):
            pass

        def init(self):
            raise agent_module.ResticError("config file already exists")

        def key_add(self, password):
            key_add_calls.append(password)

    monkeypatch.setattr(agent_module, "repositories", FakeRepositories())
    monkeypatch.setattr(agent_module, "repository_secrets", FakeRepositorySecrets())
    monkeypatch.setattr(agent_module, "decrypt_with_private_key", lambda envelope, key: "recovery-password")
    monkeypatch.setattr(agent_module, "encrypt_for_public_key", lambda plaintext, key: {"sealed": plaintext})
    monkeypatch.setattr(agent_module.secrets, "token_urlsafe", lambda length: "agent-password")
    monkeypatch.setattr(agent, "_Agent__send_request", lambda action, **kwargs: {"success": True, "result": {}})
    agent._Agent__resticapi = FakeResticApi()

    Agent._Agent__set_repository(agent, 1)

    assert key_add_calls == ["agent-password"]


def test_register_updates_runtime_configuration(monkeypatch, tmp_path):
    monkeypatch.setenv("DRASTIC_AGENT_DATA_DIR", str(tmp_path))
    agent = build_agent(identifier=None, secret=None)
    save_calls = []
    payloads = []

    class Response:
        status_code = 201

        @staticmethod
        def json():
            return {"identifier": "agent-17", "secret": "secret-17"}

    def fake_post(*args, **kwargs):
        payloads.append(kwargs["json"])
        return Response()

    monkeypatch.setattr(agent_module.requests, "post", fake_post)
    monkeypatch.setattr(agent_module, "generate_ssh_keypair", lambda: ("ssh-private-key", "ssh-public-key"))
    monkeypatch.setattr(agent, "save_config", lambda: save_calls.append(True))

    result = Agent.register(agent, "http://server.test", "user", "pass")

    assert result is True
    assert agent.identifier == "agent-17"
    assert agent._Agent__secret == "secret-17"
    assert agent.configured is True
    assert agent._Agent__config["AGENT"]["identifier"] == "agent-17"
    assert payloads[0]["ssh_public_key"] == "ssh-public-key"
    assert save_calls == [True]


def test_repository_ssh_private_key_uses_agent_identity_for_ssh_locations(monkeypatch, tmp_path):
    monkeypatch.setenv("DRASTIC_AGENT_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(agent_module, "generate_ssh_keypair", lambda: ("ssh-private-key", "ssh-public-key"))
    agent = build_agent(identifier="agent-17", secret="secret-17")

    assert Agent._Agent__repository_ssh_private_key(
        agent, {"id": 1, "location": "sftp:user@example.test:/repo"}
    ) == "ssh-private-key\n"
    assert Agent._Agent__repository_ssh_private_key(
        agent, {"id": 1, "location": "rest:http://repo.test/repo"}
    ) is None


def test_rotate_ssh_key_replaces_agent_identity(monkeypatch, tmp_path):
    monkeypatch.setenv("DRASTIC_AGENT_DATA_DIR", str(tmp_path))
    generated = iter([
        ("first-private-key", "first-public-key"),
        ("second-private-key", "second-public-key"),
    ])
    monkeypatch.setattr(agent_module, "generate_ssh_keypair", lambda: next(generated))
    agent = build_agent(identifier="agent-17", secret="secret-17")

    assert agent.ssh_public_key == "first-public-key"
    report = Agent.cmd_rotate_ssh_key(agent)

    assert report.final_state.name == "success"
    assert report.data["ssh_public_key"] == "second-public-key"
    assert agent.ssh_public_key == "second-public-key"


def test_maintain_server_connection_retries_registration_and_connects(monkeypatch):
    agent = build_agent(identifier=None, secret=None)
    calls = []

    monkeypatch.setenv("DRASTIC_USER", "user")
    monkeypatch.setenv("DRASTIC_PASSWORD", "pass")

    def fake_register(server, username, password):
        calls.append(("register", server, username, password))
        agent.identifier = "agent-17"
        agent._Agent__secret = "secret-17"
        return True

    def fake_connect():
        calls.append(("connect",))
        return True

    monkeypatch.setattr(agent, "register", fake_register)
    monkeypatch.setattr(agent, "_Agent__connect", fake_connect)

    Agent._Agent__maintain_server_connection(agent, datetime(2026, 4, 19, 12, 0, 0))

    assert calls == [
        ("register", "http://server.test", "user", "pass"),
        ("connect",),
    ]


def test_run_due_schedules_starts_each_matching_schedule_once_per_minute(monkeypatch):
    agent = build_agent(identifier="agent-17", secret="secret-17")
    started_jobs = []
    agent_module.schedule_runs.delete(schedule_id=11)
    agent_module.schedule_runs.delete(schedule_id=12)

    class FakeSchedules:
        def __init__(self, items):
            self.items = items

        def find(self, **kwargs):
            return [item for item in self.items if item["job_id"] == kwargs["job_id"]]

    class ImmediateExecution:
        def submit(self, target, *args, **kwargs):
            kwargs.pop("resources", None)
            target(*args, **kwargs)
            return object()

    monkeypatch.setattr(agent_module, "jobs", [{"id": 1}, {"id": 2}])
    monkeypatch.setattr(
        agent_module,
        "schedules",
        FakeSchedules(
            [
                {"id": 11, "job_id": 1, "repository_id": 101, "cron_string": "* * * * *"},
                {"id": 12, "job_id": 2, "repository_id": 202, "cron_string": "* * * * *"},
            ]
        ),
    )
    agent._Agent__execution = ImmediateExecution()
    monkeypatch.setattr(
        agent,
        "cmd_run_job",
        lambda job_id, repository_id, retention_id=None, run_options=None: started_jobs.append(
            (job_id, repository_id)
        ),
    )

    Agent._Agent__run_due_schedules(agent, datetime(2026, 4, 19, 12, 0, 0))
    Agent._Agent__run_due_schedules(agent, datetime(2026, 4, 19, 12, 0, 30))
    Agent._Agent__run_due_schedules(agent, datetime(2026, 4, 19, 12, 1, 0))

    assert started_jobs == [
        (1, 101),
        (2, 202),
        (1, 101),
        (2, 202),
    ]


def test_send_report_stays_pending_when_request_fails(monkeypatch):
    agent = build_agent(identifier="agent-17", secret="secret-17")
    AgentReport.pending_reports = []
    AgentReport.finished_reports.clear()

    report = AgentReport.job_report(job_id=1, repository_id=2)
    report.log_message("starting backup")

    monkeypatch.setattr(
        agent,
        "_Agent__send_request",
        lambda action, **kwargs: {"success": False, "result": {}},
    )

    assert Agent._Agent__send_report(agent, report) is False
    assert report.sent is False
    assert report.log.endswith("starting backup")


def test_send_report_only_marks_log_sent_after_success(monkeypatch):
    agent = build_agent(identifier="agent-17", secret="secret-17")
    AgentReport.pending_reports = []
    AgentReport.finished_reports.clear()

    report = AgentReport.job_report(job_id=1, repository_id=2)
    report.log_message("starting backup")
    report.log_message("stream still running")
    payloads = []

    def fake_send_request(action, **kwargs):
        payloads.append(kwargs["operation_json"])
        return {"success": len(payloads) > 1, "result": {}}

    monkeypatch.setattr(agent, "_Agent__send_request", fake_send_request)

    assert Agent._Agent__send_report(agent, report) is False
    assert payloads[0]["logs"][-1]["created"]
    assert payloads[0]["logs"][-1]["message"] == "stream still running"
    assert any("starting backup" in log["message"] for log in payloads[0]["logs"])
    assert any("stream still running" in log["message"] for log in payloads[0]["logs"])
    assert report.log.endswith("stream still running")

    assert Agent._Agent__send_report(agent, report) is True
    assert payloads[1]["logs"] == payloads[0]["logs"]
    assert report.log == ""


def test_progress_and_logs_created_during_send_remain_pending(monkeypatch):
    agent = build_agent(identifier="agent-17", secret="secret-17")
    report = AgentReport.command_report()
    report.set_data("bytes_processed", 10)
    report.log_message("before send")
    report.diagnostic_at = datetime(2000, 1, 1)
    monkeypatch.setattr(agent_module.diagnostics, "active", lambda: True)

    def send_request(action, **kwargs):
        report.set_data("bytes_processed", 20)
        report.log_message("during send")
        assert kwargs["operation_json"]["data"]["bytes_processed"] == 10
        assert kwargs["operation_json"]["diagnostic_at"] > "2000-01-01"
        return {"success": True}

    monkeypatch.setattr(agent, "_Agent__send_request", send_request)
    monkeypatch.setattr(AgentReport, "pending_reports", [report])
    monkeypatch.setattr(AgentReport, "finished_reports", [])
    Agent._Agent__flush_report_queue(agent)
    assert report.sent is False
    assert report.log == "during send"
    assert not hasattr(report, "diagnostic_at")
    report.diagnostic_at = datetime(2000, 1, 1)
    monkeypatch.setattr(agent_module.diagnostics, "active", lambda: False)
    sent = []
    monkeypatch.setattr(agent, "_Agent__send_request", lambda action, **kwargs: sent.append(kwargs["operation_json"]) or {"success": True})
    Agent._Agent__flush_report_queue(agent)
    assert "diagnostic_at" not in sent[0]


def test_sync_hydrates_and_persists_only_agent_key_envelopes(monkeypatch):
    agent = build_agent(identifier="agent-17", secret="secret-17")
    agent._Agent__config["AGENT"] = {"private_key": "private-key"}
    inserted_repositories = []
    secret_deletes = []

    class FakeRepositories:
        @staticmethod
        def delete():
            inserted_repositories.clear()

        @staticmethod
        def insert(row, **kwargs):
            inserted_repositories.append(row)

    class FakeSecrets:
        @staticmethod
        def delete(**kwargs):
            secret_deletes.append(kwargs)

    class FakeTable:
        @staticmethod
        def delete():
            return None

        @staticmethod
        def insert(*args, **kwargs):
            return None

    def fake_send_request(action, **kwargs):
        assert action == "sync"
        return {
            "success": True,
            "result": {
                "repositories": [
                    {
                        "id": 1,
                        "location": "rest:http://repo.test/repo",
                        "environment": {},
                        "encrypted_restic_access_key": {"v": 1},
                        "encrypted_recovery_key": {"should": "not-persist"},
                    }
                ],
                "retentions": [],
                "jobs": [],
                "schedules": [],
                "actions": [],
            },
        }

    monkeypatch.setattr(agent, "_Agent__send_request", fake_send_request)
    monkeypatch.setattr(agent_module, "repositories", FakeRepositories())
    monkeypatch.setattr(agent_module, "repository_secrets", FakeSecrets())
    monkeypatch.setattr(agent_module, "retentions", FakeTable())
    monkeypatch.setattr(agent_module, "jobs", FakeTable())
    monkeypatch.setattr(agent_module, "schedules", FakeTable())
    monkeypatch.setattr(agent_module, "actions", FakeTable())
    monkeypatch.setattr(agent_module, "decrypt_with_private_key", lambda envelope, key: "agent-password")

    report = Agent.cmd_sync(agent)

    assert report.final_state.name == "success"
    assert agent._Agent__repository_passwords == {1: "agent-password"}
    assert inserted_repositories == [
        {
            "id": 1,
            "location": "rest:http://repo.test/repo",
            "environment": {},
            "encrypted_restic_access_key": {"v": 1},
        }
    ]
    assert secret_deletes == [{}]


def test_handle_execute_command_rejects_async_job_without_operation_uuid(monkeypatch):
    agent = build_agent(identifier="agent-17", secret="secret-17")
    async_calls = []

    monkeypatch.setattr(agent, "_Agent__validate_run_job_admission", lambda command_args: None)
    monkeypatch.setattr(
        agent,
        "_Agent__run_command_async",
        lambda command_name, command_args: (
            async_calls.append((command_name, command_args)) or object()
        ),
    )

    report = Agent._Agent__handle_execute_command(
        agent,
        {"command": "run_job", "args": {"job_id": 3, "repository_id": 8}},
    )

    assert async_calls == []
    assert report.state.name == "failed"
    assert report.log == "Could not start run_job: operation_uuid is required"


def test_set_repository_keeps_managed_location_without_rewrite_flag(monkeypatch):
    agent = build_agent(identifier="agent-17", secret="secret-17")
    agent._Agent__repository_passwords[1] = "secret"
    configured_repository = {}

    class FakeRepositories:
        @staticmethod
        def find_one(**kwargs):
            assert kwargs == {"id": 1}
            return {
                "id": 1,
                "location": "rest:http://127.0.0.1:5050/restic/bootstrap/default",
                "password": "secret",
                "environment": {},
            }

    class FakeResticApi:
        def set_repository(self, repository):
            configured_repository["location"] = repository.location
            configured_repository["env"] = repository.env

    monkeypatch.delenv("DRASTIC_AGENT_REWRITE_MANAGED_REPO_URLS", raising=False)
    monkeypatch.setattr(agent_module, "repositories", FakeRepositories())
    monkeypatch.setattr(agent_module, "repository_secrets", FakeRepositorySecrets())
    agent._Agent__resticapi = FakeResticApi()

    Agent._Agent__set_repository(agent, 1)

    assert (
        configured_repository["location"] == "rest:http://127.0.0.1:5050/restic/bootstrap/default"
    )
    assert configured_repository["env"] == {
        "RESTIC_HOST": "drastic-agent-17",
        "RESTIC_REST_USERNAME": "agent-17",
        "RESTIC_REST_PASSWORD": "secret-17",
    }


def test_set_repository_rewrites_managed_location_when_flag_enabled(monkeypatch):
    agent = build_agent(server="http://backend:5050", identifier="agent-17", secret="secret-17")
    agent._Agent__repository_passwords[1] = "secret"
    configured_repository = {}

    class FakeRepositories:
        @staticmethod
        def find_one(**kwargs):
            assert kwargs == {"id": 1}
            return {
                "id": 1,
                "location": "rest:http://127.0.0.1:5050/restic/bootstrap/default",
                "password": "secret",
                "environment": {"RESTIC_PASSWORD_COMMAND": "ignored"},
            }

    class FakeResticApi:
        def set_repository(self, repository):
            configured_repository["location"] = repository.location
            configured_repository["env"] = repository.env

    monkeypatch.setenv("DRASTIC_AGENT_REWRITE_MANAGED_REPO_URLS", "1")
    monkeypatch.setattr(agent_module, "repositories", FakeRepositories())
    monkeypatch.setattr(agent_module, "repository_secrets", FakeRepositorySecrets())
    agent._Agent__resticapi = FakeResticApi()

    Agent._Agent__set_repository(agent, 1)

    assert configured_repository["location"] == "rest:http://backend:5050/restic/bootstrap/default"
    assert configured_repository["env"] == {
        "RESTIC_HOST": "drastic-agent-17",
        "RESTIC_PASSWORD_COMMAND": "ignored",
        "RESTIC_REST_USERNAME": "agent-17",
        "RESTIC_REST_PASSWORD": "secret-17",
    }


def test_set_repository_builds_native_location_from_agent_server(monkeypatch):
    agent = build_agent(server="http://backend:5050", identifier="agent-17", secret="secret-17")
    agent._Agent__repository_passwords[1] = "secret"
    configured_repository = {}

    class FakeRepositories:
        @staticmethod
        def find_one(**kwargs):
            assert kwargs == {"id": 1}
            return {
                "id": 1,
                "kind": "native",
                "location": "native/default",
                "password": "secret",
                "environment": {},
            }

    class FakeResticApi:
        def set_repository(self, repository):
            configured_repository["location"] = repository.location
            configured_repository["env"] = repository.env

    monkeypatch.setattr(agent_module, "repositories", FakeRepositories())
    monkeypatch.setattr(agent_module, "repository_secrets", FakeRepositorySecrets())
    agent._Agent__resticapi = FakeResticApi()

    Agent._Agent__set_repository(agent, 1)

    assert configured_repository["location"] == "rest:http://backend:5050/restic/native/default"
    assert configured_repository["env"] == {
        "RESTIC_HOST": "drastic-agent-17",
        "RESTIC_REST_USERNAME": "agent-17",
        "RESTIC_REST_PASSWORD": "secret-17",
    }


@pytest.mark.parametrize(("process_host", "repository_host", "expected"), [
    (None, None, "drastic-agent-17"),
    ("custom-host", None, "custom-host"),
    (None, "repository-host", "repository-host"),
    ("custom-host", "repository-host", "repository-host"),
])
def test_restic_host_is_stable_and_preserves_overrides(monkeypatch, process_host, repository_host, expected):
    monkeypatch.delenv("RESTIC_HOST", raising=False)
    if process_host is not None:
        monkeypatch.setenv("RESTIC_HOST", process_host)
    environment = {"RESTIC_HOST": repository_host} if repository_host is not None else {}
    repository = {"location": "/repo", "environment": dict(environment)}
    for hostname in ("old-container", "new-container"):
        monkeypatch.setattr(agent_module.platform, "node", lambda hostname=hostname: hostname)
        agent = build_agent(identifier="agent-17")
        _, env = agent._Agent__repository_location_env(repository)
        assert env["RESTIC_HOST"] == expected
    assert repository["environment"] == environment


def test_startup_warns_once_when_managed_repo_rewrite_enabled_outside_dev(monkeypatch):
    agent = build_agent(identifier="agent-17", secret="secret-17")
    warnings = []

    monkeypatch.setenv("DRASTIC_AGENT_REWRITE_MANAGED_REPO_URLS", "true")
    monkeypatch.setenv("DRASTIC_ENV", "prod")
    monkeypatch.setattr(agent_module.AgentReport, "load_queue", lambda: None)
    monkeypatch.setattr(agent, "_Agent__maintain_server_connection", lambda now: None)
    monkeypatch.setattr(agent, "_Agent__start_scheduler", lambda: None)
    monkeypatch.setattr(agent_module.logging, "warning", lambda message: warnings.append(message))

    Agent.startup(agent)
    Agent.startup(agent)

    assert warnings == [
        "Managed repository URL rewrite is enabled outside dev mode. "
        "Set DRASTIC_AGENT_REWRITE_MANAGED_REPO_URLS only for exceptional setups."
    ]


def test_cancel_job_fails_when_no_restic_pid_becomes_available(monkeypatch):
    agent = build_agent(identifier="agent-17", secret="secret-17")
    AgentReport.pending_reports = []
    AgentReport.finished_reports.clear()
    AgentReport.job_report(job_id=1, repository_id=2)
    cancel_calls = []
    monotonic_values = iter([0, 2])

    class FakeResticApi:
        @staticmethod
        def cancel_process(pid):
            cancel_calls.append(pid)

    agent._Agent__resticapi = FakeResticApi()
    monkeypatch.setenv("DRASTIC_CANCEL_WAIT_TIMEOUT_SECONDS", "1")
    monkeypatch.setattr(agent_module, "monotonic", lambda: next(monotonic_values))
    monkeypatch.setattr(agent_module, "sleep", lambda seconds: None)

    report = Agent.cmd_cancel_job(agent, job_id=1)

    assert report.final_state.name == "failed"
    assert "No restic process became available" in report.log
    assert cancel_calls == []


def test_cancel_restore_can_be_requested_between_processes():
    agent = build_agent(identifier="agent-17", secret="secret-17")
    AgentReport.pending_reports = []
    AgentReport.finished_reports.clear()
    restore_report = AgentReport.restore_report(
        report_uuid="restore-report-1",
        job_id=1,
        repository_id=2,
        data={"snapshot_id": "snap-1"},
    )
    report = Agent.cmd_cancel_restore(agent, report_uuid="restore-report-1")

    assert report.final_state.name == "success"
    assert restore_report.cancel_event.is_set()
    assert restore_report.state.name == "running"


def test_cancel_job_only_marks_operation_cancelled_after_process_was_killed():
    agent = build_agent(identifier="agent-17", secret="secret-17")
    AgentReport.pending_reports = []
    AgentReport.finished_reports.clear()
    AgentReport.running_operations = {}
    job_report = AgentReport.job_report(
        job_id=31,
        repository_id=2,
        operation_uuid="cancel-job-31",
    )
    job_report.data["pid"] = 731

    class MissingProcessResticApi:
        @staticmethod
        def cancel_process(pid):
            assert pid == 731
            return False

    agent._Agent__resticapi = MissingProcessResticApi()

    report = Agent.cmd_cancel_job(agent, job_id=31, operation_uuid="cancel-job-31")

    assert report.final_state.name == "failed"
    assert job_report.final_state.name == "success"
    assert "no longer running" in report.log


def test_cancel_restore_leaves_terminal_reporting_to_the_worker():
    agent = build_agent(identifier="agent-17", secret="secret-17")
    AgentReport.pending_reports = []
    AgentReport.finished_reports.clear()
    AgentReport.running_operations = {}
    restore_report = AgentReport.restore_report(
        report_uuid="cancel-restore-32",
        job_id=32,
        repository_id=2,
    )
    AgentReport.process_restore_status(
        None,
        operation_uuid="cancel-restore-32",
        pid=732,
    )

    report = Agent.cmd_cancel_restore(agent, operation_uuid="cancel-restore-32")

    assert report.final_state.name == "success"
    assert restore_report.cancel_event.is_set()
    assert restore_report.state.name == "running"


def _queue_operation(agent, operation_uuid):
    release = Event()
    started = Event()
    queued_ran = Event()
    execution = ExecutionManager(max_workers=1, max_pending=2)

    def blocked():
        started.set()
        release.wait()

    execution.submit(blocked)
    assert started.wait(1)
    execution.submit(queued_ran.set, operation_uuid=operation_uuid)
    agent._Agent__execution = execution
    return execution, release, queued_ran


def test_cancel_job_finishes_successfully_cancelled_queued_operation():
    agent = build_agent(identifier="agent-17", secret="secret-17")
    AgentReport.pending_reports = []
    AgentReport.finished_reports.clear()
    AgentReport.running_operations = {}
    execution, release, queued_ran = _queue_operation(agent, "queued-job-operation")

    report = Agent.cmd_cancel_job(
        agent,
        job_id=41,
        operation_uuid="queued-job-operation",
    )

    terminal = AgentReport.finished_reports[-1]
    assert report.final_state == AgentReportState.success
    assert terminal.uuid == "queued-job-operation"
    assert terminal.type == AgentReportType.backup
    assert terminal.final_state == AgentReportState.cancelled
    assert terminal.ended is not None
    assert not queued_ran.is_set()
    release.set()
    execution.shutdown()


def test_cancel_restore_finishes_successfully_cancelled_queued_operation():
    agent = build_agent(identifier="agent-17", secret="secret-17")
    AgentReport.pending_reports = []
    AgentReport.finished_reports.clear()
    AgentReport.running_operations = {}
    execution, release, queued_ran = _queue_operation(agent, "queued-restore-operation")

    report = Agent.cmd_cancel_restore(agent, operation_uuid="queued-restore-operation")

    terminal = AgentReport.finished_reports[-1]
    assert report.final_state == AgentReportState.success
    assert terminal.uuid == "queued-restore-operation"
    assert terminal.type == AgentReportType.restore
    assert terminal.final_state == AgentReportState.cancelled
    assert terminal.ended is not None
    assert not queued_ran.is_set()
    release.set()
    execution.shutdown()


def test_restic_download_verifies_sha256sum(tmp_path, monkeypatch):
    archive_data = bz2.compress(b"restic-binary")
    checksum = hashlib.sha256(archive_data).hexdigest()
    calls = []
    smoke_calls = []
    replacements = []

    class Response:
        def __init__(self, content=b"", text=""):
            self.content = content
            self.text = text

        @staticmethod
        def raise_for_status():
            return None

    def fake_get(url, timeout):
        calls.append(url)
        if url.endswith("SHA256SUMS"):
            return Response(text=f"{checksum} restic_0.18.1_linux_amd64.bz2\n")
        return Response(content=archive_data)

    def fake_run(command, **kwargs):
        candidate_path = command[0]
        smoke_calls.append((command, kwargs))
        with open(candidate_path, "rb") as candidate:
            assert candidate.read() == b"restic-binary"
        assert os.access(candidate_path, os.X_OK)
        return SimpleNamespace(returncode=0, stdout="restic 0.18.1 compiled with go", stderr="")

    real_replace = os.replace

    def fake_replace(source, destination):
        replacements.append((source, destination))
        real_replace(source, destination)

    monkeypatch.setenv("DRASTIC_AGENT_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(agent_module.Agent, "os_clean", property(lambda self: "linux"))
    monkeypatch.setattr(agent_module.Agent, "arch", property(lambda self: "amd64"))
    monkeypatch.setattr(agent_module.requests, "get", fake_get)
    monkeypatch.setattr(agent_module.subprocess, "run", fake_run)
    monkeypatch.setattr(agent_module.os, "replace", fake_replace)

    agent = Agent.__new__(Agent)
    Agent._Agent__check_restic_binary(agent)

    binary_path = tmp_path / "bin" / "restic_0.18.1_linux_amd64"
    assert binary_path.read_bytes() == b"restic-binary"
    assert os.access(binary_path, os.X_OK)
    assert len(smoke_calls) == 1
    assert smoke_calls[0][0][1] == "version"
    assert smoke_calls[0][1]["timeout"] == 5
    assert replacements[0][1] == str(binary_path)
    assert os.path.dirname(replacements[0][0]) == str(binary_path.parent)
    assert list(binary_path.parent.iterdir()) == [binary_path]
    assert calls == [
        "https://github.com/restic/restic/releases/download/v0.18.1/restic_0.18.1_linux_amd64.bz2",
        "https://github.com/restic/restic/releases/download/v0.18.1/SHA256SUMS",
    ]


def test_restic_download_rejects_sha256sum_mismatch(tmp_path, monkeypatch):
    archive_data = bz2.compress(b"restic-binary")

    class Response:
        content = archive_data
        text = "0" * 64 + " restic_0.18.1_linux_amd64.bz2\n"

        @staticmethod
        def raise_for_status():
            return None

    monkeypatch.setenv("DRASTIC_AGENT_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(agent_module.Agent, "os_clean", property(lambda self: "linux"))
    monkeypatch.setattr(agent_module.Agent, "arch", property(lambda self: "amd64"))
    monkeypatch.setattr(agent_module.requests, "get", lambda url, timeout: Response())

    agent = Agent.__new__(Agent)

    try:
        Agent._Agent__check_restic_binary(agent)
    except AgentExeption as exc:
        assert "checksum mismatch" in str(exc)
    else:
        raise AssertionError("checksum mismatch should fail restic download")

    binary_folder = tmp_path / "bin"
    assert list(binary_folder.iterdir()) == []


def test_existing_restic_binary_requires_expected_version_without_download(tmp_path, monkeypatch):
    binary_path = tmp_path / "bin" / "restic_0.18.1_linux_amd64"
    binary_path.parent.mkdir()
    binary_path.write_bytes(b"existing-restic")
    binary_path.chmod(0o755)
    smoke_calls = []

    def fake_run(command, **kwargs):
        smoke_calls.append((command, kwargs))
        return SimpleNamespace(returncode=0, stdout="restic 0.18.1 compiled with go", stderr="")

    monkeypatch.setenv("DRASTIC_AGENT_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(agent_module.Agent, "os_clean", property(lambda self: "linux"))
    monkeypatch.setattr(agent_module.Agent, "arch", property(lambda self: "amd64"))
    monkeypatch.setattr(agent_module.subprocess, "run", fake_run)
    monkeypatch.setattr(
        agent_module.requests,
        "get",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("network must not be used")),
    )

    Agent._Agent__check_restic_binary(Agent.__new__(Agent))

    assert smoke_calls == [
        (
            [str(binary_path), "version"],
            {"capture_output": True, "text": True, "timeout": 5, "check": False},
        )
    ]
    assert binary_path.read_bytes() == b"existing-restic"


def test_existing_restic_binary_with_wrong_version_is_replaced(tmp_path, monkeypatch):
    archive_data = bz2.compress(b"replacement-restic")
    checksum = hashlib.sha256(archive_data).hexdigest()
    binary_path = tmp_path / "bin" / "restic_0.18.1_linux_amd64"
    binary_path.parent.mkdir()
    binary_path.write_bytes(b"old-restic")
    binary_path.chmod(0o755)

    class Response:
        def __init__(self, content=b"", text=""):
            self.content = content
            self.text = text

        @staticmethod
        def raise_for_status():
            return None

    def fake_get(url, timeout):
        if url.endswith("SHA256SUMS"):
            return Response(text=f"{checksum} restic_0.18.1_linux_amd64.bz2\n")
        return Response(content=archive_data)

    def fake_run(command, **kwargs):
        version = "0.17.3" if command[0] == str(binary_path) else "0.18.1"
        return SimpleNamespace(returncode=0, stdout=f"restic {version} compiled with go", stderr="")

    monkeypatch.setenv("DRASTIC_AGENT_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(agent_module.Agent, "os_clean", property(lambda self: "linux"))
    monkeypatch.setattr(agent_module.Agent, "arch", property(lambda self: "amd64"))
    monkeypatch.setattr(agent_module.requests, "get", fake_get)
    monkeypatch.setattr(agent_module.subprocess, "run", fake_run)

    Agent._Agent__check_restic_binary(Agent.__new__(Agent))

    assert binary_path.read_bytes() == b"replacement-restic"
    assert list(binary_path.parent.iterdir()) == [binary_path]


def test_failed_restic_smoke_test_preserves_existing_binary(tmp_path, monkeypatch):
    archive_data = bz2.compress(b"broken-replacement")
    checksum = hashlib.sha256(archive_data).hexdigest()
    binary_path = tmp_path / "bin" / "restic_0.18.1_linux_amd64"
    binary_path.parent.mkdir()
    binary_path.write_bytes(b"partial-existing-restic")
    binary_path.chmod(0o755)

    class Response:
        content = archive_data
        text = f"{checksum} restic_0.18.1_linux_amd64.bz2\n"

        @staticmethod
        def raise_for_status():
            return None

    monkeypatch.setenv("DRASTIC_AGENT_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(agent_module.Agent, "os_clean", property(lambda self: "linux"))
    monkeypatch.setattr(agent_module.Agent, "arch", property(lambda self: "amd64"))
    monkeypatch.setattr(agent_module.requests, "get", lambda url, timeout: Response())
    monkeypatch.setattr(
        agent_module.subprocess,
        "run",
        lambda command, **kwargs: SimpleNamespace(
            returncode=1, stdout="", stderr="invalid executable"
        ),
    )

    try:
        Agent._Agent__check_restic_binary(Agent.__new__(Agent))
    except AgentExeption as exc:
        assert "failed version check" in str(exc)
    else:
        raise AssertionError("failed smoke test should fail restic download")

    assert binary_path.read_bytes() == b"partial-existing-restic"
    assert list(binary_path.parent.iterdir()) == [binary_path]
