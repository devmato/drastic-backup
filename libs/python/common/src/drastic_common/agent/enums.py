import enum


class AgentOperationType(enum.Enum):
    backup = {"text": "Backup"}
    restore = {"text": "Backup restore"}
    retention = {"text": "Retention"}
    repository_check = {"text": "Repository check"}
    repository_unlock = {"text": "Repository unlock"}
    repository_stats = {"text": "Repository stats"}
    sync = {"text": "Agent sync"}
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


AgentReportType = AgentOperationType
AgentReportState = AgentOperationState
