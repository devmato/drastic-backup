import bz2
import configparser
import hashlib
import os
from datetime import datetime, timedelta

import drastic_agent.agent.agent as agent_module
from drastic_agent.agent.agent import Agent
from drastic_agent.agent.exceptions import AgentExeption
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
    return agent


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
            "request",
            "store_repository_agent_key",
            {"repository_id": 1, "encrypted_agent_key": {"sealed": "agent-password"}},
        ),
        ("delete_secret", {"repository_id": 1}),
        ("set_repository", "agent-password"),
        ("set_repository", "agent-password"),
    ]


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
                "encrypted_agent_key": {"v": 1},
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

    class FakeResticApi:
        def set_repository(self, repository):
            calls.append(("set_repository", repository.password))

        def init(self):
            calls.append(("init",))

        def key_add(self, password):
            calls.append(("key_add", password))

    monkeypatch.setattr(agent_module, "repository_secrets", FakeRepositorySecrets())
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


def test_register_updates_runtime_configuration(monkeypatch):
    agent = build_agent(identifier=None, secret=None)
    save_calls = []

    class Response:
        status_code = 201

        @staticmethod
        def json():
            return {"identifier": "agent-17", "secret": "secret-17"}

    monkeypatch.setattr(agent_module.requests, "post", lambda *args, **kwargs: Response())
    monkeypatch.setattr(agent, "save_config", lambda: save_calls.append(True))

    result = Agent.register(agent, "http://server.test", "user", "pass")

    assert result is True
    assert agent.identifier == "agent-17"
    assert agent._Agent__secret == "secret-17"
    assert agent.configured is True
    assert agent._Agent__config["AGENT"]["identifier"] == "agent-17"
    assert save_calls == [True]


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

    class FakeSchedules:
        def __init__(self, items):
            self.items = items

        def find(self, **kwargs):
            return [item for item in self.items if item["job_id"] == kwargs["job_id"]]

    class ImmediateThread:
        def __init__(self, target, args, daemon):
            self.target = target
            self.args = args
            self.daemon = daemon

        def start(self):
            self.target(*self.args)

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
    monkeypatch.setattr(agent_module.croniter, "match", lambda cron_string, now: True)
    monkeypatch.setattr(agent_module, "Thread", ImmediateThread)
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


def test_sync_hydrates_agent_keys_without_persisting_secret_payloads(monkeypatch):
    agent = build_agent(identifier="agent-17", secret="secret-17")
    agent._Agent__config["AGENT"] = {"private_key": "private-key"}
    inserted_repositories = []
    secret_deletes = []

    class FakeRepositories:
        @staticmethod
        def delete():
            inserted_repositories.clear()

        @staticmethod
        def insert_many(rows):
            inserted_repositories.extend(rows)

    class FakeSecrets:
        @staticmethod
        def delete(**kwargs):
            secret_deletes.append(kwargs)

    class FakeTable:
        @staticmethod
        def delete():
            return None

        @staticmethod
        def insert_many(*args, **kwargs):
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
                        "encrypted_agent_key": {"v": 1},
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
        {"id": 1, "location": "rest:http://repo.test/repo", "environment": {}}
    ]
    assert secret_deletes == [{}]


def test_handle_execute_command_runs_jobs_async(monkeypatch):
    agent = build_agent(identifier="agent-17", secret="secret-17")
    async_calls = []

    monkeypatch.setattr(
        agent,
        "_Agent__run_command_async",
        lambda command_name, command_args: async_calls.append((command_name, command_args)),
    )

    report = Agent._Agent__handle_execute_command(
        agent,
        {"command": "run_job", "args": {"job_id": 3, "repository_id": 8}},
    )

    assert async_calls == [("run_job", {"job_id": 3, "repository_id": 8})]
    assert report.state.name == "success"
    assert report.log == "Started job 3 on repository 8"


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
        "RESTIC_REST_USERNAME": "agent-17",
        "RESTIC_REST_PASSWORD": "secret-17",
    }


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


def test_cancel_restore_fails_when_no_restic_pid_becomes_available(monkeypatch):
    agent = build_agent(identifier="agent-17", secret="secret-17")
    AgentReport.pending_reports = []
    AgentReport.finished_reports.clear()
    AgentReport.restore_report(
        report_uuid="restore-report-1",
        job_id=1,
        repository_id=2,
        data={"snapshot_id": "snap-1"},
    )
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

    report = Agent.cmd_cancel_restore(agent, report_uuid="restore-report-1")

    assert report.final_state.name == "failed"
    assert "No restic process became available" in report.log
    assert cancel_calls == []


def test_restic_download_verifies_sha256sum(tmp_path, monkeypatch):
    archive_data = bz2.compress(b"restic-binary")
    checksum = hashlib.sha256(archive_data).hexdigest()
    calls = []

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

    monkeypatch.setenv("DRASTIC_AGENT_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(agent_module.Agent, "os_clean", property(lambda self: "linux"))
    monkeypatch.setattr(agent_module.Agent, "arch", property(lambda self: "amd64"))
    monkeypatch.setattr(agent_module.requests, "get", fake_get)

    agent = Agent.__new__(Agent)
    Agent._Agent__check_restic_binary(agent)

    binary_path = tmp_path / "bin" / "restic_0.18.1_linux_amd64"
    assert binary_path.read_bytes() == b"restic-binary"
    assert os.access(binary_path, os.X_OK)
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
