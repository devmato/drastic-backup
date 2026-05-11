from drastic_agent.jobs.file_backup import FileBackupJobHandler
from drastic_agent.jobs.proxmox_backup import ProxmoxBackupJobHandler


def get_job_handler(agent, job, repository_id, retention_id=None, run_options=None):
    job_type = job.get("type")

    if job_type == "file":
        return FileBackupJobHandler(
            agent=agent,
            job=job,
            repository_id=repository_id,
            retention_id=retention_id,
            run_options=run_options,
        )

    if job_type == "proxmox":
        return ProxmoxBackupJobHandler(
            agent=agent,
            job=job,
            repository_id=repository_id,
            retention_id=retention_id,
            run_options=run_options,
        )

    raise ValueError(f"Unsupported job type: {job_type}")
