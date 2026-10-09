"""Job, schedule and action writes, with commit-before-publish ordering."""

from drastic_server.extensions import db
from drastic_server.integrations.agent import AgentService
from drastic_server.models.job import JobAction, JobActionModuleEnum, JobSchedule
from drastic_server.schemas.job import ActionCreateInputSchema
from drastic_server.services.exceptions import ResourceConflict
from drastic_server.services.jobs.configuration import (
    create_job_instance,
    create_job_schedule,
    ensure_job_connection,
    update_job_instance,
    update_job_schedule,
)
from drastic_server.services.jobs.queries import get_action, get_agent, get_job, get_schedule
from drastic_server.utils.realtime import emit_job_state, emit_jobs_update


def publish_job(job):
    """Publish only committed state; offline agents pick it up on reconnect."""
    emit_job_state(job)
    if job.agent.online:
        AgentService.sync(job.agent)


def create_job(user_id, data):
    agent = get_agent(user_id, data["agent_id"])
    if not agent.online:
        raise ValueError("Agent is offline")
    ensure_job_connection(agent, data["type"], data.get("config"))
    job = create_job_instance(data=data, agent_id=agent.id)
    db.session.add(job)
    db.session.commit()
    publish_job(job)
    return job


def update_job(user_id, job_id, data):
    job = get_job(user_id, job_id)
    if not job.agent.online:
        raise ValueError("Agent is offline")
    ensure_job_connection(job.agent, job.type.name, data.get("config", job.config))
    update_job_instance(job=job, data=data)
    db.session.commit()
    publish_job(job)


def delete_job(user_id, job_id):
    from drastic_server.services.chains import require_unused_chain_reference

    job = get_job(user_id, job_id)
    agent = job.agent
    try:
        require_unused_chain_reference(user_id, "job_id", job.id)
    except ValueError as exc:
        raise ResourceConflict(str(exc)) from exc
    JobSchedule.query.filter(JobSchedule.job_id == job.id).delete()
    JobAction.query.filter(JobAction.job_id == job.id).delete()
    db.session.delete(job)
    db.session.commit()
    emit_jobs_update(user_id)
    if agent.online:
        AgentService.sync(agent)


def create_schedule(user_id, job_id, data):
    job = get_job(user_id, job_id)
    schedule = create_job_schedule(user_id, job, data)
    db.session.commit()
    publish_job(job)
    return schedule


def update_schedule(user_id, schedule_id, data):
    schedule = get_schedule(user_id, schedule_id)
    update_job_schedule(user_id, schedule, data)
    job = schedule.job
    db.session.commit()
    publish_job(job)


def delete_schedule(user_id, schedule_id):
    schedule = get_schedule(user_id, schedule_id)
    job = schedule.job
    db.session.delete(schedule)
    db.session.commit()
    publish_job(job)


def create_action(user_id, job_id, data):
    job = get_job(user_id, job_id)
    try:
        module = JobActionModuleEnum[data["module"]]
    except KeyError as exc:
        raise ValueError("Invalid module") from exc
    action = JobAction(job_id=job.id, module=module, hook=data["hook"], data=data["data"])
    db.session.add(action)
    db.session.commit()
    publish_job(job)
    return action


def update_action(user_id, action_id, data):
    action = get_action(user_id, action_id)
    # Validate the merged state too: partial updates can change the module's requirements.
    payload = ActionCreateInputSchema().load({
        "module": data.get("module", action.module.name if action.module else None),
        "hook": data.get("hook", action.hook),
        "data": data.get("data", action.data or {}),
    })
    action.hook = payload["hook"]
    action.data = payload["data"]
    action.module = JobActionModuleEnum[payload["module"]]
    db.session.commit()
    publish_job(action.job)


def delete_action(user_id, action_id):
    action = get_action(user_id, action_id)
    job = action.job
    db.session.delete(action)
    db.session.commit()
    publish_job(job)
