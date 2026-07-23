import json
from pathlib import Path
from uuid import uuid4

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
)
from drastic_server.models.repository import Repository
from drastic_server.models.user import User
from drastic_server.services.repository import (
    quarantine_native_repository,
    reconcile_native_repository_quarantine,
)
from drastic_server.views.api import repositories as repository_views


def _build_app(monkeypatch, data_directory, database_uri="sqlite:///:memory:"):
    monkeypatch.setenv("DRASTIC_ENV", "test")
    monkeypatch.setenv("DRASTIC_APP_MASTER_SECRET", "test-master-secret")
    monkeypatch.setenv("DRASTIC_SQLALCHEMY_DATABASE_URI", database_uri)
    monkeypatch.setenv("DRASTIC_SQLALCHEMY_TRACK_MODIFICATIONS", "false")
    monkeypatch.setenv("DRASTIC_JWT_COOKIE_CSRF_PROTECT", "false")
    app = create_app()
    app.config.update(TESTING=True, REST_SERVER_DATA_DIRECTORY=str(data_directory))
    return app


def _login(client):
    response = client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "account-password"},
    )
    assert response.status_code == 200


def _create_repository(storage_root):
    user = User(name="admin")
    user.set_initial_password("account-password", recovery_key="recovery-key")
    repository = Repository(
        user=user,
        name="Native",
        kind=Repository.KIND_NATIVE,
        location="native/repository-id",
    )
    db.session.add_all([user, repository])
    db.session.commit()
    repository_path = storage_root / "native" / "repository-id"
    repository_path.mkdir(parents=True)
    (repository_path / "config").write_text("repository data", encoding="utf-8")
    return repository, repository_path


def test_native_repository_is_deleted_after_database_commit(monkeypatch, tmp_path):
    app = _build_app(monkeypatch, tmp_path)
    with app.app_context():
        db.create_all()
        try:
            repository, repository_path = _create_repository(tmp_path)
            client = app.test_client()
            _login(client)

            response = client.delete(f"/api/repositories/{repository.id}")

            assert response.status_code == 200
            assert db.session.get(Repository, repository.id) is None
            assert not repository_path.exists()
            assert not any((tmp_path / ".quarantine").iterdir())
        finally:
            db.session.remove()
            db.drop_all()


def test_native_repository_with_running_operation_is_not_quarantined(monkeypatch, tmp_path):
    app = _build_app(monkeypatch, tmp_path)
    with app.app_context():
        db.create_all()
        try:
            repository, repository_path = _create_repository(tmp_path)
            agent = Agent(user=repository.user, secret="agent-secret")
            operation = AgentOperation(
                uuid="running-repository-operation",
                agent=agent,
                repository=repository,
                type=AgentOperationType.repository_check,
                state=AgentOperationState.running,
                source=AgentOperationSource.manual,
            )
            db.session.add_all([agent, operation])
            db.session.commit()
            monkeypatch.setattr(
                repository_views,
                "quarantine_native_repository",
                lambda _path: pytest.fail("repository must not be quarantined"),
            )
            client = app.test_client()
            _login(client)

            response = client.delete(f"/api/repositories/{repository.id}")

            assert response.status_code == 409
            assert "running operation" in response.json["message"]
            assert db.session.get(Repository, repository.id) is not None
            assert repository_path.is_dir()
        finally:
            db.session.remove()
            db.drop_all()


def test_quarantine_manifest_durably_records_original_path(monkeypatch, tmp_path):
    app = _build_app(monkeypatch, tmp_path)
    with app.app_context():
        repository_path = tmp_path / "native" / "repository-id"
        repository_path.mkdir(parents=True)
        (repository_path / "config").write_text("repository data", encoding="utf-8")

        quarantine = quarantine_native_repository("native/repository-id")

        assert quarantine is not None
        assert json.loads((quarantine.entry_path / "manifest.json").read_text(encoding="utf-8")) == {
            "original_path": "native/repository-id",
            "version": 1,
        }
        assert quarantine.quarantined_path.is_dir()
        assert not repository_path.exists()


def test_database_failure_restores_repository_from_quarantine(monkeypatch, tmp_path):
    app = _build_app(monkeypatch, tmp_path)
    with app.app_context():
        db.create_all()
        try:
            repository, repository_path = _create_repository(tmp_path)
            repository_id = repository.id
            client = app.test_client()
            _login(client)

            def fail_commit():
                raise RuntimeError("database unavailable")

            monkeypatch.setattr(db.session, "commit", fail_commit)
            response = client.delete(f"/api/repositories/{repository_id}")

            assert response.status_code == 409
            assert db.session.get(Repository, repository_id) is not None
            assert repository_path.is_dir()
            assert (repository_path / "config").read_text(encoding="utf-8") == "repository data"
            assert not any((tmp_path / ".quarantine").iterdir())
        finally:
            db.session.remove()
            db.drop_all()


@pytest.mark.parametrize(
    "repository_path", ["../outside", "/absolute/path", "custom/path", "native"]
)
def test_quarantine_rejects_unsafe_repository_paths(monkeypatch, tmp_path, repository_path):
    app = _build_app(monkeypatch, tmp_path)
    outside = Path(tmp_path).parent / "outside"
    outside.mkdir(exist_ok=True)

    with app.app_context(), pytest.raises(RuntimeError, match="Invalid native repository path"):
        quarantine_native_repository(repository_path)

    assert outside.is_dir()


def test_quarantine_rejects_repository_symlink(monkeypatch, tmp_path):
    app = _build_app(monkeypatch, tmp_path)
    outside = Path(tmp_path).parent / "outside-repository"
    outside.mkdir(exist_ok=True)
    native_root = tmp_path / "native"
    native_root.mkdir()
    (native_root / "repository-link").symlink_to(outside, target_is_directory=True)

    with app.app_context(), pytest.raises(RuntimeError, match="Invalid native repository path"):
        quarantine_native_repository("native/repository-link")

    assert outside.is_dir()


def test_quarantine_preserves_locked_repository(monkeypatch, tmp_path):
    app = _build_app(monkeypatch, tmp_path)
    repository_path = tmp_path / "native" / "repository-id"
    locks_path = repository_path / "locks"
    locks_path.mkdir(parents=True)
    (locks_path / "active-lock").write_text("lock", encoding="utf-8")

    with app.app_context(), pytest.raises(RuntimeError, match="currently locked or in use"):
        quarantine_native_repository("native/repository-id")

    assert repository_path.is_dir()
    assert not (tmp_path / ".quarantine").exists()


def test_reconcile_restores_after_crash_between_rename_and_database_commit(monkeypatch, tmp_path):
    database_path = tmp_path / "backend.sqlite"
    database_uri = f"sqlite:///{database_path}"
    app = _build_app(monkeypatch, tmp_path, database_uri)
    with app.app_context():
        db.create_all()
        repository, repository_path = _create_repository(tmp_path)
        repository_id = repository.id

    class SimulatedCrash(BaseException):
        pass

    def crash_before_commit():
        raise SimulatedCrash

    client = app.test_client()
    _login(client)
    monkeypatch.setattr(db.session, "commit", crash_before_commit)
    with pytest.raises(SimulatedCrash):
        client.delete(f"/api/repositories/{repository_id}")

    assert not repository_path.exists()
    assert len(list((tmp_path / ".quarantine").iterdir())) == 1

    restarted_app = _build_app(monkeypatch, tmp_path, database_uri)
    with restarted_app.app_context():
        assert reconcile_native_repository_quarantine() == (1, 0)
        assert db.session.get(Repository, repository_id) is not None

    assert repository_path.is_dir()
    assert (repository_path / "config").read_text(encoding="utf-8") == "repository data"
    assert not any((tmp_path / ".quarantine").iterdir())


def test_reconcile_purges_after_crash_between_database_commit_and_purge(monkeypatch, tmp_path):
    database_path = tmp_path / "backend.sqlite"
    database_uri = f"sqlite:///{database_path}"
    app = _build_app(monkeypatch, tmp_path, database_uri)
    with app.app_context():
        db.create_all()
        repository, repository_path = _create_repository(tmp_path)
        repository_id = repository.id

    class SimulatedCrash(BaseException):
        pass

    def crash_before_purge(_quarantine):
        raise SimulatedCrash

    monkeypatch.setattr(
        repository_views, "delete_quarantined_native_repository", crash_before_purge
    )
    client = app.test_client()
    _login(client)
    with pytest.raises(SimulatedCrash):
        client.delete(f"/api/repositories/{repository_id}")

    assert not repository_path.exists()
    assert len(list((tmp_path / ".quarantine").iterdir())) == 1

    restarted_app = _build_app(monkeypatch, tmp_path, database_uri)
    with restarted_app.app_context():
        assert db.session.get(Repository, repository_id) is None
        assert reconcile_native_repository_quarantine() == (0, 1)

    assert not any((tmp_path / ".quarantine").iterdir())


def test_reconcile_leaves_unsafe_manifest_untouched(monkeypatch, tmp_path):
    app = _build_app(monkeypatch, tmp_path)
    outside = tmp_path.parent / "outside-quarantine-target"
    outside.mkdir(exist_ok=True)
    entry_path = tmp_path / ".quarantine" / uuid4().hex
    entry_path.mkdir(parents=True)
    (entry_path / "manifest.json").write_text(
        json.dumps({"version": 1, "original_path": "../outside-quarantine-target"}),
        encoding="utf-8",
    )
    (entry_path / "repository").mkdir()

    with app.app_context():
        db.create_all()
        assert reconcile_native_repository_quarantine() == (0, 0)

    assert entry_path.is_dir()
    assert outside.is_dir()


def test_app_factory_does_not_access_missing_database_tables(monkeypatch, tmp_path):
    database_path = tmp_path / "missing-tables.sqlite"

    _build_app(monkeypatch, tmp_path, f"sqlite:///{database_path}")

    assert not database_path.exists()
