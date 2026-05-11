from pathlib import Path

from croniter import croniter
from flask.views import MethodView
from flask_jwt_extended import get_jwt_identity, jwt_required
from flask_smorest import Blueprint, abort
from marshmallow import ValidationError
from sqlalchemy.orm import selectinload

from drastic_server.extensions import db
from drastic_server.models.agent import (
    Agent,
    AgentOperation,
    AgentOperationState,
    AgentOperationType,
)
from drastic_server.models.job import (
    Job,
    JobAction,
    JobActionModuleEnum,
    JobSchedule,
)
from drastic_server.models.repository import Repository
from drastic_server.models.retention import Retention
from drastic_server.schemas.agent import AgentOperationStartResponseSchema
from drastic_server.schemas.common import MessageIdSchema, MessageSchema
from drastic_server.schemas.job import (
    ActionCreateInputSchema,
    ActionUpdateInputSchema,
    AgentJobsResponseSchema,
    ContainersQuerySchema,
    ContainersResponseSchema,
    DirlistQuerySchema,
    DirlistResponseSchema,
    JobCreateInputSchema,
    JobOperationRunInputSchema,
    JobResponseSchema,
    JobStatusResponseSchema,
    JobUpdateInputSchema,
    ProxmoxGuestsQuerySchema,
    ProxmoxGuestsResponseSchema,
    ScheduleCreateInputSchema,
    ScheduleUpdateInputSchema,
)
from drastic_server.services.agent import AgentCommand, is_agent_timeout_response
from drastic_server.services.agent.operation_start import (
    agent_operation_start_response,
    start_agent_operation,
)
from drastic_server.services.job import (
    create_job_instance,
    normalize_schedule_config,
    update_job_instance,
)
from drastic_server.services.repository import (
    RepositorySecretError,
    ensure_agent_envelopes_from_user_recovery_key,
)
from drastic_server.utils.realtime import emit_job_state, emit_jobs_update

blp = Blueprint("jobs", __name__, url_prefix="/api/jobs", description="Job operations")


def _abort_recovery_key_error(exc):
    abort(401, message=str(exc), errors={"recovery_key": ["required"]})


def _abort_agent_command_failure(response):
    status_code = 504 if is_agent_timeout_response(response) else 400
    abort(status_code, message=response.get("log", "Error"))


def _assign_agent_repository(user_id, agent, repository_id, user_recovery_key=None):
    repository = Repository.query.filter(
        Repository.id == repository_id,
        Repository.user_id == user_id,
    ).first_or_404()
    try:
        ensure_agent_envelopes_from_user_recovery_key(
            agent=agent,
            repositories=[repository],
            user_recovery_key=user_recovery_key,
        )
    except RepositorySecretError as exc:
        _abort_recovery_key_error(exc)

    assigned = any(assigned_repository.id == repository.id for assigned_repository in agent.repositories)

    if not assigned:
        agent.repositories.append(repository)

    return repository, not assigned


def _validate_retention(user_id, retention_id):
    if retention_id is None:
        return None

    return Retention.query.filter(
        Retention.id == retention_id, Retention.user_id == user_id
    ).first_or_404()


@blp.route("/")
class JobList(MethodView):
    @jwt_required()
    @blp.response(200, AgentJobsResponseSchema(many=True))
    def get(self):
        user_id = get_jwt_identity()
        return (
            Agent.query.options(
                selectinload(Agent.repositories),
                selectinload(Agent.jobs)
                .selectinload(Job.schedules)
                .selectinload(JobSchedule.repository),
                selectinload(Agent.jobs)
                .selectinload(Job.schedules)
                .selectinload(JobSchedule.retention),
                selectinload(Agent.jobs).selectinload(Job.actions),
                selectinload(Agent.jobs).selectinload(Job.operations),
            )
            .filter(Agent.user_id == user_id)
            .all()
        )

    @jwt_required()
    @blp.arguments(JobCreateInputSchema)
    @blp.response(201, MessageIdSchema)
    def post(self, data):
        user_id = get_jwt_identity()
        agent_id = data["agent_id"]

        agent = Agent.query.filter(Agent.id == agent_id, Agent.user_id == user_id).first_or_404()

        if not agent.online:
            abort(400, message="Agent is offline")

        try:
            job = create_job_instance(data=data, agent_id=agent.id)
        except ValueError as exc:
            abort(400, message=str(exc))

        db.session.add(job)
        db.session.commit()
        emit_job_state(job)

        AgentCommand(agent=agent).sync()

        return {"msg": "Job created", "id": job.id}


@blp.route("/<int:job_id>")
class JobDetail(MethodView):
    @jwt_required()
    @blp.response(200, JobResponseSchema)
    def get(self, job_id):
        user_id = get_jwt_identity()
        return (
            Job.query.options(
                selectinload(Job.agent),
                selectinload(Job.agent).selectinload(Agent.repositories),
                selectinload(Job.schedules).selectinload(JobSchedule.repository),
                selectinload(Job.schedules).selectinload(JobSchedule.retention),
                selectinload(Job.actions),
                selectinload(Job.operations),
            )
            .join(Agent)
            .filter(Job.id == job_id, Agent.user_id == user_id)
            .first_or_404()
        )

    @jwt_required()
    @blp.arguments(JobUpdateInputSchema)
    @blp.response(200, MessageSchema)
    def put(self, data, job_id):
        user_id = get_jwt_identity()
        job = (
            Job.query.join(Agent).filter(Job.id == job_id, Agent.user_id == user_id).first_or_404()
        )
        agent = job.agent

        if not agent.online:
            abort(400, message="Agent is offline")

        try:
            update_job_instance(job=job, data=data)
        except ValueError as exc:
            abort(400, message=str(exc))

        db.session.commit()
        emit_job_state(job)
        AgentCommand(agent=agent).sync()

        return {"msg": "Job updated"}

    @jwt_required()
    @blp.response(200, MessageSchema)
    def delete(self, job_id):
        user_id = get_jwt_identity()
        job = (
            Job.query.join(Agent).filter(Job.id == job_id, Agent.user_id == user_id).first_or_404()
        )
        agent = job.agent

        JobSchedule.query.filter(JobSchedule.job_id == job.id).delete()
        JobAction.query.filter(JobAction.job_id == job.id).delete()

        db.session.delete(job)
        db.session.commit()
        emit_jobs_update(user_id)

        if agent.online:
            AgentCommand(agent=agent).sync()

        return {"msg": "Job deleted"}


@blp.route("/<int:job_id>/run")
class JobOperationRun(MethodView):
    @jwt_required()
    @blp.arguments(JobOperationRunInputSchema)
    @blp.response(202, AgentOperationStartResponseSchema)
    def post(self, data, job_id):
        user_id = get_jwt_identity()
        job = (
            Job.query.join(Agent).filter(Job.id == job_id, Agent.user_id == user_id).first_or_404()
        )

        if not job.agent.online:
            abort(400, message="Agent is offline")

        repository_id = data["repository_id"]
        repository, newly_assigned = _assign_agent_repository(
            user_id,
            job.agent,
            repository_id,
            data.get("recovery_key"),
        )

        if newly_assigned:
            db.session.commit()
            response = AgentCommand(agent=job.agent).sync(await_response=True)
            if response.get("state") != AgentOperationState.success:
                _abort_agent_command_failure(response)

        run_options = normalize_schedule_config(data.get("options") or {})
        operation = start_agent_operation(
            agent=job.agent,
            job=job,
            repository=repository,
            operation_type=AgentOperationType.backup,
            msg="Backup job started",
            log_message="Backup job queued",
            data={"options": run_options},
        )
        AgentCommand(agent=job.agent).run_job(
            job_id=job.id,
            repository_id=repository_id,
            operation_uuid=operation.uuid,
            run_options=run_options,
        )
        return agent_operation_start_response(operation, "Backup job started")


@blp.route("/<int:job_id>/cancel")
class JobCancel(MethodView):
    @jwt_required()
    @blp.response(200, MessageSchema)
    def post(self, job_id):
        user_id = get_jwt_identity()
        job = (
            Job.query.join(Agent).filter(Job.id == job_id, Agent.user_id == user_id).first_or_404()
        )

        AgentCommand(job.agent).cancel_job(job_id=job.id)

        if job.last_operation and job.last_operation.state == AgentOperationState.running:
            job.last_operation.state = AgentOperationState.failed
            db.session.commit()
            emit_job_state(job)

        return {"msg": "Job canceled"}


@blp.route("/<int:job_id>/status")
class JobStatus(MethodView):
    @jwt_required()
    @blp.response(200, JobStatusResponseSchema(many=True))
    def get(self, job_id):
        user_id = get_jwt_identity()
        return (
            AgentOperation.query.join(Agent)
            .filter(
                AgentOperation.job_id == job_id,
                Agent.user_id == user_id,
            )
            .order_by(AgentOperation.started.desc())
            .all()
        )


@blp.route("/dirlist")
class JobDirlist(MethodView):
    @jwt_required()
    @blp.arguments(DirlistQuerySchema, location="query")
    @blp.response(200, DirlistResponseSchema)
    def get(self, args):
        user_id = get_jwt_identity()
        agent_id = args["agent_id"]
        base_directory = args["base_directory"]

        agent = Agent.query.filter(Agent.id == agent_id, Agent.user_id == user_id).first_or_404()

        if not agent.online:
            abort(400, message="Agent is offline")

        response = AgentCommand(agent=agent).get_dirlist(base_directory=base_directory)

        if response.get("state") != AgentOperationState.success:
            _abort_agent_command_failure(response)

        parent_directory = str(Path(base_directory).parent.absolute())

        return {
            "base_directory": base_directory,
            "parent_directory": parent_directory,
            "directories": response.get("data", {}).get("dirlist", []),
        }


@blp.route("/proxmox-guests")
class JobProxmoxGuests(MethodView):
    @jwt_required()
    @blp.arguments(ProxmoxGuestsQuerySchema, location="query")
    @blp.response(200, ProxmoxGuestsResponseSchema)
    def get(self, args):
        user_id = get_jwt_identity()
        agent_id = args["agent_id"]

        agent = Agent.query.filter(Agent.id == agent_id, Agent.user_id == user_id).first_or_404()

        if not agent.online:
            abort(400, message="Agent is offline")

        response = AgentCommand(agent=agent).get_proxmox_guests()

        if response.get("state") != AgentOperationState.success:
            _abort_agent_command_failure(response)

        return {"guests": response.get("data", {}).get("guests", [])}


@blp.route("/<int:job_id>/schedules")
class JobScheduleList(MethodView):
    @jwt_required()
    @blp.arguments(ScheduleCreateInputSchema)
    @blp.response(201, MessageIdSchema)
    def post(self, data, job_id):
        user_id = get_jwt_identity()
        job = (
            Job.query.join(Agent).filter(Job.id == job_id, Agent.user_id == user_id).first_or_404()
        )

        minute = data["minute"]
        hour = data["hour"]
        day_of_week = data["day_of_week"]
        enabled = data["enabled"]
        repository_id = data["repository_id"]
        retention_id = data["retention_id"]
        config = normalize_schedule_config(data.get("config") or {})

        _assign_agent_repository(user_id, job.agent, repository_id, data.get("recovery_key"))
        retention = _validate_retention(user_id, retention_id)

        dow_str = "*" if len(day_of_week) == 7 else ",".join(str(d) for d in day_of_week)
        cron_string = f"{minute} {hour} * * {dow_str}"

        if not croniter.is_valid(cron_string):
            abort(400, message="Invalid cron syntax")

        schedule = JobSchedule(
            job_id=job.id,
            cron_string=cron_string,
            enabled=enabled,
            advanced=False,
            repository_id=repository_id,
            retention_id=retention.id if retention else None,
            config=config,
        )
        db.session.add(schedule)
        db.session.commit()
        emit_job_state(job)

        if job.agent.online:
            AgentCommand(agent=job.agent).sync()

        return {"msg": "Schedule created", "id": schedule.id}


@blp.route("/schedules/<int:schedule_id>")
class JobScheduleDetail(MethodView):
    @jwt_required()
    @blp.arguments(ScheduleUpdateInputSchema)
    @blp.response(200, MessageSchema)
    def put(self, data, schedule_id):
        user_id = get_jwt_identity()
        schedule = (
            JobSchedule.query.join(Job)
            .join(Agent)
            .filter(
                JobSchedule.id == schedule_id,
                Agent.user_id == user_id,
            )
            .first_or_404()
        )

        minute = data["minute"]
        hour = data["hour"]
        day_of_week = data["day_of_week"]
        enabled = data["enabled"]
        repository_id = data["repository_id"]
        retention_id = data["retention_id"]
        config = normalize_schedule_config(data.get("config") or {})

        _assign_agent_repository(
            user_id,
            schedule.job.agent,
            repository_id,
            data.get("recovery_key"),
        )
        retention = _validate_retention(user_id, retention_id)

        dow_str = "*" if len(day_of_week) == 7 else ",".join(str(d) for d in day_of_week)
        cron_string = f"{minute} {hour} * * {dow_str}"

        if not croniter.is_valid(cron_string):
            abort(400, message="Invalid cron syntax")

        job = schedule.job
        schedule.cron_string = cron_string
        schedule.enabled = enabled
        schedule.repository_id = repository_id
        schedule.retention_id = retention.id if retention else None
        schedule.config = config
        db.session.commit()
        emit_job_state(job)

        if job.agent.online:
            AgentCommand(agent=job.agent).sync()

        return {"msg": "Schedule updated"}

    @jwt_required()
    @blp.response(200, MessageSchema)
    def delete(self, schedule_id):
        user_id = get_jwt_identity()
        schedule = (
            JobSchedule.query.join(Job)
            .join(Agent)
            .filter(
                JobSchedule.id == schedule_id,
                Agent.user_id == user_id,
            )
            .first_or_404()
        )

        job = schedule.job
        db.session.delete(schedule)
        db.session.commit()
        emit_job_state(job)

        if job.agent.online:
            AgentCommand(agent=job.agent).sync()

        return {"msg": "Schedule deleted"}


@blp.route("/<int:job_id>/actions")
class JobActionList(MethodView):
    @jwt_required()
    @blp.arguments(ActionCreateInputSchema)
    @blp.response(201, MessageIdSchema)
    def post(self, data, job_id):
        user_id = get_jwt_identity()
        job = (
            Job.query.join(Agent).filter(Job.id == job_id, Agent.user_id == user_id).first_or_404()
        )

        module = data["module"]
        hook = data["hook"]
        action_data = data["data"]

        try:
            module_enum = JobActionModuleEnum[module]
        except KeyError:
            abort(400, message="Invalid module")

        action = JobAction(job_id=job.id, module=module_enum, hook=hook, data=action_data)
        db.session.add(action)
        db.session.commit()
        emit_job_state(job)

        if job.agent.online:
            AgentCommand(agent=job.agent).sync()

        return {"msg": "Action created", "id": action.id}


@blp.route("/actions/<int:action_id>")
class JobActionDetail(MethodView):
    @jwt_required()
    @blp.arguments(ActionUpdateInputSchema)
    @blp.response(200, MessageSchema)
    def put(self, data, action_id):
        user_id = get_jwt_identity()
        action = (
            JobAction.query.join(Job)
            .join(Agent)
            .filter(
                JobAction.id == action_id,
                Agent.user_id == user_id,
            )
            .first_or_404()
        )

        try:
            payload = ActionCreateInputSchema().load(
                {
                    "module": data.get("module", action.module.name if action.module else None),
                    "hook": data.get("hook", action.hook),
                    "data": data.get("data", action.data or {}),
                }
            )
        except ValidationError as exc:
            abort(422, errors=exc.messages)

        action.hook = payload["hook"]
        action.data = payload["data"]
        action.module = JobActionModuleEnum[payload["module"]]

        db.session.commit()

        job = action.job
        emit_job_state(job)
        if job.agent.online:
            AgentCommand(agent=job.agent).sync()

        return {"msg": "Action updated"}

    @jwt_required()
    @blp.response(200, MessageSchema)
    def delete(self, action_id):
        user_id = get_jwt_identity()
        action = (
            JobAction.query.join(Job)
            .join(Agent)
            .filter(
                JobAction.id == action_id,
                Agent.user_id == user_id,
            )
            .first_or_404()
        )

        job = action.job
        agent = job.agent

        db.session.delete(action)
        db.session.commit()
        emit_job_state(job)

        if agent.online:
            AgentCommand(agent=agent).sync()

        return {"msg": "Action deleted"}


@blp.route("/containers")
class JobContainers(MethodView):
    @jwt_required()
    @blp.arguments(ContainersQuerySchema, location="query")
    @blp.response(200, ContainersResponseSchema)
    def get(self, args):
        user_id = get_jwt_identity()
        agent_id = args["agent_id"]
        agent = Agent.query.filter(Agent.id == agent_id, Agent.user_id == user_id).first_or_404()

        if not agent.online:
            abort(400, message="Agent is offline")

        response = AgentCommand(agent=agent).get_containers()
        if response.get("state") != AgentOperationState.success:
            _abort_agent_command_failure(response)

        containers = response.get("data", {}).get("containers", [])

        return {"containers": containers}
