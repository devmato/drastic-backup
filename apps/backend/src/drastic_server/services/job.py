from drastic_server.models.job import Job, JobType


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
