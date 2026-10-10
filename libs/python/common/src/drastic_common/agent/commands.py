import enum

from marshmallow import EXCLUDE, Schema, fields, validate

AGENT_PROTOCOL_VERSION = 16
PATH_INCLUDE_EXCEPTIONS_PROTOCOL = 16
DEBUG_SECTIONS = ("runtime", "logs", "threads")


class AgentCommandName(str, enum.Enum):
    list_restore_snapshots = "list_restore_snapshots"
    list_restore_entries = "list_restore_entries"
    run_restore = "run_restore"
    cancel_restore = "cancel_restore"
    proxmox_restore = "proxmox_restore"
    init_repository = "init_repository"
    sync = "sync"
    get_repository_stats = "get_repository_stats"
    get_dirlist = "get_dirlist"
    get_proxmox_guests = "get_proxmox_guests"
    get_proxmox_settings = "get_proxmox_settings"
    update_proxmox_settings = "update_proxmox_settings"
    test_proxmox_settings = "test_proxmox_settings"
    truenas_settings = "truenas_settings"
    delete_connection = "delete_connection"
    run_job = "run_job"
    get_job_status = "get_job_status"
    get_operation_status = "get_operation_status"
    preview_schedule = "preview_schedule"
    add_job = "add_job"
    delete_job = "delete_job"
    cancel_job = "cancel_job"
    unlock_repository = "unlock_repository"
    check_repository = "check_repository"
    get_containers = "get_containers"
    reset_known_hosts = "reset_known_hosts"
    rotate_ssh_key = "rotate_ssh_key"
    update = "update"
    debug_state = "debug_state"


# Protocol 0 is the legacy command set, before web-based Proxmox settings.
AGENT_COMMAND_MIN_PROTOCOL = {
    AgentCommandName.run_job: 15,
    AgentCommandName.list_restore_snapshots: 15,
    AgentCommandName.list_restore_entries: 15,
    AgentCommandName.run_restore: 15,
    AgentCommandName.get_repository_stats: 15,
    AgentCommandName.preview_schedule: 12,
    AgentCommandName.get_operation_status: 11,
    AgentCommandName.debug_state: 6,
    AgentCommandName.truenas_settings: 3,
    AgentCommandName.delete_connection: 3,
    AgentCommandName.proxmox_restore: 15,
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
    args = fields.Dict(keys=fields.String(), values=fields.Raw(allow_none=True), load_default=dict)


class AgentDebugRequestSchema(Schema):
    enabled = fields.Boolean(required=True)
    section = fields.String(load_default=None, allow_none=True, validate=validate.OneOf(DEBUG_SECTIONS))
    limit = fields.Integer(load_default=100, strict=True, validate=validate.Range(min=1, max=200))


def agent_command_value(command: AgentCommandName | str) -> str:
    if isinstance(command, AgentCommandName):
        return command.value
    return str(command)
