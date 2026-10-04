from datetime import datetime, timedelta, timezone

import pytest
from flask import Flask

from drastic_server import models as _models  # noqa: F401
from drastic_server.extensions import db
from drastic_server.models.agent import (
    Agent,
    AgentOperation,
    AgentOperationArtifact,
    AgentOperationLog,
    AgentOperationLogLevel,
    AgentOperationSource,
    AgentOperationState,
    AgentOperationType,
)
from drastic_server.models.job import Job, JobSchedule, JobType
from drastic_server.models.repository import Repository
from drastic_server.models.retention import Retention
from drastic_server.models.secret import AgentSecretEnvelope, UserSecret
from drastic_server.models.user import User
from drastic_server.schemas.agent import AgentOperationResponseSchema
from drastic_server.schemas.job import JobLastOperationSchema, JobStatusResponseSchema
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


@pytest.mark.parametrize("timestamp, utc_hour", [
    ("2026-10-04T09:54:41+02:00", 7),
    ("2026-10-04T07:54:41Z", 7),
    ("2026-01-04T09:54:41+01:00", 8),
    ("2026-10-04T07:54:41", 7),
])
def test_operation_timestamps_survive_database_roundtrip_with_utc_offset(monkeypatch, timestamp, utc_hour):
    app = _build_app()
    monkeypatch.setattr(agent_operations, "emit_operation_update", lambda operation: None)
    monkeypatch.setattr(operation_start, "emit_operation_update", lambda operation: None)
    start = datetime.fromisoformat(timestamp)
    expected = start.replace(hour=utc_hour, tzinfo=None)
    with app.app_context():
        db.create_all()
        try:
            user = User(name="admin")
            user.set_initial_password("password")
            agent = Agent(user=user, secret="agent-secret")
            db.session.add_all([user, agent])
            db.session.commit()
            before = datetime.now(timezone.utc).replace(tzinfo=None)
            operation = operation_start.start_agent_operation(
                agent=agent, operation_type=AgentOperationType.backup, msg="Backup queued",
            )
            after = datetime.now(timezone.utc).replace(tzinfo=None)
            assert before <= operation.started <= after
            assert before <= operation.logs[0].created <= after

            AgentRequestService(agent).operation({
                "uuid": operation.uuid, "type": "backup", "state": "success", "source": "manual",
                "started": timestamp, "ended": (start + timedelta(seconds=6)).isoformat(),
                "logs": [{"sequence": 1, "level": "info", "message": "Finished", "created": timestamp},
                         {"sequence": 2, "level": "info", "message": "No timestamp", "created": None}],
                "artifacts": [{"uuid": "utc-artifact", "artifact_key": "default", "state": "success",
                               "forgotten_at": (start + timedelta(seconds=6)).isoformat()}],
            })
            db.session.expire_all()
            assert operation.started == expected
            assert (operation.ended - operation.started).total_seconds() == 6
            log = next(log for log in operation.logs if log.sequence == 1)
            assert log.created == expected
            log = next(log for log in operation.logs if log.sequence == 2)
            assert before <= log.created <= datetime.now(timezone.utc).replace(tzinfo=None)
            assert operation.artifacts[0].forgotten_at == expected + timedelta(seconds=6)
            for schema in (AgentOperationResponseSchema(), JobLastOperationSchema(), JobStatusResponseSchema()):
                response = schema.dump(operation)
                assert response["started"] == expected.replace(tzinfo=timezone.utc).isoformat()
                assert response["ended"] == (expected + timedelta(seconds=6)).replace(tzinfo=timezone.utc).isoformat()
            response = AgentOperationResponseSchema().dump(operation)
            assert all(log["created"].endswith("+00:00") for log in response["logs"])
            assert response["artifacts"][0]["forgotten_at"].endswith("+00:00")
        finally:
            db.session.remove()
            db.drop_all()


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
            agent.repositories.append(repository)
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
    monkeypatch.setattr(
        operation_start, "emit_job_state", lambda job: emitted_job_ids.append(job.id)
    )
    monkeypatch.setattr(
        operation_start,
        "emit_operation_update",
        lambda operation: emitted_operation_ids.append(operation.id),
    )
    monkeypatch.setattr(
        agent_operations, "emit_job_state", lambda job: emitted_job_ids.append(job.id)
    )
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


def test_agent_cannot_modify_another_agents_existing_operation(monkeypatch):
    app = _build_app()
    monkeypatch.setattr(agent_operations, "emit_job_state", lambda job: None)
    monkeypatch.setattr(agent_operations, "emit_operation_update", lambda operation: None)

    with app.app_context():
        db.create_all()
        try:
            user = User(name="admin")
            user.set_initial_password("password")
            agent = Agent(user=user, secret="agent-secret")
            other_agent = Agent(user=user, secret="other-secret")
            repository = Repository(
                user=user,
                name="Repo",
                kind=Repository.KIND_CUSTOM,
                location="rest:http://repo.test/repo",
            )
            other_repository = Repository(
                user=user,
                name="Other repo",
                kind=Repository.KIND_CUSTOM,
                location="rest:http://repo.test/other",
            )
            job = Job(agent=agent, name="Files", type=JobType.file, config={"paths": []})
            other_job = Job(
                agent=other_agent, name="Other", type=JobType.file, config={"paths": []}
            )
            operation = AgentOperation(
                uuid="operation-terminal",
                agent=agent,
                job=job,
                repository=repository,
                type=AgentOperationType.backup,
                state=AgentOperationState.success,
                source=AgentOperationSource.manual,
                data={"owner": "original"},
            )
            log = AgentOperationLog(
                operation=operation,
                sequence=1,
                level=AgentOperationLogLevel.info,
                message="original log",
            )
            artifact = AgentOperationArtifact(
                operation=operation,
                uuid="artifact-original",
                artifact_key="default",
                snapshot_id="snapshot-original",
                state="success",
                data={"owner": "original"},
            )
            db.session.add_all(
                [
                    user,
                    agent,
                    other_agent,
                    repository,
                    other_repository,
                    job,
                    other_job,
                    operation,
                    log,
                    artifact,
                ]
            )
            db.session.commit()

            with pytest.raises(PermissionError, match="does not belong"):
                AgentRequestService(other_agent).operation(
                    {
                        "uuid": operation.uuid,
                        "type": "restore",
                        "state": "running",
                        "source": "triggered",
                        "job_id": other_job.id,
                        "repository_id": other_repository.id,
                        "data": {
                            "owner": "attacker",
                            "forgotten_artifacts": [
                                {
                                    "uuid": artifact.uuid,
                                    "forgotten_at": "2026-05-10T11:00:30",
                                }
                            ],
                        },
                        "logs": [{"sequence": 1, "level": "error", "message": "attacker log"}],
                        "artifacts": [
                            {
                                "uuid": artifact.uuid,
                                "artifact_key": "default",
                                "state": "failed",
                                "data": {"owner": "attacker"},
                            }
                        ],
                    }
                )

            db.session.refresh(operation)
            db.session.refresh(log)
            db.session.refresh(artifact)
            assert operation.agent_id == agent.id
            assert operation.job_id == job.id
            assert operation.repository_id == repository.id
            assert operation.type == AgentOperationType.backup
            assert operation.source == AgentOperationSource.manual
            assert operation.state == AgentOperationState.success
            assert operation.data == {"owner": "original"}
            assert log.message == "original log"
            assert artifact.state == "success"
            assert artifact.data == {"owner": "original"}
            assert artifact.forgotten_at is None
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
            assignment = (
                db.session.execute(
                    _models.agent_repositories.select().where(
                        _models.agent_repositories.c.agent_id == agent.id,
                        _models.agent_repositories.c.repository_id == repository.id,
                    )
                )
                .mappings()
                .first()
            )
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
            agent.repositories.append(repository)
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


@pytest.mark.parametrize(
    "foreign_reference",
    ["job", "repository", "schedule", "retention", "parent"],
)
def test_new_operation_rejects_cross_agent_references(monkeypatch, foreign_reference):
    app = _build_app()
    monkeypatch.setattr(agent_operations, "emit_job_state", lambda job: None)
    monkeypatch.setattr(agent_operations, "emit_operation_update", lambda operation: None)

    with app.app_context():
        db.create_all()
        try:
            owner_user = User(name="owner")
            owner_user.set_initial_password("password")
            reporter_user = User(name="reporter")
            reporter_user.set_initial_password("password")
            owner = Agent(user=owner_user, secret="owner-secret")
            reporter = Agent(user=reporter_user, secret="reporter-secret")
            repository = Repository(
                user=owner_user,
                name="Owner repo",
                kind=Repository.KIND_CUSTOM,
                location="rest:http://repo.test/owner",
            )
            job = Job(agent=owner, name="Owner job", type=JobType.file, config={"paths": []})
            retention = Retention(user=owner_user, name="Owner retention", keep_last=1)
            schedule = JobSchedule(
                job=job,
                repository=repository,
                retention=retention,
                cron_string="0 0 * * *",
            )
            parent = AgentOperation(
                uuid="owner-parent",
                agent=owner,
                type=AgentOperationType.command,
                state=AgentOperationState.success,
                source=AgentOperationSource.manual,
            )
            owner.repositories.append(repository)
            db.session.add_all(
                [
                    owner_user,
                    reporter_user,
                    owner,
                    reporter,
                    repository,
                    job,
                    retention,
                    schedule,
                    parent,
                ]
            )
            db.session.commit()

            payload = {
                "uuid": f"cross-agent-{foreign_reference}",
                "type": "command",
                "state": "success",
                "source": "manual",
                "logs": [],
            }
            if foreign_reference == "job":
                payload["job_id"] = job.id
            elif foreign_reference == "repository":
                payload["repository_id"] = repository.id
            elif foreign_reference == "schedule":
                payload["schedule_id"] = schedule.id
            elif foreign_reference == "retention":
                payload["retention_id"] = retention.id
            else:
                payload["parent_operation_uuid"] = parent.uuid

            with pytest.raises(PermissionError):
                AgentRequestService(reporter).operation(payload)

            assert AgentOperation.query.filter_by(uuid=payload["uuid"]).first() is None
        finally:
            db.session.remove()
            db.drop_all()


def test_new_operation_accepts_deleted_references_and_links_late_parent(monkeypatch):
    app = _build_app()
    monkeypatch.setattr(agent_operations, "emit_job_state", lambda job: None)
    monkeypatch.setattr(agent_operations, "emit_operation_update", lambda operation: None)

    with app.app_context():
        db.create_all()
        try:
            user = User(name="admin")
            user.set_initial_password("password")
            agent = Agent(user=user, secret="agent-secret")
            db.session.add_all([user, agent])
            db.session.commit()

            child_id = AgentRequestService(agent).operation(
                {
                    "uuid": "offline-child",
                    "type": "repository_check",
                    "state": "success",
                    "source": "triggered",
                    "job_id": 99901,
                    "repository_id": 99902,
                    "schedule_id": 99903,
                    "retention_id": 99904,
                    "parent_operation_uuid": "offline-parent",
                    "data": {"result": "ok"},
                    "logs": [],
                }
            )

            child = db.session.get(AgentOperation, child_id)
            assert child.job_id is None
            assert child.repository_id is None
            assert child.schedule_id is None
            assert child.retention_id is None
            assert child.parent_operation_id is None

            parent_id = AgentRequestService(agent).operation(
                {
                    "uuid": "offline-parent",
                    "type": "backup",
                    "state": "success",
                    "source": "manual",
                    "data": {},
                    "logs": [],
                }
            )

            db.session.refresh(child)
            assert child.parent_operation_id == parent_id
            assert child.data == {"result": "ok"}
        finally:
            db.session.remove()
            db.drop_all()


def test_agent_cannot_mark_another_agents_artifact_forgotten(monkeypatch):
    app = _build_app()
    monkeypatch.setattr(agent_operations, "emit_job_state", lambda job: None)
    monkeypatch.setattr(agent_operations, "emit_operation_update", lambda operation: None)

    with app.app_context():
        db.create_all()
        try:
            user = User(name="admin")
            user.set_initial_password("password")
            owner = Agent(user=user, secret="owner-secret")
            reporter = Agent(user=user, secret="reporter-secret")
            operation = AgentOperation(
                uuid="owner-operation",
                agent=owner,
                type=AgentOperationType.backup,
                state=AgentOperationState.success,
                source=AgentOperationSource.manual,
            )
            artifact = AgentOperationArtifact(
                uuid="owner-artifact",
                operation=operation,
                artifact_key="default",
                state="success",
            )
            db.session.add_all([user, owner, reporter, operation, artifact])
            db.session.commit()

            with pytest.raises(PermissionError, match="Artifact"):
                AgentRequestService(reporter).operation(
                    {
                        "uuid": "reporter-retention",
                        "type": "retention",
                        "state": "success",
                        "source": "manual",
                        "data": {
                            "forgotten_artifacts": [
                                {
                                    "uuid": artifact.uuid,
                                    "forgotten_at": "2026-05-10T11:00:30",
                                }
                            ]
                        },
                        "logs": [],
                    }
                )

            db.session.refresh(artifact)
            assert artifact.forgotten_at is None
            assert AgentOperation.query.filter_by(uuid="reporter-retention").first() is None
        finally:
            db.session.remove()
            db.drop_all()
