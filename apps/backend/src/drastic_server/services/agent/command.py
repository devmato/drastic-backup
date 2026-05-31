from flask import current_app
from flask_socketio import call, emit
from socketio.exceptions import TimeoutError

from drastic_common.agent.commands import AgentCommandName, agent_command_value
from drastic_server.env_loader import parse_int_value
from drastic_server.models.agent import AgentOperationState, AgentOperationType
from drastic_server.schemas.agent import AgentOperationSchema
from drastic_server.schemas.job import JobSchema


def is_agent_timeout_response(response):
    return bool((response or {}).get("data", {}).get("timeout"))


def _load_operation_response(payload):
    response = AgentOperationSchema().load(payload)
    response["log"] = "\n".join(log.get("message", "") for log in response.get("logs", []))
    return response


class AgentService:
    @staticmethod
    def failed_command_report(log, *, timeout=False):
        response = AgentOperationSchema().load(
            {
                "state": AgentOperationState.failed,
                "type": AgentOperationType.command,
                "data": {"timeout": timeout},
                "logs": [{"sequence": 1, "level": "error", "message": log}],
            }
        )
        response["log"] = log
        return response

    @staticmethod
    def command_timeout():
        return parse_int_value(current_app.config.get("COMMAND_TIMEOUT_SECONDS"), 60)

    @classmethod
    def send_command(cls, agent, command, await_response=True, **kwargs):
        command = agent_command_value(command)
        if not agent.online:
            return cls.failed_command_report("Agent is offline")

        try:
            if await_response:
                return _load_operation_response(
                    call(
                        "execute",
                        {"command": command, "args": kwargs},
                        namespace="/agent",
                        to=agent.session.request_sid,
                        timeout=cls.command_timeout(),
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
        return cls.send_command(agent, AgentCommandName.run_restore, await_response=False, **kwargs)

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
    def run_job(cls, agent, job_id, repository_id, operation_uuid=None, run_options=None):
        return cls.send_command(
            agent,
            AgentCommandName.run_job,
            job_id=job_id,
            repository_id=repository_id,
            operation_uuid=operation_uuid,
            run_options=run_options or {},
            await_response=False,
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
    def cancel_job(cls, agent, job_id):
        return cls.send_command(agent, AgentCommandName.cancel_job, job_id=job_id)

    @classmethod
    def unlock_repository(cls, agent, repository_id, operation_uuid=None):
        return cls.send_command(
            agent,
            AgentCommandName.unlock_repository,
            repository_id=repository_id,
            operation_uuid=operation_uuid,
            await_response=False,
        )

    @classmethod
    def check_repository(cls, agent, repository_id, read_data_subset=None, operation_uuid=None):
        return cls.send_command(
            agent,
            AgentCommandName.check_repository,
            repository_id=repository_id,
            read_data_subset=read_data_subset,
            operation_uuid=operation_uuid,
        )

    @classmethod
    def get_containers(cls, agent):
        return cls.send_command(agent, AgentCommandName.get_containers)

    @classmethod
    def reset_known_hosts(cls, agent):
        return cls.send_command(agent, AgentCommandName.reset_known_hosts)

    @classmethod
    def rotate_ssh_key(cls, agent):
        return cls.send_command(agent, AgentCommandName.rotate_ssh_key)


class AgentCommand:
    def __init__(self, agent):
        self.agent = agent

    def init_repository(self, location, password, env):
        return AgentService.init_repository(self.agent, location=location, password=password, env=env)

    def sync(self, await_response=False):
        return AgentService.sync(self.agent, await_response=await_response)

    def get_repository_stats(self, repository_id):
        return AgentService.get_repository_stats(self.agent, repository_id=repository_id)

    def get_dirlist(self, base_directory="/"):
        return AgentService.get_dirlist(self.agent, base_directory=base_directory)

    def get_proxmox_guests(self):
        return AgentService.get_proxmox_guests(self.agent)

    def run_job(self, job_id, repository_id, operation_uuid=None, run_options=None):
        return AgentService.run_job(
            self.agent,
            job_id=job_id,
            repository_id=repository_id,
            operation_uuid=operation_uuid,
            run_options=run_options,
        )

    def get_job_status(self, job_id):
        return AgentService.get_job_status(self.agent, job_id=job_id)

    def add_job(self, job):
        return AgentService.add_job(self.agent, job=job)

    def delete_job(self, job_id):
        return AgentService.delete_job(self.agent, job_id=job_id)

    def cancel_job(self, job_id):
        return AgentService.cancel_job(self.agent, job_id=job_id)

    def unlock_repository(self, repository_id, operation_uuid=None):
        return AgentService.unlock_repository(
            self.agent,
            repository_id=repository_id,
            operation_uuid=operation_uuid,
        )

    def check_repository(self, repository_id, read_data_subset=None, operation_uuid=None):
        return AgentService.check_repository(
            self.agent,
            repository_id=repository_id,
            read_data_subset=read_data_subset,
            operation_uuid=operation_uuid,
        )

    def get_containers(self):
        return AgentService.get_containers(self.agent)

    def reset_known_hosts(self):
        return AgentService.reset_known_hosts(self.agent)

    def rotate_ssh_key(self):
        return AgentService.rotate_ssh_key(self.agent)
