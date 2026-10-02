from flask import Flask
from sqlalchemy import UniqueConstraint, delete, text

from drastic_common.agent.enums import (
    AgentJobActionModule,
    AgentJobType,
    AgentOperationSource,
    AgentOperationState,
    AgentOperationType,
)
from drastic_server import models as _models  # noqa: F401
from drastic_server.extensions import db
from drastic_server.models.agent import (
    Agent,
    AgentOperation,
    AgentOperationArtifact,
    AgentSession,
)
from drastic_server.models.job import Job, JobAction, JobSchedule
from drastic_server.models.notification import NotificationConfig, NotificationDelivery
from drastic_server.models.repository import Repository
from drastic_server.models.retention import Retention
from drastic_server.models.user import User


def _build_app():
    app = Flask(__name__)
    app.config.update(
        SQLALCHEMY_DATABASE_URI="sqlite:///:memory:",
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        ENCRYPTION_KEY="test-key",
    )
    db.init_app(app)
    return app


def _create_graph():
    user = User(name="admin")
    user.set_initial_password("password")
    agent = Agent(user=user, secret="agent-secret")
    repository = Repository(
        user=user,
        name="Repo",
        kind=Repository.KIND_CUSTOM,
        location="rest:http://repo.test/repo",
    )
    job = Job(agent=agent, name="Files", type=AgentJobType.file)
    db.session.add_all([user, agent, repository, job])
    db.session.flush()
    return user, agent, repository, job


def test_json_defaults_are_independent_and_in_place_action_updates_are_persisted():
    app = _build_app()
    with app.app_context():
        db.create_all()
        _user, agent, _repository, job = _create_graph()
        action = JobAction(
            job=job,
            module=AgentJobActionModule.command,
            hook="before",
        )
        first_operation = AgentOperation(
            agent=agent,
            state=AgentOperationState.running,
            type=AgentOperationType.backup,
            source=AgentOperationSource.manual,
        )
        second_operation = AgentOperation(
            agent=agent,
            state=AgentOperationState.running,
            type=AgentOperationType.backup,
            source=AgentOperationSource.manual,
        )
        artifact = AgentOperationArtifact(
            operation=first_operation,
            artifact_key="snapshot",
            state="success",
        )
        db.session.add_all([action, first_operation, second_operation, artifact])
        db.session.commit()

        assert first_operation.data == {}
        assert second_operation.data == {}
        assert first_operation.data is not second_operation.data
        assert artifact.data == {}

        action.data["command"] = "touch /tmp/marker"
        db.session.commit()
        db.session.expire(action)

        assert action.data["command"] == "touch /tmp/marker"


def test_notification_list_defaults_are_independent_and_mutable():
    app = _build_app()
    with app.app_context():
        db.create_all()
        user, _agent, _repository, _job = _create_graph()
        first = NotificationConfig(user=user)
        first.url = "mailto://first@example.test"
        second = NotificationConfig(user=user)
        second.url = "mailto://second@example.test"
        db.session.add_all([first, second])
        db.session.commit()

        first.operation_types.append("backup")
        db.session.commit()
        db.session.expire_all()

        assert db.session.get(NotificationConfig, first.id).operation_types == ["backup"]
        assert db.session.get(NotificationConfig, second.id).operation_types == []


def test_deleting_used_schedule_preserves_operation_history():
    app = _build_app()
    with app.app_context():
        db.session.execute(text("PRAGMA foreign_keys = ON"))
        db.create_all()
        _user, agent, repository, job = _create_graph()
        schedule = JobSchedule(
            job=job,
            repository=repository,
            cron_string="0 2 * * *",
        )
        operation = AgentOperation(
            agent=agent,
            job=job,
            repository=repository,
            schedule=schedule,
            state=AgentOperationState.success,
            type=AgentOperationType.backup,
            source=AgentOperationSource.schedule,
        )
        db.session.add_all([schedule, operation])
        db.session.commit()

        db.session.execute(delete(JobSchedule).where(JobSchedule.id == schedule.id))
        db.session.commit()
        db.session.expire(operation)

        assert operation.schedule_id is None
        assert operation.job_id == job.id
        assert operation.repository_id == repository.id


def test_encrypted_columns_use_public_physical_names():
    assert NotificationConfig._url.property.columns[0].name == "encrypted_url"
    assert Repository._environment.property.columns[0].name == "encrypted_environment"


def test_required_nullability_and_uniques_are_declared():
    assert not User.name.nullable
    assert not User.password.nullable
    assert not User.encrypted_recovery_key.nullable
    assert not Retention.user_id.nullable
    assert not JobSchedule.enabled.nullable
    assert not JobSchedule.advanced.nullable
    assert not NotificationConfig._url.property.columns[0].nullable
    assert not AgentOperation.data.nullable
    assert not AgentOperationArtifact.data.nullable

    agent_session_uniques = {
        tuple(column.name for column in constraint.columns)
        for constraint in AgentSession.__table__.constraints
        if isinstance(constraint, UniqueConstraint)
    }
    retention_uniques = {
        tuple(column.name for column in constraint.columns)
        for constraint in Retention.__table__.constraints
        if isinstance(constraint, UniqueConstraint)
    }
    assert ("agent_id",) in agent_session_uniques
    assert ("request_sid",) in agent_session_uniques
    assert ("user_id", "name") in retention_uniques
    assert "ix_notification_deliveries_open" in {
        index.name for index in NotificationDelivery.__table__.indexes
    }
