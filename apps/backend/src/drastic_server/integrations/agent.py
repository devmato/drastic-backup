"""Socket.IO command transport, including protocol and timeout handling."""

from flask import current_app
from flask_socketio import call, emit
from socketio.exceptions import TimeoutError

from drastic_common.agent.commands import (
    AGENT_COMMAND_MIN_PROTOCOL,
    AGENT_PROTOCOL_VERSION,
    AgentCommandName,
    agent_command_value,
)
from drastic_server.config import DefaultConfig, parse_int_value
from drastic_server.models.agent import AgentOperationState, AgentOperationType
from drastic_server.schemas.agent import AgentOperationSchema
from drastic_server.schemas.job import JobSchema


def is_agent_timeout_response(response):
    return bool((response or {}).get("data", {}).get("timeout"))


def is_agent_conflict_response(response):
    log = str((response or {}).get("log") or "").lower()
    return "resource is busy" in log or "execution capacity" in log


def _load_operation_response(payload):
    response = AgentOperationSchema().load(payload)
    response["log"] = "\n".join(log.get("message", "") for log in response.get("logs", []))
    return response


class AgentService:
    @staticmethod
    def failed_command_report(log, *, timeout=False):
        response = AgentOperationSchema().load(
            {
                "state": AgentOperationState.running if timeout else AgentOperationState.failed,
                "type": AgentOperationType.command,
                "data": {"timeout": timeout},
                "logs": [{"sequence": 1, "level": "error", "message": log}],
            }
        )
        response["log"] = log
        return response

    @staticmethod
    def command_timeout():
        return parse_int_value(
            current_app.config.get("COMMAND_TIMEOUT_SECONDS"),
            DefaultConfig.COMMAND_TIMEOUT_SECONDS,
        )

    @classmethod
    def send_command(cls, agent, command, await_response=True, timeout=None, **kwargs):
        command = agent_command_value(command)
        if not agent.online:
            return cls.failed_command_report("Agent is offline")

        protocol_version = getattr(agent, "protocol_version", 0) or 0
        required_version = AGENT_COMMAND_MIN_PROTOCOL.get(command, 0)
        if not required_version <= protocol_version <= AGENT_PROTOCOL_VERSION:
            return cls.failed_command_report(
                f"Command {command} is not supported by agent protocol {protocol_version}; update the agent"
            )

        try:
            if await_response:
                return _load_operation_response(
                    call(
                        "execute",
                        {"command": command, "args": kwargs},
                        namespace="/agent",
                        to=agent.session.request_sid,
                        timeout=timeout or cls.command_timeout(),
                    )
                )

            return emit(
                "execute",
                {"command": command, "args": kwargs},
                namespace="/agent",
                to=agent.session.request_sid,
            )
        except TimeoutError:
            return cls.failed_command_report("Agent request timed out", timeout=True)

    @classmethod
    def list_restore_snapshots(cls, agent, **kwargs):
        return cls.send_command(agent, AgentCommandName.list_restore_snapshots, **kwargs)

    @classmethod
    def list_restore_entries(cls, agent, **kwargs):
        return cls.send_command(agent, AgentCommandName.list_restore_entries, **kwargs)

    @classmethod
    def run_restore(cls, agent, **kwargs):
        return cls.send_command(agent, AgentCommandName.run_restore, timeout=5, **kwargs)

    @classmethod
    def cancel_restore(cls, agent, **kwargs):
        return cls.send_command(agent, AgentCommandName.cancel_restore, **kwargs)

    @classmethod
    def init_repository(cls, agent, location, password, env):
        return cls.send_command(
            agent, AgentCommandName.init_repository, location=location, password=password, env=env
        )

    @classmethod
    def sync(cls, agent, await_response=False):
        return cls.send_command(agent, AgentCommandName.sync, await_response=await_response)

    @classmethod
    def get_repository_stats(cls, agent, repository_id):
        return cls.send_command(
            agent, AgentCommandName.get_repository_stats, repository_id=repository_id
        )

    @classmethod
    def get_dirlist(cls, agent, base_directory="/"):
        return cls.send_command(agent, AgentCommandName.get_dirlist, base_directory=base_directory)

    @classmethod
    def get_proxmox_guests(cls, agent):
        return cls.send_command(agent, AgentCommandName.get_proxmox_guests)

    @classmethod
    def get_proxmox_settings(cls, agent):
        return cls.send_command(agent, AgentCommandName.get_proxmox_settings)

    @classmethod
    def update_proxmox_settings(cls, agent, **kwargs):
        return cls.send_command(agent, AgentCommandName.update_proxmox_settings, **kwargs)

    @classmethod
    def test_proxmox_settings(cls, agent, **kwargs):
        return cls.send_command(agent, AgentCommandName.test_proxmox_settings, **kwargs)

    @classmethod
    def truenas_settings(cls, agent, **kwargs):
        return cls.send_command(agent, AgentCommandName.truenas_settings, **kwargs)

    @classmethod
    def delete_connection(cls, agent, **kwargs):
        return cls.send_command(agent, AgentCommandName.delete_connection, **kwargs)

    @classmethod
    def run_job(cls, agent, job_id, repository_id, operation_uuid=None, run_options=None, retention_id=None):
        return cls.send_command(
            agent,
            AgentCommandName.run_job,
            job_id=job_id,
            repository_id=repository_id,
            operation_uuid=operation_uuid,
            run_options=run_options or {},
            **({"retention_id": retention_id} if retention_id is not None else {}),
            timeout=5,
        )

    @classmethod
    def get_job_status(cls, agent, job_id):
        return cls.send_command(agent, AgentCommandName.get_job_status, job_id=job_id)

    @classmethod
    def add_job(cls, agent, job):
        return cls.send_command(agent, AgentCommandName.add_job, job_data=JobSchema().dump(job))

    @classmethod
    def delete_job(cls, agent, job_id):
        return cls.send_command(agent, AgentCommandName.delete_job, job_id=job_id)

    @classmethod
    def cancel_job(cls, agent, job_id, operation_uuid=None, cancel_if_missing=False):
        return cls.send_command(
            agent, AgentCommandName.cancel_job, job_id=job_id, operation_uuid=operation_uuid,
            **({"cancel_if_missing": True} if cancel_if_missing else {}),
        )

    @classmethod
    def unlock_repository(cls, agent, repository_id, operation_uuid=None):
        return cls.send_command(
            agent,
            AgentCommandName.unlock_repository,
            repository_id=repository_id,
            operation_uuid=operation_uuid,
            timeout=5,
        )

    @classmethod
    def check_repository(cls, agent, repository_id, read_data_subset=None, operation_uuid=None):
        return cls.send_command(
            agent,
            AgentCommandName.check_repository,
            repository_id=repository_id,
            read_data_subset=read_data_subset,
            operation_uuid=operation_uuid,
            timeout=5,
        )

    @classmethod
    def get_containers(cls, agent):
        return cls.send_command(agent, AgentCommandName.get_containers)
