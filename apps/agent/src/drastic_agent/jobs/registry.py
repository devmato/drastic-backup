from drastic_agent.jobs.file_backup import FileBackupJobHandler
from drastic_agent.jobs.proxmox_backup import ProxmoxBackupJobHandler
from drastic_agent.jobs.truenas_backup import TrueNASBackupJobHandler
from drastic_common.agent.enums import AgentJobType


def get_job_handler(
    agent,
    job,
    repository_id,
    retention_id=None,
    operation_uuid=None,
    run_options=None,
):
    job_type = job.get("type")

    if job_type == AgentJobType.truenas.name:
        return TrueNASBackupJobHandler(agent, job, repository_id, retention_id, operation_uuid, run_options)

    if job_type == AgentJobType.file.name:
        return FileBackupJobHandler(
            agent=agent,
            job=job,
            repository_id=repository_id,
            retention_id=retention_id,
            operation_uuid=operation_uuid,
            run_options=run_options,
        )

    if job_type == AgentJobType.proxmox.name:
        return ProxmoxBackupJobHandler(
            agent=agent,
            job=job,
            repository_id=repository_id,
            retention_id=retention_id,
            operation_uuid=operation_uuid,
            run_options=run_options,
        )

    raise ValueError(f"Unsupported job type: {job_type}")
