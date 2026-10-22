from drastic_server import models as _models  # noqa: F401
from drastic_server.app import create_app
from drastic_server.extensions import db
from drastic_server.models.agent import Agent, AgentSession
from drastic_server.models.repository import Repository
from drastic_server.models.user import User
from drastic_server.services.agent.request import AgentRequestService
from drastic_server.services.repository.management import update_repository


def build_app(monkeypatch):
    monkeypatch.setenv("DRASTIC_ENV", "test")
    monkeypatch.setenv("DRASTIC_APP_MASTER_SECRET", "test-master-secret")
    monkeypatch.setenv("DRASTIC_SQLALCHEMY_DATABASE_URI", "sqlite:///:memory:")
    monkeypatch.setenv("DRASTIC_SQLALCHEMY_TRACK_MODIFICATIONS", "false")
    monkeypatch.setenv("DRASTIC_JWT_COOKIE_CSRF_PROTECT", "false")
    app = create_app()
    app.config["TESTING"] = True
    return app


def test_repository_update_rejects_password_rotation(monkeypatch):
    app = build_app(monkeypatch)
    with app.app_context():
        db.create_all()
        try:
            user = User(name="admin")
            user.set_initial_password("account-password", recovery_key="user-recovery-key")
            repository = Repository(
                user=user,
                name="Repo",
                kind=Repository.KIND_CUSTOM,
                location="rest:http://repo.test/repo",
            )
            db.session.add_all([user, repository])
            db.session.commit()

            client = app.test_client()
            login_response = client.post(
                "/api/auth/login",
                json={"username": "admin", "password": "account-password"},
            )
            assert login_response.status_code == 200

            response = client.put(
                f"/api/repositories/{repository.id}",
                json={"name": "Repo", "password": "new-repository-password"},
            )

            assert response.status_code == 400
            assert response.json["message"] == "Repository password rotation is not supported yet"
        finally:
            db.session.remove()
            db.drop_all()


def test_repository_update_commits_before_syncing_assigned_online_agents(monkeypatch):
    app = build_app(monkeypatch)
    with app.app_context():
        db.create_all()
        try:
            user = User(name="owner", password="unused", encrypted_recovery_key={})
            repository = Repository(user=user, name="Repo", location="/old", password_secret_id=1)
            online = Agent(user=user, secret="unused", repositories=[repository])
            offline = Agent(user=user, secret="unused", repositories=[repository])
            unrelated = Agent(user=user, secret="unused")
            db.session.add_all([online, offline, unrelated])
            db.session.flush()
            db.session.add_all([AgentSession(agent=online, request_sid="online"), AgentSession(agent=unrelated, request_sid="unrelated")])
            db.session.commit()
            synced = []

            def sync(agent):
                db.session.rollback()
                synced.append((agent.id, AgentRequestService(agent).get_repositories()[0]["location"]))

            monkeypatch.setattr("drastic_server.integrations.agent.AgentService.sync", sync)
            update_repository(user.id, repository.id, {"location": "/new", "environment": {"AWS_PROFILE": "backup"}})
            assert synced == [(online.id, "/new")]
            assert repository.environment == {"AWS_PROFILE": "backup"}
            assert AgentRequestService(offline).get_repositories()[0]["location"] == "/new"
        finally:
            db.session.remove()
            db.drop_all()
