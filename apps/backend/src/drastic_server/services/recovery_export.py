from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import date, datetime
from enum import Enum
from typing import Any

from flask import current_app

from drastic_common.secret_envelope import decrypt_with_password
from drastic_server.models.agent import Agent
from drastic_server.models.chain import BackupChain
from drastic_server.models.job import Job
from drastic_server.models.repository import Repository
from drastic_server.models.retention import Retention
from drastic_server.models.user import User
from drastic_server.services.repository import decrypt_recovery_key
from drastic_server.utils.crypto import CryptoError, decrypt


class RecoveryExportPasswordError(ValueError):
    pass


class RecoveryExportError(ValueError):
    pass


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
                  "schedules": chain.schedules, "timezone": "UTC",
                 "start_timeout_minutes": chain.start_timeout_minutes, "steps": chain.steps}
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
    }


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
                "retention": (
                    {
                        "id": schedule.retention_id,
                        "name": retention_names.get(schedule.retention_id),
                    }
                    if schedule.retention_id is not None
                    else None
                ),
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
