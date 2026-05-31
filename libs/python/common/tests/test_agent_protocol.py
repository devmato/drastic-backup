from datetime import datetime

from drastic_common.agent.commands import (
    ASYNC_AGENT_COMMANDS,
    AgentCommandName,
    AgentCommandRequestSchema,
)
from drastic_common.agent.enums import AgentJobType, AgentOperationState, AgentOperationType
from drastic_common.agent.schemas import AgentOperationSchema, AgentSyncSchema


def test_agent_operation_schema_loads_wire_payload():
    payload = {
        "uuid": "operation-1",
        "type": "backup",
        "state": "success",
        "source": "manual",
        "job_id": 1,
        "repository_id": 2,
        "logs": [
            {
                "sequence": 1,
                "level": "info",
                "created": "2026-05-10T10:00:01",
                "message": "done",
            }
        ],
        "artifacts": [{"uuid": "artifact-1", "artifact_key": "default", "state": "success"}],
        "ignored": "value",
    }

    loaded = AgentOperationSchema().load(payload)

    assert loaded["type"] == AgentOperationType.backup
    assert loaded["state"] == AgentOperationState.success
    assert loaded["logs"][0]["created"] == datetime(2026, 5, 10, 10, 0, 1)
    assert loaded["logs"][0]["message"] == "done"
    assert loaded["artifacts"][0]["artifact_key"] == "default"
    assert "ignored" not in loaded


def test_agent_sync_schema_dumps_backend_like_objects():
    class Value:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    payload = AgentSyncSchema().dump(
        {
            "repositories": [Value(id=1, kind="custom", location="rest:http://repo", environment={})],
            "retentions": [Value(id=1, name="Daily", keep_last=1)],
            "jobs": [Value(id=1, uuid="job-1", type=Value(name="file"), config={"paths": []})],
            "actions": [Value(id=1, job_id=1, module=Value(name="command"), hook="start", data={})],
            "schedules": [
                Value(
                    id=1,
                    job_id=1,
                    enabled=True,
                    repository_id=1,
                    retention_id=1,
                    cron_string="0 1 * * *",
                    config={},
                )
            ],
        }
    )

    assert payload["jobs"][0]["type"] == "file"
    assert payload["actions"][0]["module"] == "command"
    assert payload["schedules"][0]["config"] == {}


def test_agent_command_request_schema_loads_known_command():
    payload = AgentCommandRequestSchema().load(
        {"command": "run_job", "args": {"job_id": 1, "repository_id": 2}}
    )

    assert payload["command"] == AgentCommandName.run_job
    assert payload["command"] in ASYNC_AGENT_COMMANDS
    assert payload["args"] == {"job_id": 1, "repository_id": 2}


def test_shared_job_type_names_match_wire_values():
    assert AgentJobType.file.name == "file"
    assert AgentJobType.proxmox.name == "proxmox"
