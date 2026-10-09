"""HTTP contracts for jobs; application services own all workflows and writes."""

from flask.views import MethodView
from flask_jwt_extended import get_jwt_identity, jwt_required
from flask_smorest import Blueprint

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
from drastic_server.schemas.scheduling import SchedulePreviewSchema
from drastic_server.services.jobs import execution, management, queries
from drastic_server.views.api.errors import register_service_errors

blp = Blueprint("jobs", __name__, url_prefix="/api/jobs", description="Job operations")
register_service_errors(blp)


@blp.route("/schedules/preview")
class SchedulePreview(MethodView):
    @jwt_required()
    @blp.arguments(SchedulePreviewSchema)
    def post(self, data):
        return execution.preview_schedule(get_jwt_identity(), data)


@blp.route("/")
class JobList(MethodView):
    @jwt_required()
    @blp.response(200, AgentJobsResponseSchema(many=True))
    def get(self):
        return queries.list_jobs(get_jwt_identity())

    @jwt_required()
    @blp.arguments(JobCreateInputSchema)
    @blp.response(201, MessageIdSchema)
    def post(self, data):
        job = management.create_job(get_jwt_identity(), data)
        return {"msg": "Job created", "id": job.id}


@blp.route("/<int:job_id>")
class JobDetail(MethodView):
    @jwt_required()
    @blp.response(200, JobResponseSchema)
    def get(self, job_id):
        return queries.job_detail(get_jwt_identity(), job_id)

    @jwt_required()
    @blp.arguments(JobUpdateInputSchema)
    @blp.response(200, MessageSchema)
    def put(self, data, job_id):
        management.update_job(get_jwt_identity(), job_id, data)
        return {"msg": "Job updated"}

    @jwt_required()
    @blp.response(200, MessageSchema)
    def delete(self, job_id):
        management.delete_job(get_jwt_identity(), job_id)
        return {"msg": "Job deleted"}


@blp.route("/<int:job_id>/run")
class JobOperationRun(MethodView):
    @jwt_required()
    @blp.arguments(JobOperationRunInputSchema)
    @blp.response(202, AgentOperationStartResponseSchema)
    def post(self, data, job_id):
        return execution.start_backup(get_jwt_identity(), job_id, data)


@blp.route("/<int:job_id>/cancel")
class JobCancel(MethodView):
    @jwt_required()
    @blp.response(200, MessageSchema)
    def post(self, job_id):
        execution.cancel_job(get_jwt_identity(), job_id)
        return {"msg": "Job cancellation requested"}


@blp.route("/<int:job_id>/status")
class JobStatus(MethodView):
    @jwt_required()
    @blp.response(200, JobStatusResponseSchema(many=True))
    def get(self, job_id):
        return queries.job_status(get_jwt_identity(), job_id)


@blp.route("/dirlist")
class JobDirlist(MethodView):
    @jwt_required()
    @blp.arguments(DirlistQuerySchema, location="query")
    @blp.response(200, DirlistResponseSchema)
    def get(self, args):
        return execution.list_directories(get_jwt_identity(), args["agent_id"], args["base_directory"])


@blp.route("/proxmox-guests")
class JobProxmoxGuests(MethodView):
    @jwt_required()
    @blp.arguments(ProxmoxGuestsQuerySchema, location="query")
    @blp.response(200, ProxmoxGuestsResponseSchema)
    def get(self, args):
        return execution.list_proxmox_guests(get_jwt_identity(), args["agent_id"])


@blp.route("/<int:job_id>/schedules")
class JobScheduleList(MethodView):
    @jwt_required()
    @blp.arguments(ScheduleCreateInputSchema)
    @blp.response(201, MessageIdSchema)
    def post(self, data, job_id):
        schedule = management.create_schedule(get_jwt_identity(), job_id, data)
        return {"msg": "Schedule created", "id": schedule.id}


@blp.route("/schedules/<int:schedule_id>")
class JobScheduleDetail(MethodView):
    @jwt_required()
    @blp.arguments(ScheduleUpdateInputSchema)
    @blp.response(200, MessageSchema)
    def put(self, data, schedule_id):
        management.update_schedule(get_jwt_identity(), schedule_id, data)
        return {"msg": "Schedule updated"}

    @jwt_required()
    @blp.response(200, MessageSchema)
    def delete(self, schedule_id):
        management.delete_schedule(get_jwt_identity(), schedule_id)
        return {"msg": "Schedule deleted"}


@blp.route("/<int:job_id>/actions")
class JobActionList(MethodView):
    @jwt_required()
    @blp.arguments(ActionCreateInputSchema)
    @blp.response(201, MessageIdSchema)
    def post(self, data, job_id):
        action = management.create_action(get_jwt_identity(), job_id, data)
        return {"msg": "Action created", "id": action.id}


@blp.route("/actions/<int:action_id>")
class JobActionDetail(MethodView):
    @jwt_required()
    @blp.arguments(ActionUpdateInputSchema)
    @blp.response(200, MessageSchema)
    def put(self, data, action_id):
        management.update_action(get_jwt_identity(), action_id, data)
        return {"msg": "Action updated"}

    @jwt_required()
    @blp.response(200, MessageSchema)
    def delete(self, action_id):
        management.delete_action(get_jwt_identity(), action_id)
        return {"msg": "Action deleted"}


@blp.route("/containers")
class JobContainers(MethodView):
    @jwt_required()
    @blp.arguments(ContainersQuerySchema, location="query")
    @blp.response(200, ContainersResponseSchema)
    def get(self, args):
        return execution.list_containers(get_jwt_identity(), args["agent_id"])
