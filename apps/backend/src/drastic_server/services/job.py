from croniter import croniter
from marshmallow import ValidationError

from drastic_common.agent.schemas import AgentProxmoxBackupJobConfigSchema
from drastic_common.scheduling import as_cron
from drastic_common.truenas import TrueNASBackupConfigSchema
from drastic_server.extensions import db
from drastic_server.models.job import Job, JobSchedule, JobType
from drastic_server.models.repository import Repository
from drastic_server.models.retention import Retention
from drastic_server.services.repository import ensure_agent_envelopes_from_user_recovery_key


def _normalize_check_config(config):
    config = config or {}
    repository_check = config.get("repository_check") or {}
    enabled = bool(repository_check.get("enabled"))
    read_data = str(repository_check.get("read_data") or "").strip() or None

    if not enabled:
        read_data = None

    return {
        "repository_check": {
            "enabled": enabled,
            "read_data": read_data,
        }
    }


def _normalize_file_backup_config(config):
    config = config or {}

    return {
        "paths": config.get("paths", []),
        "exclude_patterns": config.get("exclude_patterns", []),
    }


def _normalize_proxmox_backup_config(config):
    try:
        config = AgentProxmoxBackupJobConfigSchema().load(config or {})
    except ValidationError as exc:
        raise ValueError(f"Invalid Proxmox configuration: {exc}") from exc
    config["guest_ids"].sort()
    config["exclude_guest_ids"].sort()
    return config


def normalize_schedule_config(config):
    return _normalize_check_config(config)


def assign_agent_repository(user_id, agent, repository_id, user_recovery_key=None):
    repository = Repository.query.filter(
        Repository.id == repository_id,
        Repository.user_id == user_id,
    ).first_or_404()
    ensure_agent_envelopes_from_user_recovery_key(
        agent=agent,
        repositories=[repository],
        user_recovery_key=user_recovery_key,
    )

    assigned = any(assigned_repository.id == repository.id for assigned_repository in agent.repositories)
    if not assigned:
        agent.repositories.append(repository)

    return repository, not assigned


def validate_retention(user_id, retention_id):
    if retention_id is None:
        return None

    return Retention.query.filter(
        Retention.id == retention_id, Retention.user_id == user_id
    ).first_or_404()


def build_schedule_cron_string(minute, hour, day_of_week):
    dow_str = "*" if len(day_of_week) == 7 else ",".join(str(d) for d in day_of_week)
    cron_string = f"{minute} {hour} * * {dow_str}"

    if not croniter.is_valid(cron_string):
        raise ValueError("Invalid cron syntax")

    return cron_string


def schedule_trigger(data):
    if "timing" in data:
        return {"enabled": data["enabled"], "timing": data["timing"], "cron_string": as_cron(data["timing"])}
    return {"enabled": data["enabled"], "cron_string": build_schedule_cron_string(data["minute"], data["hour"], data["day_of_week"])}


def job_schedule_config(agent, data):
    config = normalize_schedule_config(data.get("config") or {})
    if "timing" in data:
        if (agent.protocol_version or 0) < 12:
            raise ValueError("Update the agent to use schedule types (protocol 12 required)")
        config["timing"] = data["timing"]
    return config


def create_job_schedule(user_id, job, data):
    config = job_schedule_config(job.agent, data)
    repository_id = data["repository_id"]
    retention_id = data["retention_id"]
    assign_agent_repository(user_id, job.agent, repository_id, data.get("recovery_key"))
    retention = validate_retention(user_id, retention_id)
    cron_string = schedule_trigger(data)["cron_string"]

    schedule = JobSchedule(
        job_id=job.id,
        cron_string=cron_string,
        enabled=data["enabled"],
        advanced=False,
        repository_id=repository_id,
        retention_id=retention.id if retention else None,
        config=config,
    )
    db.session.add(schedule)
    return schedule


def update_job_schedule(user_id, schedule, data):
    config = job_schedule_config(schedule.job.agent, data)
    repository_id = data["repository_id"]
    retention_id = data["retention_id"]
    assign_agent_repository(user_id, schedule.job.agent, repository_id, data.get("recovery_key"))
    retention = validate_retention(user_id, retention_id)
    cron_string = schedule_trigger(data)["cron_string"]

    schedule.cron_string = cron_string
    schedule.enabled = data["enabled"]
    schedule.repository_id = repository_id
    schedule.retention_id = retention.id if retention else None
    schedule.config = config
    return schedule


def _get_job_type(job_type):
    try:
        return JobType[job_type]
    except KeyError as exc:
        raise ValueError("Invalid job type") from exc


def ensure_job_connection(agent, job_type, config=None):
    if job_type == "proxmox" and (config or {}).get("backup_mode", "snapshot") != "snapshot":
        if (agent.protocol_version or 0) < 8:
            raise ValueError("Update the agent to use native Proxmox backups (protocol 8 required)")
        if not config.get("fleecing_storage") and (agent.protocol_version or 0) < 9:
            raise ValueError("Update the agent to select temporary backup storage automatically (protocol 9 required)")
        error = ((agent.connections or {}).get("proxmox") or {}).get("native_backups_error")
        if error:
            raise ValueError(error)
    if job_type not in {"proxmox", "truenas"}:
        return
    if (agent.protocol_version or 0) < 3:
        if job_type == "truenas":
            raise ValueError("Update the agent to use TrueNAS backups (protocol 3 required)")
        return  # Existing Proxmox agents predate connection status reporting.
    connection = (agent.connections or {}).get(job_type) or {}
    if not connection.get("configured") or not connection.get("available"):
        raise ValueError(f"Configure the {job_type} connection and its local prerequisites on the agent first")


def _normalize_truenas_config(config):
    try:
        return TrueNASBackupConfigSchema().load(config)
    except ValidationError as exc:
        raise ValueError(f"Invalid TrueNAS configuration: {exc}") from exc


def create_job_instance(data, agent_id):
    job_type = _get_job_type(data["type"])
    config = data.get("config", {})

    if job_type == JobType.truenas:
        return Job(name=data["name"], agent_id=agent_id, type=job_type, config=_normalize_truenas_config(config))

    if job_type == JobType.file:
        normalized_config = _normalize_file_backup_config(config)
        return Job(
            name=data["name"],
            agent_id=agent_id,
            type=job_type,
            config=normalized_config,
        )

    if job_type == JobType.proxmox:
        normalized_config = _normalize_proxmox_backup_config(config)
        return Job(
            name=data["name"],
            agent_id=agent_id,
            type=job_type,
            config=normalized_config,
        )

    raise ValueError("Invalid job type")


def update_job_instance(job, data):
    if "name" in data:
        job.name = data["name"]

    if "config" not in data:
        return job

    if job.type == JobType.truenas:
        job.config = _normalize_truenas_config(data["config"])
        return job

    if job.type == JobType.file:
        normalized_config = _normalize_file_backup_config(data["config"])
        job.config = normalized_config
        return job

    if job.type == JobType.proxmox:
        normalized_config = _normalize_proxmox_backup_config(data["config"])
        job.config = normalized_config
        return job

    raise ValueError("Unsupported job type")
