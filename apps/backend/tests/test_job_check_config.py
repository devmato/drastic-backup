from drastic_server.models.job import Job, JobType
from drastic_server.schemas.job import JobCreateInputSchema, ScheduleCreateInputSchema
from drastic_server.schemas.repository import CheckInputSchema
from drastic_server.services.job import (
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

    assert job.config == {"selection_mode": "include", "guest_ids": [101, 102]}
    assert job.config_envelope == {"data": job.config}


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


def test_repository_check_input_accepts_optional_read_data_subset():
    data = CheckInputSchema().load({"agent_id": 3, "read_data_subset": "5%"})

    assert data == {"agent_id": 3, "read_data_subset": "5%"}
