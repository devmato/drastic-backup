"""Owner-scoped job lookups shared by management and execution."""

from sqlalchemy.orm import selectinload

from drastic_server.models.agent import Agent, AgentOperation
from drastic_server.models.job import Job, JobAction, JobSchedule
from drastic_server.services.queries import require_result


def get_agent(user_id, agent_id):
    return require_result(Agent.query.filter_by(id=agent_id, user_id=user_id))


def get_job(user_id, job_id):
    return require_result(Job.query.join(Agent).filter(Job.id == job_id, Agent.user_id == user_id))


def get_schedule(user_id, schedule_id):
    return require_result(JobSchedule.query.join(Job).join(Agent).filter(
        JobSchedule.id == schedule_id, Agent.user_id == user_id,
    ))


def get_action(user_id, action_id):
    return require_result(JobAction.query.join(Job).join(Agent).filter(
        JobAction.id == action_id, Agent.user_id == user_id,
    ))


def list_jobs(user_id):
    return Agent.query.options(
        selectinload(Agent.repositories),
        selectinload(Agent.jobs).selectinload(Job.schedules).selectinload(JobSchedule.repository),
        selectinload(Agent.jobs).selectinload(Job.schedules).selectinload(JobSchedule.retention),
        selectinload(Agent.jobs).selectinload(Job.actions),
        selectinload(Agent.jobs).selectinload(Job.operations),
    ).filter(Agent.user_id == user_id).all()


def job_detail(user_id, job_id):
    return require_result(Job.query.options(
        selectinload(Job.agent).selectinload(Agent.repositories),
        selectinload(Job.schedules).selectinload(JobSchedule.repository),
        selectinload(Job.schedules).selectinload(JobSchedule.retention),
        selectinload(Job.actions),
        selectinload(Job.operations),
    ).join(Agent).filter(Job.id == job_id, Agent.user_id == user_id))


def job_status(user_id, job_id):
    return AgentOperation.query.join(Agent).filter(
        AgentOperation.job_id == job_id, Agent.user_id == user_id,
    ).order_by(AgentOperation.started.desc()).all()
