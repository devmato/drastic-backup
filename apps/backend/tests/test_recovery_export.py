import json
import re
from io import BytesIO
from zipfile import ZipFile

import pytest

from drastic_common.agent.enums import (
    AgentJobActionModule,
    AgentJobType,
    AgentOperationSource,
    AgentOperationState,
    AgentOperationType,
)
from drastic_server import models as _models  # noqa: F401
from drastic_server.app import create_app
from drastic_server.extensions import db
from drastic_server.models.agent import Agent, AgentOperation, AgentOperationLog
from drastic_server.models.chain import BackupChain
from drastic_server.models.job import Job, JobAction, JobSchedule
from drastic_server.models.notification import NotificationConfig
from drastic_server.models.repository import Repository
from drastic_server.models.retention import Retention
from drastic_server.models.user import User
from drastic_server.services.repository import store_recovery_key

ACCOUNT_PASSWORD = "account-password"
RECOVERY_KEY = "user-recovery-key-DO-NOT-EXPORT"


@pytest.fixture
def app(monkeypatch):
    monkeypatch.setenv("DRASTIC_ENV", "test")
    monkeypatch.setenv("DRASTIC_APP_MASTER_SECRET", "test-master-secret-DO-NOT-EXPORT")
    monkeypatch.setenv("DRASTIC_SQLALCHEMY_DATABASE_URI", "sqlite:///:memory:")
    monkeypatch.setenv("DRASTIC_SQLALCHEMY_TRACK_MODIFICATIONS", "false")
    monkeypatch.setenv("DRASTIC_JWT_COOKIE_CSRF_PROTECT", "false")
    application = create_app()
    application.config["TESTING"] = True
    with application.app_context():
        db.create_all()
        yield application
        db.session.remove()
        db.drop_all()


def _create_user(name="owner<script>"):
    user = User(name=name, email="owner@example.test")
    user.set_initial_password(ACCOUNT_PASSWORD, recovery_key=RECOVERY_KEY)
    db.session.add(user)
    db.session.flush()
    return user


def _create_repository(user, *, name="Repo <script>alert(1)</script>"):
    repository = Repository(
        user=user,
        user_id=user.id,
        name=name,
        kind=Repository.KIND_CUSTOM,
        location="s3:https://storage.example.test/bucket<&>",
        restic_id="restic-reference",
    )
    repository.environment = {"AWS_SECRET_ACCESS_KEY": "provider-secret<&>"}
    store_recovery_key(repository, "repository-password<&>", RECOVERY_KEY)
    db.session.add(repository)
    db.session.flush()
    return repository


def _login(client, username="owner<script>"):
    response = client.post(
        "/api/auth/login",
        json={"username": username, "password": ACCOUNT_PASSWORD},
    )
    assert response.status_code == 200


def test_recovery_export_zip_contains_scoped_escaped_reconstruction_data(app):
    with app.app_context():
        user = _create_user()
        repository = _create_repository(user)
        agent = Agent(
            user=user,
            secret="agent-secret-DO-NOT-EXPORT",
            public_key="agent-encryption-key-DO-NOT-EXPORT",
            ssh_public_key="ssh-ed25519 included-public-key",
            ssh_key_fingerprint="SHA256:included-fingerprint",
            ssh_key_algorithm="ssh-ed25519",
            hostname="agent <primary>",
            install_type="release",
            os="Linux",
            version="1.2.3",
        )
        agent.repositories.append(repository)
        retention = Retention(user=user, name="Daily", keep_last=5, keep_weekly=2)
        job = Job(agent=agent, name="Files <nightly>", type=AgentJobType.file)
        job.config = {"paths": [{"path": "/srv/<important>", "group": "folder"}]}
        action = JobAction(
            job=job,
            module=AgentJobActionModule.command,
            hook="start",
            data={"command": "echo <unsafe>"},
        )
        schedule = JobSchedule(
            job=job,
            repository=repository,
            retention=retention,
            enabled=True,
            advanced=True,
            cron_string="15 2 * * 1",
        )
        schedule.config = {"repository_check": {"enabled": True, "read_data": "10%"}}
        notification = NotificationConfig(user=user)
        notification.url = "mailto://notification-secret-DO-NOT-EXPORT@example.test"
        operation = AgentOperation(
            agent=agent,
            job=job,
            repository=repository,
            state=AgentOperationState.failed,
            type=AgentOperationType.backup,
            source=AgentOperationSource.manual,
            data={"history-secret": "operation-data-DO-NOT-EXPORT"},
        )
        operation_log = AgentOperationLog(
            operation=operation,
            sequence=1,
            level="error",
            message="operation-log-DO-NOT-EXPORT",
        )
        db.session.add_all(
            [agent, retention, job, action, schedule, notification, operation, operation_log]
        )
        second_job = Job(agent=agent, name="Second backup", type=AgentJobType.file, config={})
        db.session.add(second_job)
        db.session.flush()
        chain_schedules = [
            {"enabled": True, "timing": {"type": "periodic", "interval": 90, "offset": 5}, "cron_string": ""},
            {"enabled": False, "cron_string": "0 4 * * 0,6"},
        ]
        chain_steps = [
            {"job_id": second_job.id, "repository_id": repository.id, "retention_id": retention.id,
             "config": {"repository_check": {"enabled": True, "read_data": "5%"}}},
            {"job_id": job.id, "repository_id": repository.id, "retention_id": None, "config": {}},
        ]
        db.session.add(BackupChain(user_id=user.id, name="Chain <script>alert(2)</script>",
                                   schedules=chain_schedules, steps=chain_steps, start_timeout_minutes=47))

        other_user = User(name="other-user", email="other-user-DO-NOT-EXPORT@example.test")
        other_user.set_initial_password("other-password", recovery_key="other-recovery-key")
        db.session.add(other_user)
        db.session.flush()
        _create_repository(other_user, name="other-repository-DO-NOT-EXPORT")
        db.session.add(BackupChain(user_id=other_user.id, name="other-chain-DO-NOT-EXPORT",
                                   schedules=[], steps=[], start_timeout_minutes=60))
        db.session.commit()

        client = app.test_client()
        _login(client)
        response = client.post("/api/user/recovery-export", json={"password": ACCOUNT_PASSWORD})

        assert response.status_code == 200
        assert response.content_type == "application/zip"
        assert response.headers["Cache-Control"] == "no-store"
        assert response.headers["Content-Disposition"].startswith(
            "attachment; filename=drastic-recovery-"
        )
        assert response.headers["Content-Disposition"].endswith(".zip")

        with ZipFile(BytesIO(response.data)) as archive:
            assert archive.namelist() == ["recovery.html"]
            html = archive.read("recovery.html").decode()

        assert "contains plaintext repository passwords" in html
        assert "repository-password&lt;&amp;&gt;" in html
        assert "provider-secret" in html
        assert "agent &lt;primary&gt;" in html
        assert f"drastic-{agent.uuid}" in html
        assert "Repo &lt;script&gt;alert(1)&lt;/script&gt;" in html
        assert "ssh-ed25519 included-public-key" in html
        assert "Files &lt;nightly&gt;" in html
        assert "15 2 * * 1" in html
        chains_html = html.split("<h2>Backup chains</h2>", 1)[1]
        assert "Chain &lt;script&gt;alert(2)&lt;/script&gt;" in chains_html
        assert "47 minutes" in chains_html and "UTC" in chains_html
        schedules_json, steps_json = re.findall(r"<pre>(.*?)</pre>", chains_html, re.DOTALL)
        assert json.loads(schedules_json) == chain_schedules
        assert json.loads(steps_json) == chain_steps
        assert "<script>alert(2)</script>" not in html
        assert "other-chain-DO-NOT-EXPORT" not in html
        assert "Daily" in html
        assert "SSH/SFTP target access and private agent SSH material are not included" in html
        assert "Proxmox credentials and host prerequisites are not included" in html
        assert "Native repository storage must be separately backed up" in html
        assert "<script>alert(1)</script>" not in html
        assert ACCOUNT_PASSWORD not in html
        assert RECOVERY_KEY not in html
        assert "agent-secret-DO-NOT-EXPORT" not in html
        assert "agent-encryption-key-DO-NOT-EXPORT" not in html
        assert "notification-secret-DO-NOT-EXPORT" not in html
        assert "operation-data-DO-NOT-EXPORT" not in html
        assert "operation-log-DO-NOT-EXPORT" not in html
        assert "other-user-DO-NOT-EXPORT" not in html
        assert "other-repository-DO-NOT-EXPORT" not in html
        assert "test-master-secret-DO-NOT-EXPORT" not in html


def test_recovery_export_rejects_wrong_password(app):
    with app.app_context():
        _create_user()
        db.session.commit()
        client = app.test_client()
        _login(client)

        response = client.post("/api/user/recovery-export", json={"password": "wrong"})

        assert response.status_code == 401
        assert response.json["message"] == "Wrong password"


def test_recovery_export_requires_jwt(app):
    response = app.test_client().post(
        "/api/user/recovery-export", json={"password": ACCOUNT_PASSWORD}
    )

    assert response.status_code == 401
    assert response.json["message"] == "Authentication required"


@pytest.mark.parametrize(
    "corruption", ["missing_password", "corrupt_password", "corrupt_environment"]
)
def test_recovery_export_is_all_or_nothing_when_repository_secrets_are_invalid(app, corruption):
    with app.app_context():
        user = _create_user()
        repository = _create_repository(user)
        db.session.commit()

        if corruption == "missing_password":
            repository.password_secret = None
        elif corruption == "corrupt_password":
            repository.password_secret.encrypted_value = {"v": 999}
        else:
            repository._environment = {"v": 999}
        db.session.commit()

        client = app.test_client()
        _login(client)
        response = client.post("/api/user/recovery-export", json={"password": ACCOUNT_PASSWORD})

        assert response.status_code == 422
        assert response.json["message"] == "Recovery export could not decrypt all required data"
        assert not response.data.startswith(b"PK")
