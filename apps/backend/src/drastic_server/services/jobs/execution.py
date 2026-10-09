"""Dispatch backups and discovery commands without HTTP request dependencies."""

from datetime import datetime, timezone
from pathlib import Path

from drastic_common.agent.commands import AgentCommandName
from drastic_common.scheduling import preview
from drastic_server.extensions import db
from drastic_server.integrations.agent import AgentService, is_agent_timeout_response
from drastic_server.models.agent import AgentOperationState, AgentOperationType
from drastic_server.services.exceptions import AgentCommandFailed, ResourceConflict
from drastic_server.services.jobs.configuration import (
    assign_agent_repository,
    ensure_job_connection,
    normalize_schedule_config,
)
from drastic_server.services.jobs.queries import get_agent, get_job
from drastic_server.services.operations.lifecycle import (
    agent_operation_start_response,
    fail_started_agent_operation,
    start_agent_operation,
    unknown_agent_operation_dispatch_response,
)


def require_success(response):
    if response.get("state") != AgentOperationState.success:
        raise AgentCommandFailed(response)
    return response.get("data", {})


def start_backup(user_id, job_id, data):
    """Persist intent before dispatch; a timeout keeps the operation reconcilable."""
    job = get_job(user_id, job_id)
    if not job.agent.online:
        raise ValueError("Agent is offline")
    ensure_job_connection(job.agent, job.type.name, job.config)
    repository, newly_assigned = assign_agent_repository(
        user_id, job.agent, data["repository_id"], data.get("recovery_key"),
    )
    if newly_assigned:
        db.session.commit()
        require_success(AgentService.sync(job.agent, await_response=True))

    options = normalize_schedule_config(data.get("options") or {})
    operation = start_agent_operation(
        agent=job.agent, job=job, repository=repository,
        operation_type=AgentOperationType.backup,
        msg="Backup job started", log_message="Backup job queued", data={"options": options},
    )
    response = AgentService.run_job(
        job.agent, job_id=job.id, repository_id=repository.id,
        operation_uuid=operation.uuid, run_options=options,
    )
    if is_agent_timeout_response(response):
        return unknown_agent_operation_dispatch_response(operation, "Backup dispatch status unknown")
    if response.get("state") != AgentOperationState.success:
        fail_started_agent_operation(operation, response.get("log", "Could not start backup"))
        raise AgentCommandFailed(response)
    return agent_operation_start_response(operation, "Backup job started")


def cancel_job(user_id, job_id):
    job = get_job(user_id, job_id)
    latest = job.last_operation
    operation_uuid = latest.uuid if latest and latest.state == AgentOperationState.running else None
    require_success(AgentService.cancel_job(job.agent, job_id=job.id, operation_uuid=operation_uuid))


def preview_schedule(user_id, data):
    if data["agent_id"] is None:
        return preview(data["timing"], datetime.now(timezone.utc))
    agent = get_agent(user_id, data["agent_id"])
    if not agent.online:
        raise ResourceConflict("Next execution is available when the agent is online (agent local time)")
    return require_success(AgentService.send_command(
        agent, AgentCommandName.preview_schedule, timing=data["timing"], timeout=5,
    ))


def list_directories(user_id, agent_id, base_directory):
    agent = get_agent(user_id, agent_id)
    if not agent.online:
        raise ValueError("Agent is offline")
    result = require_success(AgentService.get_dirlist(agent, base_directory=base_directory))
    return {
        "base_directory": base_directory,
        "parent_directory": str(Path(base_directory).parent.absolute()),
        "directories": result.get("dirlist", []),
    }


def list_proxmox_guests(user_id, agent_id):
    agent = get_agent(user_id, agent_id)
    if not agent.online:
        raise ValueError("Agent is offline")
    return {"guests": require_success(AgentService.get_proxmox_guests(agent)).get("guests", [])}


def list_containers(user_id, agent_id):
    agent = get_agent(user_id, agent_id)
    if not agent.online:
        raise ValueError("Agent is offline")
    return {"containers": require_success(AgentService.get_containers(agent)).get("containers", [])}
