from datetime import datetime, timedelta

import pytest

from drastic_server import models as _models  # noqa: F401
from drastic_server.app import create_app
from drastic_server.extensions import db
from drastic_server.models.agent import (
    Agent,
    AgentOperation,
    AgentOperationSource,
    AgentOperationState,
    AgentOperationType,
    AgentSession,
    agent_repositories,
)
from drastic_server.models.job import Job, JobType
from drastic_server.models.repository import Repository
from drastic_server.models.user import User
from drastic_server.services.agent import AgentRequestService, AgentService
from drastic_server.services.agent.operation_start import (
    DISPATCH_STATUS_UPDATED_AT_KEY,
)
from drastic_server.services.restore import RESTORE_MODE_PLAIN_FILE, RestoreService


def _build_app(monkeypatch):
    monkeypatch.setenv("DRASTIC_ENV", "test")
    monkeypatch.setenv("DRASTIC_APP_MASTER_SECRET", "test-master-secret")
    monkeypatch.setenv("DRASTIC_SQLALCHEMY_DATABASE_URI", "sqlite:///:memory:")
    monkeypatch.setenv("DRASTIC_SQLALCHEMY_TRACK_MODIFICATIONS", "false")
    monkeypatch.setenv("DRASTIC_JWT_COOKIE_CSRF_PROTECT", "false")
    app = create_app()
    app.config["TESTING"] = True
    return app


def _login(client):
    response = client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "account-password"},
    )
    assert response.status_code == 200


def _successful_report(operation):
    return {
        "uuid": operation.uuid,
        "type": operation.type.name,
        "state": "success",
        "source": "manual",
        "job_id": operation.job_id,
        "repository_id": operation.repository_id,
        "data": {"completed": True},
        "logs": [{"sequence": 1, "level": "info", "message": "completed"}],
    }


@pytest.mark.parametrize("state", ["success", "failed"])
def test_agent_update_uses_existing_operation_history_and_logs(monkeypatch, state):
    app = _build_app(monkeypatch)
    with app.app_context():
        db.create_all()
        try:
            user = User(name="admin")
            user.set_initial_password("account-password", recovery_key="user-recovery-key")
            agent = Agent(user=user, secret="agent-secret", hostname="backup-host")
            db.session.add_all([user, agent])
            db.session.commit()
            service = AgentRequestService(agent)
            payload = {"uuid": "update-1", "type": "agent_update", "state": "running", "source": "manual",
                       "logs": [{"sequence": 1, "level": "info", "message": "Preparing release"}]}
            service.operation(payload)
            operation = AgentOperation.query.filter_by(uuid="update-1").one()
            assert operation.state == AgentOperationState.running
            payload["state"] = state
            payload["logs"].append({"sequence": 2, "level": "error" if state == "failed" else "info",
                                    "message": f"Update {state}"})
            service.operation(payload)
            service.operation(payload)  # Reconnect/retry must keep one operation and stable log sequences.
            client = app.test_client()
            _login(client)
            detail = client.get(f"/api/agents/operations/{operation.id}")
            assert detail.status_code == 200
            assert detail.json["type_text"] == "Agent update"
            assert detail.json["state"] == state
            assert [log["message"] for log in detail.json["logs"]] == ["Preparing release", f"Update {state}"]
            assert AgentOperation.query.count() == 1
        finally:
            db.session.remove()
            db.drop_all()


def test_backup_admission_timeout_returns_202_and_later_report_succeeds(monkeypatch):
    app = _build_app(monkeypatch)
    with app.app_context():
        db.create_all()
        try:
            user = User(name="admin")
            user.set_initial_password("account-password", recovery_key="user-recovery-key")
            agent = Agent(user=user, secret="agent-secret", hostname="backup-host")
            AgentSession(agent=agent, request_sid="agent-sid")
            repository = Repository(
                user=user,
                name="Repo",
                kind=Repository.KIND_CUSTOM,
                location="rest:http://repo.test/repo",
            )
            agent.repositories.append(repository)
            job = Job(agent=agent, name="Files", type=JobType.file, config={"paths": []})
            db.session.add_all([user, agent, repository, job])
            db.session.commit()

            monkeypatch.setattr(
                "drastic_server.views.api.jobs.assign_agent_repository",
                lambda *_args, **_kwargs: (repository, False),
            )

            def run_job(target_agent, **_kwargs):
                assert target_agent.id == agent.id
                return AgentService.failed_command_report("Agent request timed out", timeout=True)

            monkeypatch.setattr(AgentService, "run_job", run_job)
            client = app.test_client()
            _login(client)

            response = client.post(
                f"/api/jobs/{job.id}/run",
                json={"repository_id": repository.id},
            )

            assert response.status_code == 202
            assert response.json["state"] == "running"
            assert response.json["dispatch_status"] == "unknown"

            operation = db.session.get(AgentOperation, response.json["operation_id"])
            assert operation.state == AgentOperationState.running
            assert operation.data["options"] == {
                "repository_check": {"enabled": False, "read_data": None}
            }
            assert operation.data["dispatch_status"] == "unknown"
            datetime.fromisoformat(operation.data[DISPATCH_STATUS_UPDATED_AT_KEY])

            running_report = _successful_report(operation)
            running_report.update(state="running", data={"files_processed": 1})
            AgentRequestService(agent).operation(running_report)

            db.session.refresh(operation)
            assert operation.state == AgentOperationState.running
            assert operation.data == {"files_processed": 1}

            AgentRequestService(agent).operation(_successful_report(operation))

            db.session.refresh(operation)
            assert operation.state == AgentOperationState.success
            assert operation.data == {"completed": True}
            detail = client.get(f"/api/agents/operations/{operation.id}")
            assert detail.status_code == 200
            assert detail.json["agent_hostname"] == "backup-host"
            assert detail.json["job_name"] == "Files"
            assert detail.json["repository_name"] == "Repo"

            operation.job = None
            operation.repository = None
            db.session.commit()
            detail = client.get(f"/api/agents/operations/{operation.id}")
            assert detail.status_code == 200
            assert detail.json["job_name"] is None
            assert detail.json["repository_name"] is None
        finally:
            db.session.remove()
            db.drop_all()


def test_explicit_backup_admission_rejection_is_failed_conflict(monkeypatch):
    app = _build_app(monkeypatch)
    with app.app_context():
        db.create_all()
        try:
            user = User(name="admin")
            user.set_initial_password("account-password", recovery_key="user-recovery-key")
            agent = Agent(user=user, secret="agent-secret")
            AgentSession(agent=agent, request_sid="agent-sid")
            repository = Repository(
                user=user,
                name="Repo",
                kind=Repository.KIND_CUSTOM,
                location="rest:http://repo.test/repo",
            )
            agent.repositories.append(repository)
            job = Job(agent=agent, name="Files", type=JobType.file, config={"paths": []})
            db.session.add_all([user, agent, repository, job])
            db.session.commit()

            monkeypatch.setattr(
                "drastic_server.views.api.jobs.assign_agent_repository",
                lambda *_args, **_kwargs: (repository, False),
            )

            def run_job(target_agent, **_kwargs):
                assert target_agent.id == agent.id
                return AgentService.failed_command_report("Resource is busy")

            monkeypatch.setattr(AgentService, "run_job", run_job)
            client = app.test_client()
            _login(client)

            response = client.post(
                f"/api/jobs/{job.id}/run",
                json={"repository_id": repository.id},
            )

            operation = AgentOperation.query.one()
            assert response.status_code == 409
            assert operation.state == AgentOperationState.failed
        finally:
            db.session.remove()
            db.drop_all()


def test_cross_agent_restore_timeout_uses_target_key_and_later_report_succeeds(monkeypatch):
    app = _build_app(monkeypatch)
    with app.app_context():
        db.create_all()
        try:
            user = User(name="admin")
            user.set_initial_password("account-password", recovery_key="user-recovery-key")
            source_agent = Agent(user=user, secret="source-secret")
            target_agent = Agent(user=user, secret="target-secret")
            AgentSession(agent=target_agent, request_sid="target-sid")
            repository = Repository(
                user=user,
                name="Repo",
                kind=Repository.KIND_CUSTOM,
                location="rest:http://repo.test/repo",
            )
            source_agent.repositories.append(repository)
            target_agent.repositories.append(repository)
            job = Job(
                agent=source_agent,
                name="Source files",
                type=JobType.file,
                config={"paths": []},
            )
            db.session.add_all([user, source_agent, target_agent, repository, job])
            db.session.flush()
            db.session.execute(
                agent_repositories.update()
                .where(
                    agent_repositories.c.agent_id == source_agent.id,
                    agent_repositories.c.repository_id == repository.id,
                )
                .values(encrypted_restic_access_key={"key": "source"}, provisioned=True)
            )
            db.session.execute(
                agent_repositories.update()
                .where(
                    agent_repositories.c.agent_id == target_agent.id,
                    agent_repositories.c.repository_id == repository.id,
                )
                .values(encrypted_restic_access_key={"key": "target"}, provisioned=True)
            )
            db.session.commit()

            monkeypatch.setattr(
                RestoreService,
                "_ensure_snapshot_belongs_to_job",
                lambda *_args, **_kwargs: None,
            )
            dispatched = {}

            def timeout_restore(agent, **kwargs):
                dispatched["agent_id"] = agent.id
                dispatched.update(kwargs)
                return AgentService.failed_command_report("Agent request timed out", timeout=True)

            monkeypatch.setattr(AgentService, "run_restore", timeout_restore)

            response = RestoreService.start_restore(
                user.id,
                {
                    "job_id": job.id,
                    "agent_id": target_agent.id,
                    "repository_id": repository.id,
                    "mode": RESTORE_MODE_PLAIN_FILE,
                    "snapshot_id": "snapshot-1",
                    "restore_location": "/srv/restore",
                    "include_paths": ["/etc/hosts"],
                    "overwrite_policy": "fail_if_exists",
                },
            )

            operation = db.session.get(AgentOperation, response["operation_id"])
            assert response["state"] == "running"
            assert response["dispatch_status"] == "unknown"
            assert operation.data["mode"] == RESTORE_MODE_PLAIN_FILE
            assert operation.data["dispatch_status"] == "unknown"
            datetime.fromisoformat(operation.data[DISPATCH_STATUS_UPDATED_AT_KEY])
            assert operation.agent_id == target_agent.id
            assert operation.job_id == job.id
            assert job.agent_id == source_agent.id
            assert dispatched["agent_id"] == target_agent.id
            assert dispatched["repository"]["encrypted_restic_access_key"] == {
                "key": "target"
            }

            AgentRequestService(target_agent).operation(_successful_report(operation))

            db.session.refresh(operation)
            assert operation.state == AgentOperationState.success
        finally:
            db.session.remove()
            db.drop_all()


def test_startup_maintenance_only_reconciles_stale_unknown_dispatches(monkeypatch):
    app = _build_app(monkeypatch)
    monkeypatch.setattr(
        "drastic_server.cli.reconcile_native_repository_quarantine", lambda: (0, 0)
    )

    with app.app_context():
        db.create_all()
        try:
            now = datetime.now()
            user = User(name="admin")
            user.set_initial_password("account-password", recovery_key="user-recovery-key")
            agent = Agent(user=user, secret="agent-secret")
            stale_unknown = AgentOperation(
                uuid="stale-unknown",
                agent=agent,
                type=AgentOperationType.backup,
                state=AgentOperationState.running,
                source=AgentOperationSource.manual,
                started=now - timedelta(hours=1),
                data={
                    "dispatch_status": "unknown",
                    DISPATCH_STATUS_UPDATED_AT_KEY: (now - timedelta(minutes=16)).isoformat(),
                },
            )
            recent_unknown = AgentOperation(
                uuid="recent-unknown",
                agent=agent,
                type=AgentOperationType.restore,
                state=AgentOperationState.running,
                source=AgentOperationSource.manual,
                started=now - timedelta(hours=1),
                data={
                    "dispatch_status": "unknown",
                    DISPATCH_STATUS_UPDATED_AT_KEY: (now - timedelta(minutes=14)).isoformat(),
                },
            )
            confirmed_long_running = AgentOperation(
                uuid="confirmed-long-running",
                agent=agent,
                type=AgentOperationType.repository_check,
                state=AgentOperationState.running,
                source=AgentOperationSource.manual,
                started=now - timedelta(days=1),
                data={"progress": 1},
            )
            terminal_unknown = AgentOperation(
                uuid="terminal-unknown",
                agent=agent,
                type=AgentOperationType.repository_unlock,
                state=AgentOperationState.success,
                source=AgentOperationSource.manual,
                started=now - timedelta(hours=1),
                ended=now - timedelta(minutes=30),
                data={
                    "dispatch_status": "unknown",
                    DISPATCH_STATUS_UPDATED_AT_KEY: (now - timedelta(minutes=30)).isoformat(),
                },
            )
            db.session.add_all(
                [
                    user,
                    agent,
                    stale_unknown,
                    recent_unknown,
                    confirmed_long_running,
                    terminal_unknown,
                ]
            )
            db.session.commit()

            result = app.test_cli_runner().invoke(args=["maintenance", "startup"])

            assert result.exit_code == 0
            db.session.refresh(stale_unknown)
            db.session.refresh(recent_unknown)
            db.session.refresh(confirmed_long_running)
            db.session.refresh(terminal_unknown)
            assert stale_unknown.state == AgentOperationState.failed
            assert stale_unknown.ended is not None
            assert stale_unknown.logs[-1].level.name == "error"
            assert "did not confirm operation dispatch" in stale_unknown.logs[-1].message
            assert recent_unknown.state == AgentOperationState.running
            assert recent_unknown.ended is None
            assert confirmed_long_running.state == AgentOperationState.running
            assert confirmed_long_running.ended is None
            assert terminal_unknown.state == AgentOperationState.success
            assert "1 unknown dispatches reconciled" in result.output
        finally:
            db.session.remove()
            db.drop_all()
