from datetime import datetime

from flask import Flask

from drastic_server import models as _models  # noqa: F401
from drastic_server.extensions import db
from drastic_server.models.agent import (
    Agent,
    AgentOperation,
    AgentOperationArtifact,
    AgentOperationSource,
    AgentOperationState,
    AgentOperationType,
)
from drastic_server.models.job import Job, JobType
from drastic_server.models.repository import Repository
from drastic_server.models.secret import AgentSecretEnvelope, UserSecret
from drastic_server.models.user import User
from drastic_server.services.agent import AgentRequestService, operation_start
from drastic_server.services.agent import operations as agent_operations


def _build_app():
    app = Flask(__name__)
    app.config.update(
        SQLALCHEMY_DATABASE_URI="sqlite:///:memory:",
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        ENCRYPTION_KEY="test-key",
    )
    db.init_app(app)
    return app


def test_agent_operation_upserts_logs_and_artifacts(monkeypatch):
    app = _build_app()
    monkeypatch.setattr(agent_operations, "emit_job_state", lambda job: None)
    monkeypatch.setattr(agent_operations, "emit_operation_update", lambda operation: None)

    with app.app_context():
        db.create_all()
        try:
            user = User(name="admin")
            user.set_initial_password("password")
            agent = Agent(user=user, secret="agent-secret")
            repository = Repository(
                user=user,
                name="Repo",
                kind=Repository.KIND_CUSTOM,
                location="rest:http://repo.test/repo",
            )
            job = Job(
                agent=agent,
                name="Files",
                type=JobType.file,
                config={"paths": [], "exclude_patterns": []},
            )
            db.session.add_all([user, agent, repository, job])
            db.session.commit()

            AgentRequestService(agent).operation(
                {
                    "uuid": "operation-1",
                    "type": "backup",
                    "state": "success",
                    "source": "manual",
                    "job_id": job.id,
                    "repository_id": repository.id,
                    "started": "2026-05-10T10:00:00",
                    "ended": "2026-05-10T10:05:00",
                    "data": {},
                    "logs": [
                        {
                            "sequence": 1,
                            "level": "info",
                            "created": "2026-05-10T10:00:03",
                            "message": "backup finished",
                        }
                    ],
                    "artifacts": [
                        {
                            "uuid": "artifact-1",
                            "artifact_key": "default",
                            "snapshot_id": "snapshot-1",
                            "state": "success",
                            "data": {"path_count": 1},
                        }
                    ],
                }
            )

            operation = AgentOperation.query.filter_by(uuid="operation-1").one()
            artifact = AgentOperationArtifact.query.filter_by(uuid="artifact-1").one()

            assert operation.job_id == job.id
            assert operation.agent_id == agent.id
            assert operation.repository_id == repository.id
            assert operation.state == AgentOperationState.success
            assert operation.log == "backup finished"
            assert operation.logs[0].created == datetime(2026, 5, 10, 10, 0, 3)
            assert artifact.operation_id == operation.id
            assert artifact.artifact_key == "default"
            assert artifact.snapshot_id == "snapshot-1"
            assert artifact.data == {"path_count": 1}
        finally:
            db.session.remove()
            db.drop_all()


def test_started_agent_operation_is_updated_by_agent_report(monkeypatch):
    app = _build_app()
    emitted_job_ids = []
    emitted_operation_ids = []
    monkeypatch.setattr(operation_start, "emit_job_state", lambda job: emitted_job_ids.append(job.id))
    monkeypatch.setattr(
        operation_start,
        "emit_operation_update",
        lambda operation: emitted_operation_ids.append(operation.id),
    )
    monkeypatch.setattr(agent_operations, "emit_job_state", lambda job: emitted_job_ids.append(job.id))
    monkeypatch.setattr(
        agent_operations,
        "emit_operation_update",
        lambda operation: emitted_operation_ids.append(operation.id),
    )

    with app.app_context():
        db.create_all()
        try:
            user = User(name="admin")
            user.set_initial_password("password")
            agent = Agent(user=user, secret="agent-secret")
            repository = Repository(
                user=user,
                name="Repo",
                kind=Repository.KIND_CUSTOM,
                location="rest:http://repo.test/repo",
            )
            job = Job(
                agent=agent,
                name="Files",
                type=JobType.file,
                config={"paths": [], "exclude_patterns": []},
            )
            db.session.add_all([user, agent, repository, job])
            db.session.commit()

            operation = operation_start.start_agent_operation(
                agent=agent,
                job=job,
                repository=repository,
                operation_type=AgentOperationType.backup,
                msg="Backup job started",
                log_message="Backup job queued",
            )
            operation_id = operation.id

            AgentRequestService(agent).operation(
                {
                    "uuid": operation.uuid,
                    "type": "backup",
                    "state": "running",
                    "source": "manual",
                    "job_id": job.id,
                    "repository_id": repository.id,
                    "started": "2026-05-10T10:00:00",
                    "ended": None,
                    "data": {"files_processed": 1},
                    "logs": [{"sequence": 1, "level": "info", "message": "backup running"}],
                }
            )

            operations = AgentOperation.query.filter_by(uuid=operation.uuid).all()

            assert len(operations) == 1
            assert operations[0].id == operation_id
            assert operations[0].state == AgentOperationState.running
            assert operations[0].data == {"files_processed": 1}
            assert operations[0].log == "Backup job queued\nbackup running"
            assert emitted_job_ids == [job.id]
        finally:
            db.session.remove()
            db.drop_all()


def test_agent_repository_payload_uses_agent_key_and_recovery_on_demand():
    app = _build_app()

    with app.app_context():
        db.create_all()
        try:
            user = User(name="admin")
            user.set_initial_password("password")
            agent = Agent(user=user, secret="agent-secret")
            repository = Repository(
                user=user,
                name="Repo",
                kind=Repository.KIND_CUSTOM,
                location="rest:http://repo.test/repo",
            )
            repository.password_secret = UserSecret(
                user=user,
                type=UserSecret.TYPE_REPOSITORY_PASSWORD,
                name="Repo repository password",
                encrypted_value={"user": "secret"},
                public_data={},
                version=1,
            )
            agent.repositories.append(repository)
            db.session.add_all([user, agent, repository])
            db.session.flush()
            envelope = AgentSecretEnvelope(
                agent=agent,
                user_secret=repository.password_secret,
                encrypted_value={"recovery": "envelope"},
                secret_version=1,
                active=True,
            )
            db.session.add(envelope)
            db.session.execute(
                _models.agent_repositories.update()
                .where(
                    _models.agent_repositories.c.agent_id == agent.id,
                    _models.agent_repositories.c.repository_id == repository.id,
                )
                .values(encrypted_restic_access_key={"agent": "envelope"}, provisioned=True)
            )
            db.session.commit()

            service = AgentRequestService(agent)
            repositories = service.get_repositories()

            assert repositories[0]["encrypted_restic_access_key"] == {"agent": "envelope"}
            assert "encrypted_recovery_key" not in repositories[0]
            assert service.repository_recovery_envelope(repository.id) == {
                "encrypted_recovery_key": {"recovery": "envelope"}
            }

            assert service.store_repository_agent_key(repository.id, {"new": "agent-envelope"}) == {
                "ok": True
            }
            assignment = db.session.execute(
                _models.agent_repositories.select().where(
                    _models.agent_repositories.c.agent_id == agent.id,
                    _models.agent_repositories.c.repository_id == repository.id,
                )
            ).mappings().first()
            assert assignment["encrypted_restic_access_key"] == {"new": "agent-envelope"}
            assert assignment["provisioned"] is True
        finally:
            db.session.remove()
            db.drop_all()


def test_retention_operation_marks_forgotten_artifacts(monkeypatch):
    app = _build_app()
    monkeypatch.setattr(agent_operations, "emit_job_state", lambda job: None)
    monkeypatch.setattr(agent_operations, "emit_operation_update", lambda operation: None)

    with app.app_context():
        db.create_all()
        try:
            user = User(name="admin")
            user.set_initial_password("password")
            agent = Agent(user=user, secret="agent-secret")
            repository = Repository(
                user=user,
                name="Repo",
                kind=Repository.KIND_CUSTOM,
                location="rest:http://repo.test/repo",
            )
            job = Job(
                agent=agent,
                name="Files",
                type=JobType.file,
                config={"paths": [], "exclude_patterns": []},
            )
            operation = AgentOperation(
                uuid="operation-1",
                job=job,
                agent=agent,
                repository=repository,
                type=AgentOperationType.backup,
                state=AgentOperationState.success,
                source=AgentOperationSource.manual,
            )
            artifact = AgentOperationArtifact(
                uuid="artifact-1",
                operation=operation,
                artifact_key="default",
                snapshot_id="snapshot-1",
                state="success",
            )
            db.session.add_all([user, agent, repository, job, operation, artifact])
            db.session.commit()

            AgentRequestService(agent).operation(
                {
                    "uuid": "retention-operation-1",
                    "type": "retention",
                    "state": "success",
                    "source": "triggered",
                    "repository_id": repository.id,
                    "started": "2026-05-10T11:00:00",
                    "ended": "2026-05-10T11:01:00",
                    "data": {
                        "forgotten_artifacts": [
                            {
                                "uuid": "artifact-1",
                                "forgotten_at": "2026-05-10T11:00:30",
                                "snapshot_id": "snapshot-1",
                            }
                        ]
                    },
                    "logs": [{"sequence": 1, "level": "info", "message": "retention finished"}],
                }
            )

            db.session.refresh(artifact)

            assert artifact.forgotten_at.isoformat() == "2026-05-10T11:00:30"
        finally:
            db.session.remove()
            db.drop_all()
