import pytest
from marshmallow import ValidationError

from drastic_common.agent.schemas import AgentJobSchema
from drastic_server.models.job import Job, JobType
from drastic_server.schemas.job import JobCreateInputSchema, ScheduleCreateInputSchema
from drastic_server.schemas.repository import CheckInputSchema
from drastic_server.services.job import (
    build_schedule_cron_string,
    create_job_instance,
    normalize_schedule_config,
    update_job_instance,
)


def test_file_job_stores_config_in_standard_envelope():
    data = JobCreateInputSchema().load(
        {
            "agent_id": 1,
            "name": "Files",
            "type": "file",
            "config": {
                "paths": [{"path": "/data", "group": "folder"}],
            },
        }
    )

    job = create_job_instance(data=data, agent_id=data["agent_id"])

    assert isinstance(job, Job)
    assert job.type == JobType.file
    assert job.config == {"paths": [{"path": "/data", "group": "folder"}], "exclude_patterns": []}
    assert job.config_envelope == {"data": job.config}


def test_proxmox_job_updates_config_envelope():
    job = Job(
        name="VMs",
        agent_id=1,
        type=JobType.proxmox,
        config={"selection_mode": "all", "guest_ids": []},
    )

    update_job_instance(
        job,
        {
            "config": {
                "selection_mode": "include",
                "guest_ids": [102, 101],
            }
        },
    )

    assert job.config == {"selection_mode": "include", "guest_ids": [101, 102], "exclude_guest_ids": []}
    assert job.config_envelope == {"data": job.config}


def test_proxmox_exclusions_survive_create_update_and_agent_serialization():
    data = JobCreateInputSchema().load({
        "agent_id": 1, "name": "VMs", "type": "proxmox",
        "config": {"exclude_guest_ids": [103, 101]},
    })
    job = create_job_instance(data=data, agent_id=1)
    assert job.config == {"selection_mode": "all", "guest_ids": [], "exclude_guest_ids": [101, 103]}
    update_job_instance(job, {"config": {"selection_mode": "all", "exclude_guest_ids": [104, 102]}})
    assert job.config == {"selection_mode": "all", "guest_ids": [], "exclude_guest_ids": [102, 104]}
    assert AgentJobSchema().dump(job)["config"] == job.config


@pytest.mark.parametrize("ids", [[99], [101, 101], ["invalid"], None, "101"])
def test_proxmox_exclusions_are_validated_on_create_and_update(ids):
    config = {"selection_mode": "all", "exclude_guest_ids": ids}
    with pytest.raises(ValidationError):
        JobCreateInputSchema().load({"agent_id": 1, "name": "VMs", "type": "proxmox", "config": config})
    job = Job(name="VMs", agent_id=1, type=JobType.proxmox, config={})
    with pytest.raises(ValueError, match="exclude_guest_ids"):
        update_job_instance(job, {"config": config})


def test_schedule_config_accepts_repository_check_read_data():
    data = ScheduleCreateInputSchema().load(
        {
            "hour": "1",
            "minute": "0",
            "day_of_week": [1],
            "enabled": True,
            "repository_id": 2,
            "config": {"repository_check": {"enabled": True, "read_data": "100%"}},
        }
    )

    assert normalize_schedule_config(data["config"]) == {
        "repository_check": {"enabled": True, "read_data": "100%"}
    }


def test_build_schedule_cron_string_collapses_full_week():
    assert build_schedule_cron_string("0", "1", [0, 1, 2, 3, 4, 5, 6]) == "0 1 * * *"


def test_build_schedule_cron_string_uses_selected_weekdays():
    assert build_schedule_cron_string("30", "2", [1, 5]) == "30 2 * * 1,5"


def test_repository_check_input_accepts_optional_read_data_subset():
    data = CheckInputSchema().load({"agent_id": 3, "read_data_subset": "5%"})

    assert data == {"agent_id": 3, "read_data_subset": "5%"}
