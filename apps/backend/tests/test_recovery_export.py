import bz2
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
from html import unescape
from io import BytesIO
from zipfile import ZipFile as PlainZipFile

import pytest
import pyzipper
import requests
from urllib3.exceptions import ProtocolError

import drastic_common.restic.binary as binary_download
import drastic_server.services.recovery_export as recovery_export
from drastic_common.agent.enums import (
    AgentJobActionModule,
    AgentJobType,
    AgentOperationSource,
    AgentOperationState,
    AgentOperationType,
)
from drastic_common.restic import RESTIC_VERSION
from drastic_common.restic.client import ResticApi
from drastic_common.restic.repository import ResticRepository
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
from drastic_server.services.recovery_export import _repository_setup
from drastic_server.services.repository import store_recovery_key

ACCOUNT_PASSWORD = "account-password"
RECOVERY_KEY = "user-recovery-key-DO-NOT-EXPORT"
ARCHIVE_PASSWORD = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdef"
EXPORT_REQUEST = {"password": ACCOUNT_PASSWORD, "archive_password": ARCHIVE_PASSWORD}


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


def test_recovery_export_zip_contains_scoped_escaped_reconstruction_data(app, monkeypatch):
    monkeypatch.setattr(requests, "get", lambda *args, **kwargs: pytest.fail("No platforms selected; no download needed"))
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
        retention = Retention(user=user, name="Daily", rules=[{"type": "last", "count": 5}, {"type": "last", "period": "week", "count": 2}])
        job = Job(agent=agent, name="Files <nightly>", type=AgentJobType.file, retention=retention, retention_mode="custom")
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
                                   schedules=chain_schedules, steps=chain_steps, start_timeout_minutes=47,
                                   retry={"enabled": True, "max_attempts": 3, "delay_minutes": 10}))

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
        response = client.post("/api/user/recovery-export", json=EXPORT_REQUEST)

        assert response.status_code == 200
        assert response.content_type == "application/zip"
        assert response.headers["Cache-Control"] == "no-store"
        assert response.headers["Content-Disposition"].startswith(
            "attachment; filename=drastic-recovery-"
        )
        assert response.headers["Content-Disposition"].endswith(".zip")

        assert b"repository-password" not in response.data
        assert b"provider-secret" not in response.data
        assert ARCHIVE_PASSWORD.encode() not in response.data
        with pyzipper.AESZipFile(BytesIO(response.data)) as archive:
            assert archive.namelist() == [f"repositories/{repository.id}/password.txt", "recovery.html"]
            assert all(info.flag_bits & 1 and info.wz_aes_strength == 3 for info in archive.infolist())
            with pytest.raises(RuntimeError):
                archive.read("recovery.html")
            archive.setpassword(b"wrong-password")
            with pytest.raises(RuntimeError):
                archive.read("recovery.html")
            archive.setpassword(ARCHIVE_PASSWORD.encode())
            assert archive.read(f"repositories/{repository.id}/password.txt") == b"repository-password<&>"
            html = archive.read("recovery.html").decode()

        assert "contains plaintext repository passwords" in html
        assert "repository-password&lt;&amp;&gt;" in html
        assert "provider-secret" in html
        assert "agent &lt;primary&gt;" in html
        assert f"drastic-{agent.uuid}" in html
        assert user.backup_owner_uuid in html
        assert "Repo &lt;script&gt;alert(1)&lt;/script&gt;" in html
        assert "ssh-ed25519 included-public-key" in html
        assert "Files &lt;nightly&gt;" in html
        assert "15 2 * * 1" in html
        chains_html = html.split("<h2>Backup chains</h2>", 1)[1]
        assert "Chain &lt;script&gt;alert(2)&lt;/script&gt;" in chains_html
        assert "47 minutes" in chains_html and "UTC" in chains_html
        assert "3 total attempts, 10 minutes apart" in chains_html
        schedules_json, steps_json = re.findall(r"<pre>(.*?)</pre>", chains_html, re.DOTALL)
        assert json.loads(schedules_json) == chain_schedules
        assert json.loads(steps_json) == chain_steps
        assert "<script>alert(2)</script>" not in html
        assert "other-chain-DO-NOT-EXPORT" not in html
        assert "Daily" in html
        retention_html = html.split("<h2>Retention policies</h2>", 1)[1].split("</section>", 1)[0]
        assert [json.loads(value) for value in re.findall(r"<li><code>(.*?)</code></li>", retention_html)] == retention.rules
        assert "account time zone UTC" in retention_html
        assert "SSH/SFTP target access and private agent SSH material are not included" in html
        assert "Proxmox credentials and host prerequisites are not included" in html
        assert "Native repository storage must be separately backed up" in html
        assert "<script>alert(1)</script>" not in html
        assert ACCOUNT_PASSWORD not in html
        assert ARCHIVE_PASSWORD not in html
        assert RECOVERY_KEY not in html
        assert "agent-secret-DO-NOT-EXPORT" not in html
        assert "agent-encryption-key-DO-NOT-EXPORT" not in html
        assert "notification-secret-DO-NOT-EXPORT" not in html
        assert "operation-data-DO-NOT-EXPORT" not in html
        assert "operation-log-DO-NOT-EXPORT" not in html
        assert "other-user-DO-NOT-EXPORT" not in html
        assert "other-repository-DO-NOT-EXPORT" not in html
        assert "test-master-secret-DO-NOT-EXPORT" not in html
        assert "Start recovery without dRastic" in html
        assert "Show all restore points" in html
        assert "not complete disk images" in html
        assert "No Restic archives selected" in html
        assert f"repositories/{repository.id}/password.txt" in html
        assert "export RESTIC_REPOSITORY='s3:https://storage.example.test/bucket<&>'" in unescape(html)


def test_recovery_export_rejects_wrong_password(app, monkeypatch):
    monkeypatch.setattr(requests, "get", lambda *args, **kwargs: pytest.fail("Authenticate before downloading"))
    with app.app_context():
        _create_user()
        db.session.commit()
        client = app.test_client()
        _login(client)

        response = client.post("/api/user/recovery-export", json={
            **EXPORT_REQUEST, "password": "wrong", "restic_targets": ["linux_amd64"],
        })

        assert response.status_code == 401
        assert response.json["message"] == "Wrong password"


def test_recovery_export_requires_jwt(app):
    response = app.test_client().post(
        "/api/user/recovery-export", json=EXPORT_REQUEST
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
        response = client.post("/api/user/recovery-export", json=EXPORT_REQUEST)

        assert response.status_code == 422
        assert response.json["message"] == "Recovery export could not decrypt all required data"
        assert not response.data.startswith(b"PK")


@pytest.mark.parametrize("invalid", [
    {"archive_password": None}, {"archive_password": "short"},
    {"archive_password": " " * 32}, {"restic_targets": ["../../etc/passwd"]},
    {"restic_targets": ["windows_arm64"]}, {"restic_targets": ["linux_amd64"] * 6},
])
def test_recovery_export_validates_encryption_and_platform_inputs(app, invalid):
    _create_user()
    db.session.commit()
    client = app.test_client()
    _login(client)
    response = client.post("/api/user/recovery-export", json={**EXPORT_REQUEST, **invalid})
    assert response.status_code == 422
    assert not response.data.startswith(b"PK")


@pytest.mark.parametrize("target", ["linux_amd64", "darwin_arm64", "windows_amd64"])
def test_recovery_export_includes_unchanged_verified_release_archive(app, monkeypatch, target):
    _create_user()
    db.session.commit()
    binary = b"foreign-platform-executable"
    name = f"restic_{RESTIC_VERSION}_{target}"
    if target.startswith("windows"):
        compressed = BytesIO()
        with PlainZipFile(compressed, "w") as archive:
            archive.writestr(f"{name}.exe", binary)
        compressed = compressed.getvalue()
        extension = "zip"
    else:
        compressed = bz2.compress(binary)
        extension = "bz2"
    calls = []

    def get(url, **kwargs):
        calls.append(url)
        response = requests.Response()
        response.status_code = 200
        response._content = (f"{hashlib.sha256(compressed).hexdigest()}  {name}.{extension}\n".encode()
                             if url.endswith("SHA256SUMS") else compressed)
        response.raw = BytesIO(response._content)
        return response

    monkeypatch.setattr(requests, "get", get)
    client = app.test_client()
    _login(client)
    response = client.post("/api/user/recovery-export", json={**EXPORT_REQUEST, "restic_targets": [target, target]})
    assert response.status_code == 200
    assert len(calls) == 2
    with pyzipper.AESZipFile(BytesIO(response.data)) as archive:
        archive.setpassword(ARCHIVE_PASSWORD.encode())
        filename = f"bin/{name}.{extension}"
        assert archive.read(filename) == compressed
        assert archive.getinfo(filename).compress_type == pyzipper.ZIP_STORED
        assert archive.read("bin/SHA256SUMS").decode() == f"{hashlib.sha256(compressed).hexdigest()}  {name}.{extension}\n"
        assert "BSD 2-Clause License" in archive.read("bin/LICENSE.txt").decode()
        html = archive.read("recovery.html").decode()
        assert filename in html and "Unpack the matching archive" in html
        assert all(info.flag_bits & 1 for info in archive.infolist())


@pytest.mark.parametrize("failure", ["network", "stream", "checksum", "missing_checksum"])
def test_recovery_export_download_failure_never_returns_a_partial_kit(app, monkeypatch, failure):
    _create_user()
    db.session.commit()
    compressed = bz2.compress(b"restic-binary")

    class BrokenStream(BytesIO):
        def read1(self, size):
            raise ProtocolError("Connection reset during download")

    def get(url, **kwargs):
        if failure == "network":
            raise requests.ConnectionError("unavailable")
        response = requests.Response()
        response.status_code = 200
        checksum = "0" * 64 if failure == "checksum" else hashlib.sha256(compressed).hexdigest()
        response._content = (f"{checksum}  restic_{RESTIC_VERSION}_linux_amd64.bz2\n".encode()
                             if url.endswith("SHA256SUMS") else compressed)
        if url.endswith("SHA256SUMS") and failure == "missing_checksum":
            response._content = b""
        response.raw = BrokenStream(response._content) if failure == "stream" else BytesIO(response._content)
        return response

    monkeypatch.setattr(requests, "get", get)
    client = app.test_client()
    _login(client)
    response = client.post("/api/user/recovery-export", json={**EXPORT_REQUEST, "restic_targets": ["linux_amd64"]})
    assert response.status_code == 422
    assert "Could not download and verify Restic for linux_amd64" in response.json["message"]
    assert not response.data.startswith(b"PK")


def test_encrypted_export_opens_with_7zip(app, tmp_path):
    seven_zip = shutil.which("7z")
    if not seven_zip:
        pytest.skip("7-Zip is not installed")
    user = _create_user()
    repository = _create_repository(user)
    db.session.commit()
    client = app.test_client()
    _login(client)
    response = client.post("/api/user/recovery-export", json=EXPORT_REQUEST)
    assert response.status_code == 200
    kit = tmp_path / "kit.zip"
    kit.write_bytes(response.data)
    result = subprocess.run([seven_zip, "x", str(kit), f"-p{ARCHIVE_PASSWORD}", f"-o{tmp_path / 'unpacked'}"],
                            capture_output=True, check=True)
    assert b"Everything is Ok" in result.stdout
    assert (tmp_path / f"unpacked/repositories/{repository.id}/password.txt").read_text() == "repository-password<&>"


@pytest.mark.parametrize("password", ["repository-password<&>", " \tsecret 'quote'\r\n", "\ufeffsecret"])
def test_native_storage_recovery_uses_exported_password_without_drastic(app, tmp_path, password):
    restic = os.environ.get("RESTIC_BINARY") or shutil.which("restic")
    if not restic:
        pytest.skip("Restic is not installed")
    user = _create_user()
    repository = _create_repository(user)
    repository.kind = Repository.KIND_NATIVE
    repository.location = "native/recovery-test"
    store_recovery_key(repository, password, RECOVERY_KEY)
    db.session.commit()
    storage = tmp_path / "recovered-storage" / repository.location
    storage.parent.mkdir(parents=True)
    source = tmp_path / "source"
    source.mkdir()
    (source / "important.txt").write_text("offline recovery works\n")
    env = {key: value for key, value in os.environ.items() if not key.startswith("RESTIC_PASSWORD")}
    env.update(RESTIC_REPOSITORY=str(storage), RESTIC_PASSWORD=password)
    for args in (["init"], ["backup", str(source)]):
        subprocess.run([restic, *args], env=env, capture_output=True, check=True)
    client = app.test_client()
    _login(client)
    response = client.post("/api/user/recovery-export", json=EXPORT_REQUEST)
    assert response.status_code == 200
    with pyzipper.AESZipFile(BytesIO(response.data)) as archive:
        archive.setpassword(ARCHIVE_PASSWORD.encode())
        archive.extractall(tmp_path / "kit")
    html = (tmp_path / "kit/recovery.html").read_text()
    assert "&lt;storage-root&gt;/native/recovery-test" in html
    assert "/path/to/rest-server-storage/native/recovery-test" in html
    snapshots = json.loads(subprocess.check_output([restic, "snapshots", "--json"], env=env))
    target = tmp_path / "restored"
    commands = unescape(re.search(r"<h4>Linux/macOS shell</h4>\s*<pre>(.*?)</pre>", html, re.DOTALL)[1])
    commands = commands.replace("/path/to/rest-server-storage", str(storage.parent.parent))
    commands = commands.replace("SNAPSHOT_ID", snapshots[0]["id"])
    commands = commands.replace(f"/path/to/new-empty-restore-{repository.id}", str(target))
    env.update(RESTIC_PASSWORD="wrong inherited password", RESTIC_PASSWORD_COMMAND="false",
               RESTIC_PASSWORD_FILE="/missing/inherited/password-file")
    env["RESTIC"] = restic
    subprocess.run(["bash", "-eu", "-c", commands], env=env, cwd=tmp_path / "kit",
                   capture_output=True, check=True)
    assert (target / source.relative_to(source.anchor) / "important.txt").read_text() == "offline recovery works\n"


@pytest.mark.parametrize("explicit", [False, True])
def test_recovery_setup_escapes_values_and_preserves_explicit_password_sources(tmp_path, explicit):
    repository = Repository(id=12, kind=Repository.KIND_CUSTOM, location="rest:https://example.test/O’Neil")
    environment = {
        "RESTIC_REST_PASSWORD": "don't ‘a’ ‚b‛ $value `text",
        "RESTIC_PASSWORD_COMMAND": "old-command",
        "RESTIC_PASSWORD_FILE": "old-file",
        "RESTIC_PASSWORD": "old-password",
        "restic_password_command": "old-windows-command",
        "RESTIC_HOST": "wrong-host-filter",
    }
    if not explicit:
        environment = {key: value for key, value in environment.items()
                       if key.upper() not in {"RESTIC_PASSWORD", "RESTIC_PASSWORD_FILE", "RESTIC_PASSWORD_COMMAND"}}
    password = "\ufeff secret\r\n"
    password_file = tmp_path / "repositories/12/password.txt"
    password_file.parent.mkdir(parents=True)
    password_file.write_bytes(password.encode("utf-8"))
    setup = _repository_setup(repository, environment, password, powershell=True)
    assert "-LiteralPath 'Env:RESTIC_REST_PASSWORD' -Value 'don''t ‘‘a’’ ‚‚b‛‛ $value `text'" in setup
    assert "-LiteralPath 'Env:RESTIC_REPOSITORY' -Value 'rest:https://example.test/O’’Neil'" in setup
    assert ("repositories/12/password.txt" in setup) is not explicit
    assert password not in setup
    assert setup.startswith("Remove-Item -LiteralPath 'Env:RESTIC_PASSWORD', 'Env:RESTIC_PASSWORD_FILE', "
                            "'Env:RESTIC_PASSWORD_COMMAND', 'Env:RESTIC_HOST' -ErrorAction SilentlyContinue\n")
    for old_value in ("old-command", "old-file", "old-password"):
        assert (old_value in setup) is explicit
    assert "old-windows-command" not in setup
    assert "wrong-host-filter" not in setup
    shell_setup = _repository_setup(repository, environment, "normal-password", powershell=False)
    result = subprocess.run(["bash", "-eu", "-c", shell_setup + "\nprintenv -0"],
                            env={"PATH": os.defpath}, capture_output=True, check=True)
    exported = dict(item.split(b"=", 1) for item in result.stdout.split(b"\0") if item)
    if explicit:
        assert exported[b"RESTIC_PASSWORD_FILE"] == b"old-file"
        assert exported[b"RESTIC_PASSWORD_COMMAND"] == b"old-command" and exported[b"RESTIC_PASSWORD"] == b"old-password"
        assert exported[b"restic_password_command"] == b"old-windows-command"
    else:
        assert exported[b"RESTIC_PASSWORD_FILE"] == b"repositories/12/password.txt"
        assert b"RESTIC_PASSWORD_COMMAND" not in exported and b"RESTIC_PASSWORD" not in exported
    assert b"RESTIC_HOST" not in exported

    if powershell := shutil.which("pwsh"):
        result = subprocess.run([powershell, "-NoProfile", "-NonInteractive", "-Command",
                                 setup + "\n@($env:RESTIC_REST_PASSWORD, $env:RESTIC_REPOSITORY, "
                                 "$env:RESTIC_PASSWORD, $env:RESTIC_PASSWORD_FILE, "
                                 "$env:RESTIC_PASSWORD_COMMAND) | ConvertTo-Json -Compress"],
                                cwd=tmp_path, capture_output=True, encoding="utf-8", check=True)
        assert json.loads(result.stdout) == [environment["RESTIC_REST_PASSWORD"], repository.location,
                                            *(["old-password", "old-file", "old-command"] if explicit else [password, None, None])]


@pytest.mark.parametrize("source_variable", ["RESTIC_PASSWORD", "RESTIC_PASSWORD_FILE", "RESTIC_PASSWORD_COMMAND"])
def test_custom_recovery_matches_runtime_password_sources_and_removes_host_filters(app, tmp_path, source_variable):
    restic = os.environ.get("RESTIC_BINARY") or shutil.which("restic")
    if not restic:
        pytest.skip("Restic is not installed")
    user = _create_user()
    repository = _create_repository(user)
    repository.location = str(tmp_path / "repo")
    actual_password = "the password actually used by backups"
    password_file = tmp_path / "external-password"
    password_file.write_text(actual_password)
    value = {"RESTIC_PASSWORD": actual_password, "RESTIC_PASSWORD_FILE": str(password_file),
             "RESTIC_PASSWORD_COMMAND": f"printf %s {shlex.quote(actual_password)}"}[source_variable]
    repository.environment = {source_variable: value, "RESTIC_HOST": "unused-runtime-filter"}
    db.session.commit()
    source = tmp_path / "source"
    source.mkdir()
    (source / "data").write_text("recoverable\n")
    api = ResticApi(restic, ResticRepository(repository.location, "repository-password<&>",
        env=repository.environment, backup_host="drastic-test-agent"))
    api.init()
    snapshot_id = api.backup([str(source)])["snapshot_id"]
    client = app.test_client()
    _login(client)
    response = client.post("/api/user/recovery-export", json=EXPORT_REQUEST)
    assert response.status_code == 200
    with pyzipper.AESZipFile(BytesIO(response.data)) as archive:
        archive.setpassword(ARCHIVE_PASSWORD.encode())
        archive.extractall(tmp_path / "kit")
    html = (tmp_path / "kit/recovery.html").read_text()
    commands = unescape(re.search(r"<h4>Linux/macOS shell</h4>\s*<pre>(.*?)</pre>", html, re.DOTALL)[1])
    environment = {**os.environ, "RESTIC": restic, "RESTIC_HOST": "wrong-inherited-filter",
                   "RESTIC_PASSWORD": "wrong", "RESTIC_PASSWORD_FILE": "/missing", "RESTIC_PASSWORD_COMMAND": "false"}
    setup = commands.split('"$RESTIC" snapshots')[0]
    snapshots = json.loads(subprocess.check_output(["bash", "-eu", "-c", setup + '\n"$RESTIC" snapshots --json'],
        env=environment, cwd=tmp_path / "kit"))
    assert [item["id"] for item in snapshots] == [snapshot_id]
    target = tmp_path / "restored"
    commands = commands.replace("SNAPSHOT_ID", snapshot_id).replace(f"/path/to/new-empty-restore-{repository.id}", str(target))
    subprocess.run(["bash", "-eu", "-c", commands], env=environment, cwd=tmp_path / "kit", capture_output=True, check=True)
    assert (target / source.relative_to(source.anchor) / "data").read_text() == "recoverable\n"


@pytest.mark.parametrize("slow_body", [False, True])
def test_recovery_export_has_one_download_budget_and_closes_timed_out_streams(app, monkeypatch, slow_body):
    _create_user()
    db.session.commit()
    clock = [0]
    monkeypatch.setattr(recovery_export, "monotonic", lambda: clock[0])
    monkeypatch.setattr(binary_download, "monotonic", lambda: clock[0])
    compressed = bz2.compress(b"restic-binary")
    streams, calls = [], []
    targets = ["linux_amd64", "linux_arm64", "darwin_amd64", "darwin_arm64", "windows_amd64"]
    checksums = "".join(f"{hashlib.sha256(compressed).hexdigest()}  restic_{RESTIC_VERSION}_{target}."
                        f"{'zip' if target.startswith('windows') else 'bz2'}\n" for target in targets)

    class TimedStream(BytesIO):
        def read1(self, size):
            if self.tell() < len(self.getvalue()):
                clock[0] += 50 if slow_body else 70
            return super().read1(1 if slow_body else size)

    def get(url, **kwargs):
        calls.append(url)
        assert kwargs["stream"] and 0 < kwargs["timeout"] <= 10
        response = requests.Response()
        response.status_code = 200
        # Checksum requests are fast; every platform body still shares the same budget.
        response.raw = BytesIO(checksums.encode()) if url.endswith("SHA256SUMS") else TimedStream(compressed)
        streams.append(response.raw)
        return response

    monkeypatch.setattr(requests, "get", get)
    client = app.test_client()
    _login(client)
    response = client.post("/api/user/recovery-export", json={**EXPORT_REQUEST, "restic_targets": targets})
    assert response.status_code == 422
    assert "select fewer Restic platforms" in response.json["message"]
    assert not response.data.startswith(b"PK") and len(calls) < 10
    assert all(stream.closed for stream in streams)
