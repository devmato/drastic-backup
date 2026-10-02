from datetime import datetime

from drastic_common.agent.enums import AgentJobType
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
)
from drastic_server.models.job import Job
from drastic_server.models.user import User
from drastic_server.services.agent import AgentRequestService, AgentService


def build_app(monkeypatch):
    monkeypatch.setenv("DRASTIC_ENV", "test")
    monkeypatch.setenv("DRASTIC_APP_MASTER_SECRET", "test-master-secret")
    monkeypatch.setenv("DRASTIC_SQLALCHEMY_DATABASE_URI", "sqlite:///:memory:")
    monkeypatch.setenv("DRASTIC_SQLALCHEMY_TRACK_MODIFICATIONS", "false")
    monkeypatch.setenv("DRASTIC_JWT_COOKIE_CSRF_PROTECT", "false")
    app = create_app()
    app.config["TESTING"] = True
    return app


def create_running_job():
    user = User(name="admin")
    user.set_initial_password("account-password", recovery_key="user-recovery-key")
    agent = Agent(user=user, secret="agent-secret")
    AgentSession(agent=agent, request_sid="agent-sid")
    job = Job(agent=agent, name="Files", type=AgentJobType.file, config={"paths": []})
    operation = AgentOperation(
        uuid="operation-1",
        agent=agent,
        job=job,
        type=AgentOperationType.backup,
        state=AgentOperationState.running,
        source=AgentOperationSource.manual,
        started=datetime.now(),
    )
    db.session.add_all([user, agent, job, operation])
    db.session.commit()
    return job, operation


def login(client):
    response = client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "account-password"},
    )
    assert response.status_code == 200


def test_successful_job_cancellation_waits_for_terminal_agent_report(monkeypatch):
    app = build_app(monkeypatch)
    with app.app_context():
        db.create_all()
        try:
            job, operation = create_running_job()

            def cancel_job(agent, **kwargs):
                assert agent.id == job.agent_id
                assert kwargs == {"job_id": job.id, "operation_uuid": operation.uuid}
                return {"state": AgentOperationState.success}

            monkeypatch.setattr(AgentService, "cancel_job", cancel_job)
            client = app.test_client()
            login(client)

            response = client.post(f"/api/jobs/{job.id}/cancel")

            db.session.refresh(operation)
            assert response.status_code == 200
            assert response.json["msg"] == "Job cancellation requested"
            assert operation.state == AgentOperationState.running
            assert operation.ended is None

            AgentRequestService(job.agent).operation(
                {
                    "uuid": operation.uuid,
                    "type": "backup",
                    "state": "cancelled",
                    "source": "manual",
                    "job_id": job.id,
                    "started": operation.started.isoformat(),
                    "ended": "2026-07-23T12:00:00",
                    "logs": [
                        {
                            "sequence": 1,
                            "level": "warning",
                            "message": "Backup cancelled",
                        }
                    ],
                }
            )

            db.session.refresh(operation)
            assert operation.state == AgentOperationState.cancelled
            assert operation.ended == datetime(2026, 7, 23, 12, 0)
        finally:
            db.session.remove()
            db.drop_all()


def test_failed_job_cancellation_leaves_backend_operation_running(monkeypatch):
    app = build_app(monkeypatch)
    with app.app_context():
        db.create_all()
        try:
            job, operation = create_running_job()

            def cancel_job(agent, **kwargs):
                assert agent.id == job.agent_id
                return {
                    "state": AgentOperationState.failed,
                    "log": "Restic process is no longer running",
                }

            monkeypatch.setattr(AgentService, "cancel_job", cancel_job)
            client = app.test_client()
            login(client)

            response = client.post(f"/api/jobs/{job.id}/cancel")

            db.session.refresh(operation)
            assert response.status_code == 400
            assert operation.state == AgentOperationState.running
        finally:
            db.session.remove()
            db.drop_all()
