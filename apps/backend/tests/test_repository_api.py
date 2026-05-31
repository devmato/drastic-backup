from drastic_server import models as _models  # noqa: F401
from drastic_server.app import create_app
from drastic_server.extensions import db
from drastic_server.models.repository import Repository
from drastic_server.models.user import User


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
