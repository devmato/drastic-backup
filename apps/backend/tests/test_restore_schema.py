import pytest
from marshmallow import ValidationError

from drastic_server.models.agent import AgentOperationState
from drastic_server.schemas.restore import RestoreStartInputSchema
from drastic_server.services.exceptions import RestoreServiceException
from drastic_server.services.restore import RestoreService


def restore_payload(**overrides):
    payload = {
        "job_id": 1,
        "agent_id": 2,
        "repository_id": 3,
        "mode": "plain_file",
        "snapshot_id": "snapshot-id",
        "restore_location": "/srv//restores/.",
        "include_paths": ["etc//hosts", "/etc/hosts", "/var/./log"],
    }
    payload.update(overrides)
    return payload


def test_restore_input_normalizes_paths_and_defaults_to_safe_overwrite_policy():
    data = RestoreStartInputSchema().load(restore_payload())

    assert data["restore_location"] == "/srv/restores"
    assert data["include_paths"] == ["/etc/hosts", "/var/log"]
    assert data["overwrite_policy"] == "fail_if_exists"


@pytest.mark.parametrize("target", ["relative", "/", "//", "///", "/srv/../etc", "/srv/\x00bad"])
def test_restore_input_rejects_unsafe_target(target):
    with pytest.raises(ValidationError):
        RestoreStartInputSchema().load(restore_payload(restore_location=target))


@pytest.mark.parametrize("path", ["../etc/passwd", "/var/../etc", "/bad\x00path"])
def test_restore_input_rejects_unsafe_include_path(path):
    with pytest.raises(ValidationError):
        RestoreStartInputSchema().load(restore_payload(include_paths=[path]))


def test_execution_requires_exact_snapshot_id_and_job_tag(monkeypatch):
    monkeypatch.setattr(RestoreService, "_repository_payload", lambda repository, agent: {})
    monkeypatch.setattr(
        "drastic_server.services.restore.AgentService.list_restore_snapshots",
        lambda agent, **kwargs: {
            "state": AgentOperationState.success,
            "data": {
                "snapshots": [
                    {"id": "snapshot-id", "tags": ["job_uuid:different-job"]},
                    {"id": "different-id", "tags": ["job_uuid:job-uuid"]},
                ]
            },
        },
    )

    with pytest.raises(RestoreServiceException, match="does not belong"):
        RestoreService._ensure_snapshot_belongs_to_job(
            object(), object(), "snapshot-id", type("Job", (), {"uuid": "job-uuid"})()
        )
