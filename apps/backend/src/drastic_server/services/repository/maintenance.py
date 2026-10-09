"""Dispatch repository checks and unlocks through the durable operation lifecycle."""

from drastic_server.integrations.agent import AgentService, is_agent_timeout_response
from drastic_server.models.agent import AgentOperationState, AgentOperationType
from drastic_server.services.exceptions import AgentCommandFailed
from drastic_server.services.jobs.queries import get_agent
from drastic_server.services.operations.lifecycle import (
    agent_operation_start_response,
    fail_started_agent_operation,
    start_agent_operation,
    unknown_agent_operation_dispatch_response,
)
from drastic_server.services.repository.management import get_repository


def start_check(user_id, repository_id, data):
    return _start(user_id, repository_id, data, check=True)


def start_unlock(user_id, repository_id, data):
    return _start(user_id, repository_id, data, check=False)


def _start(user_id, repository_id, data, *, check):
    repository = get_repository(user_id, repository_id)
    agent = get_agent(user_id, data["agent_id"])
    if not any(assigned.id == repository.id for assigned in agent.repositories):
        raise ValueError("Repository is not assigned to this agent")
    if not agent.online:
        raise ValueError("Agent is offline")
    label = "Repository check" if check else "Repository unlock"
    operation = start_agent_operation(
        agent=agent, repository=repository,
        operation_type=AgentOperationType.repository_check if check else AgentOperationType.repository_unlock,
        msg=f"{label} started", log_message=f"{label} queued",
        data={"read_data": data.get("read_data_subset")} if check else None,
    )
    if check:
        response = AgentService.check_repository(agent, repository_id=repository.id,
            read_data_subset=data.get("read_data_subset"), operation_uuid=operation.uuid)
    else:
        response = AgentService.unlock_repository(agent, repository_id=repository.id, operation_uuid=operation.uuid)
    if is_agent_timeout_response(response):
        return unknown_agent_operation_dispatch_response(operation, f"{label} dispatch status unknown")
    if response.get("state") != AgentOperationState.success:
        message = response.get("log", f"Could not start {label.lower()}")
        fail_started_agent_operation(operation, message)
        raise AgentCommandFailed({**response, "log": message})
    return agent_operation_start_response(operation, f"{label} started")
