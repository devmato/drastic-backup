import os

import dataset


def _agent_data_dir() -> str:
    return os.environ.get("DRASTIC_AGENT_DATA_DIR", "/app/data")


os.makedirs(_agent_data_dir(), exist_ok=True)
db = dataset.connect(f"sqlite:///{_agent_data_dir()}/database.db?check_same_thread=False")

agent = db["agent"]
repositories = db["repositories"]
repository_secrets = db["repository_secrets"]
retentions = db["retentions"]
jobs = db["jobs"]
actions = db["job_actions"]
schedules = db["job_schedules"]
agent_operations = db["agent_operations"]
agent_operation_artifacts = db["agent_operation_artifacts"]
