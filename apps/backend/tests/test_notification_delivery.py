from flask import Flask

from drastic_server import models as _models  # noqa: F401
from drastic_server.cli import register_cli
from drastic_server.extensions import db
from drastic_server.models.agent import (
    Agent,
    AgentOperation,
    AgentOperationArtifact,
    AgentOperationLog,
    AgentOperationSource,
    AgentOperationState,
    AgentOperationType,
)
from drastic_server.models.notification import NotificationConfig, NotificationDelivery
from drastic_server.models.user import User
from drastic_server.services.agent import AgentRequestService
from drastic_server.services.operations import reports as agent_operations
from drastic_server.services.operations.reports import INGEST_NOTIFICATION_RETRY_LIMIT


def _build_app():
    app = Flask("drastic_server")
    app.config.update(
        SQLALCHEMY_DATABASE_URI="sqlite:///:memory:",
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        ENCRYPTION_KEY="test-key",
    )
    db.init_app(app)
    return app


def _operation(uuid, operation_type="backup"):
    return {
        "uuid": uuid,
        "type": operation_type,
        "state": "success",
        "source": "manual",
        "started": "2026-07-23T10:00:00",
        "ended": "2026-07-23T10:01:00",
        "logs": [],
    }


def test_notification_failure_does_not_fail_ingest_and_is_retried(monkeypatch):
    app = _build_app()
    monkeypatch.setattr(agent_operations, "emit_operation_update", lambda operation: None)

    class FailingApprise:
        def add(self, url):
            raise RuntimeError(f"could not use {url}")

    class SuccessfulApprise:
        notifications = []

        def add(self, url):
            return True

        def notify(self, **message):
            self.notifications.append(message)
            return True

    with app.app_context():
        db.create_all()
        try:
            user = User(name="admin")
            user.set_initial_password("password")
            agent = Agent(user=user, secret="agent-secret")
            config = NotificationConfig(
                user=user,
                operation_types=["backup"],
                operation_states=["success"],
            )
            config.url = "mailto://secret@example.test"
            db.session.add_all([user, agent, config])
            db.session.commit()

            monkeypatch.setattr(agent_operations, "Apprise", FailingApprise)
            operation_id = AgentRequestService(agent).operation(_operation("operation-1"))

            assert db.session.get(AgentOperation, operation_id) is not None
            delivery = NotificationDelivery.query.one()
            assert delivery.attempts == 1
            assert delivery.sent_at is None
            assert delivery.last_error == "Notification delivery failed (RuntimeError)"
            assert "secret@example.test" not in delivery.last_error

            monkeypatch.setattr(agent_operations, "Apprise", SuccessfulApprise)
            AgentRequestService(agent).operation(_operation("operation-2", "sync"))

            db.session.refresh(delivery)
            assert delivery.attempts == 2
            assert delivery.sent_at is not None
            assert delivery.last_error is None
            assert SuccessfulApprise.notifications
            assert "Backup success" in SuccessfulApprise.notifications[0]["title"]
        finally:
            db.session.remove()
            db.drop_all()


def test_repeated_operation_state_creates_only_one_delivery(monkeypatch):
    app = _build_app()
    monkeypatch.setattr(agent_operations, "emit_operation_update", lambda operation: None)

    class SuccessfulApprise:
        def add(self, url):
            return True

        def notify(self, **message):
            return True

    monkeypatch.setattr(agent_operations, "Apprise", SuccessfulApprise)

    with app.app_context():
        db.create_all()
        try:
            user = User(name="admin")
            user.set_initial_password("password")
            agent = Agent(user=user, secret="agent-secret")
            config = NotificationConfig(
                user=user,
                operation_types=["backup"],
                operation_states=["success"],
            )
            config.url = "mailto://user@example.test"
            db.session.add_all([user, agent, config])
            db.session.commit()

            report = _operation("operation-1")
            AgentRequestService(agent).operation(report)
            AgentRequestService(agent).operation(report)

            assert NotificationDelivery.query.count() == 1
            assert NotificationDelivery.query.one().attempts == 1
        finally:
            db.session.remove()
            db.drop_all()


def test_terminal_replay_restores_missing_delivery(monkeypatch):
    app = _build_app()
    monkeypatch.setattr(agent_operations, "emit_operation_update", lambda operation: None)

    with app.app_context():
        db.create_all()
        try:
            user = User(name="admin")
            user.set_initial_password("password")
            agent = Agent(user=user, secret="agent-secret")
            config = NotificationConfig(
                user=user,
                operation_types=["backup"],
                operation_states=["success"],
            )
            config.url = "mailto://user@example.test"
            operation = AgentOperation(
                uuid="operation-without-delivery",
                agent=agent,
                type=AgentOperationType.backup,
                state=AgentOperationState.success,
                source=AgentOperationSource.manual,
                data={},
            )
            db.session.add_all([user, agent, config, operation])
            db.session.commit()

            attempted_delivery_ids = []
            monkeypatch.setattr(
                agent_operations.AgentOperationService,
                "_deliver_notification",
                staticmethod(attempted_delivery_ids.append),
            )

            operation_id = AgentRequestService(agent).operation(
                _operation("operation-without-delivery")
            )

            delivery = NotificationDelivery.query.one()
            assert delivery.operation_id == operation_id
            assert attempted_delivery_ids == [delivery.id]
        finally:
            db.session.remove()
            db.drop_all()


def test_delivery_and_operation_changes_roll_back_together(monkeypatch):
    app = _build_app()
    monkeypatch.setattr(agent_operations, "emit_operation_update", lambda operation: None)

    with app.app_context():
        db.create_all()
        try:
            user = User(name="admin")
            user.set_initial_password("password")
            agent = Agent(user=user, secret="agent-secret")
            config = NotificationConfig(
                user=user,
                operation_types=["backup"],
                operation_states=["success"],
            )
            config.url = "mailto://user@example.test"
            db.session.add_all([user, agent, config])
            db.session.commit()

            queue_deliveries = agent_operations.AgentOperationService._queue_notification_deliveries

            def fail_before_commit(operation, *, new_operation):
                delivery_ids = queue_deliveries(operation, new_operation=new_operation)
                assert delivery_ids
                raise RuntimeError("fail before ingest commit")

            monkeypatch.setattr(
                agent_operations.AgentOperationService,
                "_queue_notification_deliveries",
                staticmethod(fail_before_commit),
            )
            report = _operation("atomic-operation")
            report["logs"] = [{"sequence": 1, "level": "info", "message": "complete"}]
            report["artifacts"] = [
                {
                    "uuid": "atomic-artifact",
                    "artifact_key": "default",
                    "state": "success",
                }
            ]

            try:
                AgentRequestService(agent).operation(report)
            except RuntimeError as exc:
                assert str(exc) == "fail before ingest commit"
            else:
                raise AssertionError("ingest unexpectedly succeeded")
            db.session.rollback()

            assert AgentOperation.query.filter_by(uuid="atomic-operation").first() is None
            assert AgentOperationLog.query.count() == 0
            assert AgentOperationArtifact.query.count() == 0
            assert NotificationDelivery.query.count() == 0
        finally:
            db.session.remove()
            db.drop_all()


def test_emit_failure_does_not_fail_ingest(monkeypatch):
    app = _build_app()

    def fail_emit(operation):
        raise RuntimeError(f"could not emit {operation.uuid}")

    monkeypatch.setattr(agent_operations, "emit_operation_update", fail_emit)

    with app.app_context():
        db.create_all()
        try:
            user = User(name="admin")
            user.set_initial_password("password")
            agent = Agent(user=user, secret="agent-secret")
            db.session.add_all([user, agent])
            db.session.commit()

            operation_id = AgentRequestService(agent).operation(_operation("emit-failure"))

            assert db.session.get(AgentOperation, operation_id).uuid == "emit-failure"
        finally:
            db.session.remove()
            db.drop_all()


def test_ingest_retries_only_limited_old_deliveries_after_current_delivery(monkeypatch):
    app = _build_app()
    monkeypatch.setattr(agent_operations, "emit_operation_update", lambda operation: None)

    with app.app_context():
        db.create_all()
        try:
            user = User(name="admin")
            user.set_initial_password("password")
            agent = Agent(user=user, secret="agent-secret")
            config = NotificationConfig(
                user=user,
                operation_types=["backup"],
                operation_states=["success"],
            )
            config.url = "mailto://user@example.test"
            db.session.add_all([user, agent, config])
            db.session.flush()

            old_deliveries = []
            for index in range(INGEST_NOTIFICATION_RETRY_LIMIT + 2):
                operation = AgentOperation(
                    uuid=f"old-operation-{index}",
                    agent=agent,
                    type=AgentOperationType.backup,
                    state=AgentOperationState.success,
                    source=AgentOperationSource.manual,
                    data={},
                )
                delivery = NotificationDelivery(
                    operation=operation,
                    state="success",
                    notification_config=config,
                )
                db.session.add_all([operation, delivery])
                old_deliveries.append(delivery)
            db.session.commit()
            old_delivery_ids = [delivery.id for delivery in old_deliveries]

            attempted_delivery_ids = []
            monkeypatch.setattr(
                agent_operations.AgentOperationService,
                "_deliver_notification",
                staticmethod(attempted_delivery_ids.append),
            )

            operation_id = AgentRequestService(agent).operation(_operation("current-operation"))
            current_delivery = NotificationDelivery.query.filter_by(operation_id=operation_id).one()

            assert attempted_delivery_ids == [
                current_delivery.id,
                *old_delivery_ids[:INGEST_NOTIFICATION_RETRY_LIMIT],
            ]
        finally:
            db.session.remove()
            db.drop_all()


def test_startup_maintenance_retries_open_notification_delivery(monkeypatch):
    app = _build_app()
    register_cli(app)

    class SuccessfulApprise:
        def add(self, url):
            return True

        def notify(self, **message):
            return True

    monkeypatch.setattr(agent_operations, "Apprise", SuccessfulApprise)
    monkeypatch.setattr("drastic_server.cli.reconcile_native_repository_quarantine", lambda: (0, 0))

    with app.app_context():
        db.create_all()
        try:
            user = User(name="admin")
            user.set_initial_password("password")
            agent = Agent(user=user, secret="agent-secret")
            config = NotificationConfig(
                user=user,
                operation_types=["backup"],
                operation_states=["success"],
            )
            config.url = "mailto://user@example.test"
            operation = AgentOperation(
                uuid="startup-operation",
                agent=agent,
                type=AgentOperationType.backup,
                state=AgentOperationState.success,
                source=AgentOperationSource.manual,
                data={},
            )
            delivery = NotificationDelivery(
                operation=operation,
                state="success",
                notification_config=config,
            )
            db.session.add_all([user, agent, config, operation, delivery])
            db.session.commit()
            delivery_id = delivery.id

            result = app.test_cli_runner().invoke(args=["maintenance", "startup"])

            assert result.exit_code == 0
            db.session.expire_all()
            delivery = db.session.get(NotificationDelivery, delivery_id)
            assert delivery.attempts == 1
            assert delivery.sent_at is not None
            assert "1 notification deliveries retried" in result.output
        finally:
            db.session.remove()
            db.drop_all()
