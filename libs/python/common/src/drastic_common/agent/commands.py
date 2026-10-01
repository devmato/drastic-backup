import enum

from marshmallow import EXCLUDE, Schema, fields

AGENT_PROTOCOL_VERSION = 1


class AgentCommandName(str, enum.Enum):
    list_restore_snapshots = "list_restore_snapshots"
    list_restore_entries = "list_restore_entries"
    run_restore = "run_restore"
    cancel_restore = "cancel_restore"
    init_repository = "init_repository"
    sync = "sync"
    get_repository_stats = "get_repository_stats"
    get_dirlist = "get_dirlist"
    get_proxmox_guests = "get_proxmox_guests"
    get_proxmox_settings = "get_proxmox_settings"
    update_proxmox_settings = "update_proxmox_settings"
    test_proxmox_settings = "test_proxmox_settings"
    run_job = "run_job"
    get_job_status = "get_job_status"
    add_job = "add_job"
    delete_job = "delete_job"
    cancel_job = "cancel_job"
    unlock_repository = "unlock_repository"
    check_repository = "check_repository"
    get_containers = "get_containers"
    reset_known_hosts = "reset_known_hosts"
    rotate_ssh_key = "rotate_ssh_key"
    update = "update"


# Protocol 0 is the legacy command set, before web-based Proxmox settings.
AGENT_COMMAND_MIN_PROTOCOL = {
    AgentCommandName.get_proxmox_settings: 1,
    AgentCommandName.update_proxmox_settings: 1,
    AgentCommandName.test_proxmox_settings: 1,
    AgentCommandName.update: 1,
}


ASYNC_AGENT_COMMANDS = frozenset(
    {
        AgentCommandName.run_job,
        AgentCommandName.run_restore,
        AgentCommandName.check_repository,
        AgentCommandName.unlock_repository,
    }
)


class AgentCommandRequestSchema(Schema):
    class Meta:
        unknown = EXCLUDE

    command = fields.Enum(AgentCommandName, by_value=True, required=True)
    args = fields.Dict(keys=fields.String(), values=fields.Raw(), load_default=dict)


def agent_command_value(command: AgentCommandName | str) -> str:
    if isinstance(command, AgentCommandName):
        return command.value
    return str(command)
