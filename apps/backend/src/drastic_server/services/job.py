from croniter import croniter

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
    config = config or {}

    selection_mode = config.get("selection_mode") or "all"
    guest_ids = sorted({int(guest_id) for guest_id in config.get("guest_ids", [])})

    if selection_mode not in {"all", "include"}:
        raise ValueError("Invalid Proxmox selection mode")

    if selection_mode == "include" and not guest_ids:
        raise ValueError("Proxmox include mode requires at least one guest ID")

    return {
        "selection_mode": selection_mode,
        "guest_ids": guest_ids,
    }


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


def create_job_schedule(user_id, job, data):
    repository_id = data["repository_id"]
    retention_id = data["retention_id"]
    assign_agent_repository(user_id, job.agent, repository_id, data.get("recovery_key"))
    retention = validate_retention(user_id, retention_id)
    cron_string = build_schedule_cron_string(
        minute=data["minute"],
        hour=data["hour"],
        day_of_week=data["day_of_week"],
    )

    schedule = JobSchedule(
        job_id=job.id,
        cron_string=cron_string,
        enabled=data["enabled"],
        advanced=False,
        repository_id=repository_id,
        retention_id=retention.id if retention else None,
        config=normalize_schedule_config(data.get("config") or {}),
    )
    db.session.add(schedule)
    return schedule


def update_job_schedule(user_id, schedule, data):
    repository_id = data["repository_id"]
    retention_id = data["retention_id"]
    assign_agent_repository(user_id, schedule.job.agent, repository_id, data.get("recovery_key"))
    retention = validate_retention(user_id, retention_id)
    cron_string = build_schedule_cron_string(
        minute=data["minute"],
        hour=data["hour"],
        day_of_week=data["day_of_week"],
    )

    schedule.cron_string = cron_string
    schedule.enabled = data["enabled"]
    schedule.repository_id = repository_id
    schedule.retention_id = retention.id if retention else None
    schedule.config = normalize_schedule_config(data.get("config") or {})
    return schedule


def _get_job_type(job_type):
    try:
        return JobType[job_type]
    except KeyError as exc:
        raise ValueError("Invalid job type") from exc


def create_job_instance(data, agent_id):
    job_type = _get_job_type(data["type"])
    config = data.get("config", {})

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

    if job.type == JobType.file:
        normalized_config = _normalize_file_backup_config(data["config"])
        job.config = normalized_config
        return job

    if job.type == JobType.proxmox:
        normalized_config = _normalize_proxmox_backup_config(data["config"])
        job.config = normalized_config
        return job

    raise ValueError("Unsupported job type")
