import enum


class AgentOperationType(enum.Enum):
    backup = {"text": "Backup"}
    restore = {"text": "Backup restore"}
    retention = {"text": "Retention"}
    repository_check = {"text": "Repository check"}
    repository_unlock = {"text": "Repository unlock"}
    repository_stats = {"text": "Repository stats"}
    sync = {"text": "Agent sync"}
    agent_update = {"text": "Agent update"}
    command = {"text": "Agent command"}


class AgentOperationState(enum.Enum):
    running = {"icon": "cogs", "notification": "started"}
    warning = {"icon": "yellow exclamation triangle", "notification": "finished with warning"}
    failed = {"icon": "red exclamation triangle", "notification": "failed"}
    success = {"icon": "green check", "notification": "finished successfull"}
    cancelled = {"icon": "ban", "notification": "cancelled"}


class AgentOperationSource(enum.Enum):
    manual = {"text": "Manual"}
    schedule = {"text": "Schedule"}
    triggered = {"text": "Triggered"}
    system = {"text": "System"}


class AgentOperationLogLevel(enum.Enum):
    info = {"text": "Info"}
    warning = {"text": "Warning"}
    error = {"text": "Error"}


class AgentJobType(enum.Enum):
    file = {"text": "File-Backup"}
    proxmox = {"text": "Proxmox-Backup"}
    truenas = {"text": "TrueNAS-Backup"}


class AgentJobActionModule(enum.Enum):
    command = {"text": "Execute command on agent"}
    docker = {"text": "Control docker container"}


class AgentRepositoryKind(str, enum.Enum):
    custom = "custom"
    native = "native"


AgentReportType = AgentOperationType
AgentReportState = AgentOperationState
