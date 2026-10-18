"""Export account-scoped recovery material and offline instructions in an AES ZIP."""

from __future__ import annotations

import json
import shlex
from collections.abc import Mapping
from datetime import date, datetime
from enum import Enum
from tempfile import SpooledTemporaryFile
from time import monotonic
from typing import Any

import pyzipper
import requests
from flask import current_app, render_template
from urllib3.exceptions import HTTPError

from drastic_common.restic import RESTIC_VERSION
from drastic_common.restic.binary import RELEASE_URL, download_archive
from drastic_common.secret_envelope import decrypt_with_password
from drastic_common.version import get_version
from drastic_server.models.agent import Agent
from drastic_server.models.chain import BackupChain
from drastic_server.models.job import Job
from drastic_server.models.repository import Repository
from drastic_server.models.retention import Retention
from drastic_server.models.user import User
from drastic_server.services.exceptions import InvalidInput
from drastic_server.services.repository import decrypt_recovery_key
from drastic_server.utils.crypto import CryptoError, decrypt


class RecoveryExportPasswordError(ValueError):
    pass


class RecoveryExportError(ValueError):
    pass


def build_recovery_archive(payload, archive_password, restic_targets):
    """Return a seeked encrypted file, owned by send_file; discard it on any failure.

    No database or agent changes are made. Only encrypted ZIP bytes may spill to disk.
    Official release archives are bundled unchanged, without unpacking or recompression.
    """
    archive = SpooledTemporaryFile(max_size=8 * 1024 * 1024, mode="w+b")
    archives = []
    deadline = monotonic() + 240  # Leave room below the documented 300-second proxy timeout.
    payload["restic_version"] = RESTIC_VERSION
    payload["drastic_version"] = get_version()
    payload["restic_release_url"] = RELEASE_URL
    payload["restic_archives"] = archives
    try:
        with pyzipper.AESZipFile(archive, "w", compression=pyzipper.ZIP_DEFLATED,
                                 encryption=pyzipper.WZ_AES) as recovery_zip:
            recovery_zip.setpassword(archive_password.encode("ascii"))
            recovery_zip.setencryption(pyzipper.WZ_AES, nbits=256)
            for target in dict.fromkeys(restic_targets):
                try:
                    filename, content, checksum = download_archive(
                        f"restic_{RESTIC_VERSION}_{target}", get=requests.get, deadline=deadline)
                except (TimeoutError, requests.RequestException, HTTPError, ValueError) as exc:
                    if isinstance(exc, TimeoutError) or monotonic() >= deadline:
                        raise InvalidInput("Recovery export time limit reached; select fewer Restic platforms and retry") from exc
                    raise InvalidInput(f"Could not download and verify Restic for {target}; retry the export") from exc
                recovery_zip.writestr(f"bin/{filename}", content, compress_type=pyzipper.ZIP_STORED)
                archives.append({"filename": filename, "sha256": checksum})
            if archives:
                recovery_zip.writestr("bin/SHA256SUMS", "".join(
                    f"{item['sha256']}  {item['filename']}\n" for item in archives
                ))
                recovery_zip.writestr("bin/LICENSE.txt", render_template("recovery/restic-LICENSE.txt"))
            for repository in payload["repositories"]:
                recovery_zip.writestr(f"repositories/{repository['id']}/password.txt", repository["password"])
            recovery_zip.writestr("recovery.html", render_template("recovery/recovery.html", export=payload))
        archive.seek(0)
        return archive
    except BaseException:
        archive.close()
        raise


def build_recovery_export_payload(user: User, password: str) -> dict[str, Any]:
    try:
        password_matches = user.check_password(password)
    except (TypeError, ValueError) as exc:
        raise RecoveryExportError("The account password could not be verified") from exc
    if not password_matches:
        raise RecoveryExportPasswordError("Wrong password")

    try:
        recovery_key = decrypt_with_password(user.encrypted_recovery_key, password)
    except (AttributeError, TypeError, ValueError) as exc:
        raise RecoveryExportError("The account recovery key could not be decrypted") from exc

    repositories = sorted(
        Repository.query.filter(Repository.user_id == user.id).all(),
        key=lambda item: ((item.name or "").casefold(), item.id),
    )
    repository_payloads = [
        _repository_payload(repository, user.id, recovery_key) for repository in repositories
    ]
    repository_names = {item["id"]: item["name"] for item in repository_payloads}

    agents = sorted(
        Agent.query.filter(Agent.user_id == user.id).all(),
        key=lambda item: (item.display_name.casefold(), item.id),
    )

    retentions = sorted(
        Retention.query.filter(Retention.user_id == user.id).all(),
        key=lambda item: ((item.name or "").casefold(), item.id),
    )
    retention_names = {retention.id: retention.name for retention in retentions}

    jobs = sorted(
        Job.query.join(Agent).filter(Agent.user_id == user.id).all(),
        key=lambda item: ((item.name or "").casefold(), item.id),
    )

    assignments = []
    for agent in agents:
        for repository in sorted(agent.repositories, key=lambda item: item.id):
            if repository.id not in repository_names:
                continue
            assignments.append(
                {
                    "agent": {"id": agent.id, "hostname": agent.hostname, "display_name": agent.display_name},
                    "repository": {
                        "id": repository.id,
                        "name": repository_names[repository.id],
                    },
                }
            )

    return _normalize(
        {
            "generated_at": datetime.now().astimezone(),
            "user": {
                "id": user.id,
                "name": user.name,
                "email": user.email,
                "default_retention_id": user.default_retention_id,
                "backup_owner_uuid": user.backup_owner_uuid,
                "schedule_timezone": user.schedule_timezone,
            },
            "agents": [
                {
                    "id": agent.id,
                    "uuid": agent.uuid,
                    "hostname": agent.hostname,
                    "alias": agent.alias,
                    "display_name": agent.display_name,
                    "install_type": agent.install_type,
                    "os": agent.os,
                    "version": agent.version,
                    "ssh_public_key": agent.ssh_public_key,
                    "ssh_key_fingerprint": agent.ssh_key_fingerprint,
                    "ssh_key_algorithm": agent.ssh_key_algorithm,
                }
                for agent in agents
            ],
            "repository_assignments": assignments,
            "repositories": repository_payloads,
            "retentions": [
                {
                    "id": retention.id,
                    "name": retention.name,
                    "keep_last": retention.keep_last,
                    "keep_hourly": retention.keep_hourly,
                    "keep_weekly": retention.keep_weekly,
                    "keep_monthly": retention.keep_monthly,
                    "keep_yearly": retention.keep_yearly,
                }
                for retention in retentions
            ],
            "jobs": [
                _job_payload(job, repository_names, retention_names) for job in jobs
            ],
            "backup_chains": [
                {"id": chain.id, "name": chain.name, "enabled": chain.enabled,
                   "schedules": chain.schedules, "timezone": user.schedule_timezone,
                 "start_timeout_minutes": chain.start_timeout_minutes, "steps": chain.steps, "retry": chain.retry or {}}
                for chain in BackupChain.query.filter_by(user_id=user.id).order_by(BackupChain.id)
            ],
        }
    )


def _repository_payload(repository: Repository, user_id: int, recovery_key: str) -> dict[str, Any]:
    if repository.password_secret is None or repository.password_secret.user_id != user_id:
        raise RecoveryExportError("A repository password is missing")
    try:
        repository_password = decrypt_recovery_key(repository, recovery_key)
    except (AttributeError, TypeError, ValueError) as exc:
        raise RecoveryExportError("A repository password could not be decrypted") from exc

    environment = {}
    if repository._environment is not None:
        try:
            environment = json.loads(
                decrypt(**repository._environment, key=current_app.config["ENCRYPTION_KEY"])
            )
        except (CryptoError, TypeError, KeyError, json.JSONDecodeError) as exc:
            raise RecoveryExportError("A repository environment could not be decrypted") from exc
        if not isinstance(environment, dict):
            raise RecoveryExportError("A repository environment is invalid")

    return {
        "id": repository.id,
        "name": repository.name,
        "kind": repository.kind,
        "location": repository.location,
        "native_repository_path": repository.repository_path,
        "restic_id": repository.restic_id,
        "password": repository_password,
        "environment": environment,
        "shell_setup": _repository_setup(repository, environment, repository_password, powershell=False),
        "powershell_setup": _repository_setup(repository, environment, repository_password, powershell=True),
    }


def _repository_setup(repository, environment, password, *, powershell):
    location = (f"/path/to/rest-server-storage/{repository.repository_path}"
                if repository.kind == Repository.KIND_NATIVE else repository.location)
    password_variables = ("RESTIC_PASSWORD", "RESTIC_PASSWORD_FILE", "RESTIC_PASSWORD_COMMAND")
    cleared_variables = (*password_variables, "RESTIC_HOST")
    # POSIX agents ignore lowercase aliases; Windows would merge them into the real password sources.
    values = {key: value for key, value in environment.items() if key.upper() != "RESTIC_HOST"
              and (not powershell or key.upper() not in password_variables or key in password_variables)}
    use_exported_password = not any(key in password_variables for key in values)
    values["RESTIC_REPOSITORY"] = location
    password_file = f"repositories/{repository.id}/password.txt"
    # Restic trims password files and strips a UTF-8 BOM, unlike RESTIC_PASSWORD.
    use_password_file = use_exported_password and password == password.strip() and not password.startswith("\ufeff")
    if use_password_file:
        values["RESTIC_PASSWORD_FILE"] = password_file
    if powershell:
        paths = ", ".join(f"'Env:{name}'" for name in cleared_variables)
        lines = [f"Remove-Item -LiteralPath {paths} -ErrorAction SilentlyContinue"]
        # PowerShell also recognizes these typographic apostrophes as string delimiters.
        quotes = str.maketrans({quote: quote * 2 for quote in "'‘’‚‛"})
        for key, value in values.items():
            key, value = str(key).translate(quotes), str(value).translate(quotes)
            lines.append(f"Set-Item -LiteralPath 'Env:{key}' -Value '{value}'")
        if use_exported_password and not use_password_file:
            lines.append("$env:RESTIC_PASSWORD = [Text.Encoding]::UTF8.GetString("
                         f"[IO.File]::ReadAllBytes((Resolve-Path -LiteralPath '{password_file}').Path))")
        return "\n".join(lines)
    lines = ["unset " + " ".join(cleared_variables)]
    lines.extend(f"export {shlex.quote(str(key))}={shlex.quote(str(value))}" for key, value in values.items())
    if use_exported_password and not use_password_file:
        # The sentinel prevents command substitution from dropping trailing newlines.
        lines.extend([f'RESTIC_PASSWORD="$(cat {shlex.quote(password_file)} && printf .)"',
                      'export RESTIC_PASSWORD="${RESTIC_PASSWORD%.}"'])
    return "\n".join(lines)


def _job_payload(
    job: Job,
    repository_names: dict[int, str],
    retention_names: dict[int, str],
) -> dict[str, Any]:
    actions = sorted(job.actions, key=lambda item: item.id)
    schedules = sorted(job.schedules, key=lambda item: item.id)
    return {
        "id": job.id,
        "uuid": job.uuid,
        "name": job.name,
        "agent": {
            "id": job.agent_id,
            "hostname": job.agent.hostname,
            "display_name": job.agent.display_name,
        },
        "type": job.type,
        "config": job.config,
        "retention": {
            "mode": job.retention_mode,
            "id": job.retention_id,
            "effective_id": job.effective_retention_id,
            "name": retention_names.get(job.effective_retention_id),
        },
        "actions": [
            {
                "id": action.id,
                "module": action.module,
                "hook": action.hook,
                "data": action.data,
            }
            for action in actions
        ],
        "schedules": [
            {
                "id": schedule.id,
                "enabled": schedule.enabled,
                "advanced": schedule.advanced,
                "cron_string": schedule.cron_string,
                "config": schedule.config,
                "repository": {
                    "id": schedule.repository_id,
                    "name": repository_names.get(schedule.repository_id),
                },
            }
            for schedule in schedules
        ],
    }


def _normalize(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.name
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, Mapping):
        return {
            str(key): _normalize(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, (list, tuple)):
        return [_normalize(item) for item in value]
    raise RecoveryExportError(f"Unsupported recovery export value type: {type(value).__name__}")
